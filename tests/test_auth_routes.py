"""
Access control, CSRF and login behaviour.

Before these fixes only /admin itself checked the login, delete/toggle/run-AI
were GET links, the gate password set no session, and nothing had CSRF.
"""

import pytest

from conftest import CSRF

ADMIN_PAGES = [
    "/admin", "/admin/vehicles", "/register", "/admin/violations",
    "/admin/cameras", "/admin/cameras/add", "/admin/roads", "/admin/roads/add",
    "/admin/location/add", "/admin/road/prohibited/Hostel%20Gate",
]


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_admin_pages_require_login(client, url):
    response = client.get(url)
    assert response.status_code == 302
    assert "/admin-login" in response.headers["Location"]


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_admin_pages_render_for_admin(admin_client, url):
    assert admin_client.get(url).status_code == 200


@pytest.mark.parametrize("url", [
    "/delete_vehicle/1", "/vehicles/1/toggle_status",
    "/admin/cameras/toggle/1", "/run_ai",
])
def test_mutations_are_post_only(admin_client, url):
    """These were GET links: any crawler or <img src> could delete or toggle."""
    assert admin_client.get(url).status_code == 405


def test_post_without_csrf_token_is_rejected(admin_client):
    response = admin_client.post("/delete_vehicle/1")
    assert response.status_code == 400
    with admin_client.application.app_context():
        from backend.db import vehicle_db
        with vehicle_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM vehicles WHERE id=1").fetchone()[0] == 1


def test_csrf_header_is_accepted(gate_client):
    response = gate_client.post("/gate/resume_detection", headers={"X-CSRFToken": CSRF})
    assert response.status_code == 200
    assert response.get_json() == {"status": "resumed"}


# ---------------- admin login ----------------
def _admin_login(client, password, **extra):
    return client.post("/admin-login", data={
        "username": "admin", "password": password, "csrf_token": CSRF, **extra})


def test_admin_login_with_hashed_password(client):
    response = _admin_login(client, "admin123")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin")
    assert client.get("/admin").status_code == 200


def test_admin_login_wrong_password(client):
    assert _admin_login(client, "nope").status_code == 401
    assert client.get("/admin").status_code == 302


def test_admin_login_is_throttled(client):
    for _ in range(5):
        assert _admin_login(client, "wrong").status_code == 401
    # Even the right password is refused while locked out.
    response = _admin_login(client, "admin123")
    assert response.status_code == 429
    assert b"Too many failed attempts" in response.data


def test_login_rejects_offsite_next(client):
    response = _admin_login(client, "admin123", next="//evil.example.com/steal")
    assert response.headers["Location"].endswith("/admin")


def test_login_follows_local_next(client):
    response = _admin_login(client, "admin123", next="/admin/vehicles")
    assert response.headers["Location"].endswith("/admin/vehicles")


def test_logout_clears_session(admin_client):
    admin_client.post("/admin-logout", data={"csrf_token": CSRF})
    assert admin_client.get("/admin").status_code == 302


# ---------------- gate login ----------------
def _gate_login(client, location="Hostel Gate", password="gate123"):
    return client.post("/gate-login", data={
        "location": location, "password": password, "csrf_token": CSRF})


def test_gate_dashboard_requires_login(client):
    response = client.get("/gate/dashboard/Hostel%20Gate")
    assert response.status_code == 302
    assert "/gate-login" in response.headers["Location"]


def test_gate_login_sets_a_real_session(client):
    response = _gate_login(client)
    assert response.status_code == 302
    page = client.get(response.headers["Location"])
    assert page.status_code == 200
    # Heading used to render "Hostel gate Gate" via |capitalize + " Gate".
    assert "Live Camera – Hostel Gate".encode() in page.data


def test_gate_login_is_case_insensitive_on_location(client):
    assert _gate_login(client, location="hostel gate").status_code == 302


def test_gate_login_rejects_wrong_password(client):
    assert _gate_login(client, password="nope").status_code == 401


def test_gate_login_rejects_unknown_location(client):
    assert _gate_login(client, location="Not A Gate").status_code == 401


def test_gate_operator_cannot_open_another_gate(gate_client):
    assert gate_client.get("/gate/dashboard/Hostel%20Gate").status_code == 200
    assert gate_client.get("/gate/dashboard/Main%20gate").status_code == 302


def test_admin_can_open_any_gate(admin_client):
    assert admin_client.get("/gate/dashboard/Main%20gate").status_code == 200


@pytest.mark.parametrize("url", [
    "/gate/latest_result", "/camera_status", "/camera_status/hostel%20gate",
    "/video_feed/Hostel%20Gate",
])
def test_gate_api_requires_login(client, url):
    response = client.get(url)
    assert response.status_code == 401
    assert response.get_json()["status"] == "error"


def test_latest_result_keeps_dashboard_contract(gate_client):
    data = gate_client.get("/gate/latest_result").get_json()
    for key in ("vehicle_number", "confidence", "decision", "reason", "violations", "sms_sent"):
        assert key in data


def test_video_feed_refuses_unknown_gate(admin_client):
    """Each distinct name used to start a detection thread that never exits."""
    assert admin_client.get("/video_feed/made-up-gate").status_code == 404

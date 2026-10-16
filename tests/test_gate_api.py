"""
The per-gate console API: status, resume/rescan, override, operator plate check,
recent events and evidence images.
"""

import pytest

from backend.db import vehicle_db
from conftest import CSRF
from gate import camera, decision

H = {"X-CSRFToken": CSRF}
BASE = "/gate/api/Hostel%20Gate"


@pytest.fixture(autouse=True)
def no_sms(monkeypatch):
    monkeypatch.setattr(decision, "send_sms", lambda phone, msg: None)


def _decide(plate, confidence=90, **extra):
    return decision.decide({"vehicle_number": plate, "confidence": confidence, **extra},
                           "hostel gate")


@pytest.mark.parametrize("method,url", [
    ("get", f"{BASE}/status"), ("get", f"{BASE}/recent"),
    ("post", f"{BASE}/resume"), ("post", f"{BASE}/rescan"),
    ("post", f"{BASE}/override"), ("post", f"{BASE}/check_plate"),
    ("get", f"{BASE}/evidence/1/crop"),
])
def test_console_api_requires_login(client, method, url):
    response = getattr(client, method)(url, headers=H)
    assert response.status_code == 401


def test_operator_cannot_use_another_gates_api(gate_client):
    assert gate_client.get("/gate/api/Main%20gate/status").status_code == 401


def test_unknown_gate_is_404(admin_client):
    assert admin_client.get("/gate/api/Nowhere/status").status_code == 404


def test_status_contract(gate_client):
    data = gate_client.get(f"{BASE}/status").get_json()
    assert data["result"]["decision"] == "WAITING" and data["paused"] is False
    for key in ("reading", "cooldown", "camera", "infer_ms", "worker", "notice"):
        assert key in data
    assert "seq" in data["result"]


def test_post_without_csrf_is_rejected(gate_client):
    assert gate_client.post(f"{BASE}/resume").status_code == 400


def test_resume_unpauses_only_this_gate(gate_client):
    _decide("MN01AB1234")
    decision.set_paused(True, "main gate")
    response = gate_client.post(f"{BASE}/resume", headers=H)
    assert response.get_json()["status"] == "resumed"
    assert not decision.is_paused("hostel gate")
    assert decision.is_paused("main gate")


def test_rescan_clears_the_cooldown(gate_client):
    camera.mark_processed("Hostel Gate", "MN01AB1234")
    assert camera.get_state("Hostel Gate").recent.remaining("MN01AB1234") > 0
    gate_client.post(f"{BASE}/rescan", headers=H)
    assert camera.get_state("Hostel Gate").recent.remaining("MN01AB1234") == 0


def test_override_api(gate_client):
    _decide("MN01AB1234", confidence=40)
    response = gate_client.post(f"{BASE}/override", json={"action": "deny"}, headers=H)
    assert response.get_json()["result"]["decision"] == "DENY ENTRY"
    with vehicle_db() as conn:
        assert conn.execute("SELECT final_decision, operator FROM gate_events").fetchone()[:] == \
            ("DENY ENTRY", "gate:Hostel Gate")


def test_override_api_validates(gate_client):
    assert gate_client.post(f"{BASE}/override", json={"action": "x"}, headers=H).status_code == 400
    assert gate_client.post(f"{BASE}/override", json={"action": "allow"}, headers=H).status_code == 409


def test_check_plate_api_decides_and_starts_cooldown(gate_client):
    response = gate_client.post(f"{BASE}/check_plate", json={"plate": "mn01 ab 1234"}, headers=H)
    result = response.get_json()["result"]
    assert (result["vehicle_number"], result["decision"]) == ("MN01AB1234", "ALLOW ENTRY")
    assert camera.get_state("Hostel Gate").recent.remaining("MN01AB1234") > 0


def test_check_plate_api_rejects_junk(gate_client):
    response = gate_client.post(f"{BASE}/check_plate", json={"plate": "nope"}, headers=H)
    assert response.status_code == 400 and response.get_json()["status"] == "error"


def test_recent_events(gate_client):
    _decide("MN01AB1234")
    events = gate_client.get(f"{BASE}/recent").get_json()["events"]
    assert events[0]["plate"] == "MN01AB1234" and events[0]["decision"] == "ALLOW ENTRY"


def _with_evidence(data_env):
    folder = data_env / "detections" / "20260101"
    folder.mkdir(parents=True)
    (folder / "a_crop.jpg").write_bytes(b"\xff\xd8crop")
    return _decide("MN01AB1234", crop_path="20260101/a_crop.jpg")


def test_evidence_is_served_to_its_gate(gate_client, data_env):
    result = _with_evidence(data_env)
    response = gate_client.get(f"{BASE}/evidence/{result['event_id']}/crop")
    assert response.status_code == 200 and response.data == b"\xff\xd8crop"
    assert gate_client.get(f"{BASE}/evidence/{result['event_id']}/frame").status_code == 404


def test_evidence_is_not_served_to_another_gate(admin_client, data_env):
    result = _with_evidence(data_env)
    url = f"/gate/api/Main%20gate/evidence/{result['event_id']}/crop"
    assert admin_client.get(url).status_code == 404


def test_evidence_path_traversal_is_refused(gate_client, data_env):
    result = _decide("MN01AB1234", crop_path="../../config.py")
    assert gate_client.get(f"{BASE}/evidence/{result['event_id']}/crop").status_code == 404


def test_legacy_latest_result_reads_the_operators_gate(gate_client):
    _decide("MN01AB1234")
    assert gate_client.get("/gate/latest_result").get_json()["vehicle_number"] == "MN01AB1234"

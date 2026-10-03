"""
Admin features: each test pins a bug that existed before this round of fixes.
"""

import pytest

from backend.db import authority_db, vehicle_db
from conftest import CSRF


def _vehicle(number):
    with vehicle_db() as conn:
        return conn.execute("SELECT * FROM vehicles WHERE number = ?", (number,)).fetchone()


def _register(client, **overrides):
    data = {"number": "MN02XY4321", "owner": "Test Owner", "type": "Car",
            "role": "Staff", "phone": "9000000099", "csrf_token": CSRF}
    data.update(overrides)
    return client.post("/register", data=data)


# ---------------- vehicles ----------------
def test_register_normalizes_plate(admin_client):
    response = _register(admin_client, number=" mn 02-xy 4321 ")
    assert response.status_code == 302
    assert _vehicle("MN02XY4321")["status"] == "ACTIVE"


@pytest.mark.parametrize("number", ["ABC", "MN0AB12", "1234567890", ""])
def test_register_rejects_invalid_plate(admin_client, number):
    response = _register(admin_client, number=number)
    assert response.status_code == 400
    assert b"valid Indian plate" in response.data


def test_register_does_not_silently_rewrite_typos(admin_client):
    """normalize_plate would turn O->0; for typed input that must be an error."""
    assert _register(admin_client, number="MNO2XY4321").status_code == 400


def test_register_rejects_duplicate(admin_client):
    response = _register(admin_client, number="mn01ab1234")   # seeded as MN01AB1234
    assert response.status_code == 409
    assert b"already registered" in response.data


@pytest.mark.parametrize("field,value", [
    ("phone", "12345"), ("type", "Spaceship"), ("role", "Pirate"), ("owner", "")])
def test_register_validates_fields(admin_client, field, value):
    assert _register(admin_client, **{field: value}).status_code == 400


def test_register_keeps_entered_values_on_error(admin_client):
    response = _register(admin_client, phone="12", owner="Kept Name")
    assert b'value="Kept Name"' in response.data


def test_delete_vehicle(admin_client):
    vehicle_id = _vehicle("MN01AB1234")["id"]
    response = admin_client.post(f"/delete_vehicle/{vehicle_id}", data={"csrf_token": CSRF})
    assert response.status_code == 302
    assert _vehicle("MN01AB1234") is None


def test_block_and_unblock_vehicle(admin_client):
    vehicle_id = _vehicle("MN01AB1234")["id"]
    admin_client.post(f"/vehicles/{vehicle_id}/toggle_status", data={"csrf_token": CSRF})
    assert _vehicle("MN01AB1234")["status"] == "BLOCKED"
    admin_client.post(f"/vehicles/{vehicle_id}/toggle_status", data={"csrf_token": CSRF})
    assert _vehicle("MN01AB1234")["status"] == "ACTIVE"


def test_vehicle_search_and_pagination(admin_client):
    page = admin_client.get("/admin/vehicles?q=mn01ab")
    assert b"MN01AB1234" in page.data and b"UP19EQ1001" not in page.data
    assert admin_client.get("/admin/vehicles?page=999").status_code == 200   # clamps


# ---------------- cameras ----------------
def test_add_camera_keeps_name_zone_and_coverage(admin_client):
    """The old INSERT only wrote (location, status); the name was dropped."""
    admin_client.post("/admin/cameras/add", data={
        "name": "Gate Cam X", "location": "Hostel Gate", "zone": "North",
        "coverage": "Entry lane", "status": "OFFLINE", "csrf_token": CSRF})
    with authority_db() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE name = 'Gate Cam X'").fetchone()
    assert (row["location"], row["zone"], row["coverage"], row["status"]) == \
           ("Hostel Gate", "North", "Entry lane", "OFFLINE")
    assert b"Gate Cam X" in admin_client.get("/admin/cameras").data


def test_add_camera_requires_name(admin_client):
    response = admin_client.post("/admin/cameras/add", data={
        "name": "", "location": "X", "csrf_token": CSRF})
    assert response.status_code == 400


# ---------------- locations / prohibited time ----------------
def test_add_location_page_exists(admin_client):
    """It used to raise TemplateNotFound."""
    assert admin_client.get("/admin/location/add").status_code == 200


def test_add_location_appears_in_gate_login(admin_client, client):
    response = admin_client.post("/admin/location/add", data={
        "name": "East Gate", "prohibited_start": "22:00", "prohibited_end": "06:00",
        "csrf_token": CSRF})
    assert response.status_code == 302
    assert b"East Gate" in client.get("/gate-login").data


@pytest.mark.parametrize("data,error", [
    ({"name": "hostel gate"}, b"already exists"),                  # case-insensitive
    ({"name": "X", "prohibited_start": "22:00"}, b"both"),
    ({"name": "X", "prohibited_start": "25:00", "prohibited_end": "06:00"}, b"HH:MM"),
    ({"name": "X", "prohibited_start": "06:00", "prohibited_end": "06:00"}, b"differ"),
])
def test_add_location_validation(admin_client, data, error):
    response = admin_client.post("/admin/location/add", data={**data, "csrf_token": CSRF})
    assert response.status_code == 400
    assert error in response.data


def test_prohibited_time_get_does_not_write(admin_client):
    admin_client.get("/admin/road/prohibited/Brand%20New%20Road")
    with vehicle_db() as conn:
        assert conn.execute("SELECT 1 FROM locations WHERE name = 'Brand New Road'").fetchone() is None


def test_prohibited_time_post_saves_and_clears(admin_client):
    url = "/admin/road/prohibited/Hostel%20Gate"
    admin_client.post(url, data={"prohibited_start": "10:00", "prohibited_end": "11:00",
                                 "csrf_token": CSRF})
    with vehicle_db() as conn:
        row = conn.execute("SELECT prohibited_start, prohibited_end FROM locations "
                           "WHERE name = 'Hostel Gate'").fetchone()
    assert tuple(row) == ("10:00", "11:00")

    admin_client.post(url, data={"prohibited_start": "", "prohibited_end": "", "csrf_token": CSRF})
    with vehicle_db() as conn:
        row = conn.execute("SELECT prohibited_start FROM locations WHERE name = 'Hostel Gate'").fetchone()
    assert row[0] is None


def test_prohibited_time_rejects_equal_times(admin_client):
    response = admin_client.post("/admin/road/prohibited/Hostel%20Gate", data={
        "prohibited_start": "09:00", "prohibited_end": "09:00", "csrf_token": CSRF})
    assert response.status_code == 400


# ---------------- roads / AI ----------------
def test_add_road_actually_saves_and_predicts(admin_client):
    """This route used to ignore POST entirely."""
    response = admin_client.post("/admin/roads/add", data={
        "segment": "Ring Road", "curve": "1", "junction": "1", "visibility": "0",
        "lane_width": "0", "traffic_density": "2", "csrf_token": CSRF})
    assert response.status_code == 302
    with authority_db() as conn:
        assert conn.execute("SELECT 1 FROM accident_data WHERE segment = 'Ring Road'").fetchone()
        prediction = conn.execute(
            "SELECT predicted_risk, explanation, curve FROM predictions WHERE segment = 'Ring Road'"
        ).fetchone()
    assert prediction["predicted_risk"] in ("LOW", "MEDIUM", "HIGH")
    assert "Curved road" in prediction["explanation"] and prediction["curve"] == 1
    with vehicle_db() as conn:
        assert conn.execute("SELECT 1 FROM locations WHERE name = 'Ring Road'").fetchone()


def test_add_road_rejects_duplicates_and_bad_values(admin_client):
    base = {"curve": "1", "junction": "0", "visibility": "1", "lane_width": "1",
            "traffic_density": "1", "csrf_token": CSRF}
    assert admin_client.post("/admin/roads/add",
                             data={**base, "segment": "north gate road"}).status_code == 409
    assert admin_client.post("/admin/roads/add",
                             data={**base, "segment": "Y", "visibility": "7"}).status_code == 400


def test_run_ai_post(admin_client):
    response = admin_client.post("/run_ai", data={"csrf_token": CSRF, "next": "roads"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/roads")


# ---------------- dashboard ----------------
def _add_violation(location, timestamp_sql="CURRENT_TIMESTAMP"):
    with vehicle_db() as conn:
        with conn:
            conn.execute(
                f"INSERT INTO violations (vehicle_number, location, violation_type, timestamp) "
                f"VALUES ('MN01AB1234', ?, 'Prohibited time entry', {timestamp_sql})", (location,))


def test_dashboard_lists_gate_locations_case_insensitively(admin_client):
    """Gate locations never appeared, and lowercase rows were counted separately."""
    _add_violation("Academic Gate")
    _add_violation("academic gate")
    page = admin_client.get("/admin").data.decode()
    block = page[page.index("<h3>Academic Gate</h3>"):][:300]
    assert "Violations: <strong>2</strong>" in block


def test_dashboard_alerts_count_only_high_risk(admin_client):
    with authority_db() as conn:
        high = conn.execute("SELECT COUNT(*) FROM predictions WHERE predicted_risk='HIGH'").fetchone()[0]
    assert f"High-Risk Roads<br><b>{high}</b>".encode() in admin_client.get("/admin").data


def test_todays_violations_use_local_time(admin_client):
    """Stored as UTC; shown in local time (they used to be 5.5 h off in IST)."""
    _add_violation("Hostel Gate")
    with vehicle_db() as conn:
        local = conn.execute(
            "SELECT datetime(timestamp, 'localtime') FROM violations ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    assert local.encode() in admin_client.get("/admin").data
    assert local.encode() in admin_client.get("/admin/violations").data


def test_violations_filter(admin_client):
    _add_violation("Library Gate")
    page = admin_client.get("/admin/violations?location=library%20gate").data
    assert b"Library Gate" in page and b"1 violation " in page


# ---------------- public pages / errors ----------------
def test_home_shows_live_counts_not_fabricated_ones(client):
    page = client.get("/").data
    assert b"120+" not in page and b"Masked" not in page
    assert b"<h3>5</h3>" in page   # 5 seeded vehicles


def test_404_page(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404 and b"Page not found" in response.data


def test_api_errors_are_json(gate_client):
    response = gate_client.get("/gate/nope")
    assert response.status_code == 404 and response.get_json()["status"] == "error"

"""
Gate decision engine.

is_prohibited_time() used to strip " gate" before matching, so all six
gate-named locations ('Hostel Gate' -> 'hostel') never matched and prohibited
time was never enforced at a gate.
"""

from datetime import time

import pytest

from backend.db import vehicle_db
from gate import decision


@pytest.mark.parametrize("start,end,now,expected", [
    ("09:00", "17:00", time(12, 0), True),     # same-day window
    ("09:00", "17:00", time(8, 59), False),
    ("09:00", "17:00", time(9, 0), True),      # edges inclusive
    ("09:00", "17:00", time(17, 0), True),
    ("22:00", "06:00", time(23, 30), True),    # crosses midnight
    ("22:00", "06:00", time(3, 0), True),
    ("22:00", "06:00", time(12, 0), False),
    ("06:00", "06:00", time(6, 0), False),     # used to mean "all day"
    ("06:00", "06:00", time(12, 0), False),
    (None, "06:00", time(3, 0), False),
    ("", "", time(3, 0), False),
])
def test_window_contains(start, end, now, expected):
    assert decision.window_contains(start, end, now) is expected


GATE_NAMED = ["Hostel Gate", "Library Gate", "Academic Gate", "Main gate",
              "North Gate Road", "South Gate Road"]


@pytest.mark.parametrize("name", GATE_NAMED)
def test_prohibited_time_matches_gate_named_locations(data_env, name):
    with vehicle_db() as conn:
        with conn:
            conn.execute("UPDATE locations SET prohibited_start='10:00', prohibited_end='11:00' "
                         "WHERE name = ?", (name,))
    # Lowercase, as the camera passes it.
    assert decision.is_prohibited_time(name.lower(), now=time(10, 30)) is True
    assert decision.is_prohibited_time(name.lower(), now=time(12, 0)) is False


def test_unknown_location_is_never_prohibited(data_env):
    assert decision.is_prohibited_time("Nowhere", now=time(3, 0)) is False


@pytest.mark.parametrize("given,expected", [
    ("hostel gate", "Hostel Gate"),
    ("  HOSTEL GATE ", "Hostel Gate"),
    ("hostel", "Hostel Gate"),           # legacy name without " Gate"
    ("main gate", "Main gate"),          # keeps the stored spelling
    ("Unknown Place", "Unknown Place"),
])
def test_canonical_location(data_env, given, expected):
    assert decision.canonical_location(given) == expected


# ---------------- decide() ----------------
@pytest.fixture
def sent_sms(monkeypatch):
    sent = []
    monkeypatch.setattr(decision, "send_sms", lambda phone, msg: sent.append((phone, msg)))
    return sent


@pytest.fixture
def prohibited(monkeypatch):
    monkeypatch.setattr(decision, "is_prohibited_time", lambda location, now=None: True)


def _decide(plate, confidence=90, gate="hostel gate"):
    decision.set_paused(False, gate)
    return decision.decide({"vehicle_number": plate, "confidence": confidence}, gate)


def _violations():
    with vehicle_db() as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT vehicle_number, location, violation_type FROM violations")]


def test_registered_vehicle_allowed(data_env, sent_sms):
    result = _decide("MN01AB1234")
    assert (result["decision"], result["reason"]) == ("ALLOW ENTRY", "Registered vehicle")
    assert decision.is_paused("hostel gate")          # freezes for the operator


def test_unregistered_vehicle_denied(data_env, sent_sms):
    assert _decide("KA09ZZ9999")["reason"] == "Unregistered vehicle"


def test_low_confidence_goes_to_manual_check(data_env, sent_sms):
    result = _decide("MN01AB1234", confidence=40)
    assert (result["decision"], result["reason"]) == ("MANUAL CHECK", "Low OCR confidence")


def test_blocked_vehicle_denied(data_env, sent_sms):
    with vehicle_db() as conn:
        with conn:
            conn.execute("UPDATE vehicles SET status='BLOCKED' WHERE number='MN01AB1234'")
    assert _decide("MN01AB1234")["reason"] == "Blocked vehicle"


def test_prohibited_time_records_canonical_violation(data_env, sent_sms, prohibited):
    result = _decide("MN01AB1234", gate="hostel gate")
    assert (result["decision"], result["reason"]) == ("DENY ENTRY", "Prohibited time violation")
    assert result["violations"] == 1 and result["sms_sent"] is True
    # Canonical spelling, not the lowercased gate key that used to split counts.
    assert _violations() == [("MN01AB1234", "Hostel Gate", "Prohibited time entry")]
    assert sent_sms and "Hostel Gate" in sent_sms[0][1]


def test_emergency_vehicle_is_exempt(data_env, sent_sms, prohibited):
    result = _decide("MN01EM9999")
    assert result["decision"] == "ALLOW ENTRY"
    assert "exemption" in result["reason"]
    assert _violations() == [] and sent_sms == []


def test_paused_gate_does_not_decide_again(data_env, sent_sms, prohibited):
    _decide("MN01AB1234")
    decision.decide({"vehicle_number": "MN01AB1234", "confidence": 90}, "hostel gate")
    assert len(_violations()) == 1


# ---------------- per-gate state ----------------
def test_pause_is_per_gate(data_env, sent_sms):
    """'Next Vehicle' at one gate used to un-pause every gate."""
    _decide("MN01AB1234", gate="hostel gate")
    assert decision.is_paused("Hostel Gate")
    assert not decision.is_paused("library gate")
    assert decision.get_latest_result("library gate")["decision"] == "WAITING"


def test_waiting_update_never_overwrites_a_decision(data_env, sent_sms):
    _decide("MN01AB1234")
    assert decision.set_waiting("Plate detected, not readable yet", "hostel gate") is None
    assert decision.get_latest_result("hostel gate")["decision"] == "ALLOW ENTRY"


def test_seq_changes_only_when_content_changes(data_env):
    first = decision.set_waiting("scanning", "hostel gate")["seq"]
    assert decision.set_waiting("scanning", "hostel gate")["seq"] == first
    assert decision.set_waiting("something else", "hostel gate")["seq"] > first


# ---------------- event log ----------------
def _events():
    with vehicle_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM gate_events ORDER BY id")]


def test_every_decision_is_logged(data_env, sent_sms):
    _decide("MN01AB1234")
    _decide("KA09ZZ9999")
    _decide("MN01AB1234", confidence=40)
    events = _events()
    assert [(e["plate"], e["decision"], e["gate"]) for e in events] == [
        ("MN01AB1234", "ALLOW ENTRY", "Hostel Gate"),
        ("KA09ZZ9999", "DENY ENTRY", "Hostel Gate"),
        ("MN01AB1234", "MANUAL CHECK", "Hostel Gate"),
    ]
    assert decision.get_latest_result("hostel gate")["event_id"] == events[-1]["id"]


def test_violation_links_to_its_event_and_records_sms(data_env, sent_sms, prohibited):
    result = _decide("MN01AB1234")
    with vehicle_db() as conn:
        violation = conn.execute("SELECT event_id, status FROM violations").fetchone()
    assert violation["event_id"] == result["event_id"] and violation["status"] == "OPEN"
    assert _events()[0]["sms_sent"] == 1


def test_result_carries_vehicle_details_without_phone(data_env, sent_sms):
    vehicle = _decide("MN01AB1234")["vehicle"]
    assert vehicle["owner"] == "Demo Student A" and vehicle["role"] == "Student"
    assert "phone" not in vehicle


# ---------------- new rules ----------------
def test_near_match_goes_to_manual_check_with_suggestion(data_env, sent_sms):
    result = _decide("MN01AB1284")        # registered: MN01AB1234
    assert result["decision"] == "MANUAL CHECK"
    assert result["suggested_plate"] == "MN01AB1234"
    assert _events()[0]["suggested_plate"] == "MN01AB1234"


def test_unrelated_unregistered_plate_is_still_denied(data_env, sent_sms):
    result = _decide("KA09ZZ9999")
    assert (result["decision"], result["suggested_plate"]) == ("DENY ENTRY", None)


def _set_valid_until(value):
    with vehicle_db() as conn:
        with conn:
            conn.execute("UPDATE vehicles SET valid_until=? WHERE number='MN01AB1234'", (value,))


def test_expired_pass_is_denied(data_env, sent_sms):
    _set_valid_until("2020-01-01")
    result = _decide("MN01AB1234")
    assert result["decision"] == "DENY ENTRY" and "Pass expired" in result["reason"]


def test_pass_valid_through_its_last_day(data_env, sent_sms):
    from datetime import date
    _set_valid_until(date.today().isoformat())
    assert _decide("MN01AB1234")["decision"] == "ALLOW ENTRY"


@pytest.mark.parametrize("valid_until,expired", [
    (None, False), ("", False), ("2020-01-01", True), ("2999-12-31", False), ("garbage", False),
])
def test_pass_expired(valid_until, expired):
    assert decision.pass_expired(valid_until) is expired


# ---------------- operator actions ----------------
def test_override_settles_the_event(data_env, sent_sms):
    _decide("MN01AB1234", confidence=40)                 # MANUAL CHECK
    result = decision.override("hostel gate", "allow", "gate:Hostel Gate")
    assert result["decision"] == "ALLOW ENTRY" and "Operator allowed" in result["reason"]
    event = _events()[0]
    assert (event["final_decision"], event["operator"]) == ("ALLOW ENTRY", "gate:Hostel Gate")
    assert decision.is_paused("hostel gate")


def test_override_without_a_decision_is_refused(data_env):
    with pytest.raises(ValueError):
        decision.override("hostel gate", "allow")


def test_operator_check_rechecks_a_corrected_plate(data_env, sent_sms):
    _decide("MN01AB1284")                                # MANUAL + suggestion
    result = decision.operator_check("hostel gate", "MN01AB1234", "gate:Hostel Gate")
    assert (result["decision"], result["source"]) == ("ALLOW ENTRY", "operator")
    events = _events()
    assert events[0]["final_decision"] == "RECHECKED"
    assert (events[1]["plate"], events[1]["source"]) == ("MN01AB1234", "operator")


def test_operator_check_applies_prohibited_time(data_env, sent_sms, prohibited):
    result = decision.operator_check("hostel gate", "MN01AB1234")
    assert result["reason"] == "Prohibited time violation" and len(_violations()) == 1


def test_operator_check_rejects_invalid_plates(data_env):
    with pytest.raises(ValueError):
        decision.operator_check("hostel gate", "HELLO")

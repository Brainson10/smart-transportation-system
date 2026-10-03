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
    decision.set_paused(False)
    return decision.decide({"vehicle_number": plate, "confidence": confidence}, gate)


def _violations():
    with vehicle_db() as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT vehicle_number, location, violation_type FROM violations")]


def test_registered_vehicle_allowed(data_env, sent_sms):
    result = _decide("MN01AB1234")
    assert (result["decision"], result["reason"]) == ("ALLOW ENTRY", "Registered vehicle")
    assert decision.is_paused()          # freezes for the operator


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

"""
Gate decision engine and the /gate JSON API.

Fixes over the previous version:
  * is_prohibited_time() stripped " gate" from the name before matching, so
    'Hostel Gate' looked up 'hostel' -- prohibited time was never enforced at any
    of the six gate-named locations. Full names now match case-insensitively.
  * A start == end window was treated as "prohibited all day"; it now means no
    restriction.
  * Violations were recorded under a lowercased gate name, splitting counts
    ('academic block road' vs 'Academic Block Road'). They now use the canonical
    locations.name spelling.
  * The vehicle's role was computed and never used, so emergency vehicles got
    violations and SMS alerts. They are now exempt from prohibited hours.
  * Connections were opened/closed by hand and leaked on any exception.
  * The /gate API was unauthenticated.
"""

import logging
import re
import threading
from datetime import datetime

from flask import Blueprint, jsonify

import config
from backend.auth import gate_api_required
from backend.db import normalize_plate_text, vehicle_db
from gate.sms import send_sms

log = logging.getLogger(__name__)

# =================================================
# PAUSE FLAG (single source of truth)
# =================================================
PAUSED_FOR_MANUAL = False
_PAUSE_LOCK = threading.Lock()


def is_paused():
    with _PAUSE_LOCK:
        return PAUSED_FOR_MANUAL


def set_paused(value):
    global PAUSED_FOR_MANUAL
    with _PAUSE_LOCK:
        PAUSED_FOR_MANUAL = bool(value)


# =================================================
# LATEST RESULT (UI STATE)
# -------------------------------------------------
# Every key the dashboard JS reads must always be present, so updates are merged
# onto this template rather than replacing the dict wholesale.
# =================================================
_DEFAULT_RESULT = {
    "vehicle_number": None,
    "confidence": 0,
    "decision": "WAITING",
    "reason": "No vehicle detected",
    "violations": 0,
    "sms_sent": False,
    "raw_text": None,
}

latest_result = dict(_DEFAULT_RESULT)
_RESULT_LOCK = threading.Lock()


def update_latest_result(data, merge=False):
    """Seed from the default template (or the current state) so no key is ever missing."""
    global latest_result
    with _RESULT_LOCK:
        base = dict(latest_result) if merge else dict(_DEFAULT_RESULT)
        base.update(data or {})
        latest_result = base
        return dict(base)


def get_latest_result():
    with _RESULT_LOCK:
        return dict(latest_result)


# =================================================
# LOCATIONS
# =================================================
_TRAILING_GATE = re.compile(r"\s+gate$", re.IGNORECASE)


def _find_location(conn, name):
    """
    Look a gate/location up case-insensitively by its FULL name, with a fallback
    for names given with or without a trailing " Gate".
    """
    name = (name or "").strip()
    if not name:
        return None
    candidates = [name]
    if _TRAILING_GATE.search(name):
        candidates.append(_TRAILING_GATE.sub("", name))
    else:
        candidates.append(f"{name} gate")
    for candidate in candidates:
        row = conn.execute(
            "SELECT name, prohibited_start, prohibited_end FROM locations "
            "WHERE LOWER(name) = LOWER(?)", (candidate,)).fetchone()
        if row:
            return row
    return None


def canonical_location(name):
    """The locations.name spelling for a gate, so violations never split by case."""
    with vehicle_db() as conn:
        row = _find_location(conn, name)
    return row["name"] if row else (name or "").strip()


def _parse_hhmm(value):
    return datetime.strptime(value.strip(), "%H:%M").time()


def window_contains(start, end, now):
    """True when `now` falls inside [start, end]; windows may cross midnight."""
    if not start or not end:
        return False
    start_time, end_time = _parse_hhmm(start), _parse_hhmm(end)
    if start_time == end_time:
        return False                      # zero-length window: no restriction
    if start_time < end_time:
        return start_time <= now <= end_time
    return now >= start_time or now <= end_time   # crosses midnight


def is_prohibited_time(location_name, now=None):
    now = now or datetime.now().time()
    try:
        with vehicle_db() as conn:
            row = _find_location(conn, location_name)
        if not row:
            log.debug("no location row for %r; no prohibited window", location_name)
            return False
        return window_contains(row["prohibited_start"], row["prohibited_end"], now)
    except ValueError as exc:
        log.warning("invalid prohibited time for %r: %s", location_name, exc)
        return False


# =================================================
# VEHICLES
# =================================================
_PLATE_MATCH = "UPPER(REPLACE(number, ' ', '')) = ?"


def get_vehicle(vehicle_number):
    with vehicle_db() as conn:
        return conn.execute(
            f"SELECT number, role, status, phone, violation_count FROM vehicles WHERE {_PLATE_MATCH}",
            (normalize_plate_text(vehicle_number),)).fetchone()


def get_violation_count(vehicle_number):
    vehicle = get_vehicle(vehicle_number)
    return vehicle["violation_count"] or 0 if vehicle else 0


def check_vehicle_db(vehicle_number):
    vehicle = get_vehicle(vehicle_number)
    if not vehicle:
        return None, None, "DENY ENTRY", "Unregistered vehicle"
    if vehicle["status"] != "ACTIVE":
        return vehicle["role"], vehicle["status"], "DENY ENTRY", "Blocked vehicle"
    return vehicle["role"], vehicle["status"], "ALLOW ENTRY", "Registered vehicle"


def is_emergency_role(role):
    return "emergency" in (role or "").lower()


def record_violation(vehicle_number, location, violation_type="Prohibited time entry"):
    """Insert the violation and bump the vehicle's count atomically. Returns (phone, count)."""
    plate = normalize_plate_text(vehicle_number)
    with vehicle_db() as conn:
        with conn:
            # timestamp defaults to CURRENT_TIMESTAMP (UTC); readers convert to local time.
            conn.execute(
                "INSERT INTO violations (vehicle_number, location, violation_type) VALUES (?, ?, ?)",
                (plate, location, violation_type))
            conn.execute(
                f"UPDATE vehicles SET violation_count = COALESCE(violation_count, 0) + 1 "
                f"WHERE {_PLATE_MATCH}", (plate,))
        row = conn.execute(
            f"SELECT phone, violation_count FROM vehicles WHERE {_PLATE_MATCH}", (plate,)).fetchone()
    return (row["phone"], row["violation_count"]) if row else (None, 0)


# =================================================
# DECISION ENGINE
# =================================================
def _finish(result):
    """Freeze the gate on every decision until the operator presses Next Vehicle."""
    set_paused(True)
    return update_latest_result(result)


def decide(anpr_result, gate_location):
    if is_paused():
        return get_latest_result()

    location = canonical_location(gate_location)
    vehicle_number = normalize_plate_text(anpr_result.get("vehicle_number")) or None
    confidence = anpr_result.get("confidence", 0)
    base = {"vehicle_number": vehicle_number, "confidence": confidence,
            "raw_text": anpr_result.get("raw_text")}

    if not vehicle_number:
        return _finish({**base, "decision": "MANUAL CHECK", "reason": "No valid plate",
                        "violations": 0})

    # Reachable since the ANPR rewrite: confidence used to be a constant 95.
    if confidence < config.MANUAL_CHECK_CONFIDENCE:
        return _finish({**base, "decision": "MANUAL CHECK", "reason": "Low OCR confidence",
                        "violations": get_violation_count(vehicle_number)})

    role, _status, decision, reason = check_vehicle_db(vehicle_number)

    if decision == "ALLOW ENTRY" and is_prohibited_time(location):
        if is_emergency_role(role):
            log.info("%s: emergency vehicle %s admitted during prohibited hours",
                     location, vehicle_number)
            return _finish({**base, "decision": "ALLOW ENTRY",
                            "reason": "Emergency vehicle - prohibited-time exemption",
                            "violations": get_violation_count(vehicle_number)})

        phone, count = record_violation(vehicle_number, location)
        sms_sent = False
        if phone:
            send_sms(phone,
                     f"ALERT: Your vehicle {vehicle_number} was denied entry at {location} "
                     f"due to prohibited time. Violation count: {count}.")
            sms_sent = True
        log.info("%s: %s denied (prohibited time), violation #%s", location, vehicle_number, count)
        return _finish({**base, "decision": "DENY ENTRY", "reason": "Prohibited time violation",
                        "violations": count, "sms_sent": sms_sent})

    return _finish({**base, "decision": decision, "reason": reason,
                    "violations": get_violation_count(vehicle_number)})


# =================================================
# GATE CONTROL API
# =================================================
gate_bp = Blueprint("gate", __name__)


@gate_bp.route("/resume_detection", methods=["POST"])
@gate_api_required
def resume_detection():
    # Imported here, not at module scope, to avoid a camera <-> decision cycle.
    from gate.camera import reset_detection_state

    set_paused(False)
    reset_detection_state()
    update_latest_result({"decision": "WAITING", "reason": "Ready for next vehicle"})
    return jsonify({"status": "resumed"})


@gate_bp.route("/latest_result")
@gate_api_required
def latest_result_api():
    return jsonify(get_latest_result())


@gate_bp.route("/send_violation_sms", methods=["POST"])
@gate_api_required
def send_violation_sms():
    vehicle_number = get_latest_result().get("vehicle_number")
    if not vehicle_number:
        return jsonify({"status": "error", "message": "No vehicle detected"}), 400

    vehicle = get_vehicle(vehicle_number)
    if not vehicle or not vehicle["phone"]:
        return jsonify({"status": "error", "message": "Phone number not found"}), 404

    send_sms(vehicle["phone"],
             f"ALERT: Your vehicle {vehicle_number} violated gate rules.\n"
             f"Violation count: {vehicle['violation_count'] or 0}.\n"
             f"Please contact security if needed.")
    return jsonify({"status": "ok", "message": f"SMS sent to {vehicle['phone']}"})

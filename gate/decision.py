"""
Gate decision engine and the /gate JSON API.

Fixes over the original version:
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

And in this round:
  * Pause state and the latest result are PER GATE. They used to be process
    globals, so "Next Vehicle" at one gate un-paused every gate.
  * Every decision is written to gate_events with its evidence images, so the
    admin can audit ALLOW / DENY / MANUAL CHECK, not only violations.
  * Visitor passes expire (vehicles.valid_until).
  * An unregistered read one glyph away from a registered plate goes to
    MANUAL CHECK with a suggestion instead of a flat DENY: it is far more likely
    a misread than a stranger. It is never auto-corrected.
  * The operator can approve / deny a decision, or re-check a corrected plate.
"""

import itertools
import logging
import re
import threading
from datetime import date, datetime

from flask import Blueprint, abort, jsonify, request, send_from_directory

import config
from backend.auth import current_gate, gate_api_required, is_admin, operator_name
from backend.db import normalize_plate_text, vehicle_db
from gate.plate_utils import clean_ocr_text, find_near_match, is_valid_plate
from gate.sms import send_sms

log = logging.getLogger(__name__)

ALLOW, DENY, MANUAL, WAITING = "ALLOW ENTRY", "DENY ENTRY", "MANUAL CHECK", "WAITING"


# =================================================
# PER-GATE STATE (pause flag + latest result)
# -------------------------------------------------
# Every key the dashboard JS reads must always be present, so updates are merged
# onto this template rather than replacing the dict wholesale. `seq` increases
# on every change, so the console re-renders only when something happened.
# =================================================
_DEFAULT_RESULT = {
    "gate": None,
    "vehicle_number": None,
    "confidence": 0,
    "decision": WAITING,
    "reason": "No vehicle detected",
    "violations": 0,
    "sms_sent": False,
    "raw_text": None,
    "event_id": None,
    "source": None,
    "final_decision": None,
    "suggested_plate": None,
    "vehicle": None,
    "has_frame": False,
    "has_crop": False,
    "decided_at": None,
}

_GATES = {}
_GATES_LOCK = threading.Lock()
_SEQ = itertools.count(1)


def gate_key(gate):
    return (gate or "default").strip().lower()


def _gate(gate):
    """The state dict for one gate. Caller holds _GATES_LOCK."""
    key = gate_key(gate)
    state = _GATES.get(key)
    if state is None:
        state = _GATES[key] = {"paused": False,
                               "result": {**_DEFAULT_RESULT, "seq": next(_SEQ)}}
    return state


def is_paused(gate=None):
    with _GATES_LOCK:
        return _gate(gate)["paused"]


def set_paused(value, gate=None):
    """gate=None applies to every gate (used by tests and shutdown)."""
    with _GATES_LOCK:
        targets = list(_GATES.values()) if gate is None else [_gate(gate)]
        for state in targets:
            state["paused"] = bool(value)


def _strip_seq(result):
    return {k: v for k, v in result.items() if k != "seq"}


def _update(state, data, merge):
    base = dict(state["result"]) if merge else dict(_DEFAULT_RESULT)
    base.update(data or {})
    # Unchanged content keeps its seq: the console does not flicker on the
    # worker's repeated "not readable yet" updates.
    if _strip_seq(base) == _strip_seq(state["result"]):
        return dict(state["result"])
    base["seq"] = next(_SEQ)
    state["result"] = base
    return dict(base)


def update_latest_result(data, gate=None, merge=False):
    """Seed from the default template (or the current state) so no key is ever missing."""
    with _GATES_LOCK:
        return _update(_gate(gate), data, merge)


def set_waiting(reason, gate=None):
    """
    A WAITING status line, applied only if the gate is not frozen on a decision.

    Checked and written under one lock: the worker can be mid-inference when an
    operator decision pauses the gate, and must not overwrite that decision.
    """
    with _GATES_LOCK:
        state = _gate(gate)
        if state["paused"]:
            return None
        return _update(state, {"decision": WAITING, "reason": reason}, merge=False)


def get_latest_result(gate=None):
    with _GATES_LOCK:
        return dict(_gate(gate)["result"])


def reset_all():
    """Forget every gate's state (tests)."""
    with _GATES_LOCK:
        _GATES.clear()


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


def known_location(name):
    with vehicle_db() as conn:
        return conn.execute("SELECT 1 FROM locations WHERE LOWER(name) = LOWER(?)",
                            ((name or "").strip(),)).fetchone() is not None


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
_VEHICLE_COLUMNS = ("id, number, owner, type, role, status, phone, violation_count, "
                    "valid_until")


def get_vehicle(vehicle_number):
    with vehicle_db() as conn:
        return conn.execute(
            f"SELECT {_VEHICLE_COLUMNS} FROM vehicles WHERE {_PLATE_MATCH}",
            (normalize_plate_text(vehicle_number),)).fetchone()


def get_violation_count(vehicle_number):
    vehicle = get_vehicle(vehicle_number)
    return vehicle["violation_count"] or 0 if vehicle else 0


def pass_expired(valid_until, today=None):
    """True once the pass's last valid day (YYYY-MM-DD) is behind us."""
    if not valid_until:
        return False
    try:
        last_day = datetime.strptime(str(valid_until).strip(), "%Y-%m-%d").date()
    except ValueError:
        log.warning("ignoring unparseable valid_until %r", valid_until)
        return False
    return (today or date.today()) > last_day


def vehicle_summary(row):
    """What the gate console shows about a vehicle (no phone number)."""
    if not row:
        return None
    return {
        "number": row["number"], "owner": row["owner"], "type": row["type"],
        "role": row["role"], "status": row["status"],
        "valid_until": row["valid_until"], "pass_expired": pass_expired(row["valid_until"]),
    }


def find_registered_near_match(plate):
    with vehicle_db() as conn:
        registered = [r["number"] for r in conn.execute("SELECT number FROM vehicles")]
    return find_near_match(plate, registered)


def check_vehicle_db(vehicle_number):
    vehicle = get_vehicle(vehicle_number)
    if not vehicle:
        return None, None, DENY, "Unregistered vehicle"
    if vehicle["status"] != "ACTIVE":
        return vehicle["role"], vehicle["status"], DENY, "Blocked vehicle"
    if pass_expired(vehicle["valid_until"]):
        return vehicle["role"], vehicle["status"], DENY, "Pass expired"
    return vehicle["role"], vehicle["status"], ALLOW, "Registered vehicle"


def is_emergency_role(role):
    return "emergency" in (role or "").lower()


def record_violation(vehicle_number, location, violation_type="Prohibited time entry",
                     event_id=None):
    """Insert the violation and bump the vehicle's count atomically. Returns (phone, count)."""
    plate = normalize_plate_text(vehicle_number)
    with vehicle_db() as conn:
        with conn:
            # timestamp defaults to CURRENT_TIMESTAMP (UTC); readers convert to local time.
            conn.execute(
                "INSERT INTO violations (vehicle_number, location, violation_type, event_id) "
                "VALUES (?, ?, ?, ?)", (plate, location, violation_type, event_id))
            conn.execute(
                f"UPDATE vehicles SET violation_count = COALESCE(violation_count, 0) + 1 "
                f"WHERE {_PLATE_MATCH}", (plate,))
        row = conn.execute(
            f"SELECT phone, violation_count FROM vehicles WHERE {_PLATE_MATCH}", (plate,)).fetchone()
    return (row["phone"], row["violation_count"]) if row else (None, 0)


# =================================================
# EVENT LOG
# =================================================
def log_event(gate, outcome, source="auto", operator=None):
    with vehicle_db() as conn:
        with conn:
            cursor = conn.execute("""
                INSERT INTO gate_events
                (gate, plate, raw_text, confidence, decision, reason, source, operator,
                 suggested_plate, evidence_path, crop_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (gate, outcome.get("vehicle_number"), outcome.get("raw_text"),
                  outcome.get("confidence"), outcome["decision"], outcome["reason"],
                  source, operator, outcome.get("suggested_plate"),
                  outcome.get("evidence_path"), outcome.get("crop_path")))
            return cursor.lastrowid


def _mark_event(event_id, **fields):
    if not event_id or not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with vehicle_db() as conn:
        with conn:
            conn.execute(f"UPDATE gate_events SET {assignments} WHERE id = ?",
                         (*fields.values(), event_id))


def get_event(event_id):
    with vehicle_db() as conn:
        return conn.execute("SELECT * FROM gate_events WHERE id = ?", (event_id,)).fetchone()


def recent_events(gate, limit=10):
    with vehicle_db() as conn:
        rows = conn.execute("""
            SELECT id, plate, decision, final_decision, reason, confidence, source,
                   datetime(created_at, 'localtime') AS local_time
            FROM gate_events WHERE LOWER(gate) = LOWER(?)
            ORDER BY id DESC LIMIT ?
        """, (canonical_location(gate), limit)).fetchall()
    return [dict(r) for r in rows]


# =================================================
# DECISION ENGINE
# =================================================
def evaluate(vehicle_number, confidence, location, now=None):
    """
    The rules, without side effects. Returns a dict with decision, reason,
    vehicle (row or None), suggested_plate, and `violation` (True when the
    caller must record a prohibited-time violation).

    Order: no plate / low confidence -> MANUAL; unregistered -> MANUAL with a
    suggestion if a registered plate is one glyph away, else DENY; blocked or
    expired pass -> DENY; prohibited window -> DENY + violation (emergency
    vehicles exempt); otherwise ALLOW.
    """
    out = {"vehicle": None, "suggested_plate": None, "violation": False}
    if not vehicle_number:
        return {**out, "decision": MANUAL, "reason": "No valid plate"}

    vehicle = get_vehicle(vehicle_number)
    out["vehicle"] = vehicle
    if not vehicle:
        out["suggested_plate"] = find_registered_near_match(vehicle_number)

    # Reachable since the ANPR rewrite: confidence used to be a constant 95.
    if confidence < config.MANUAL_CHECK_CONFIDENCE:
        return {**out, "decision": MANUAL, "reason": "Low OCR confidence"}

    if not vehicle:
        if out["suggested_plate"]:
            return {**out, "decision": MANUAL,
                    "reason": f"Not registered - possible misread of {out['suggested_plate']}"}
        return {**out, "decision": DENY, "reason": "Unregistered vehicle"}
    if vehicle["status"] != "ACTIVE":
        return {**out, "decision": DENY, "reason": "Blocked vehicle"}
    if pass_expired(vehicle["valid_until"]):
        return {**out, "decision": DENY, "reason": f"Pass expired on {vehicle['valid_until']}"}

    if is_prohibited_time(location, now=now):
        if is_emergency_role(vehicle["role"]):
            return {**out, "decision": ALLOW,
                    "reason": "Emergency vehicle - prohibited-time exemption"}
        return {**out, "decision": DENY, "reason": "Prohibited time violation",
                "violation": True}

    return {**out, "decision": ALLOW, "reason": "Registered vehicle"}


def decide(anpr_result, gate_location, source="auto", operator=None):
    """Apply the rules, log the event, freeze the gate. Returns the new result."""
    if is_paused(gate_location):
        return get_latest_result(gate_location)

    location = canonical_location(gate_location)
    vehicle_number = normalize_plate_text(anpr_result.get("vehicle_number")) or None
    confidence = anpr_result.get("confidence", 0)

    outcome = evaluate(vehicle_number, confidence, location)
    vehicle = outcome["vehicle"]
    result = {
        "gate": location,
        "vehicle_number": vehicle_number,
        "confidence": confidence,
        "raw_text": anpr_result.get("raw_text"),
        "decision": outcome["decision"],
        "reason": outcome["reason"],
        "suggested_plate": outcome["suggested_plate"],
        "vehicle": vehicle_summary(vehicle),
        "violations": (vehicle["violation_count"] or 0) if vehicle else 0,
        "source": source,
        "has_frame": bool(anpr_result.get("evidence_path")),
        "has_crop": bool(anpr_result.get("crop_path")),
        "decided_at": datetime.now().strftime("%H:%M:%S"),
    }
    result["event_id"] = log_event(location, {**result, **{
        "evidence_path": anpr_result.get("evidence_path"),
        "crop_path": anpr_result.get("crop_path")}}, source, operator)

    if outcome["violation"]:
        phone, count = record_violation(vehicle_number, location, event_id=result["event_id"])
        result["violations"] = count
        if phone:
            send_sms(phone,
                     f"ALERT: Your vehicle {vehicle_number} was denied entry at {location} "
                     f"due to prohibited time. Violation count: {count}.")
            result["sms_sent"] = True
            _mark_event(result["event_id"], sms_sent=1)
        log.info("%s: %s denied (prohibited time), violation #%s",
                 location, vehicle_number, count)
    else:
        log.info("%s: %s -> %s (%s)", location, vehicle_number, outcome["decision"],
                 outcome["reason"])

    # Freeze the gate on every decision until the operator presses Next Vehicle.
    with _GATES_LOCK:
        state = _gate(gate_location)
        state["paused"] = True
        return _update(state, result, merge=False)


def override(gate, action, operator=None):
    """Operator settles the current decision: action is 'allow' or 'deny'."""
    current = get_latest_result(gate)
    if current["decision"] == WAITING or not current.get("event_id"):
        raise ValueError("There is no decision to override.")
    final = ALLOW if action == "allow" else DENY
    _mark_event(current["event_id"], final_decision=final, operator=operator)
    log.info("%s: operator %s set %s to %s", gate, operator, current["vehicle_number"], final)
    verb = "allowed" if final == ALLOW else "denied"
    return update_latest_result({
        "decision": final, "final_decision": final,
        "reason": f"Operator {verb} entry (was: {current['reason']})",
    }, gate, merge=True)


def operator_check(gate, plate, operator=None):
    """
    Decide a plate the operator confirmed, corrected or typed in.

    The current decision (if any) is closed as RECHECKED and its evidence is
    carried over, since it is the same vehicle at the barrier.
    """
    plate = clean_ocr_text(plate)
    if not is_valid_plate(plate):
        raise ValueError("Enter a valid plate number, e.g. MN01AB1234.")
    current = get_latest_result(gate)
    evidence = {}
    if current.get("event_id") and current["decision"] != WAITING:
        event = get_event(current["event_id"])
        if event and event["final_decision"] is None:
            _mark_event(event["id"], final_decision="RECHECKED", operator=operator)
        if event:
            evidence = {"evidence_path": event["evidence_path"], "crop_path": event["crop_path"],
                        "raw_text": event["raw_text"]}
    set_paused(False, gate)
    return decide({"vehicle_number": plate, "confidence": 100, **evidence},
                  gate, source="operator", operator=operator)


# =================================================
# GATE CONTROL API
# =================================================
gate_bp = Blueprint("gate", __name__)


def _require_location(gate):
    if not known_location(gate):
        abort(404, description=f"Unknown gate '{gate}'.")


def _session_gate():
    """Legacy routes carry no gate: the operator's own, or ?gate= for an admin."""
    if is_admin():
        return request.args.get("gate") or current_gate() or "default"
    return current_gate() or "default"


def _payload():
    return request.get_json(silent=True) or request.form


def _resume(gate, clear_cooldown=False):
    # Imported here, not at module scope, to avoid a camera <-> decision cycle.
    from gate.camera import clear_cooldown as _clear, reset_detection_state

    if clear_cooldown:
        _clear(gate)
    set_paused(False, gate)
    reset_detection_state(gate)
    return update_latest_result({"decision": WAITING, "reason": "Ready for next vehicle"}, gate)


@gate_bp.route("/api/<gate>/status")
@gate_api_required
def gate_status(gate):
    _require_location(gate)
    from gate.camera import live_status
    return jsonify({"result": get_latest_result(gate), "paused": is_paused(gate),
                    **live_status(gate)})


@gate_bp.route("/api/<gate>/recent")
@gate_api_required
def gate_recent(gate):
    _require_location(gate)
    return jsonify({"events": recent_events(gate)})


@gate_bp.route("/api/<gate>/resume", methods=["POST"])
@gate_api_required
def gate_resume(gate):
    _require_location(gate)
    return jsonify({"status": "resumed", "result": _resume(gate)})


@gate_bp.route("/api/<gate>/rescan", methods=["POST"])
@gate_api_required
def gate_rescan(gate):
    """Next Vehicle, and forget the cooldown: re-read the vehicle at the barrier."""
    _require_location(gate)
    return jsonify({"status": "resumed", "result": _resume(gate, clear_cooldown=True)})


@gate_bp.route("/api/<gate>/override", methods=["POST"])
@gate_api_required
def gate_override(gate):
    _require_location(gate)
    action = (_payload().get("action") or "").strip().lower()
    if action not in ("allow", "deny"):
        abort(400, description="action must be 'allow' or 'deny'.")
    try:
        result = override(gate, action, operator_name())
    except ValueError as exc:
        abort(409, description=str(exc))
    return jsonify({"status": "ok", "result": result})


@gate_bp.route("/api/<gate>/check_plate", methods=["POST"])
@gate_api_required
def gate_check_plate(gate):
    _require_location(gate)
    try:
        result = operator_check(gate, _payload().get("plate"), operator_name())
    except ValueError as exc:
        abort(400, description=str(exc))
    # The camera must not decide this vehicle again the moment the gate resumes.
    from gate.camera import mark_processed
    mark_processed(gate, result.get("vehicle_number"))
    return jsonify({"status": "ok", "result": result})


def send_evidence(event, kind):
    """Serve one evidence image; send_from_directory refuses paths outside the folder."""
    if event is None:
        abort(404, description="No such event.")
    path = event["crop_path"] if kind == "crop" else event["evidence_path"]
    if kind not in ("crop", "frame") or not path:
        abort(404, description="No evidence image for this event.")
    response = send_from_directory(config.DETECTION_DIR, path, max_age=3600)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@gate_bp.route("/api/<gate>/evidence/<int:event_id>/<kind>")
@gate_api_required
def gate_evidence(gate, event_id, kind):
    event = get_event(event_id)
    # An operator may only see their own gate's evidence.
    if event is not None and gate_key(event["gate"]) != gate_key(canonical_location(gate)):
        abort(404, description="No such event at this gate.")
    return send_evidence(event, kind)


# ---------- legacy routes (pre per-gate API) ----------
@gate_bp.route("/resume_detection", methods=["POST"])
@gate_api_required
def resume_detection():
    _resume(_session_gate())
    return jsonify({"status": "resumed"})


@gate_bp.route("/latest_result")
@gate_api_required
def latest_result_api():
    return jsonify(get_latest_result(_session_gate()))


@gate_bp.route("/send_violation_sms", methods=["POST"])
@gate_api_required
def send_violation_sms():
    vehicle_number = get_latest_result(_session_gate()).get("vehicle_number")
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

"""
Gate Activity: every decision the gates made, with its evidence images.

Before gate_events existed only violations were stored, so an ALLOW, a DENY of
an unregistered car or a MANUAL CHECK left no trace at all.
"""

import re

from flask import Blueprint, abort, render_template, request

from backend.auth import admin_required
from backend.db import paginate, vehicle_db
from backend.exports import csv_response
from gate.decision import get_event, send_evidence
from gate.plate_utils import clean_ocr_text

event_bp = Blueprint("events", __name__)
PER_PAGE = 25
DECISIONS = ("ALLOW ENTRY", "DENY ENTRY", "MANUAL CHECK")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def date_arg(name):
    """A YYYY-MM-DD query arg, or '' when absent or malformed."""
    value = (request.args.get(name) or "").strip()
    return value if _DATE_RE.match(value) else ""


def _filters():
    return {
        "plate": clean_ocr_text(request.args.get("plate", "")),
        "gate": (request.args.get("gate") or "").strip(),
        "decision": request.args.get("decision") if request.args.get("decision") in DECISIONS else "",
        "date_from": date_arg("date_from"),
        "date_to": date_arg("date_to"),
    }


def _query(filters):
    sql = """
        SELECT e.id, e.gate, e.plate, e.raw_text, e.confidence, e.decision, e.reason,
               e.final_decision, e.source, e.operator, e.suggested_plate, e.sms_sent,
               e.evidence_path, e.crop_path,
               datetime(e.created_at, 'localtime') AS local_time,
               v.id AS vehicle_id, v.owner AS owner
        FROM gate_events e
        LEFT JOIN vehicles v ON v.number = e.plate
    """
    where, params = [], []
    if filters["plate"]:
        where.append("e.plate LIKE ?")
        params.append(f"%{filters['plate']}%")
    if filters["gate"]:
        where.append("LOWER(e.gate) = LOWER(?)")
        params.append(filters["gate"])
    if filters["decision"]:
        where.append("COALESCE(NULLIF(e.final_decision, 'RECHECKED'), e.decision) = ?")
        params.append(filters["decision"])
    if filters["date_from"]:
        where.append("DATE(e.created_at, 'localtime') >= ?")
        params.append(filters["date_from"])
    if filters["date_to"]:
        where.append("DATE(e.created_at, 'localtime') <= ?")
        params.append(filters["date_to"])
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql + " ORDER BY e.id DESC", tuple(params)


@event_bp.route("/admin/activity")
@admin_required
def gate_activity():
    filters = _filters()
    sql, params = _query(filters)

    with vehicle_db() as conn:
        if request.args.get("format") == "csv":
            rows = conn.execute(sql, params).fetchall()
            return csv_response("gate-activity", [
                "Time", "Gate", "Plate", "Decision", "Operator decision", "Reason",
                "Confidence", "Source", "Operator", "Suggested plate", "Raw OCR",
            ], [(r["local_time"], r["gate"], r["plate"], r["decision"], r["final_decision"],
                 r["reason"], r["confidence"], r["source"], r["operator"],
                 r["suggested_plate"], r["raw_text"]) for r in rows])

        events, page, pages, total = paginate(
            conn, sql, params, request.args.get("page", 1, type=int), PER_PAGE)
        gates = [r["name"] for r in conn.execute(
            "SELECT name FROM locations ORDER BY name COLLATE NOCASE")]

    args = {k: v or None for k, v in filters.items()}
    return render_template("activity.html", events=events, page=page, pages=pages,
                           total=total, filters=filters, args=args, gates=gates,
                           decisions=DECISIONS)


@event_bp.route("/admin/activity/<int:event_id>")
@admin_required
def event_detail(event_id):
    sql, params = _query({"plate": "", "gate": "", "decision": "", "date_from": "", "date_to": ""})
    sql = sql.replace(" ORDER BY e.id DESC", " WHERE e.id = ?")
    with vehicle_db() as conn:
        event = conn.execute(sql, (*params, event_id)).fetchone()
        if event is None:
            abort(404, description="No such gate event.")
        violations = conn.execute("""
            SELECT id, violation_type, status, datetime(timestamp, 'localtime') AS local_time
            FROM violations WHERE event_id = ?
        """, (event_id,)).fetchall()
    return render_template("event_detail.html", event=event, violations=violations)


@event_bp.route("/admin/evidence/<int:event_id>/<kind>")
@admin_required
def admin_evidence(event_id, kind):
    return send_evidence(get_event(event_id), kind)

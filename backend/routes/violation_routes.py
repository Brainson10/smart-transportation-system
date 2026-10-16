from flask import Blueprint, flash, redirect, render_template, request, url_for

from backend.auth import admin_required
from backend.db import paginate, vehicle_db
from backend.exports import csv_response
from backend.routes.event_routes import date_arg
from gate.plate_utils import clean_ocr_text


violation_bp = Blueprint("violation", __name__)
PER_PAGE = 25
STATUSES = ("OPEN", "WAIVED")


def _filters():
    status = (request.args.get("status") or "").upper()
    return {
        "plate": clean_ocr_text(request.args.get("plate", "")),
        "location": (request.args.get("location") or "").strip(),
        "status": status if status in STATUSES else "",
        "date_from": date_arg("date_from"),
        "date_to": date_arg("date_to"),
    }


@violation_bp.route("/admin/violations")
@admin_required
def view_violations():
    filters = _filters()

    # Stored in UTC (CURRENT_TIMESTAMP); shown in local time. Showing the raw value
    # put every violation 5.5 hours off for an IST campus.
    sql = """
        SELECT v.id, v.vehicle_number, v.location, v.violation_type, v.event_id,
               COALESCE(v.status, 'OPEN') AS status,
               datetime(v.timestamp, 'localtime') AS local_time,
               ve.id AS vehicle_id, ve.owner AS owner,
               e.crop_path AS crop_path
        FROM violations v
        LEFT JOIN vehicles ve ON ve.number = v.vehicle_number
        LEFT JOIN gate_events e ON e.id = v.event_id
    """
    where, params = [], []
    if filters["plate"]:
        where.append("v.vehicle_number LIKE ?")
        params.append(f"%{filters['plate']}%")
    if filters["location"]:
        where.append("LOWER(v.location) = LOWER(?)")
        params.append(filters["location"])
    if filters["status"]:
        where.append("COALESCE(v.status, 'OPEN') = ?")
        params.append(filters["status"])
    if filters["date_from"]:
        where.append("DATE(v.timestamp, 'localtime') >= ?")
        params.append(filters["date_from"])
    if filters["date_to"]:
        where.append("DATE(v.timestamp, 'localtime') <= ?")
        params.append(filters["date_to"])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY v.timestamp DESC, v.id DESC"

    with vehicle_db() as conn:
        if request.args.get("format") == "csv":
            rows = conn.execute(sql, tuple(params)).fetchall()
            return csv_response("violations", [
                "Time", "Vehicle", "Owner", "Location", "Type", "Status", "Gate event",
            ], [(r["local_time"], r["vehicle_number"], r["owner"], r["location"],
                 r["violation_type"], r["status"], r["event_id"]) for r in rows])

        violations, page, pages, total = paginate(
            conn, sql, tuple(params), request.args.get("page", 1, type=int), PER_PAGE)
        locations = [r["name"] for r in conn.execute(
            "SELECT name FROM locations ORDER BY name COLLATE NOCASE")]

    args = {k: v or None for k, v in filters.items()}
    return render_template("violations.html", violations=violations, page=page,
                           pages=pages, total=total, filters=filters, args=args,
                           plate=request.args.get("plate", ""), location=filters["location"],
                           locations=locations, statuses=STATUSES)


@violation_bp.route("/admin/violations/<int:violation_id>/waive", methods=["POST"])
@admin_required
def toggle_waive(violation_id):
    """
    OPEN <-> WAIVED, e.g. for a violation contested and accepted. The vehicle's
    violation_count follows, so the gate and SMS show the corrected total.
    """
    with vehicle_db() as conn:
        row = conn.execute("SELECT vehicle_number, COALESCE(status, 'OPEN') AS status "
                           "FROM violations WHERE id = ?", (violation_id,)).fetchone()
        if row:
            waive = row["status"] != "WAIVED"
            with conn:
                conn.execute("UPDATE violations SET status = ? WHERE id = ?",
                             ("WAIVED" if waive else "OPEN", violation_id))
                conn.execute(
                    "UPDATE vehicles SET violation_count = MAX(0, COALESCE(violation_count, 0) + ?) "
                    "WHERE number = ?", (-1 if waive else 1, row["vehicle_number"]))
            flash(f"Violation #{violation_id} for {row['vehicle_number']} "
                  f"{'waived' if waive else 'restored'}.")

    target = request.form.get("next") or ""
    if not target.startswith("/admin/") or "//" in target:
        target = url_for("violation.view_violations")
    return redirect(target)

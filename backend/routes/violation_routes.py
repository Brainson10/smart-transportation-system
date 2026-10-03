from flask import Blueprint, render_template, request

from backend.auth import admin_required
from backend.db import paginate, vehicle_db
from gate.plate_utils import clean_ocr_text


violation_bp = Blueprint("violation", __name__)
PER_PAGE = 25


@violation_bp.route("/admin/violations")
@admin_required
def view_violations():
    plate = clean_ocr_text(request.args.get("plate", ""))
    location = (request.args.get("location") or "").strip()

    # Stored in UTC (CURRENT_TIMESTAMP); shown in local time. Showing the raw value
    # put every violation 5.5 hours off for an IST campus.
    sql = """
        SELECT vehicle_number, location, violation_type,
               datetime(timestamp, 'localtime') AS local_time
        FROM violations
    """
    where, params = [], []
    if plate:
        where.append("vehicle_number LIKE ?")
        params.append(f"%{plate}%")
    if location:
        where.append("LOWER(location) = LOWER(?)")
        params.append(location)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY timestamp DESC, id DESC"

    with vehicle_db() as conn:
        violations, page, pages, total = paginate(
            conn, sql, tuple(params), request.args.get("page", 1, type=int), PER_PAGE)
        locations = [r["name"] for r in conn.execute(
            "SELECT name FROM locations ORDER BY name COLLATE NOCASE")]

    return render_template("violations.html", violations=violations, page=page,
                           pages=pages, total=total, plate=request.args.get("plate", ""),
                           location=location, locations=locations)

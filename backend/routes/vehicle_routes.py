import re
import sqlite3
from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from backend.auth import admin_required
from backend.db import paginate, vehicle_db
from gate.decision import pass_expired
from gate.plate_utils import clean_ocr_text, is_valid_plate


vehicle_bp = Blueprint("vehicle", __name__)

VEHICLE_TYPES = ["Car", "Bike", "Scooter", "Truck", "Bus", "Auto", "Other"]
VEHICLE_ROLES = ["Student", "Staff", "Faculty", "Visitor", "Emergency Vehicle"]
STATUSES = ["ACTIVE", "BLOCKED"]
PHONE_RE = re.compile(r"\+?\d{10,13}")
PER_PAGE = 20
MAX_NOTES = 300


@vehicle_bp.app_template_filter("pass_expired")
def _pass_expired_filter(valid_until):
    return pass_expired(valid_until)


@vehicle_bp.route("/admin/vehicles")
@admin_required
def manage_vehicles():
    raw_query = (request.args.get("q") or "").strip()
    status = request.args.get("status") if request.args.get("status") in STATUSES else ""
    role = request.args.get("role") if request.args.get("role") in VEHICLE_ROLES else ""

    sql = ("SELECT id, number, owner, phone, type, role, violation_count, status, "
           "valid_until FROM vehicles")
    where, params = [], []
    if raw_query:
        # Plate (spacing/case-insensitive), owner name or phone number.
        plate = clean_ocr_text(raw_query)
        digits = re.sub(r"\D", "", raw_query)
        clauses, values = ["owner LIKE ?"], [f"%{raw_query}%"]
        if plate:
            clauses.append("number LIKE ?")
            values.append(f"%{plate}%")
        if len(digits) >= 3:
            clauses.append("phone LIKE ?")
            values.append(f"%{digits}%")
        where.append("(" + " OR ".join(clauses) + ")")
        params.extend(values)
    if status:
        where.append("status = ?")
        params.append(status)
    if role:
        where.append("role = ?")
        params.append(role)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY number"

    with vehicle_db() as conn:
        vehicles, page, pages, total = paginate(
            conn, sql, tuple(params), request.args.get("page", 1, type=int), PER_PAGE)

    return render_template("vehicles.html", vehicles=vehicles, page=page, pages=pages,
                           total=total, q=raw_query, status=status, role=role,
                           statuses=STATUSES, roles=VEHICLE_ROLES)


def _validate_vehicle(form):
    """Returns (clean_values, error). Strict: a typo is rejected, never rewritten."""
    number = clean_ocr_text(form.get("number"))
    owner = (form.get("owner") or "").strip()
    vtype = (form.get("type") or "").strip()
    role = (form.get("role") or "").strip()
    phone = re.sub(r"[\s-]", "", form.get("phone") or "")
    valid_until = (form.get("valid_until") or "").strip()
    notes = (form.get("notes") or "").strip()

    if not is_valid_plate(number):
        return None, ("Enter a valid Indian plate number, e.g. MN01AB1234 "
                      "or 22BH1234AB.")
    if not owner or len(owner) > 80:
        return None, "Owner name is required (max 80 characters)."
    if vtype not in VEHICLE_TYPES:
        return None, "Choose a vehicle type from the list."
    if role not in VEHICLE_ROLES:
        return None, "Choose a role from the list."
    if not PHONE_RE.fullmatch(phone):
        return None, "Enter a phone number of 10-13 digits (optionally starting with +)."
    if valid_until:
        try:
            datetime.strptime(valid_until, "%Y-%m-%d")
        except ValueError:
            return None, "Pass expiry must be a date (YYYY-MM-DD)."
    if len(notes) > MAX_NOTES:
        return None, f"Notes are limited to {MAX_NOTES} characters."
    return {"number": number, "owner": owner, "type": vtype, "role": role, "phone": phone,
            "valid_until": valid_until or None, "notes": notes or None}, None


def _form_context(form, **extra):
    return {"types": VEHICLE_TYPES, "roles": VEHICLE_ROLES, "form": form,
            "today": date.today().isoformat(), **extra}


def _duplicate(conn, number, exclude_id=None):
    sql = "SELECT 1 FROM vehicles WHERE UPPER(REPLACE(number, ' ', '')) = ?"
    params = [number]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return conn.execute(sql, params).fetchone() is not None


@vehicle_bp.route("/register", methods=["GET", "POST"])
@admin_required
def register():
    context = _form_context(request.form)

    if request.method == "POST":
        values, error = _validate_vehicle(request.form)
        if error:
            return render_template("register_vehicle.html", error=error, **context), 400

        number = values["number"]
        with vehicle_db() as conn:
            if _duplicate(conn, number):
                return render_template(
                    "register_vehicle.html",
                    error=f"{number} is already registered.", **context), 409
            try:
                with conn:
                    conn.execute("""
                        INSERT INTO vehicles
                        (number, owner, type, role, phone, valid_until, notes, status,
                         violation_count)
                        VALUES (:number, :owner, :type, :role, :phone, :valid_until, :notes,
                                'ACTIVE', 0)
                    """, values)
            except sqlite3.IntegrityError:
                return render_template(
                    "register_vehicle.html",
                    error=f"{number} is already registered.", **context), 409

        flash(f"Registered {number}.")
        return redirect(url_for("vehicle.manage_vehicles"))

    return render_template("register_vehicle.html", **context)


def _vehicle_or_404(conn, vehicle_id):
    row = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    if row is None:
        abort(404, description="No such vehicle.")
    return row


@vehicle_bp.route("/admin/vehicles/<int:vehicle_id>")
@admin_required
def vehicle_detail(vehicle_id):
    with vehicle_db() as conn:
        vehicle = _vehicle_or_404(conn, vehicle_id)
        violations = conn.execute("""
            SELECT id, location, violation_type, status, event_id,
                   datetime(timestamp, 'localtime') AS local_time
            FROM violations WHERE vehicle_number = ? ORDER BY timestamp DESC, id DESC LIMIT 50
        """, (vehicle["number"],)).fetchall()
        events = conn.execute("""
            SELECT id, gate, decision, final_decision, reason, confidence, source,
                   datetime(created_at, 'localtime') AS local_time
            FROM gate_events WHERE plate = ? ORDER BY id DESC LIMIT 20
        """, (vehicle["number"],)).fetchall()
    return render_template("vehicle_detail.html", vehicle=vehicle, violations=violations,
                           events=events)


@vehicle_bp.route("/admin/vehicles/<int:vehicle_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_vehicle(vehicle_id):
    with vehicle_db() as conn:
        vehicle = _vehicle_or_404(conn, vehicle_id)

        if request.method == "POST":
            context = _form_context(request.form, vehicle=vehicle)
            values, error = _validate_vehicle(request.form)
            if error:
                return render_template("register_vehicle.html", error=error, **context), 400
            if _duplicate(conn, values["number"], exclude_id=vehicle_id):
                return render_template(
                    "register_vehicle.html",
                    error=f"{values['number']} is already registered.", **context), 409
            with conn:
                conn.execute("""
                    UPDATE vehicles SET number = :number, owner = :owner, type = :type,
                           role = :role, phone = :phone, valid_until = :valid_until,
                           notes = :notes
                    WHERE id = :id
                """, {**values, "id": vehicle_id})
                if values["number"] != vehicle["number"]:
                    # Keep the history attached to the corrected plate.
                    conn.execute("UPDATE violations SET vehicle_number = ? WHERE vehicle_number = ?",
                                 (values["number"], vehicle["number"]))
            flash(f"Saved {values['number']}.")
            return redirect(url_for("vehicle.vehicle_detail", vehicle_id=vehicle_id))

    return render_template("register_vehicle.html", **_form_context(dict(vehicle), vehicle=vehicle))


def _back_to_list():
    """Return to the page the action was taken from (list page/search or detail)."""
    target = request.form.get("next")
    if target and target.startswith("/admin/vehicles/") and "//" not in target:
        return target
    return url_for("vehicle.manage_vehicles",
                   page=request.form.get("page", type=int) or None,
                   q=request.form.get("q") or None)


@vehicle_bp.route("/delete_vehicle/<int:vehicle_id>", methods=["POST"])
@admin_required
def delete_vehicle(vehicle_id):
    # POST only: as a GET link, any crawler, prefetch or forged <img> could delete.
    with vehicle_db() as conn:
        row = conn.execute("SELECT number FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
        with conn:
            conn.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))
    if row:
        flash(f"Deleted {row['number']}.")
    return redirect(url_for("vehicle.manage_vehicles",
                            page=request.form.get("page", type=int) or None,
                            q=request.form.get("q") or None))


@vehicle_bp.route("/vehicles/<int:vehicle_id>/toggle_status", methods=["POST"])
@admin_required
def toggle_vehicle_status(vehicle_id):
    """ACTIVE <-> BLOCKED. The gate already denies blocked vehicles; now admins can block."""
    with vehicle_db() as conn:
        row = conn.execute("SELECT number, status FROM vehicles WHERE id = ?",
                           (vehicle_id,)).fetchone()
        if row:
            new_status = "BLOCKED" if row["status"] == "ACTIVE" else "ACTIVE"
            with conn:
                conn.execute("UPDATE vehicles SET status = ? WHERE id = ?",
                             (new_status, vehicle_id))
            flash(f"{row['number']} is now {new_status}.")
    return redirect(_back_to_list())

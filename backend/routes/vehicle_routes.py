import re
import sqlite3

from flask import Blueprint, flash, redirect, render_template, request, url_for

from backend.auth import admin_required
from backend.db import paginate, vehicle_db
from gate.plate_utils import clean_ocr_text, is_valid_plate


vehicle_bp = Blueprint("vehicle", __name__)

VEHICLE_TYPES = ["Car", "Bike", "Scooter", "Truck", "Bus", "Auto", "Other"]
VEHICLE_ROLES = ["Student", "Staff", "Faculty", "Visitor", "Emergency Vehicle"]
PHONE_RE = re.compile(r"\+?\d{10,13}")
PER_PAGE = 20


@vehicle_bp.route("/admin/vehicles")
@admin_required
def manage_vehicles():
    query = clean_ocr_text(request.args.get("q", ""))
    sql = ("SELECT id, number, owner, phone, type, role, violation_count, status "
           "FROM vehicles")
    params = ()
    if query:
        sql += " WHERE number LIKE ?"
        params = (f"%{query}%",)
    sql += " ORDER BY number"

    with vehicle_db() as conn:
        vehicles, page, pages, total = paginate(
            conn, sql, params, request.args.get("page", 1, type=int), PER_PAGE)

    return render_template("vehicles.html", vehicles=vehicles, page=page, pages=pages,
                           total=total, q=request.args.get("q", ""))


def _validate_vehicle(form):
    """Returns (clean_values, error). Strict: a typo is rejected, never rewritten."""
    number = clean_ocr_text(form.get("number"))
    owner = (form.get("owner") or "").strip()
    vtype = (form.get("type") or "").strip()
    role = (form.get("role") or "").strip()
    phone = re.sub(r"[\s-]", "", form.get("phone") or "")

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
    return (number, owner, vtype, role, phone), None


@vehicle_bp.route("/register", methods=["GET", "POST"])
@admin_required
def register():
    context = {"types": VEHICLE_TYPES, "roles": VEHICLE_ROLES, "form": request.form}

    if request.method == "POST":
        values, error = _validate_vehicle(request.form)
        if error:
            return render_template("register_vehicle.html", error=error, **context), 400

        number = values[0]
        with vehicle_db() as conn:
            exists = conn.execute(
                "SELECT 1 FROM vehicles WHERE UPPER(REPLACE(number, ' ', '')) = ?",
                (number,)).fetchone()
            if exists:
                return render_template(
                    "register_vehicle.html",
                    error=f"{number} is already registered.", **context), 409
            try:
                with conn:
                    conn.execute("""
                        INSERT INTO vehicles
                        (number, owner, type, role, phone, status, violation_count)
                        VALUES (?, ?, ?, ?, ?, 'ACTIVE', 0)
                    """, values)
            except sqlite3.IntegrityError:
                return render_template(
                    "register_vehicle.html",
                    error=f"{number} is already registered.", **context), 409

        flash(f"Registered {number}.")
        return redirect(url_for("vehicle.manage_vehicles"))

    return render_template("register_vehicle.html", **context)


def _back_to_list():
    """Return to the list page/search the action was taken from."""
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
    return redirect(_back_to_list())


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

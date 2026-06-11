import sqlite3

from flask import Blueprint, redirect, render_template, request, url_for

from config import VEHICLE_DB


vehicle_bp = Blueprint("vehicle", __name__)


def get_vehicle_db():
    conn = sqlite3.connect(VEHICLE_DB)
    conn.row_factory = sqlite3.Row
    return conn


@vehicle_bp.route("/admin/vehicles")
def manage_vehicles():
    conn = get_vehicle_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, number, owner, phone, type, role, violation_count, status
        FROM vehicles
    """)
    vehicles = cur.fetchall()
    conn.close()
    return render_template("vehicles.html", vehicles=vehicles)


@vehicle_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        conn = get_vehicle_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO vehicles
            (number, owner, phone, type, role, status, violation_count)
            VALUES (?, ?, ?, ?, ?, 'ACTIVE', 0)
        """, (
            request.form["number"],
            request.form["owner"],
            request.form["phone"],
            request.form["type"],
            request.form["role"],
        ))
        conn.commit()
        conn.close()
        return redirect(url_for("vehicle.manage_vehicles"))

    return render_template("register_vehicle.html")


@vehicle_bp.route("/delete_vehicle/<int:vehicle_id>")
def delete_vehicle(vehicle_id):
    conn = get_vehicle_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM vehicles WHERE id=?", (vehicle_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("vehicle.manage_vehicles"))

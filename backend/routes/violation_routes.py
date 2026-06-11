import sqlite3

from flask import Blueprint, render_template

from config import VEHICLE_DB


violation_bp = Blueprint("violation", __name__)


def get_vehicle_db():
    conn = sqlite3.connect(VEHICLE_DB)
    conn.row_factory = sqlite3.Row
    return conn


@violation_bp.route("/admin/violations")
def view_violations():
    conn = get_vehicle_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT vehicle_number, location, violation_type, timestamp
        FROM violations
        ORDER BY timestamp DESC
    """)
    violations = cur.fetchall()
    conn.close()

    return render_template("violations.html", violations=violations)

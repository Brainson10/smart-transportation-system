import sqlite3

from flask import Blueprint, redirect, render_template, request, url_for

from config import VEHICLE_DB


gate_routes_bp = Blueprint("gate_routes", __name__)
ADMIN_DB_PATH = VEHICLE_DB


@gate_routes_bp.route("/gate")
def gate_entry():
    return redirect(url_for("gate_routes.gate_login"))


@gate_routes_bp.route("/gate-login", methods=["GET", "POST"])
def gate_login():
    conn = sqlite3.connect(ADMIN_DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM locations")
    locations = [r[0] for r in cur.fetchall()]
    conn.close()

    if request.method == "POST":
        # MASTER_GATE_PASSWORD preserves the existing demo gate login behavior.
        MASTER_GATE_PASSWORD = "gate123"

        if request.form["password"] != MASTER_GATE_PASSWORD:
            return render_template(
                "gate_login.html",
                locations=locations,
                error="Invalid gate password"
            )

        return redirect(
            url_for("gate_routes.gate_dashboard", gate=request.form["location"])
        )

    return render_template("gate_login.html", locations=locations)


@gate_routes_bp.route("/gate/dashboard/<gate>")
def gate_dashboard(gate):
    return render_template("gate_dashboard.html", gate=gate)

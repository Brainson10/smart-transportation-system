from flask import Blueprint, redirect, render_template, request, url_for

from backend.auth import (check_gate_password, gate_required, login_gate,
                          login_throttle, logout, throttle_key)
from backend.db import vehicle_db


gate_routes_bp = Blueprint("gate_routes", __name__)


def _locations():
    with vehicle_db() as conn:
        return [row["name"] for row in
                conn.execute("SELECT name FROM locations ORDER BY name COLLATE NOCASE")]


@gate_routes_bp.route("/gate")
def gate_entry():
    return redirect(url_for("gate_routes.gate_login"))


@gate_routes_bp.route("/gate-login", methods=["GET", "POST"])
def gate_login():
    locations = _locations()

    if request.method == "POST":
        key = throttle_key("gate")
        wait = login_throttle.retry_after(key)
        if wait:
            return render_template(
                "gate_login.html", locations=locations,
                error=f"Too many failed attempts. Try again in {wait} seconds."), 429

        posted = (request.form.get("location") or "").strip()
        # Only a location that actually exists can be logged into, and the session
        # stores its canonical spelling.
        location = next((loc for loc in locations if loc.lower() == posted.lower()), None)

        if location is None or not check_gate_password(request.form.get("password")):
            login_throttle.record_failure(key)
            return render_template("gate_login.html", locations=locations,
                                   selected=posted,
                                   error="Invalid gate or password"), 401

        login_throttle.reset(key)
        # Previously this redirected WITHOUT setting a session, so the password
        # protected nothing: the dashboard URL worked for anyone.
        login_gate(location)
        return redirect(url_for("gate_routes.gate_dashboard", gate=location))

    return render_template("gate_login.html", locations=locations)


@gate_routes_bp.route("/gate-logout", methods=["GET", "POST"])
def gate_logout():
    logout()
    return redirect(url_for("gate_routes.gate_login"))


@gate_routes_bp.route("/gate/dashboard/<gate>")
@gate_required
def gate_dashboard(gate):
    return render_template("gate_dashboard.html", gate=gate)

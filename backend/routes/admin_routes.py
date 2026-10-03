from urllib.parse import urlparse

from flask import Blueprint, redirect, render_template, request, url_for

from backend.auth import (login_admin, login_throttle, logout, throttle_key,
                          verify_password)
from backend.db import vehicle_db


admin_bp = Blueprint("admin", __name__)


def _safe_next(target):
    """Only follow same-site relative redirects after login (no open redirect)."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/"):
        return None
    return target


@admin_bp.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    next_url = _safe_next(request.values.get("next"))

    if request.method == "POST":
        key = throttle_key("admin")
        wait = login_throttle.retry_after(key)
        if wait:
            return render_template(
                "admin_login.html", next=next_url,
                error=f"Too many failed attempts. Try again in {wait} seconds."), 429

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        with vehicle_db() as conn:
            admin = conn.execute(
                "SELECT username, password FROM admins WHERE username = ?",
                (username,)).fetchone()

        # verify_password runs even for unknown usernames (equal timing).
        if verify_password(admin["password"] if admin else None, password) and admin:
            login_throttle.reset(key)
            login_admin(admin["username"])
            return redirect(next_url or url_for("admin_dashboard"))

        login_throttle.record_failure(key)
        return render_template("admin_login.html", next=next_url,
                               error="Invalid admin credentials"), 401

    return render_template("admin_login.html", next=next_url)


@admin_bp.route("/admin-logout", methods=["GET", "POST"])
def admin_logout():
    logout()
    return redirect(url_for("admin.admin_login"))

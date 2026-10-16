from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from backend.auth import (admin_required, hash_password, login_admin, login_throttle,
                          logout, throttle_key, verify_password)
from backend.db import vehicle_db

MIN_PASSWORD_LENGTH = 8


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


@admin_bp.route("/admin/account", methods=["GET", "POST"])
@admin_required
def account():
    """Change the signed-in admin's password (the seeded one is admin123)."""
    username = session.get("admin_username")
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        with vehicle_db() as conn:
            admin = conn.execute("SELECT id, password FROM admins WHERE username = ?",
                                 (username,)).fetchone()
            error = None
            if not admin or not verify_password(admin["password"], current):
                error = "Current password is incorrect."
            elif len(new) < MIN_PASSWORD_LENGTH:
                error = f"New password must be at least {MIN_PASSWORD_LENGTH} characters."
            elif new != confirm:
                error = "New passwords do not match."
            elif new == current:
                error = "New password must differ from the current one."
            if error:
                return render_template("account.html", username=username, error=error), 400
            with conn:
                conn.execute("UPDATE admins SET password = ? WHERE id = ?",
                             (hash_password(new), admin["id"]))
        flash("Password changed.")
        return redirect(url_for("admin.account"))

    return render_template("account.html", username=username,
                           min_length=MIN_PASSWORD_LENGTH)

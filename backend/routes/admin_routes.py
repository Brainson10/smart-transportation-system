import sqlite3

from flask import Blueprint, redirect, render_template, request, session, url_for

from config import VEHICLE_DB


admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        conn = sqlite3.connect(VEHICLE_DB)
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM admins WHERE username=? AND password=?",
            (username, password),
        )
        admin = cur.fetchone()
        conn.close()

        if admin:
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))

        return render_template(
            "admin_login.html",
            error="Invalid admin credentials",
        )

    return render_template("admin_login.html")


@admin_bp.route("/admin-logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin.admin_login"))

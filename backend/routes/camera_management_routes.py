import sqlite3

from flask import Blueprint, redirect, render_template, request, url_for

from config import AUTHORITY_DB


camera_management_bp = Blueprint("camera_management", __name__)


def get_authority_db():
    conn = sqlite3.connect(AUTHORITY_DB)
    conn.row_factory = sqlite3.Row
    return conn


@camera_management_bp.route("/admin/cameras")
def manage_cameras():
    conn = get_authority_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cameras")
    cameras = cur.fetchall()
    conn.close()
    return render_template("cameras.html", cameras=cameras)


@camera_management_bp.route("/admin/cameras/add", methods=["GET", "POST"])
def add_camera():
    if request.method == "POST":
        location = request.form["location"]
        status = request.form.get("status", "ONLINE")

        conn = get_authority_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO cameras (location, status)
            VALUES (?, ?)
        """, (location, status))
        conn.commit()
        conn.close()

        return redirect(url_for("camera_management.manage_cameras"))

    return render_template("add_camera.html")


@camera_management_bp.route("/admin/cameras/toggle/<int:camera_id>")
def toggle_camera(camera_id):
    conn = get_authority_db()
    cur = conn.cursor()

    # Get current status
    cur.execute("SELECT status FROM cameras WHERE id=?", (camera_id,))
    row = cur.fetchone()

    if row:
        new_status = "OFFLINE" if row["status"] == "ONLINE" else "ONLINE"
        cur.execute(
            "UPDATE cameras SET status=? WHERE id=?",
            (new_status, camera_id)
        )
        conn.commit()

    conn.close()
    return redirect(url_for("camera_management.manage_cameras"))

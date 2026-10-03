from flask import Blueprint, flash, redirect, render_template, request, url_for

from backend.auth import admin_required
from backend.db import authority_db


camera_management_bp = Blueprint("camera_management", __name__)
CAMERA_STATUSES = ("ONLINE", "OFFLINE")


@camera_management_bp.route("/admin/cameras")
@admin_required
def manage_cameras():
    with authority_db() as conn:
        cameras = conn.execute(
            "SELECT id, name, location, zone, status, coverage FROM cameras ORDER BY id"
        ).fetchall()
    return render_template("cameras.html", cameras=cameras)


@camera_management_bp.route("/admin/cameras/add", methods=["GET", "POST"])
@admin_required
def add_camera():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        location = (request.form.get("location") or "").strip()
        zone = (request.form.get("zone") or "").strip() or None
        coverage = (request.form.get("coverage") or "").strip() or None
        status = request.form.get("status", "ONLINE")

        error = None
        if not name or not location:
            error = "Camera name and location are required."
        elif status not in CAMERA_STATUSES:
            error = "Status must be ONLINE or OFFLINE."
        if error:
            return render_template("add_camera.html", error=error, form=request.form), 400

        # The old INSERT only wrote (location, status): the name the form collected
        # was silently dropped, so every added camera appeared nameless.
        with authority_db() as conn:
            with conn:
                conn.execute(
                    "INSERT INTO cameras (name, location, zone, status, coverage) "
                    "VALUES (?, ?, ?, ?, ?)", (name, location, zone, status, coverage))
        flash(f"Added {name}.")
        return redirect(url_for("camera_management.manage_cameras"))

    return render_template("add_camera.html", form={})


@camera_management_bp.route("/admin/cameras/toggle/<int:camera_id>", methods=["POST"])
@admin_required
def toggle_camera(camera_id):
    with authority_db() as conn:
        row = conn.execute("SELECT name, status FROM cameras WHERE id = ?",
                           (camera_id,)).fetchone()
        if row:
            new_status = "OFFLINE" if row["status"] == "ONLINE" else "ONLINE"
            with conn:
                conn.execute("UPDATE cameras SET status = ? WHERE id = ?",
                             (new_status, camera_id))
            flash(f"{row['name'] or 'Camera'} is now {new_status}.")
    return redirect(url_for("camera_management.manage_cameras"))

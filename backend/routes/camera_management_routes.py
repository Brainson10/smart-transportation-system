"""
Camera inventory, now wired to the live pipeline: a camera's `source` (webcam
index, video file or rtsp:// URL) feeds the gate named in its `location`.
Cameras without a source remain inventory records, and gates without a camera
use the global ANPR_CAMERA_SOURCE.
"""

from pathlib import Path

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import config
from backend.auth import admin_required
from backend.db import authority_db, vehicle_db
from gate import capture
from gate.plate_utils import mask_source


camera_management_bp = Blueprint("camera_management", __name__)
CAMERA_STATUSES = ("ONLINE", "OFFLINE")
_STREAM_SCHEMES = ("rtsp://", "rtsps://", "rtmp://", "http://", "https://")
MAX_FIELD = 120


def validate_source(source):
    """'' (none), a webcam index, a stream URL, or an existing video file."""
    source = (source or "").strip()
    if not source:
        return None, None
    if source.isdigit():
        return source, None
    if source.lower().startswith(_STREAM_SCHEMES):
        return source, None
    path = Path(source).expanduser()
    if not path.is_absolute():
        path = config.BASE_DIR / path
    if path.is_file():
        return source, None
    return None, ("Source must be a webcam index (0, 1...), an rtsp:// or http(s):// "
                  "stream URL, or the path of an existing video file.")


def _locations():
    with vehicle_db() as conn:
        return [r["name"] for r in conn.execute(
            "SELECT name FROM locations ORDER BY name COLLATE NOCASE")]


def _validate(form):
    values = {
        "name": (form.get("name") or "").strip(),
        "location": (form.get("location") or "").strip(),
        "zone": (form.get("zone") or "").strip() or None,
        "coverage": (form.get("coverage") or "").strip() or None,
        "status": form.get("status", "ONLINE"),
    }
    if not values["name"] or not values["location"]:
        return values, "Camera name and location are required."
    if any(len(v) > MAX_FIELD for v in values.values() if v):
        return values, f"Fields are limited to {MAX_FIELD} characters."
    if values["status"] not in CAMERA_STATUSES:
        return values, "Status must be ONLINE or OFFLINE."
    values["source"], error = validate_source(form.get("source"))
    return values, error


def _live(camera):
    """Capture stats if this camera's source is streaming in this process."""
    if not camera["source"]:
        return None
    stream = capture.peek_stream(camera["source"])
    return stream.stats() if stream else None


@camera_management_bp.route("/admin/cameras")
@admin_required
def manage_cameras():
    with authority_db() as conn:
        rows = conn.execute(
            "SELECT id, name, location, zone, status, coverage, source FROM cameras ORDER BY id"
        ).fetchall()
    gate_names = {name.lower() for name in _locations()}
    cameras = [{
        **dict(c),
        "source_display": mask_source(c["source"]) if c["source"] else None,
        "feeds_gate": bool(c["source"]) and (c["location"] or "").strip().lower() in gate_names,
        "live": _live(c),
    } for c in rows]
    online = sum(1 for c in cameras if c["status"] == "ONLINE")
    return render_template("cameras.html", cameras=cameras, online=online,
                           default_source=mask_source(config.CAMERA_SOURCE))


@camera_management_bp.route("/admin/cameras/add", methods=["GET", "POST"])
@admin_required
def add_camera():
    if request.method == "POST":
        values, error = _validate(request.form)
        if error:
            return render_template("add_camera.html", error=error, form=request.form,
                                   locations=_locations()), 400

        # The old INSERT only wrote (location, status): the name the form collected
        # was silently dropped, so every added camera appeared nameless.
        with authority_db() as conn:
            with conn:
                conn.execute(
                    "INSERT INTO cameras (name, location, zone, status, coverage, source) "
                    "VALUES (:name, :location, :zone, :status, :coverage, :source)", values)
        flash(f"Added {values['name']}.")
        return redirect(url_for("camera_management.manage_cameras"))

    return render_template("add_camera.html", form={}, locations=_locations())


def _camera_or_404(conn, camera_id):
    row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    if row is None:
        abort(404, description="No such camera.")
    return row


@camera_management_bp.route("/admin/cameras/<int:camera_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_camera(camera_id):
    with authority_db() as conn:
        camera = _camera_or_404(conn, camera_id)
        if request.method == "POST":
            values, error = _validate(request.form)
            if error:
                return render_template("add_camera.html", error=error, form=request.form,
                                       camera=camera, locations=_locations()), 400
            with conn:
                conn.execute("""
                    UPDATE cameras SET name = :name, location = :location, zone = :zone,
                           status = :status, coverage = :coverage, source = :source
                    WHERE id = :id
                """, {**values, "id": camera_id})
            flash(f"Saved {values['name']}. A gate picks up a new source within a few seconds.")
            return redirect(url_for("camera_management.manage_cameras"))

    return render_template("add_camera.html", form=dict(camera), camera=camera,
                           locations=_locations())


@camera_management_bp.route("/admin/cameras/<int:camera_id>/delete", methods=["POST"])
@admin_required
def delete_camera(camera_id):
    with authority_db() as conn:
        row = conn.execute("SELECT name FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        with conn:
            conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
    if row:
        flash(f"Deleted {row['name'] or 'camera'}.")
    return redirect(url_for("camera_management.manage_cameras"))


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

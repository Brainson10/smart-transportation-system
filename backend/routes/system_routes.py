"""
System page: what the ANPR pipeline is running with, live worker/camera health,
and the runtime tunables from backend/settings.py.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

import config
from backend import settings
from backend.auth import admin_required
from gate.plate_utils import mask_source

system_bp = Blueprint("system", __name__)


def _pipeline():
    return [
        ("Default camera source", mask_source(config.CAMERA_SOURCE)),
        ("OCR engine", config.OCR_ENGINE),
        ("YOLO device", config.YOLO_DEVICE),
        ("YOLO confidence / image size", f"{config.YOLO_CONF} / {config.YOLO_IMGSZ}px"),
        ("Detection zone (ROI)", ", ".join(f"{v:.2f}" for v in config.ROI) if config.ROI else "Whole frame"),
        ("Two-line plate aspect", f"< {config.TWO_LINE_ASPECT}"),
        ("Vote window", f"{config.VOTE_WINDOW}s, at least {config.VOTE_MIN_SIGHTINGS} sightings"),
        ("Stream", f"{config.STREAM_FPS} fps, JPEG quality {config.STREAM_JPEG_QUALITY}"),
    ]


@system_bp.route("/admin/system", methods=["GET", "POST"])
@admin_required
def system():
    from gate.camera import worker_overview

    error = None
    if request.method == "POST":
        try:
            settings.save(request.form)
        except ValueError as exc:
            error = str(exc)
        else:
            flash("Settings saved and applied.")
            return redirect(url_for("system.system"))

    return render_template("system.html", tunables=settings.TUNABLES,
                           values=request.form if error else settings.current_values(),
                           error=error, pipeline=_pipeline(), workers=worker_overview()), \
        (400 if error else 200)

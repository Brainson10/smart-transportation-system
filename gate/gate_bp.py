from flask import Blueprint, Response, jsonify
from gate.camera import generate_frames
from gate.decision import get_latest_result

from flask import Blueprint, render_template, request, redirect, url_for, session

gate_bp = Blueprint("gate", __name__)

@gate_bp.route("/gate/login", methods=["GET", "POST"])
def gate_login():
    if request.method == "POST":
        gate = request.form["gate"]
        password = request.form["password"]

        # simple demo password check
        if password != "admin123":
            return render_template("gate_login.html", error="Invalid password")

        session["gate"] = gate
        return redirect(url_for("gate.gate_dashboard", gate=gate))

    return render_template("gate_login.html")


@gate_bp.route("/video_feed/hostel")
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )

@gate_bp.route("/gate/latest_result")
def latest_result():
    return jsonify(get_latest_result())

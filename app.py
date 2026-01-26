from flask import Flask, render_template, redirect, url_for, request, Response, jsonify
import sqlite3
import os
from werkzeug.utils import secure_filename

from authority.predict import run_predictions, predict_single_road
from gate.gate_db import init_gate_db
from gate.camera import generate_frames
from gate.decision import get_latest_result

# =================================================
# PATH CONFIG
# =================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

VEHICLE_DB = os.path.join(BASE_DIR, "database.db")
AUTHORITY_DB = os.path.join(BASE_DIR, "authority", "authority.db")
GATE_DB = os.path.join(BASE_DIR, "gate", "gate_security.db")

UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# =================================================
# DB HELPERS
# =================================================
def get_vehicle_db():
    conn = sqlite3.connect(VEHICLE_DB)
    conn.row_factory = sqlite3.Row
    return conn

def get_authority_db():
    conn = sqlite3.connect(AUTHORITY_DB)
    conn.row_factory = sqlite3.Row
    return conn

# =================================================
# FLASK APP
# =================================================
app = Flask(__name__)
app.secret_key = "gate-secret"

init_gate_db()

# =================================================
# HOME
# =================================================
@app.route("/")
def home():
    return render_template("index.html")

# =================================================
# ADMIN DASHBOARD
# =================================================
@app.route("/admin")
def admin_dashboard():
    conn = get_vehicle_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM vehicles")
    total_vehicles = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM vehicles WHERE status = 'ACTIVE'")
    active_vehicles = cur.fetchone()[0]
    conn.close()

    conn = get_authority_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM cameras")
    total_cameras = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM cameras WHERE status = 'ONLINE'")
    online_cameras = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM predictions")
    alerts = cur.fetchone()[0]

    cur.execute("""
        SELECT segment, predicted_risk, explanation, confidence
        FROM predictions
        ORDER BY 
            CASE predicted_risk
                WHEN 'HIGH' THEN 3
                WHEN 'MEDIUM' THEN 2
                WHEN 'LOW' THEN 1
                ELSE 0
            END DESC,
            confidence DESC
        LIMIT 3
    """)
    high_risk_roads = cur.fetchall()
    conn.close()

    return render_template(
        "admin.html",
        total_vehicles=total_vehicles,
        active_vehicles=active_vehicles,
        total_cameras=total_cameras,
        online_cameras=online_cameras,
        alerts=alerts,
        high_risk_roads=high_risk_roads
    )

# =================================================
# RUN AI
# =================================================
@app.route("/run_ai")
def run_ai():
    run_predictions()
    return redirect(url_for("admin_dashboard"))

# =================================================
# VEHICLE MANAGEMENT
# =================================================
@app.route("/admin/vehicles")
def manage_vehicles():
    conn = get_vehicle_db()
    cur = conn.cursor()
    cur.execute("SELECT id, number, owner, type, status FROM vehicles")
    vehicles = cur.fetchall()
    conn.close()
    return render_template("vehicles.html", vehicles=vehicles)

@app.route("/delete_vehicle/<int:vehicle_id>")
def delete_vehicle(vehicle_id):
    conn = get_vehicle_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("manage_vehicles"))

# =================================================
# REGISTER VEHICLE
# =================================================
@app.route("/register", methods=["GET", "POST"])
def register_vehicle():
    if request.method == "POST":
        conn = get_vehicle_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO vehicles (number, owner, type, status) VALUES (?, ?, ?, ?)",
            (
                request.form["number"],
                request.form["owner"],
                request.form["type"],
                "ACTIVE"
            )
        )
        conn.commit()
        conn.close()
        return redirect(url_for("manage_vehicles"))

    return render_template("register_vehicle.html")

# =================================================
# CAMERA MANAGEMENT
# =================================================
@app.route("/admin/cameras")
def manage_cameras():
    conn = get_authority_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, location, status FROM cameras")
    cameras = cur.fetchall()
    conn.close()
    return render_template("cameras.html", cameras=cameras)

@app.route("/admin/cameras/add", methods=["GET", "POST"])
def add_camera():
    if request.method == "POST":
        conn = get_authority_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cameras (name, location, status) VALUES (?, ?, ?)",
            (
                request.form["name"],
                request.form["location"],
                request.form["status"]
            )
        )
        conn.commit()
        conn.close()
        return redirect(url_for("manage_cameras"))

    return render_template("add_camera.html")

@app.route("/admin/cameras/toggle/<int:camera_id>")
def toggle_camera(camera_id):
    conn = get_authority_db()
    cur = conn.cursor()
    cur.execute("SELECT status FROM cameras WHERE id = ?", (camera_id,))
    status = cur.fetchone()[0]
    new_status = "OFFLINE" if status == "ONLINE" else "ONLINE"
    cur.execute("UPDATE cameras SET status = ? WHERE id = ?", (new_status, camera_id))
    conn.commit()
    conn.close()
    return redirect(url_for("manage_cameras"))

@app.route("/admin/cameras/delete/<int:camera_id>")
def delete_camera(camera_id):
    conn = get_authority_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("manage_cameras"))

# =================================================
# ROAD / AI RISK ANALYSIS
# =================================================
@app.route("/admin/roads")
def manage_roads():
    conn = get_authority_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT segment, predicted_risk, explanation, confidence
        FROM predictions
        ORDER BY confidence DESC
    """)
    roads = cur.fetchall()
    conn.close()
    return render_template("roads.html", roads=roads)

# =================================================
# GATE LOGIN
# =================================================
@app.route("/gate")
def gate_entry():
    return redirect(url_for("gate_login"))

@app.route("/gate-login", methods=["GET", "POST"])
def gate_login():
    conn = sqlite3.connect(GATE_DB)
    cur = conn.cursor()
    cur.execute("SELECT location FROM gates")
    locations = cur.fetchall()

    if request.method == "POST":
        cur.execute(
            "SELECT * FROM gates WHERE location=? AND password=?",
            (request.form["location"], request.form["password"])
        )
        gate = cur.fetchone()
        conn.close()

        if gate:
            # 🔥 FIX: pass selected gate
            return redirect(url_for("gate_dashboard", gate=gate[1]))
        else:
            return render_template(
                "gate_login.html",
                locations=locations,
                error="Invalid gate password"
            )

    conn.close()
    return render_template("gate_login.html", locations=locations)

# =================================================
# GATE DASHBOARD (LIVE ANPR)
# =================================================
@app.route("/gate/dashboard/<gate>")
def gate_dashboard(gate):
    return render_template("gate_dashboard.html", gate=gate)

@app.route("/video_feed/<gate>")
def video_feed(gate):
    return Response(
        generate_frames(gate),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )

@app.route("/gate/latest_result")
def latest_result():
    return jsonify(get_latest_result())

# =================================================
# CLEAR AI DATA
# =================================================
@app.route("/clear-risk", methods=["POST"])
def clear_risk():
    conn = get_authority_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM predictions")
    conn.commit()
    conn.close()
    return redirect(url_for("admin_dashboard"))

# =================================================
# RUN SERVER
# =================================================
if __name__ == "__main__":
    app.run(debug=True)

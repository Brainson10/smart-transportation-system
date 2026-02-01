from flask import Flask, render_template, redirect, url_for, request, Response, jsonify
import sqlite3
import os
from datetime import date

from authority.predict import predict_single_road, run_predictions
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

    cur.execute("SELECT COUNT(*) FROM vehicles WHERE status='ACTIVE'")
    active_vehicles = cur.fetchone()[0]

    cur.execute("""
        SELECT name, violation_count
        FROM locations
        ORDER BY violation_count DESC
    """)
    locations = cur.fetchall()

    today = date.today().isoformat()
    cur.execute("""
        SELECT vehicle_number, location, violation_type, timestamp
        FROM violations
        WHERE DATE(timestamp) = ?
        ORDER BY timestamp DESC
    """, (today,))
    today_violations = cur.fetchall()
    conn.close()

    conn = get_authority_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM cameras")
    total_cameras = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM cameras WHERE status='ONLINE'")
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
        high_risk_roads=high_risk_roads,
        locations=locations,
        today_violations=today_violations
    )

# =================================================
# ✅ MANAGE CAMERAS (REQUIRED BY admin.html)
# =================================================
@app.route("/admin/cameras")
def manage_cameras():
    conn = get_authority_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cameras")
    cameras = cur.fetchall()
    conn.close()
    return render_template("cameras.html", cameras=cameras)

# =================================================
# ADD ROAD
# =================================================
@app.route("/admin/roads/add", methods=["GET", "POST"])
def add_road():
    if request.method == "POST":
        segment = request.form.get("segment")

        curve = int(request.form.get("curve"))
        junction = int(request.form.get("junction"))
        visibility = int(request.form.get("visibility"))
        lane_width = int(request.form.get("lane_width"))
        traffic_density = int(request.form.get("traffic_density"))

        predicted_risk, confidence, explanation = predict_single_road(
            curve, junction, visibility, lane_width, traffic_density
        )

        conn = get_authority_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO predictions (segment, predicted_risk, explanation, confidence)
            VALUES (?, ?, ?, ?)
        """, (segment, predicted_risk, explanation, confidence))
        conn.commit()
        conn.close()

        return redirect(url_for("admin_dashboard"))

    return render_template("add_road.html")

# =================================================
# RUN AI
# =================================================
@app.route("/run_ai")
def run_ai():
    run_predictions()
    return redirect(url_for("admin_dashboard"))

# =================================================
# VEHICLES
# =================================================
@app.route("/admin/vehicles")
def manage_vehicles():
    conn = get_vehicle_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, number, owner, phone, type, role, violation_count, status
        FROM vehicles
    """)
    vehicles = cur.fetchall()
    conn.close()
    return render_template("vehicles.html", vehicles=vehicles)

# =================================================
# REGISTER VEHICLE
# =================================================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        conn = get_vehicle_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO vehicles
            (number, owner, phone, type, role, status, violation_count)
            VALUES (?, ?, ?, ?, ?, 'ACTIVE', 0)
        """, (
            request.form.get("number"),
            request.form.get("owner"),
            request.form.get("phone"),
            request.form.get("type"),
            request.form.get("role"),
        ))
        conn.commit()
        conn.close()
        return redirect(url_for("manage_vehicles"))

    return render_template("register_vehicle.html")

@app.route("/delete_vehicle/<int:vehicle_id>")
def delete_vehicle(vehicle_id):
    conn = get_vehicle_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM vehicles WHERE id=?", (vehicle_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("manage_vehicles"))

# =================================================
# MANAGE ROADS (REQUIRED BY admin.html)
# =================================================

@app.route("/admin/roads")
def manage_roads():
    conn = get_authority_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT segment, predicted_risk, explanation, confidence
        FROM predictions
        ORDER BY 
            CASE predicted_risk
                WHEN 'HIGH' THEN 3
                WHEN 'MEDIUM' THEN 2
                WHEN 'LOW' THEN 1
            END DESC,
            confidence DESC
    """)
    roads = cur.fetchall()
    conn.close()

    # IMPORTANT: use YOUR existing template
    return render_template("roads.html", roads=roads)



# =================================================
# ADD LOCATION (REQUIRED BY admin.html)
# =================================================
@app.route("/admin/location/add", methods=["GET", "POST"])
def add_location():
    if request.method == "POST":
        name = request.form.get("name")

        conn = get_vehicle_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO locations (name, violation_count) VALUES (?, 0)",
            (name,)
        )
        conn.commit()
        conn.close()

        return redirect(url_for("admin_dashboard"))

    return render_template("add_location.html")

# =================================================
# VIEW VIOLATIONS (REQUIRED BY admin.html)
# =================================================
@app.route("/admin/violations")
def view_violations():
    conn = get_vehicle_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT vehicle_number, location, violation_type, timestamp
        FROM violations
        ORDER BY timestamp DESC
    """)
    violations = cur.fetchall()
    conn.close()

    return render_template("violations.html", violations=violations)

# =================================================
# ADD CAMERA (REQUIRED BY cameras.html)
# =================================================
@app.route("/admin/cameras/add", methods=["GET", "POST"])
def add_camera():
    if request.method == "POST":
        name = request.form.get("name")
        location = request.form.get("location")
        status = request.form.get("status", "ONLINE")

        conn = get_authority_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO cameras (name, location, status)
            VALUES (?, ?, ?)
        """, (name, location, status))
        conn.commit()
        conn.close()

        return redirect(url_for("manage_cameras"))

    return render_template("add_camera.html")

# =================================================
# TOGGLE CAMERA STATUS (REQUIRED BY cameras.html)
# =================================================
@app.route("/admin/cameras/toggle/<int:camera_id>")
def toggle_camera(camera_id):
    conn = get_authority_db()
    cur = conn.cursor()

    # Get current status
    cur.execute(
        "SELECT status FROM cameras WHERE id=?",
        (camera_id,)
    )
    row = cur.fetchone()

    if row:
        new_status = "OFFLINE" if row[0] == "ONLINE" else "ONLINE"
        cur.execute(
            "UPDATE cameras SET status=? WHERE id=?",
            (new_status, camera_id)
        )
        conn.commit()

    conn.close()
    return redirect(url_for("manage_cameras"))


# =================================================
# GATE
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
            return redirect(url_for("gate_dashboard", gate=gate[1]))

        return render_template("gate_login.html", locations=locations, error="Invalid gate password")

    conn.close()
    return render_template("gate_login.html", locations=locations)

@app.route("/gate/dashboard/<gate>")
def gate_dashboard(gate):
    return render_template("gate_dashboard.html", gate=gate)

# =================================================
# VIDEO FEED (LIVE CAMERA ONLY)
# =================================================
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
# RUN SERVER
# =================================================
if __name__ == "__main__":
    app.run(debug=True)

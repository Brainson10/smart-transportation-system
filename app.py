
from flask import Flask, render_template, redirect, url_for, request, Response, jsonify
import sqlite3
import os
from datetime import date

from authority.predict import predict_single_road, run_predictions
from gate.gate_db import init_gate_db
from gate.camera import generate_frames
from gate.decision import get_latest_result
from gate import decision   # DO NOT REMOVE
from datetime import datetime

def to_12hr(time_str):
    if not time_str:
        return None
    return datetime.strptime(time_str, "%H:%M").strftime("%I:%M %p")

# =================================================
# PATH CONFIG
# =================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

VEHICLE_DB = os.path.join(BASE_DIR, "database.db")
AUTHORITY_DB = os.path.join(BASE_DIR, "authority", "authority.db")
GATE_DB = os.path.join(BASE_DIR, "gate", "gate_security.db")
ADMIN_DB_PATH = VEHICLE_DB

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
    # =============================
    # VEHICLE + VIOLATIONS DB
    # =============================
    veh = get_vehicle_db()
    vcur = veh.cursor()

    vcur.execute("SELECT COUNT(*) FROM vehicles")
    total_vehicles = vcur.fetchone()[0]

    vcur.execute("SELECT COUNT(*) FROM vehicles WHERE status='ACTIVE'")
    active_vehicles = vcur.fetchone()[0]

    today = date.today().isoformat()
    vcur.execute("""
        SELECT vehicle_number, location, violation_type, timestamp
        FROM violations
        WHERE DATE(timestamp)=?
        ORDER BY timestamp DESC
    """, (today,))
    today_violations = vcur.fetchall()

    # violation count per road
    vcur.execute("""
        SELECT location, COUNT(*)
        FROM violations
        GROUP BY location
    """)
    violation_map = {row[0]: row[1] for row in vcur.fetchall()}

    # prohibited time per road
    vcur.execute("""
        SELECT name, prohibited_start, prohibited_end
        FROM locations
    """)
    prohibited_map = {
        row[0]: (row[1], row[2]) for row in vcur.fetchall()
    }

    veh.close()

    # =============================
    # AUTHORITY DB (AI)
    # =============================
    auth = get_authority_db()
    acur = auth.cursor()

    acur.execute("SELECT COUNT(*) FROM cameras")
    total_cameras = acur.fetchone()[0]

    acur.execute("SELECT COUNT(*) FROM cameras WHERE status='ONLINE'")
    online_cameras = acur.fetchone()[0]

    acur.execute("SELECT COUNT(*) FROM predictions")
    alerts = acur.fetchone()[0]

    acur.execute("""
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
    high_risk_roads = acur.fetchall()

    acur.execute("SELECT segment FROM predictions")
    segments = acur.fetchall()

    auth.close()

    # =============================
    # LOCATION OVERVIEW (MERGED)
    # =============================
    # locations = []

    # for (road,) in segments:
    #     violations = violation_map.get(road, 0)
    #     start, end = prohibited_map.get(road, (None, None))
    #     locations.append((road, violations, start, end))
    # =============================
    # LOCATION OVERVIEW (MERGED)
    # =============================
    location_overview = []

    veh = get_vehicle_db()
    vcur = veh.cursor()

    for seg in segments:
        road = seg[0]
        count = violation_map.get(road, 0)

        vcur.execute("""
            SELECT prohibited_start, prohibited_end
            FROM locations
            WHERE name=?
        """, (road,))
        row = vcur.fetchone()

        start = to_12hr(row[0]) if row and row[0] else None
        end = to_12hr(row[1]) if row and row[1] else None

        location_overview.append((road, count, start, end))

    veh.close()

    return render_template(
    "admin.html",
    total_vehicles=total_vehicles,
    active_vehicles=active_vehicles,
    total_cameras=total_cameras,
    online_cameras=online_cameras,
    alerts=alerts,
    high_risk_roads=high_risk_roads,
    today_violations=today_violations,
    locations=location_overview   #  FIXED
)
    

# =================================================
# ADD ROAD (AI INPUT)
# =================================================
@app.route("/admin/roads/add", methods=["GET", "POST"])
def add_road():
    if request.method == "POST":
        segment = request.form["segment"]
        curve = int(request.form["curve"])
        junction = int(request.form["junction"])
        visibility = int(request.form["visibility"])
        lane_width = int(request.form["lane_width"])
        traffic_density = int(request.form["traffic_density"])

        predicted_risk, confidence, explanation = predict_single_road(
            curve, junction, visibility, lane_width, traffic_density
        )

        conn = get_authority_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO predictions
            (segment, predicted_risk, explanation, confidence,
             curve, junction, visibility, lane_width, traffic_density, accident_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            segment, predicted_risk, explanation, confidence,
            curve, junction, visibility, lane_width, traffic_density
        ))
        conn.commit()
        conn.close()

        return redirect(url_for("manage_roads"))

    return render_template("add_road.html")

# =================================================
# MANAGE ROADS (EXPLAINABLE AI)
# =================================================
@app.route("/admin/roads")
def manage_roads():
    conn = get_authority_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            p.segment            AS segment,
            p.predicted_risk     AS predicted_risk,
            p.explanation        AS explanation,
            p.confidence         AS confidence,

            a.accident_count     AS accident_count,
            a.curve              AS curve,
            a.junction           AS junction,
            a.visibility         AS visibility,
            a.lane_width         AS lane_width,
            a.traffic_density    AS traffic_density

        FROM predictions p
        JOIN accident_data a
            ON p.segment = a.segment

        ORDER BY
            CASE p.predicted_risk
                WHEN 'HIGH' THEN 3
                WHEN 'MEDIUM' THEN 2
                WHEN 'LOW' THEN 1
            END DESC,
            a.accident_count DESC
    """)

    roads = cur.fetchall()
    conn.close()

    return render_template("roads.html", roads=roads)

# =================================================
# PROHIBITED TIME
# =================================================
@app.route("/admin/road/prohibited/<segment>", methods=["GET", "POST"])
def edit_prohibited_time(segment):
    conn = get_vehicle_db()
    cur = conn.cursor()

    # 🔑 ENSURE LOCATION EXISTS
    cur.execute("""
        INSERT OR IGNORE INTO locations (name, violation_count)
        VALUES (?, 0)
    """, (segment,))

    if request.method == "POST":
        cur.execute("""
            UPDATE locations
            SET prohibited_start=?, prohibited_end=?
            WHERE name=?
        """, (
            request.form["prohibited_start"],
            request.form["prohibited_end"],
            segment
        ))
        conn.commit()
        conn.close()
        return redirect(url_for("admin_dashboard"))

    cur.execute("""
        SELECT name, prohibited_start, prohibited_end
        FROM locations
        WHERE name=?
    """, (segment,))
    road = cur.fetchone()

    conn.close()
    return render_template("edit_prohibited_time.html", road=road)

@app.route("/admin/location/add", methods=["GET", "POST"])
def add_location():
    if request.method == "POST":
        name = request.form.get("name")

        conn = get_vehicle_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO locations (name, violation_count)
            VALUES (?, 0)
        """, (name,))
        conn.commit()
        conn.close()

        return redirect(url_for("admin_dashboard"))

    return render_template("add_location.html")

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
            request.form["number"],
            request.form["owner"],
            request.form["phone"],
            request.form["type"],
            request.form["role"],
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

@app.route("/admin/cameras")
def manage_cameras():
    conn = get_authority_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cameras")
    cameras = cur.fetchall()
    conn.close()
    return render_template("cameras.html", cameras=cameras)

@app.route("/admin/cameras/add", methods=["GET", "POST"])
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

        return redirect(url_for("manage_cameras"))

    return render_template("add_camera.html")

@app.route("/admin/cameras/toggle/<int:camera_id>")
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
    return redirect(url_for("manage_cameras"))

# =================================================
# GATE
# =================================================
@app.route("/gate")
def gate_entry():
    return redirect(url_for("gate_login"))

@app.route("/gate-login", methods=["GET", "POST"])
def gate_login():
    conn = sqlite3.connect(ADMIN_DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM locations")
    locations = [r[0] for r in cur.fetchall()]
    conn.close()

    if request.method == "POST":
        gate = sqlite3.connect(GATE_DB)
        cur = gate.cursor()
        cur.execute("""
            SELECT * FROM gates WHERE location=? AND password=?
        """, (request.form["location"], request.form["password"]))
        row = cur.fetchone()
        gate.close()

        if row:
            return redirect(url_for("gate_dashboard", gate=request.form["location"]))

        return render_template("gate_login.html", locations=locations, error="Invalid password")

    return render_template("gate_login.html", locations=locations)

@app.route("/gate/dashboard/<gate>")
def gate_dashboard(gate):
    return render_template("gate_dashboard.html", gate=gate)

@app.route("/gate/latest_result")
def gate_latest_result():
    return jsonify(get_latest_result())

@app.route("/video_feed/<gate>")
def video_feed(gate):
    return Response(
        generate_frames(gate),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )

# =================================================
# RUN SERVER
# =================================================
if __name__ == "__main__":
    app.run(debug=True)
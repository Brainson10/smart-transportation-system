
import sqlite3
from datetime import date
from datetime import datetime

from flask import Flask, render_template, redirect, url_for, request, Response
from flask import session

from authority.predict import run_predictions
from backend.routes.admin_routes import admin_bp
from backend.routes.camera_management_routes import camera_management_bp
from backend.routes.gate_routes import gate_routes_bp
from backend.routes.vehicle_routes import vehicle_bp
from backend.routes.violation_routes import violation_bp
from config import AUTHORITY_DB, STATIC_DIR, TEMPLATE_DIR, VEHICLE_DB
from gate.gate_db import init_gate_db
from gate.camera import generate_frames
from gate import decision   # DO NOT REMOVE
from gate.decision import gate_bp

def to_12hr(time_str):
    if not time_str:
        return None
    return datetime.strptime(time_str, "%H:%M").strftime("%I:%M %p")

# =================================================
# PATH CONFIG
# =================================================
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
app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR),
)
app.secret_key = "gate-secret"

app.register_blueprint(admin_bp)
app.register_blueprint(camera_management_bp)
app.register_blueprint(gate_routes_bp)
app.register_blueprint(vehicle_bp)
app.register_blueprint(violation_bp)

app.register_blueprint(gate_bp, url_prefix="/gate")

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

    # 🔐 ADMIN LOGIN PROTECTION (MANDATORY)
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin.admin_login"))

    # =============================
    # VEHICLE + VIOLATIONS DB
    # =============================
    veh = get_vehicle_db()
    vcur = veh.cursor()

    # Vehicle stats
    vcur.execute("SELECT COUNT(*) FROM vehicles")
    total_vehicles = vcur.fetchone()[0]

    vcur.execute("SELECT COUNT(*) FROM vehicles WHERE status='ACTIVE'")
    active_vehicles = vcur.fetchone()[0]

    # Today's violations
    today = date.today().isoformat()
    vcur.execute("""
        SELECT vehicle_number, location, violation_type, timestamp
        FROM violations
        WHERE DATE(timestamp)=?
        ORDER BY timestamp DESC
    """, (today,))
    today_violations = vcur.fetchall()

    # Violation count per road
    vcur.execute("""
        SELECT location, COUNT(*)
        FROM violations
        GROUP BY location
    """)
    violation_map = {row[0]: row[1] for row in vcur.fetchall()}

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

    # Top 3 high-risk roads
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

    # All AI road segments
    acur.execute("SELECT segment FROM predictions")
    segments = acur.fetchall()

    auth.close()

    # =============================
    # LOCATION OVERVIEW (MERGED)
    # =============================
    location_overview = []

    veh = get_vehicle_db()
    vcur = veh.cursor()

    for (road,) in segments:
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

    # =============================
    # RENDER DASHBOARD
    # =============================
    return render_template(
        "admin.html",
        total_vehicles=total_vehicles,
        active_vehicles=active_vehicles,
        total_cameras=total_cameras,
        online_cameras=online_cameras,
        alerts=alerts,
        high_risk_roads=high_risk_roads,
        today_violations=today_violations,
        locations=location_overview
    )
    

# =================================================
# ADD ROAD (AI INPUT)
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

            COALESCE(a.accident_count, 0)  AS accident_count,
            COALESCE(a.curve, 0)            AS curve,
            COALESCE(a.junction, 0)         AS junction,
            COALESCE(a.visibility, 0)       AS visibility,
            COALESCE(a.lane_width, 0)       AS lane_width,
            COALESCE(a.traffic_density, 0)  AS traffic_density

        FROM predictions p
        LEFT JOIN accident_data a
            ON p.segment = a.segment

        ORDER BY
            CASE p.predicted_risk
                WHEN 'HIGH' THEN 3
                WHEN 'MEDIUM' THEN 2
                WHEN 'LOW' THEN 1
            END DESC,
            accident_count DESC
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

@app.route("/admin/roads/add", methods=["GET", "POST"])
def add_road():
    return render_template("add_road.html")

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

# =================================================
# RUN AI
# =================================================
@app.route("/run_ai")
def run_ai():
    run_predictions()
    return redirect(url_for("admin_dashboard"))

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

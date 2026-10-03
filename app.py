import logging
import re
from datetime import datetime

from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, url_for)
from werkzeug.exceptions import HTTPException

import config
from authority.predict import predict_single_road, run_predictions
from backend import auth
from backend.auth import admin_required, gate_api_required
from backend.db import authority_db, init_databases, vehicle_db
from backend.routes.admin_routes import admin_bp
from backend.routes.camera_management_routes import camera_management_bp
from backend.routes.gate_routes import gate_routes_bp
from backend.routes.vehicle_routes import vehicle_bp
from backend.routes.violation_routes import violation_bp
from gate import capture
from gate import decision   # noqa: F401  (registers pause/result state)
from gate.camera import generate_frames
from gate.decision import gate_bp

config.setup_logging()
log = logging.getLogger(__name__)

HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_NAME_LENGTH = 80


def to_12hr(time_str):
    if not time_str:
        return None
    try:
        return datetime.strptime(time_str, "%H:%M").strftime("%I:%M %p")
    except ValueError:
        return time_str


# =================================================
# FLASK APP
# =================================================
app = Flask(
    __name__,
    template_folder=str(config.TEMPLATE_DIR),
    static_folder=str(config.STATIC_DIR),
)
auth.init_app(app)          # secret key, session cookie, CSRF, template helpers

app.register_blueprint(admin_bp)
app.register_blueprint(camera_management_bp)
app.register_blueprint(gate_routes_bp)
app.register_blueprint(vehicle_bp)
app.register_blueprint(violation_bp)
app.register_blueprint(gate_bp, url_prefix="/gate")

# Creates/upgrades both databases and seeds demo data into empty tables, so a
# fresh clone (where *.db is gitignored) starts instead of crashing.
init_databases()


# =================================================
# ERRORS
# =================================================
ERROR_TITLES = {
    400: "Bad request", 401: "Login required", 403: "Forbidden",
    404: "Page not found", 405: "Method not allowed", 500: "Something went wrong",
}


def _wants_json():
    path = request.path
    return ((path.startswith("/gate/") and not path.startswith("/gate/dashboard"))
            or path.startswith("/camera_status"))


@app.errorhandler(HTTPException)
def handle_http_error(exc):
    if _wants_json():
        return jsonify({"status": "error", "message": exc.description}), exc.code
    return render_template("error.html", code=exc.code,
                           title=ERROR_TITLES.get(exc.code, exc.name),
                           message=exc.description), exc.code


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    log.exception("unhandled error on %s %s", request.method, request.path)
    if _wants_json():
        return jsonify({"status": "error", "message": "Internal server error"}), 500
    return render_template("error.html", code=500, title=ERROR_TITLES[500],
                           message="The error has been logged. Please try again."), 500


# =================================================
# HOME
# =================================================
def _count(conn, sql):
    try:
        return conn.execute(sql).fetchone()[0]
    except Exception:
        return 0


@app.route("/")
def home():
    # Real numbers instead of the hardcoded "120+ / 500+" the page used to show.
    with vehicle_db() as conn:
        vehicles = _count(conn, "SELECT COUNT(*) FROM vehicles")
        locations = _count(conn, "SELECT COUNT(*) FROM locations")
        violations = _count(conn, "SELECT COUNT(*) FROM violations")
    with authority_db() as conn:
        segments = _count(conn, "SELECT COUNT(*) FROM accident_data")
    return render_template("index.html", stats={
        "vehicles": vehicles, "locations": locations,
        "violations": violations, "segments": segments,
    })


# =================================================
# ADMIN DASHBOARD
# =================================================
@app.route("/admin")
@admin_required
def admin_dashboard():
    with vehicle_db() as conn:
        total_vehicles = _count(conn, "SELECT COUNT(*) FROM vehicles")
        active_vehicles = _count(conn, "SELECT COUNT(*) FROM vehicles WHERE status = 'ACTIVE'")

        # Timestamps are stored in UTC; compare and display in local time.
        today_violations = conn.execute("""
            SELECT vehicle_number, location, violation_type,
                   datetime(timestamp, 'localtime') AS local_time
            FROM violations
            WHERE DATE(timestamp, 'localtime') = DATE('now', 'localtime')
            ORDER BY timestamp DESC
        """).fetchall()

        # Case-insensitive, so 'academic block road' and 'Academic Block Road' count
        # as one place.
        violation_counts = {row[0]: row[1] for row in conn.execute(
            "SELECT LOWER(location), COUNT(*) FROM violations GROUP BY LOWER(location)")}

        location_rows = conn.execute("""
            SELECT name, prohibited_start, prohibited_end FROM locations
            ORDER BY name COLLATE NOCASE
        """).fetchall()

    with authority_db() as conn:
        total_cameras = _count(conn, "SELECT COUNT(*) FROM cameras")
        online_cameras = _count(conn, "SELECT COUNT(*) FROM cameras WHERE status = 'ONLINE'")
        alerts = _count(conn, "SELECT COUNT(*) FROM predictions WHERE predicted_risk = 'HIGH'")
        high_risk_roads = conn.execute("""
            SELECT segment, predicted_risk, explanation, confidence
            FROM predictions
            ORDER BY CASE predicted_risk WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2
                                         WHEN 'LOW' THEN 1 END DESC,
                     confidence DESC
            LIMIT 3
        """).fetchall()
        segments = [row[0] for row in conn.execute("SELECT segment FROM predictions")]

    # Every gate and road: the overview used to list only AI road segments, so gate
    # locations (e.g. 'Academic Gate', 123 violations) never appeared.
    overview, seen = [], set()
    for row in location_rows:
        seen.add(row["name"].lower())
        overview.append((row["name"], violation_counts.get(row["name"].lower(), 0),
                         to_12hr(row["prohibited_start"]), to_12hr(row["prohibited_end"])))
    for segment in segments:
        if segment.lower() not in seen:
            seen.add(segment.lower())
            overview.append((segment, violation_counts.get(segment.lower(), 0), None, None))

    return render_template(
        "admin.html",
        total_vehicles=total_vehicles,
        active_vehicles=active_vehicles,
        total_cameras=total_cameras,
        online_cameras=online_cameras,
        alerts=alerts,
        high_risk_roads=high_risk_roads,
        today_violations=today_violations,
        locations=overview,
    )


# =================================================
# ROADS (AI RISK)
# =================================================
@app.route("/admin/roads")
@admin_required
def manage_roads():
    with authority_db() as conn:
        roads = conn.execute("""
            SELECT
                p.segment                       AS segment,
                p.predicted_risk                AS predicted_risk,
                p.explanation                   AS explanation,
                p.confidence                    AS confidence,
                COALESCE(a.accident_count, 0)   AS accident_count,
                COALESCE(a.curve, 0)           AS curve,
                COALESCE(a.junction, 0)        AS junction,
                COALESCE(a.visibility, 0)      AS visibility,
                COALESCE(a.lane_width, 0)      AS lane_width,
                COALESCE(a.traffic_density, 0) AS traffic_density
            FROM predictions p
            LEFT JOIN accident_data a ON p.segment = a.segment
            ORDER BY CASE p.predicted_risk WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2
                                           WHEN 'LOW' THEN 1 END DESC,
                     accident_count DESC
        """).fetchall()
    return render_template("roads.html", roads=roads)


def _int_choice(form, field, allowed):
    try:
        value = int(form.get(field, ""))
    except ValueError:
        return None
    return value if value in allowed else None


@app.route("/admin/roads/add", methods=["GET", "POST"])
@admin_required
def add_road():
    if request.method == "POST":
        # This route used to ignore POST entirely, so "Predict Risk & Save" did nothing.
        segment = (request.form.get("segment") or "").strip()
        features = {
            "curve": _int_choice(request.form, "curve", (0, 1)),
            "junction": _int_choice(request.form, "junction", (0, 1)),
            "visibility": _int_choice(request.form, "visibility", (0, 1, 2)),
            "lane_width": _int_choice(request.form, "lane_width", (0, 1, 2)),
            "traffic_density": _int_choice(request.form, "traffic_density", (0, 1, 2)),
        }

        error = None
        if not segment or len(segment) > MAX_NAME_LENGTH:
            error = f"Road name is required (max {MAX_NAME_LENGTH} characters)."
        elif any(value is None for value in features.values()):
            error = "Choose a value for every road attribute."
        if error:
            return render_template("add_road.html", error=error, form=request.form), 400

        with authority_db() as conn:
            if conn.execute("SELECT 1 FROM accident_data WHERE LOWER(segment) = LOWER(?)",
                            (segment,)).fetchone():
                return render_template("add_road.html", form=request.form,
                                       error=f"'{segment}' already exists."), 409

            risk, risk_score, explanation = predict_single_road(**features)
            with conn:
                conn.execute("""
                    INSERT INTO accident_data
                    (segment, curve, junction, visibility, lane_width, traffic_density,
                     accident_count)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                """, (segment, *features.values()))
                conn.execute("""
                    INSERT INTO predictions
                    (segment, predicted_risk, explanation, confidence, curve, junction,
                     visibility, lane_width, traffic_density, accident_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """, (segment, risk, explanation, risk_score, *features.values()))

        # Make it selectable for prohibited times and gate login.
        with vehicle_db() as conn:
            with conn:
                conn.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (segment,))

        flash(f"Added {segment}: predicted {risk} risk ({explanation}).")
        return redirect(url_for("manage_roads"))

    return render_template("add_road.html", form={})


@app.route("/admin/road/prohibited/<segment>", methods=["GET", "POST"])
@admin_required
def edit_prohibited_time(segment):
    if request.method == "POST":
        start = (request.form.get("prohibited_start") or "").strip()
        end = (request.form.get("prohibited_end") or "").strip()

        error = None
        if bool(start) != bool(end):
            error = "Set both a start and an end time, or clear both."
        elif start and not (HHMM_RE.match(start) and HHMM_RE.match(end)):
            error = "Times must be in HH:MM (24-hour) format."
        elif start and start == end:
            error = "Start and end must differ (equal times would mean no restriction)."
        if error:
            return render_template("edit_prohibited_time.html", error=error,
                                   road=(segment, start, end)), 400

        with vehicle_db() as conn:
            with conn:
                conn.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (segment,))
                conn.execute("""
                    UPDATE locations SET prohibited_start = ?, prohibited_end = ?
                    WHERE LOWER(name) = LOWER(?)
                """, (start or None, end or None, segment))
        flash(f"Prohibited time for {segment} saved.")
        return redirect(url_for("admin_dashboard"))

    # GET no longer writes to the DB.
    with vehicle_db() as conn:
        row = conn.execute("""
            SELECT name, prohibited_start, prohibited_end FROM locations
            WHERE LOWER(name) = LOWER(?)
        """, (segment,)).fetchone()
    road = tuple(row) if row else (segment, None, None)
    return render_template("edit_prohibited_time.html", road=road)


@app.route("/admin/location/add", methods=["GET", "POST"])
@admin_required
def add_location():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        start = (request.form.get("prohibited_start") or "").strip()
        end = (request.form.get("prohibited_end") or "").strip()

        error = None
        if not name or len(name) > MAX_NAME_LENGTH:
            error = f"Location name is required (max {MAX_NAME_LENGTH} characters)."
        elif bool(start) != bool(end):
            error = "Set both prohibited times, or leave both empty."
        elif start and not (HHMM_RE.match(start) and HHMM_RE.match(end)):
            error = "Times must be in HH:MM (24-hour) format."
        elif start and start == end:
            error = "Start and end must differ."

        if not error:
            with vehicle_db() as conn:
                if conn.execute("SELECT 1 FROM locations WHERE LOWER(name) = LOWER(?)",
                                (name,)).fetchone():
                    error = f"'{name}' already exists."
                else:
                    with conn:
                        conn.execute("""
                            INSERT INTO locations (name, prohibited_start, prohibited_end)
                            VALUES (?, ?, ?)
                        """, (name, start or None, end or None))
        if error:
            return render_template("add_location.html", error=error, form=request.form), 400

        flash(f"Added location {name}.")
        return redirect(url_for("admin_dashboard"))

    return render_template("add_location.html", form={})


@app.route("/run_ai", methods=["POST"])
@admin_required
def run_ai():
    count = run_predictions()
    flash(f"Risk predictions refreshed for {count} road segments.")
    back_to_roads = request.form.get("next") == "roads"
    return redirect(url_for("manage_roads" if back_to_roads else "admin_dashboard"))


# =================================================
# LIVE VIDEO
# =================================================
def _known_location(name):
    with vehicle_db() as conn:
        return conn.execute("SELECT 1 FROM locations WHERE LOWER(name) = LOWER(?)",
                            ((name or "").strip(),)).fetchone() is not None


@app.route("/video_feed/<gate>")
@gate_api_required
def video_feed(gate):
    # Every distinct gate name starts a detection worker thread that never exits,
    # so only real locations may open a stream.
    if not _known_location(gate):
        abort(404, description=f"Unknown gate '{gate}'.")
    return Response(
        generate_frames(gate),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/camera_status")
@app.route("/camera_status/<gate>")
@gate_api_required
def camera_status(gate="default"):
    """Capture health, so a stalled camera is diagnosable during a demo."""
    return jsonify(capture.get_stream(gate.strip().lower()).stats())


# =================================================
# RUN SERVER
# =================================================
if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG,
            threaded=True, use_reloader=False)

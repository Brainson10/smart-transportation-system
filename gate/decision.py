from flask import Blueprint, render_template, request, redirect, session
import sqlite3
import os
import re

gate_bp = Blueprint("gate", __name__)

# =================================================
# PATH CONFIG
# =================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Gate security DB (for gate login, locations)
DB_PATH = os.path.join(BASE_DIR, "gate_security.db")

# Admin vehicle DB (authority)
ADMIN_DB_PATH = os.path.join(
    os.path.dirname(BASE_DIR),
    "database.db"
)

# =================================================
# FRAME STABILITY CONFIG
# =================================================
NO_VEHICLE_COUNT = 0
PLATE_COUNT = 0

NO_VEHICLE_THRESHOLD = 5   # frames
PLATE_THRESHOLD = 2        # frames

# =================================================
# DB HELPERS
# =================================================
def get_gate_db():
    return sqlite3.connect(DB_PATH)

# =================================================
# GATE LOGIN
# =================================================
@gate_bp.route("/gate", methods=["GET", "POST"])
def gate_login():
    conn = get_gate_db()
    cur = conn.cursor()

    cur.execute("SELECT location FROM gates")
    locations = cur.fetchall()

    if request.method == "POST":
        location = request.form.get("location")
        password = request.form.get("password")

        cur.execute(
            "SELECT * FROM gates WHERE location=? AND password=?",
            (location, password)
        )
        gate = cur.fetchone()
        conn.close()

        if gate:
            session["gate_location"] = location
            return redirect("/gate/dashboard")
        else:
            return render_template(
                "gate_login.html",
                locations=locations,
                error="Invalid gate password"
            )

    conn.close()
    return render_template("gate_login.html", locations=locations)

# =================================================
# GATE DASHBOARD
# =================================================
@gate_bp.route("/gate/dashboard")
def gate_dashboard():
    if "gate_location" not in session:
        return redirect("/gate")

    return render_template(
        "gate_dashboard.html",
        location=session["gate_location"]
    )

# =================================================
# LATEST RESULT (SHARED STATE)
# =================================================
latest_result = {
    "vehicle_number": None,
    "confidence": 0,
    "decision": "WAITING",
    "reason": "No vehicle detected"
}

def update_latest_result(data):
    global latest_result
    latest_result = data

def get_latest_result():
    return latest_result

# =================================================
# VEHICLE NUMBER NORMALIZATION (IMPORTANT)
# =================================================
def normalize_vehicle_number(raw):
    if not raw:
        return None

    # Uppercase + remove spaces/symbols
    cleaned = re.sub(r"[^A-Z0-9]", "", raw.upper())

    # Indian plates usually 8–10 chars
    if len(cleaned) < 8:
        return None

    return cleaned

# =================================================
# ADMIN VEHICLE CHECK
# =================================================
def check_vehicle_db(vehicle_number):
    conn = sqlite3.connect(ADMIN_DB_PATH)
    cur = conn.cursor()

    cur.execute(
        "SELECT status FROM vehicles WHERE REPLACE(number,' ','') = ?",
        (vehicle_number,)
    )
    row = cur.fetchone()
    conn.close()

    if row and row[0] == "ACTIVE":
        return "ALLOW ENTRY", "Registered vehicle"
    else:
        return "DENY ENTRY", "Unregistered vehicle"

# =================================================
# DECISION ENGINE
# =================================================
def decide(anpr_result):
    global NO_VEHICLE_COUNT, PLATE_COUNT

    # =================================================
    # NO VEHICLE DETECTED
    # =================================================
    if anpr_result.get("status") == "NO_VEHICLE":
        NO_VEHICLE_COUNT += 1
        PLATE_COUNT = 0

        if NO_VEHICLE_COUNT >= NO_VEHICLE_THRESHOLD:
            result = {
                "vehicle_number": None,
                "confidence": 0,
                "decision": "WAITING",
                "reason": "No vehicle detected"
            }
            update_latest_result(result)
            return result
        else:
            return get_latest_result()

    # =================================================
    # VEHICLE / PLATE DETECTED
    # =================================================
    PLATE_COUNT += 1
    NO_VEHICLE_COUNT = 0

    if PLATE_COUNT < PLATE_THRESHOLD:
        result = {
            "vehicle_number": None,
            "confidence": 0,
            "decision": "WAITING",
            "reason": "Vehicle approaching"
        }
        update_latest_result(result)
        return result

    # =================================================
    # NORMALIZE OCR OUTPUT
    # =================================================
    raw_plate = anpr_result.get("vehicle_number")
    vehicle_number = normalize_vehicle_number(raw_plate)

    if vehicle_number is None:
        result = {
            "vehicle_number": None,
            "confidence": 0,
            "decision": "WAITING",
            "reason": "Unreadable plate"
        }
        update_latest_result(result)
        return result

    # =================================================
    # LOW OCR CONFIDENCE
    # =================================================
    if anpr_result.get("confidence", 0) < 80:
        result = {
            "vehicle_number": vehicle_number,
            "confidence": anpr_result.get("confidence"),
            "decision": "MANUAL CHECK",
            "reason": "Low OCR confidence"
        }
        update_latest_result(result)
        return result

    # =================================================
    # VALID OCR → CHECK ADMIN DB
    # =================================================
    decision, reason = check_vehicle_db(vehicle_number)

    result = {
        "vehicle_number": vehicle_number,
        "confidence": anpr_result.get("confidence"),
        "decision": decision,
        "reason": reason
    }
    update_latest_result(result)
    return result

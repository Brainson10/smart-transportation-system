from flask import Blueprint, render_template, request, redirect, session
import sqlite3
import os
import re
from datetime import datetime
from gate.sms import send_sms   # ✅ USE THIS

gate_bp = Blueprint("gate", __name__)

# =================================================
# PATH CONFIG
# =================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, "gate_security.db")
ADMIN_DB_PATH = os.path.join(os.path.dirname(BASE_DIR), "database.db")

# =================================================
# FRAME STABILITY CONFIG
# =================================================
NO_VEHICLE_COUNT = 0
PLATE_COUNT = 0

NO_VEHICLE_THRESHOLD = 5
PLATE_THRESHOLD = 2

# =================================================
# DB HELPERS
# =================================================
def get_gate_db():
    return sqlite3.connect(DB_PATH)

# =================================================
# LATEST RESULT
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
# NORMALIZE VEHICLE NUMBER
# =================================================
def normalize_vehicle_number(raw):
    if not raw:
        return None
    cleaned = re.sub(r"[^A-Z0-9]", "", raw.upper())
    return cleaned if len(cleaned) == 10 else None

# =================================================
# CHECK VEHICLE DB
# =================================================
def check_vehicle_db(vehicle_number):
    conn = sqlite3.connect(ADMIN_DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT role, status
        FROM vehicles
        WHERE REPLACE(number,' ','') = ?
    """, (vehicle_number,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None, None, "DENY ENTRY", "Unregistered vehicle"

    role, status = row
    if status != "ACTIVE":
        return role, status, "DENY ENTRY", "Blocked vehicle"

    return role, status, "ALLOW ENTRY", "Registered vehicle"

# =================================================
# PROHIBITED TIME CHECK
# =================================================
def is_prohibited_time(location_name):
    conn = sqlite3.connect(ADMIN_DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT prohibited_start, prohibited_end
        FROM locations
        WHERE name = ?
    """, (location_name,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return False

    start, end = row
    now = datetime.now().time()

    start_time = datetime.strptime(start, "%H:%M").time()
    end_time = datetime.strptime(end, "%H:%M").time()

    if start_time < end_time:
        return start_time <= now <= end_time
    return now >= start_time or now <= end_time

# =================================================
# DECISION ENGINE
# =================================================
def decide(anpr_result):
    global NO_VEHICLE_COUNT, PLATE_COUNT

    # -------- NO VEHICLE --------
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

        return get_latest_result()

    # -------- VEHICLE APPROACHING --------
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

    # -------- NORMALIZE --------
    vehicle_number = normalize_vehicle_number(anpr_result.get("vehicle_number"))

    if not vehicle_number:
        result = {
            "vehicle_number": None,
            "confidence": anpr_result.get("confidence", 0),
            "decision": "MANUAL CHECK",
            "reason": "Invalid plate format"
        }
        update_latest_result(result)
        return result

    if anpr_result.get("confidence", 0) < 80:
        result = {
            "vehicle_number": vehicle_number,
            "confidence": anpr_result.get("confidence"),
            "decision": "MANUAL CHECK",
            "reason": "Low OCR confidence"
        }
        update_latest_result(result)
        return result

    # -------- ADMIN CHECK --------
    role, status, decision, reason = check_vehicle_db(vehicle_number)
    location = session.get("gate_location")

    # 🔥 PROHIBITED TIME VIOLATION + SMS
    if decision == "ALLOW ENTRY" and is_prohibited_time(location):
        if role not in ["staff", "emergency"]:
            conn = sqlite3.connect(ADMIN_DB_PATH)
            cur = conn.cursor()

            # Insert violation
            cur.execute("""
                INSERT INTO violations (vehicle_number, location, violation_type)
                VALUES (?, ?, ?)
            """, (vehicle_number, location, "Prohibited time entry"))

            # Update counts
            cur.execute("""
                UPDATE vehicles
                SET violation_count = violation_count + 1
                WHERE REPLACE(number,' ','') = ?
            """, (vehicle_number,))

            cur.execute("""
                UPDATE locations
                SET violation_count = violation_count + 1
                WHERE name = ?
            """, (location,))

            # Fetch phone + updated count
            cur.execute("""
                SELECT phone, violation_count
                FROM vehicles
                WHERE REPLACE(number,' ','') = ?
            """, (vehicle_number,))
            phone, count = cur.fetchone()

            conn.commit()
            conn.close()

            # 📩 DEMO SMS
            if phone:
                send_sms(
                    phone,
                    f"ALERT: Vehicle {vehicle_number} violated gate rules at {location}. "
                    f"Violation count: {count}. Further violations may lead to blocking."
                )

            result = {
                "vehicle_number": vehicle_number,
                "confidence": anpr_result.get("confidence"),
                "decision": "DENY ENTRY",
                "reason": "Prohibited time violation"
            }
            update_latest_result(result)
            return result

    # -------- FINAL --------
    result = {
        "vehicle_number": vehicle_number,
        "confidence": anpr_result.get("confidence"),
        "decision": decision,
        "reason": reason
    }
    update_latest_result(result)
    return result

# import sqlite3
# import os
# import re
# from datetime import datetime
# from gate.sms import send_sms  

# # 🔴 PAUSE FLAG
# PAUSED_FOR_MANUAL = False


# BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# DB_PATH = os.path.join(BASE_DIR, "gate_security.db")
# ADMIN_DB_PATH = os.path.join(os.path.dirname(BASE_DIR), "database.db")

# NO_VEHICLE_COUNT = 0
# PLATE_COUNT = 0

# NO_VEHICLE_THRESHOLD = 5
# PLATE_THRESHOLD = 2


# latest_result = {
#     "vehicle_number": None,
#     "confidence": 0,
#     "decision": "WAITING",
#     "reason": "No vehicle detected"
# }

# def update_latest_result(data):
#     global latest_result
#     latest_result = data

# def get_latest_result():
#     return latest_result


# def normalize_vehicle_number(raw):
#     if not raw:
#         return None
#     cleaned = re.sub(r"[^A-Z0-9]", "", raw.upper())
#     return cleaned if len(cleaned) == 10 else None


# def check_vehicle_db(vehicle_number):
#     conn = sqlite3.connect(ADMIN_DB_PATH)
#     cur = conn.cursor()

#     cur.execute("""
#         SELECT role, status
#         FROM vehicles
#         WHERE REPLACE(number,' ','') = ?
#     """, (vehicle_number,))
#     row = cur.fetchone()
#     conn.close()

#     if not row:
#         return None, None, "DENY ENTRY", "Unregistered vehicle"

#     role, status = row
#     if status != "ACTIVE":
#         return role, status, "DENY ENTRY", "Blocked vehicle"

#     return role, status, "ALLOW ENTRY", "Registered vehicle"


# def is_prohibited_time(location_name):
#     conn = sqlite3.connect(ADMIN_DB_PATH)
#     cur = conn.cursor()

#     cur.execute("""
#         SELECT prohibited_start, prohibited_end
#         FROM locations
#         WHERE name = ?
#     """, (location_name,))
#     row = cur.fetchone()
#     conn.close()

#     if not row:
#         return False

#     start, end = row
#     now = datetime.now().time()

#     start_time = datetime.strptime(start, "%H:%M").time()
#     end_time = datetime.strptime(end, "%H:%M").time()

#     if start_time < end_time:
#         return start_time <= now <= end_time
#     return now >= start_time or now <= end_time


# def decide(anpr_result, gate_location):
#     global NO_VEHICLE_COUNT, PLATE_COUNT, PAUSED_FOR_MANUAL

#     # ⛔ HARD STOP IF PAUSED
#     if PAUSED_FOR_MANUAL:
#         return get_latest_result()

#     # -------- NO VEHICLE --------
#     if anpr_result.get("status") == "NO_VEHICLE":
#         NO_VEHICLE_COUNT += 1
#         PLATE_COUNT = 0

#         if NO_VEHICLE_COUNT >= NO_VEHICLE_THRESHOLD:
#             result = {
#                 "vehicle_number": None,
#                 "confidence": 0,
#                 "decision": "WAITING",
#                 "reason": "No vehicle detected"
#             }
#             update_latest_result(result)
#             return result

#         return get_latest_result()

#     # -------- VEHICLE APPROACHING --------
#     PLATE_COUNT += 1
#     NO_VEHICLE_COUNT = 0

#     if PLATE_COUNT < PLATE_THRESHOLD:
#         result = {
#             "vehicle_number": None,
#             "confidence": 0,
#             "decision": "WAITING",
#             "reason": "Vehicle approaching"
#         }
#         update_latest_result(result)
#         return result

#     # -------- NORMALIZE --------
#     vehicle_number = normalize_vehicle_number(
#         anpr_result.get("vehicle_number")
#     )

#     # -------- INVALID FORMAT → MANUAL --------
#     if not vehicle_number:
#         result = {
#             "vehicle_number": None,
#             "confidence": anpr_result.get("confidence", 0),
#             "decision": "MANUAL CHECK",
#             "reason": "Invalid plate format"
#         }

#         PAUSED_FOR_MANUAL = True  # 🔒 HOLD SYSTEM
#         update_latest_result(result)
#         return result

#     # -------- LOW CONFIDENCE → MANUAL --------
#     if anpr_result.get("confidence", 0) < 80:
#         result = {
#             "vehicle_number": vehicle_number,
#             "confidence": anpr_result.get("confidence"),
#             "decision": "MANUAL CHECK",
#             "reason": "Low OCR confidence"
#         }

#         PAUSED_FOR_MANUAL = True  # 🔒 HOLD SYSTEM
#         update_latest_result(result)
#         return result

#     # -------- ADMIN CHECK --------
#     role, status, decision, reason = check_vehicle_db(vehicle_number)

#     # -------- PROHIBITED TIME --------
#     if decision == "ALLOW ENTRY" and is_prohibited_time(gate_location):
#         if role not in ["staff", "emergency"]:
#             conn = sqlite3.connect(ADMIN_DB_PATH)
#             cur = conn.cursor()

#             cur.execute("""
#                 INSERT INTO violations (vehicle_number, location, violation_type)
#                 VALUES (?, ?, ?)
#             """, (vehicle_number, gate_location, "Prohibited time entry"))

#             cur.execute("""
#                 UPDATE vehicles
#                 SET violation_count = violation_count + 1
#                 WHERE REPLACE(number,' ','') = ?
#             """, (vehicle_number,))

#             cur.execute("""
#                 UPDATE locations
#                 SET violation_count = violation_count + 1
#                 WHERE name = ?
#             """, (gate_location,))

#             cur.execute("""
#                 SELECT phone, violation_count
#                 FROM vehicles
#                 WHERE REPLACE(number,' ','') = ?
#             """, (vehicle_number,))
#             phone, count = cur.fetchone()

#             conn.commit()
#             conn.close()

#             if phone:
#                 send_sms(
#                     phone,
#                     f"ALERT: Vehicle {vehicle_number} violated gate rules at {gate_location}. "
#                     f"Violation count: {count}. Further violations may lead to blocking."
#                 )

#             result = {
#                 "vehicle_number": vehicle_number,
#                 "confidence": anpr_result.get("confidence"),
#                 "decision": "DENY ENTRY",
#                 "reason": "Prohibited time violation"
#             }

#             PAUSED_FOR_MANUAL = True  # 🔒 HOLD SYSTEM
#             update_latest_result(result)
#             return result

#     # -------- FINAL DECISION (ALLOW / DENY) --------
#     violation_count = get_violation_count(vehicle_number)

#     result = {
#         "vehicle_number": vehicle_number,
#         "confidence": anpr_result.get("confidence"),
#         "decision": decision,
#         "reason": reason,
#         "violations": violation_count   # 🔥 ADD THIS
#     }

#     PAUSED_FOR_MANUAL = True  # 🔒 HOLD SYSTEM
#     update_latest_result(result)
#     return result

# def get_violation_count(vehicle_number):
#     conn = sqlite3.connect(ADMIN_DB_PATH)
#     cur = conn.cursor()

#     cur.execute("""
#         SELECT violation_count
#         FROM vehicles
#         WHERE REPLACE(number,' ','') = ?
#     """, (vehicle_number,))

#     row = cur.fetchone()
#     conn.close()

#     return row[0] if row else 0

# from flask import Blueprint, jsonify

# gate_bp = Blueprint("gate", __name__)

# # 🔥 already exists
# PAUSED_FOR_MANUAL = False


# @gate_bp.route("/gate/resume_detection", methods=["POST"])
# def resume_detection():
#     global PAUSED_FOR_MANUAL, NO_VEHICLE_COUNT, PLATE_COUNT

#     PAUSED_FOR_MANUAL = False
#     NO_VEHICLE_COUNT = 0
#     PLATE_COUNT = 0

#     update_latest_result({
#         "vehicle_number": None,
#         "confidence": 0,
#         "decision": "WAITING",
#         "reason": "Ready for next vehicle"
#     })

#     return jsonify({"status": "resumed"})

import sqlite3
import os
import re
from datetime import datetime
from flask import Blueprint, jsonify
from gate.sms import send_sms

# =================================================
# 🔴 PAUSE FLAG (ONLY ONE)
# =================================================
PAUSED_FOR_MANUAL = False

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
# LATEST RESULT
# =================================================
latest_result = {
    "vehicle_number": None,
    "confidence": 0,
    "decision": "WAITING",
    "reason": "No vehicle detected",
    "violations": 0
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
# GET VIOLATION COUNT
# =================================================
def get_violation_count(vehicle_number):
    conn = sqlite3.connect(ADMIN_DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT violation_count
        FROM vehicles
        WHERE REPLACE(number,' ','') = ?
    """, (vehicle_number,))

    row = cur.fetchone()
    conn.close()

    return row[0] if row else 0

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
# def is_prohibited_time(location_name):
#     conn = sqlite3.connect(ADMIN_DB_PATH)
#     cur = conn.cursor()

#     cur.execute("""
#         SELECT prohibited_start, prohibited_end
#         FROM locations
#         WHERE name = ?
#     """, (location_name,))
#     row = cur.fetchone()
#     conn.close()

#     if not row:
#         return False

#     start, end = row
#     now = datetime.now().time()

#     start_time = datetime.strptime(start, "%H:%M").time()
#     end_time = datetime.strptime(end, "%H:%M").time()

#     if start_time < end_time:
#         return start_time <= now <= end_time
#     return now >= start_time or now <= end_time

def is_prohibited_time(location_name):
    import sqlite3
    import os
    from datetime import datetime

    # 🔹 authority.db path
    AUTHORITY_DB_PATH = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "authority.db"
    )

    conn = sqlite3.connect(AUTHORITY_DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT prohibited_start, prohibited_end
        FROM predictions
        WHERE segment = ?
    """, (location_name,))

    row = cur.fetchone()
    conn.close()

    # ❌ No prohibited time defined
    if not row or not row[0] or not row[1]:
        return False

    start, end = row
    now = datetime.now().time()

    start_time = datetime.strptime(start, "%H:%M").time()
    end_time = datetime.strptime(end, "%H:%M").time()

    # ✅ Normal time range (e.g. 22:00 → 06:00)
    if start_time < end_time:
        return start_time <= now <= end_time

    # ✅ Overnight range (e.g. 22:00 → 06:00)
    return now >= start_time or now <= end_time
# =================================================
# DECISION ENGINE
# =================================================
def decide(anpr_result, gate_location):
    global NO_VEHICLE_COUNT, PLATE_COUNT, PAUSED_FOR_MANUAL

    # ⛔ HARD STOP IF PAUSED
    if PAUSED_FOR_MANUAL:
        return get_latest_result()

    # -------- NO VEHICLE --------
    if anpr_result.get("status") == "NO_VEHICLE":
        NO_VEHICLE_COUNT += 1
        PLATE_COUNT = 0

        if NO_VEHICLE_COUNT >= NO_VEHICLE_THRESHOLD:
            result = {
                "vehicle_number": None,
                "confidence": 0,
                "decision": "WAITING",
                "reason": "No vehicle detected",
                "violations": 0
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
            "reason": "Vehicle approaching",
            "violations": 0
        }
        update_latest_result(result)
        return result

    # -------- NORMALIZE --------
    vehicle_number = normalize_vehicle_number(
        anpr_result.get("vehicle_number")
    )

    # -------- INVALID FORMAT → MANUAL --------
    if not vehicle_number:
        result = {
            "vehicle_number": None,
            "confidence": anpr_result.get("confidence", 0),
            "decision": "MANUAL CHECK",
            "reason": "Invalid plate format",
            "violations": 0
        }

        PAUSED_FOR_MANUAL = True
        update_latest_result(result)
        return result

    # -------- LOW CONFIDENCE → MANUAL --------
    if anpr_result.get("confidence", 0) < 80:
        result = {
            "vehicle_number": vehicle_number,
            "confidence": anpr_result.get("confidence"),
            "decision": "MANUAL CHECK",
            "reason": "Low OCR confidence",
            "violations": get_violation_count(vehicle_number)
        }

        PAUSED_FOR_MANUAL = True
        update_latest_result(result)
        return result

    # -------- ADMIN CHECK --------
    role, status, decision, reason = check_vehicle_db(vehicle_number)

    # -------- PROHIBITED TIME --------
    if decision == "ALLOW ENTRY" and is_prohibited_time(gate_location):
        if role not in ["staff", "emergency"]:
            conn = sqlite3.connect(ADMIN_DB_PATH)
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO violations (vehicle_number, location, violation_type)
                VALUES (?, ?, ?)
            """, (vehicle_number, gate_location, "Prohibited time entry"))

            cur.execute("""
                UPDATE vehicles
                SET violation_count = violation_count + 1
                WHERE REPLACE(number,' ','') = ?
            """, (vehicle_number,))

            cur.execute("""
                UPDATE locations
                SET violation_count = violation_count + 1
                WHERE name = ?
            """, (gate_location,))

            cur.execute("""
                SELECT phone, violation_count
                FROM vehicles
                WHERE REPLACE(number,' ','') = ?
            """, (vehicle_number,))
            phone, count = cur.fetchone()

            conn.commit()
            conn.close()

            if phone:
                send_sms(
                    phone,
                    f"ALERT: Vehicle {vehicle_number} violated gate rules at {gate_location}. "
                    f"Violation count: {count}."
                )

            result = {
                "vehicle_number": vehicle_number,
                "confidence": anpr_result.get("confidence"),
                "decision": "DENY ENTRY",
                "reason": "Prohibited time violation",
                "violations": count
            }

            PAUSED_FOR_MANUAL = True
            update_latest_result(result)
            return result

    # -------- FINAL DECISION --------
    violation_count = get_violation_count(vehicle_number)

    result = {
        "vehicle_number": vehicle_number,
        "confidence": anpr_result.get("confidence"),
        "decision": decision,
        "reason": reason,
        "violations": violation_count
    }

    PAUSED_FOR_MANUAL = True
    update_latest_result(result)
    return result

# =================================================
# GATE CONTROL API
# =================================================
gate_bp = Blueprint("gate", __name__)

@gate_bp.route("/gate/resume_detection", methods=["POST"])
def resume_detection():
    global PAUSED_FOR_MANUAL, NO_VEHICLE_COUNT, PLATE_COUNT

    PAUSED_FOR_MANUAL = False
    NO_VEHICLE_COUNT = 0
    PLATE_COUNT = 0

    update_latest_result({
        "vehicle_number": None,
        "confidence": 0,
        "decision": "WAITING",
        "reason": "Ready for next vehicle",
        "violations": 0
    })

    return jsonify({"status": "resumed"})
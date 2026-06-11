import sqlite3
from datetime import datetime
from flask import Blueprint, jsonify, request
from config import VEHICLE_DB
from gate.sms import send_sms

# =================================================
# 🔴 PAUSE FLAG (ONLY ONE SOURCE OF TRUTH)
# =================================================
PAUSED_FOR_MANUAL = False

# =================================================
# PATH CONFIG
# =================================================
ADMIN_DB_PATH = VEHICLE_DB

# =================================================
# LATEST RESULT (UI STATE)
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
# DB HELPERS
# =================================================
def get_violation_count(vehicle_number):
    conn = sqlite3.connect(ADMIN_DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT violation_count FROM vehicles
        WHERE REPLACE(number,' ','') = ?
    """, (vehicle_number,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0


def check_vehicle_db(vehicle_number):
    conn = sqlite3.connect(ADMIN_DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT role, status FROM vehicles
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

def is_prohibited_time(location_name):
    import sqlite3
    from datetime import datetime

    # STEP A: normalize location
    location_name = location_name.lower().replace(" gate", "").strip()
    print("STEP 3 ▶ normalized location_name:", location_name)

    row = None  # ✅ CRITICAL FIX: define row upfront

    try:
        conn = sqlite3.connect(ADMIN_DB_PATH)
        cur = conn.cursor()

        cur.execute("""
            SELECT prohibited_start, prohibited_end
            FROM locations
            WHERE LOWER(name) = ?
        """, (location_name,))

        row = cur.fetchone()
        conn.close()

        print("STEP 4 ▶ DB row fetched:", row)

        # No prohibited time set
        if not row or not row[0] or not row[1]:
            return False

        start, end = row

        now = datetime.now().time()
        start_time = datetime.strptime(start, "%H:%M").time()
        end_time = datetime.strptime(end, "%H:%M").time()

        print("STEP 5 ▶ now:", now)
        print("STEP 5 ▶ start:", start_time, "end:", end_time)

        # Same-day window
        if start_time < end_time:
            return start_time <= now <= end_time

        # Cross-midnight window
        return now >= start_time or now <= end_time

    except Exception as e:
        print("PROHIBITED TIME CHECK ERROR:", e)
        print("DEBUG ▶ row value at error:", row)
        return False
    
def send_violation_sms_internal(vehicle_number, location):
    conn = sqlite3.connect(ADMIN_DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT phone
        FROM vehicles
        WHERE number = ?
    """, (vehicle_number,))

    row = cur.fetchone()
    conn.close()

    if not row or not row[0]:
        print("⚠️ No phone number registered")
        return

    phone = row[0]

    message = (
        f"ALERT: Your vehicle {vehicle_number} was denied entry at {location} "
        f"due to prohibited time access. Repeated violations may lead to blocking."
    )

    send_sms(phone, message)

# =================================================
# DECISION ENGINE (FINAL & CLEAN)
# =================================================
def decide(anpr_result, gate_location):
    global PAUSED_FOR_MANUAL

    # ⛔ If already paused, do nothing
    if PAUSED_FOR_MANUAL:
        return get_latest_result()

    # Normalize gate location
    gate_location = gate_location.lower().strip()

    vehicle_number = anpr_result.get("vehicle_number")
    confidence = anpr_result.get("confidence", 0)

    # =============================
    # SAFETY: NO PLATE
    # =============================
    if not vehicle_number:
        result = {
            "vehicle_number": None,
            "confidence": confidence,
            "decision": "MANUAL CHECK",
            "reason": "No valid plate",
            "violations": 0
        }
        PAUSED_FOR_MANUAL = True
        update_latest_result(result)
        return result

    # =============================
    # LOW CONFIDENCE
    # =============================
    if confidence < 80:
        result = {
            "vehicle_number": vehicle_number,
            "confidence": confidence,
            "decision": "MANUAL CHECK",
            "reason": "Low OCR confidence",
            "violations": get_violation_count(vehicle_number)
        }
        PAUSED_FOR_MANUAL = True
        update_latest_result(result)
        return result

    # =============================
    # ADMIN / VEHICLE CHECK
    # =============================
    role, status, decision, reason = check_vehicle_db(vehicle_number)
    role = role.lower().strip() if role else ""

    # =============================
    # PROHIBITED TIME (AUTO DENY + SMS)
    # =============================
    print("STEP 1 ▶ gate_location received:", gate_location)
    if decision == "ALLOW ENTRY" and is_prohibited_time(gate_location):

        
        if True:

            conn = sqlite3.connect(ADMIN_DB_PATH)
            cur = conn.cursor()

            # Insert violation
            cur.execute("""
                INSERT INTO violations (vehicle_number, location, violation_type, timestamp)
                VALUES (?, ?, ?, datetime('now'))
            """, (vehicle_number, gate_location, "Prohibited time entry"))

            # Update vehicle violation count
            cur.execute("""
                UPDATE vehicles
                SET violation_count = violation_count + 1
                WHERE REPLACE(number,' ','') = ?
            """, (vehicle_number,))

            # Fetch phone + updated count
            cur.execute("""
                SELECT phone, violation_count
                FROM vehicles
                WHERE REPLACE(number,' ','') = ?
            """, (vehicle_number,))
            row = cur.fetchone()

            conn.commit()
            conn.close()

            phone = row[0] if row else None
            count = row[1] if row else get_violation_count(vehicle_number)

            # Send SMS if registered
            sms_sent = False

            if phone:
                send_sms(
                    phone,
                    f"ALERT: Your vehicle {vehicle_number} was denied entry at "
                    f"{gate_location} due to prohibited time. "
                    f"Violation count: {count}."
                )
                sms_sent = True

            result = {
                "vehicle_number": vehicle_number,
                "confidence": confidence,
                "decision": "DENY ENTRY",
                "reason": "Prohibited time violation",
                "violations": count,
                "sms_sent": sms_sent
            }

            PAUSED_FOR_MANUAL = True
            update_latest_result(result)
            return result

    # # =============================
    # # FINAL NORMAL DECISION
    # # =============================
    # result = {
    #     "vehicle_number": vehicle_number,
    #     "confidence": confidence,
    #     "decision": decision,
    #     "reason": reason,
    #     "violations": get_violation_count(vehicle_number)
    # }

    # # ❗ IMPORTANT FIX:
    # # Only pause for DENY or MANUAL, NOT ALLOW
    # if decision != "ALLOW ENTRY":
    #     PAUSED_FOR_MANUAL = True

    # update_latest_result(result)
    # return result
     # =============================
    # FINAL DECISION (FREEZE FOR ALL)
    # =============================
    result = {
        "vehicle_number": vehicle_number,
        "confidence": confidence,
        "decision": decision,
        "reason": reason,
        "violations": get_violation_count(vehicle_number)
    }

    # ✅ FREEZE SYSTEM FOR EVERY VEHICLE (ALLOW / DENY / MANUAL)
    PAUSED_FOR_MANUAL = True

    update_latest_result(result)
    return result


# =================================================
# GATE CONTROL API
# =================================================
gate_bp = Blueprint("gate", __name__)

@gate_bp.route("/resume_detection", methods=["POST"])
def resume_detection():
    global PAUSED_FOR_MANUAL
    from gate.camera import PLATE_VOTES, frame_count, prev_gray

    PAUSED_FOR_MANUAL = False

    # 🔥 FULL RESET
    PLATE_VOTES.clear()
    frame_count = 0
    prev_gray = None

    update_latest_result({
        "vehicle_number": None,
        "confidence": 0,
        "decision": "WAITING",
        "reason": "Ready for next vehicle",
        "violations": 0
    })

    return jsonify({"status": "resumed"})


@gate_bp.route("/latest_result")
def latest_result_api():
    return jsonify(get_latest_result())

@gate_bp.route("/send_violation_sms", methods=["POST"])
def send_violation_sms():
    data = get_latest_result()

    vehicle_number = data.get("vehicle_number")
    if not vehicle_number:
        return jsonify({"status": "error", "message": "No vehicle detected"}), 400

    conn = sqlite3.connect(ADMIN_DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT phone, violation_count
        FROM vehicles
        WHERE REPLACE(number,' ','') = ?
    """, (vehicle_number,))

    row = cur.fetchone()
    conn.close()

    if not row or not row[0]:
        return jsonify({"status": "error", "message": "Phone number not found"}), 404

    phone, count = row

    # 📩 SEND SMS
    send_sms(
        phone,
        f"ALERT: Your vehicle {vehicle_number} violated gate rules.\n"
        f"Violation count: {count}.\n"
        f"Please contact security if needed."
    )

    return jsonify({
        "status": "ok",
        "message": f"SMS sent to {phone}"
    })

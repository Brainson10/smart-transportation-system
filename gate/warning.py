import sqlite3
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VEHICLE_DB = os.path.join(BASE_DIR, "database.db")

def send_warning(vehicle_number, location):
    """
    Simulate sending warning to vehicle owner
    """

    conn = sqlite3.connect(VEHICLE_DB)
    cur = conn.cursor()

    cur.execute("""
        SELECT owner, phone, violation_count
        FROM vehicles
        WHERE number = ?
    """, (vehicle_number,))

    row = cur.fetchone()

    if not row:
        conn.close()
        return

    owner, phone, count = row
    count += 1

    # Update violation count
    cur.execute("""
        UPDATE vehicles
        SET violation_count = ?
        WHERE number = ?
    """, (count, vehicle_number))

    conn.commit()
    conn.close()

    #  SIMULATED WARNING (for now)
    print(" AUTO WARNING SENT")
    print(f"Owner: {owner}")
    print(f"Phone: {phone}")
    print(f"Vehicle: {vehicle_number}")
    print(f"Location: {location}")
    print(f"Violations: {count}")
    print("-------------------------")

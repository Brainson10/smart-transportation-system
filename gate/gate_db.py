
import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "gate_security.db")

def get_gate_db():
    return sqlite3.connect(DB_PATH)

def init_gate_db():
    conn = get_gate_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS gates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        location TEXT UNIQUE,
        password TEXT
    )
    """)

    # Demo gates (insert only once)
    cur.execute("SELECT COUNT(*) FROM gates")
    if cur.fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO gates (location, password) VALUES (?, ?)",
            [
                ("Main Gate", "main123"),
                ("Hostel Gate", "hostel123"),
                ("Academic Gate", "acad123")
            ]
        )

    conn.commit()
    conn.close()

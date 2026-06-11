import sqlite3

from config import GATE_DB


conn = sqlite3.connect(GATE_DB)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS vehicle_pass (
    plate TEXT,
    priority INTEGER
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS violations (
    plate TEXT,
    count INTEGER
)
""")

cur.execute("INSERT INTO vehicle_pass VALUES ('MN**34',1)")
conn.commit()
conn.close()

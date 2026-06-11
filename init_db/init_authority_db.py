import sqlite3

from config import AUTHORITY_DB


conn = sqlite3.connect(AUTHORITY_DB)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS accident_risk (
    segment INTEGER PRIMARY KEY,
    risk TEXT,
    reason TEXT
)
""")

conn.commit()
conn.close()

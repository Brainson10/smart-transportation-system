# import sqlite3

# conn = sqlite3.connect("authority/authority.db")
# cur = conn.cursor()

# cur.execute("""
# CREATE TABLE IF NOT EXISTS accident_data (
#     segment_id INTEGER,
#     accidents INTEGER,
#     visibility INTEGER,
#     curve INTEGER
# )
# """)

# cur.execute("""
# CREATE TABLE IF NOT EXISTS predictions (
#     segment_id INTEGER,
#     risk TEXT,
#     reason TEXT
# )
# """)

# cur.execute("INSERT INTO accident_data VALUES (1, 15, 1, 1)")
# cur.execute("INSERT INTO accident_data VALUES (2, 3, 3, 0)")

# conn.commit()
# conn.close()

import sqlite3

conn = sqlite3.connect("authority.db")
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

import sqlite3

from config import AUTHORITY_DB

DB = AUTHORITY_DB

def fetch_data():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT * FROM accident_data")
    data = cur.fetchall()
    conn.close()
    return data

def save_prediction(segment, risk, reason):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("INSERT INTO predictions VALUES (?,?,?)",
                (segment, risk, reason))
    conn.commit()
    conn.close()

def fetch_predictions():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT * FROM predictions")
    rows = cur.fetchall()
    conn.close()
    return rows

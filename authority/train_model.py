import sqlite3
import pandas as pd
from sklearn.tree import DecisionTreeRegressor
import joblib
import os

# =================================================
# PATH CONFIG
# =================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "authority.db")
MODEL_PATH = os.path.join(BASE_DIR, "model.pkl")

# =================================================
# LOAD DATA FROM SQLITE
# =================================================
conn = sqlite3.connect(DB_PATH)

query = """
SELECT
    curve,
    junction,
    visibility,
    lane_width,
    traffic_density,
    accident_count
FROM accident_data
"""

df = pd.read_sql(query, conn)
conn.close()

# =================================================
# BASIC DATA VALIDATION
# =================================================
if df.empty:
    raise ValueError("No training data found in accident_data table")

# =================================================
# HANDLE OLD DATA (IMPORTANT FIX)
# =================================================
# Older rows may not have lane_width / traffic_density
# Fill with MEDIUM (1) as neutral default
df["lane_width"] = df["lane_width"].fillna(1)
df["traffic_density"] = df["traffic_density"].fillna(1)

# =================================================
# FEATURE SELECTION (MATCH PREDICTION)
# =================================================
FEATURES = [
    "curve",
    "junction",
    "visibility",
    "lane_width",
    "traffic_density"
]

TARGET = "accident_count"

X = df[FEATURES]
y = df[TARGET]

# =================================================
# TRAIN ML MODEL
# =================================================
model = DecisionTreeRegressor(
    max_depth=5,
    min_samples_leaf=2,
    random_state=42
)

model.fit(X, y)

# =================================================
# SAVE TRAINED MODEL
# =================================================
joblib.dump(model, MODEL_PATH)

print(" ML model trained successfully")
print(" Features used:", FEATURES)
print(f" Model saved at: {MODEL_PATH}")

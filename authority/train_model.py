import sqlite3
import pandas as pd
from sklearn.tree import DecisionTreeRegressor
import joblib

from config import AUTHORITY_DB, AUTHORITY_MODEL

# =================================================
# PATH CONFIG
# =================================================
DB_PATH = AUTHORITY_DB
MODEL_PATH = AUTHORITY_MODEL

# =================================================
# LOAD DATA FROM SQLITE (CORRECT JOIN)
# =================================================
conn = sqlite3.connect(DB_PATH)

query = """
SELECT
    r.curve,
    r.junction,
    r.visibility,
    r.lane_width,
    r.traffic_density,
    COALESCE(a.accident_count, 0) AS accident_count
FROM road_features r
LEFT JOIN accident_data a
ON r.segment = a.segment
"""

df = pd.read_sql(query, conn)
conn.close()

# =================================================
# BASIC DATA VALIDATION
# =================================================
if df.empty:
    raise ValueError("No training data found. Check road_features / accident_data")

# =================================================
# HANDLE OLD / MISSING DATA (SAFE)
# =================================================
df["lane_width"] = df["lane_width"].fillna(1)
df["traffic_density"] = df["traffic_density"].fillna(1)
df["accident_count"] = df["accident_count"].fillna(0)

# =================================================
# FEATURE SELECTION (AI-VALID)
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

print("✅ ML model trained successfully")
print("✅ Features used:", FEATURES)
print("✅ Target:", TARGET)
print(f"✅ Model saved at: {MODEL_PATH}")

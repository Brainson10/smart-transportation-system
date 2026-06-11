import sqlite3
import joblib
import pandas as pd

from config import AUTHORITY_DB, AUTHORITY_MODEL

# =================================================
# PATH CONFIG
# =================================================
DB_PATH = AUTHORITY_DB
MODEL_PATH = AUTHORITY_MODEL

# =================================================
# LOAD MODEL (ONCE)
# =================================================
model = joblib.load(MODEL_PATH)

# =================================================
# HELPER: SCORE → RISK LABEL
# =================================================
def score_to_risk(score):
    if score >= 5:
        return "HIGH"
    elif score >= 2:
        return "MEDIUM"
    else:
        return "LOW"

# =================================================
# SINGLE ROAD PREDICTION (USED BY ADD ROAD)
# =================================================
def predict_single_road(curve, junction, visibility, lane_width, traffic_density):
    """
    Predict risk for ONE road segment using form input
    """

    X_input = pd.DataFrame(
        [[curve, junction, visibility, lane_width, traffic_density]],
        columns=[
            "curve",
            "junction",
            "visibility",
            "lane_width",
            "traffic_density"
        ]
    )

    score = model.predict(X_input)[0]
    confidence = min(100, int((score / 8) * 100))
    predicted_risk = score_to_risk(score)

    reasons = []
    if curve == 1:
        reasons.append("Curved road")
    if junction == 1:
        reasons.append("Junction present")
    if visibility == 0:
        reasons.append("Low visibility")
    if lane_width == 0:
        reasons.append("Narrow lane")
    if traffic_density == 2:
        reasons.append("High traffic density")

    explanation = ", ".join(reasons) if reasons else "Normal road conditions"

    return predicted_risk, confidence, explanation

# =================================================
# BULK PREDICTION (USED BY RUN AI BUTTON)
# =================================================
def run_predictions():
    """
    Predict risk for ALL stored road data
    """

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Clear old predictions
    cur.execute("DELETE FROM predictions")

    # Fetch road features
    cur.execute("""
        SELECT
            segment,
            curve,
            junction,
            visibility,
            lane_width,
            traffic_density
        FROM accident_data
    """)
    rows = cur.fetchall()

    for segment, curve, junction, visibility, lane_width, traffic_density in rows:

        X_input = pd.DataFrame(
            [[curve, junction, visibility, lane_width, traffic_density]],
            columns=[
                "curve",
                "junction",
                "visibility",
                "lane_width",
                "traffic_density"
            ]
        )

        score = model.predict(X_input)[0]
        confidence = min(100, int((score / 8) * 100))
        predicted_risk = score_to_risk(score)

        reasons = []
        if curve == 1:
            reasons.append("Curved road")
        if junction == 1:
            reasons.append("Junction present")
        if visibility == 0:
            reasons.append("Low visibility")
        if lane_width == 0:
            reasons.append("Narrow lane")
        if traffic_density == 2:
            reasons.append("High traffic density")

        explanation = ", ".join(reasons) if reasons else "Normal road conditions"

        cur.execute("""
            INSERT INTO predictions
            (segment, predicted_risk, explanation, confidence)
            VALUES (?, ?, ?, ?)
        """, (segment, predicted_risk, explanation, confidence))

    conn.commit()
    conn.close()

    print("AI predictions generated and stored successfully")

# =================================================
# ALLOW DIRECT RUN (OPTIONAL)
# =================================================
if __name__ == "__main__":
    run_predictions()

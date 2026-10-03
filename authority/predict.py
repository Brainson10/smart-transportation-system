"""
Accident-risk scoring for road segments.

The model predicts an expected accident count from road features. The value
stored in predictions.confidence (shown as "Risk score") is that prediction
scaled to 0-100 -- it is NOT a statistical confidence, and the UI no longer
labels it as one.

The model is loaded lazily, so importing this module (which app.py does) no
longer reads model.pkl at import time.
"""

import logging
import threading

import joblib
import pandas as pd

import config
from backend.db import authority_db

log = logging.getLogger(__name__)

FEATURES = ["curve", "junction", "visibility", "lane_width", "traffic_density"]
SCORE_SCALE = 8.0   # predicted accidents that map to a risk score of 100

_model = None
_model_lock = threading.Lock()


def get_model():
    global _model
    with _model_lock:
        if _model is None:
            _model = joblib.load(config.AUTHORITY_MODEL)
        return _model


def reload_model():
    """Drop the cached model so the next prediction reads model.pkl again."""
    global _model
    with _model_lock:
        _model = None


def score_to_risk(score):
    if score >= 5:
        return "HIGH"
    if score >= 2:
        return "MEDIUM"
    return "LOW"


def _explain(curve, junction, visibility, lane_width, traffic_density):
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
    return ", ".join(reasons) if reasons else "Normal road conditions"


def _score(features):
    """features: dict or 5-tuple in FEATURES order -> (risk_label, risk_score, raw)."""
    values = [int(features[name]) for name in FEATURES] if isinstance(features, dict) \
        else [int(v) for v in features]
    raw = float(get_model().predict(pd.DataFrame([values], columns=FEATURES))[0])
    risk_score = max(0, min(100, int(round(raw / SCORE_SCALE * 100))))
    return score_to_risk(raw), risk_score, raw


def predict_single_road(curve, junction, visibility, lane_width, traffic_density):
    """Score one segment (used by Add Road). Returns (risk, risk_score, explanation)."""
    features = (curve, junction, visibility, lane_width, traffic_density)
    risk, risk_score, _ = _score(features)
    return risk, risk_score, _explain(*[int(v) for v in features])


def run_predictions():
    """Re-score every segment in accident_data (used by the Run AI button)."""
    with authority_db() as conn:
        rows = conn.execute(f"""
            SELECT segment, {", ".join(FEATURES)}, accident_count
            FROM accident_data
        """).fetchall()

        results = []
        for row in rows:
            features = [int(row[name] if row[name] is not None else 1) for name in FEATURES]
            risk, risk_score, _ = _score(features)
            results.append((row["segment"], risk, _explain(*features), risk_score,
                            *features, row["accident_count"]))

        with conn:
            conn.execute("DELETE FROM predictions")
            conn.executemany(f"""
                INSERT INTO predictions
                (segment, predicted_risk, explanation, confidence,
                 {", ".join(FEATURES)}, accident_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, results)

    log.info("risk predictions generated for %d segment(s)", len(results))
    return len(results)


if __name__ == "__main__":
    config.setup_logging()
    run_predictions()

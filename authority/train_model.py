"""
Train the accident-risk model.

    python -m authority.train_model

Previously this trained on `road_features LEFT JOIN accident_data`, which yielded
only 4 samples (with visibility/lane values outside the 0-2 range the app uses),
producing a depth-1 tree that could output just two values. It now trains on
accident_data -- every segment that has a recorded accident count -- using the
same 0/1/2 feature encoding that prediction and the Add Road form use.

The dataset is still tiny (one row per road segment), so treat the model as a
demonstration of the workflow, not a validated risk estimator.
"""

import logging

import joblib
import pandas as pd
from sklearn.tree import DecisionTreeRegressor

import config
from authority import predict
from backend.db import authority_db, init_authority_db

log = logging.getLogger(__name__)

FEATURES = predict.FEATURES
TARGET = "accident_count"


def load_training_data():
    with authority_db() as conn:
        df = pd.read_sql(
            f"SELECT segment, {', '.join(FEATURES)}, {TARGET} FROM accident_data", conn)
    if df.empty:
        raise ValueError("No training data: accident_data is empty")
    df["lane_width"] = df["lane_width"].fillna(1)
    df["traffic_density"] = df["traffic_density"].fillna(1)
    df[TARGET] = df[TARGET].fillna(0)
    df[FEATURES] = df[FEATURES].astype(int)
    return df


def _labels(model, df):
    raw = model.predict(df[FEATURES])
    return [predict.score_to_risk(score) for score in raw], raw


def train(save=True):
    df = load_training_data()

    before = None
    if config.AUTHORITY_MODEL.exists():
        try:
            before = _labels(joblib.load(config.AUTHORITY_MODEL), df)
        except Exception as exc:
            log.warning("could not score with the previous model: %s", exc)

    model = DecisionTreeRegressor(max_depth=5, min_samples_leaf=2, random_state=42)
    model.fit(df[FEATURES], df[TARGET])
    after = _labels(model, df)

    print(f"Trained on {len(df)} segments "
          f"(tree depth {model.get_depth()}, {model.get_n_leaves()} leaves)\n")
    print(f"{'segment':22} {'accidents':>9}  {'before':>14}  {'after':>14}")
    for i, row in df.iterrows():
        old = f"{before[0][i]} ({before[1][i]:.1f})" if before else "-"
        new = f"{after[0][i]} ({after[1][i]:.1f})"
        print(f"{row['segment']:22} {int(row[TARGET]):>9}  {old:>14}  {new:>14}")

    if save:
        joblib.dump(model, config.AUTHORITY_MODEL)
        predict.reload_model()
        print(f"\nModel saved to {config.AUTHORITY_MODEL}")
    return model


if __name__ == "__main__":
    config.setup_logging()
    init_authority_db()          # make sure the schema/migrations are applied
    train()
    predict.run_predictions()
    print("Predictions refreshed.")

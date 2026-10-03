"""
Accident-risk model. It used to be trained on a 4-row join (visibility on a 1-3
scale) and then fed 0.25-0.85 visibility floats at prediction time.
"""

import pytest

from authority import predict, train_model


def test_explanations():
    assert predict._explain(1, 1, 0, 0, 2) == \
        "Curved road, Junction present, Low visibility, Narrow lane, High traffic density"
    assert predict._explain(0, 0, 2, 2, 0) == "Normal road conditions"


@pytest.mark.parametrize("score,label", [(0, "LOW"), (1.9, "LOW"), (2, "MEDIUM"),
                                         (4.9, "MEDIUM"), (5, "HIGH"), (9, "HIGH")])
def test_score_to_risk(score, label):
    assert predict.score_to_risk(score) == label


def test_risk_score_is_bounded():
    risk, score, explanation = predict.predict_single_road(1, 1, 0, 0, 2)
    assert risk in ("LOW", "MEDIUM", "HIGH") and 0 <= score <= 100


def test_shipped_model_uses_the_app_feature_encoding():
    assert list(predict.get_model().feature_names_in_) == predict.FEATURES


def test_training_uses_every_labelled_segment(data_env):
    model = train_model.train(save=False)
    assert model.tree_.n_node_samples[0] == 10        # was 4
    assert model.get_n_leaves() > 2                    # was a single split


def test_run_predictions_fills_feature_columns(data_env):
    assert predict.run_predictions() == 10
    from backend.db import authority_db
    with authority_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM predictions WHERE curve IS NULL").fetchone()[0] == 0

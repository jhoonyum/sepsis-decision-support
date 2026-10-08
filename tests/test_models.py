import numpy as np
import pandas as pd
import pytest

from sepsis_decision_support.models.baselines import RecalibratedScore, news2_score
from sepsis_decision_support.models.landmark_model import Preprocessor, fit_landmark_model


@pytest.fixture(scope="module")
def simulated():
    random = np.random.default_rng(3)
    rows = 3000
    features = pd.DataFrame(
        {
            "lactate_last": random.gamma(2.0, 1.0, rows),
            "mean_arterial_pressure_last": random.normal(75, 10, rows),
            "constant": 1.0,
        }
    )
    features.loc[random.random(rows) < 0.3, "lactate_last"] = np.nan
    log_odds = (
        -2.0
        + 0.6 * features["lactate_last"].fillna(2.0)
        - 0.05 * (features["mean_arterial_pressure_last"] - 75)
    )
    outcome = pd.Series((random.random(rows) < 1 / (1 + np.exp(-log_odds))).astype(int))
    groups = pd.Series(np.repeat(np.arange(rows // 3), 3))
    return features, outcome, groups


def test_preprocessor_adds_missing_indicators_and_drops_constants(simulated):
    features, _, _ = simulated
    preprocessor = Preprocessor.fit(features)
    assert "lactate_last_missing" in preprocessor.output_columns
    assert "constant" not in preprocessor.output_columns
    design = preprocessor.transform(features)
    assert design.notna().all().all()
    assert design.mean().abs().max() < 1e-9


def test_contributions_add_up_to_the_prediction(simulated):
    features, outcome, groups = simulated
    model = fit_landmark_model(
        features, outcome, groups, "pre_shock", "event_within_horizon",
        inverse_regularization_grid=(0.1, 1.0), inner_folds=3, bootstrap_replicates=10, seed=0,
    )  # fmt: skip
    contributions = model.contributions(features)
    rebuilt = model.intercept + contributions.sum(axis=1).to_numpy()
    np.testing.assert_allclose(rebuilt, model.linear_predictor(features))
    assert model.coefficients["lactate_last"] > 0
    assert model.coefficients["mean_arterial_pressure_last"] < 0

    low, high = model.predict_interval(features.head(50), coverage=0.9)
    assert np.all(low <= high)
    assert model.bootstrap_coefficients.shape == (10, len(model.coefficients))


def test_news2_points():
    features = pd.DataFrame(
        {
            "respiratory_rate_last": [16, 25],
            "spo2_last": [97, 91],
            "on_supplemental_oxygen": [0.0, 1.0],
            "on_invasive_ventilation": [0.0, 0.0],
            "systolic_blood_pressure_last": [120, 88],
            "heart_rate_last": [70, 135],
            "gcs_total_last": [15, 13],
            "temperature_last": [37.0, 39.5],
        }
    )
    # Normal patient: 0. Second patient: 3 + 3 + 2 + 3 + 3 + 3 + 2 = 19.
    assert list(news2_score(features)) == [0, 19]


def test_recalibrated_score_is_monotone():
    features = pd.DataFrame({"sofa_24h": [0, 2, 4, 6, 8, 10] * 20})
    outcome = pd.Series([0, 0, 0, 1, 1, 1] * 20)
    score = RecalibratedScore.fit("sofa", features, outcome)
    probabilities = score.predict_probability(features.head(6))
    assert np.all(np.diff(probabilities) > 0)

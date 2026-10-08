import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from sepsis_decision_support.evaluation.alerts import _alert_times, alert_burden
from sepsis_decision_support.evaluation.metrics import (
    auroc,
    bootstrap_indices,
    brier_score,
    calibration_bands,
    calibration_bins,
    calibration_intercept,
    calibration_slope,
    decision_curve,
    integrated_calibration_index,
    summarise_predictions,
)


@pytest.fixture(scope="module")
def calibrated():
    """Outcomes drawn from the predicted probabilities themselves: perfectly calibrated."""
    random = np.random.default_rng(1)
    probability = 1 / (1 + np.exp(-random.normal(-1.5, 1.2, 40_000)))
    outcome = (random.random(len(probability)) < probability).astype(int)
    return outcome, probability


def test_discrimination_and_brier(calibrated):
    outcome, probability = calibrated
    assert auroc(outcome, probability) == pytest.approx(roc_auc_score(outcome, probability))
    assert brier_score(outcome, probability) == pytest.approx(np.mean((probability - outcome) ** 2))


def test_calibration_of_a_calibrated_model(calibrated):
    outcome, probability = calibrated
    assert calibration_slope(outcome, probability) == pytest.approx(1.0, abs=0.05)
    assert calibration_intercept(outcome, probability) == pytest.approx(0.0, abs=0.05)
    assert integrated_calibration_index(outcome, probability) < 0.01


def test_calibration_detects_extreme_and_low_predictions(calibrated):
    outcome, probability = calibrated
    log_odds = np.log(probability / (1 - probability))
    too_extreme = 1 / (1 + np.exp(-2 * log_odds))
    too_low = 1 / (1 + np.exp(-(log_odds - 1)))
    assert calibration_slope(outcome, too_extreme) == pytest.approx(0.5, abs=0.05)
    assert calibration_intercept(outcome, too_low) > 0.5  # positive: model under-predicts


def test_decision_curve_net_benefit():
    outcome = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    perfect = outcome.astype(float)
    rows = decision_curve(outcome, perfect, (0.1, 0.5))
    for row in rows:
        assert row["net_benefit_model"] == pytest.approx(0.2)  # perfect model: prevalence
    weight = 0.1 / 0.9
    assert rows[0]["net_benefit_treat_all"] == pytest.approx(0.2 - 0.8 * weight, abs=1e-5)


def test_summaries_hide_measures_when_events_are_few():
    outcome = np.array([1] * 5 + [0] * 200)
    probability = np.linspace(0.01, 0.5, len(outcome))
    groups = np.arange(len(outcome))
    summary = summarise_predictions(outcome, probability, groups, None, minimum_cell_size=11)
    assert summary["measures"] is None
    assert summary["observed_rate"] == "<11"


def test_patient_bootstrap_moves_all_rows_of_a_patient_together():
    groups = np.array([1, 1, 1, 2, 2, 3])
    for rows in bootstrap_indices(groups, replicates=20, seed=0):
        sampled = pd.Series(groups[rows]).value_counts()
        for patient, count in sampled.items():
            assert count % (groups == patient).sum() == 0


def test_calibration_bins_merge_neighbours_instead_of_hiding_them():
    # Four bins of 50 rows with 12, 3, 20 and 25 events: the second is too small to
    # publish, so it is merged with the third; nothing is hidden.
    probability = np.linspace(0.01, 0.99, 200)
    outcome = np.zeros(200, dtype=int)
    for start, events in zip((0, 50, 100, 150), (12, 3, 20, 25), strict=True):
        outcome[start : start + events] = 1
    bins = calibration_bins(outcome, probability, number_of_bins=4, minimum_cell_size=11)
    assert len(bins) == 3
    assert [bin_["observed"]["numerator"] for bin_ in bins] == [12, 23, 25]
    assert [bin_["observed"]["denominator"] for bin_ in bins] == [50, 100, 50]


def hourly_rows(risks, event_hour=None):
    start = pd.Timestamp("2150-01-01")
    times = [start + pd.Timedelta(hours=hour) for hour in range(len(risks))]
    event = start + pd.Timedelta(hours=event_hour) if event_hour is not None else pd.NaT
    return pd.DataFrame(
        {"stay_id": 1, "landmark_time": times, "risk": risks, "next_stage_time": event}
    )


def test_alerts_fire_on_rising_edges_and_repeat_each_horizon():
    hourly = hourly_rows([0.1] + [0.6] * 30 + [0.1, 0.6])
    fires = _alert_times(hourly, hourly["risk"] >= 0.5, pd.Timedelta(hours=24))
    assert list(np.flatnonzero(fires)) == [1, 25, 32]


def test_alert_burden_counts_true_alerts_and_detected_events():
    hourly = hourly_rows([0.1, 0.2, 0.7, 0.8, 0.9], event_hour=6)
    (row,) = alert_burden(hourly, "risk", (0.5,), horizon_hours=24, minimum_cell_size=1)
    assert row["alerts"] == 1
    assert row["positive_predictive_value"] == "<1"  # one alert, all true: complement is 0
    assert row["sensitivity"] == "<1"
    assert row["lead_time_hours_quartiles"] == [4.0, 4.0, 4.0]


def test_calibration_bands_are_half_open_and_merge_small_bands():
    probability = np.array([0.04, 0.05, 0.05, 0.10] + [0.3] * 40 + [0.6] * 30 + [1.0])
    outcome = np.array([0, 0, 1, 0] + [1] * 15 + [0] * 25 + [1] * 15 + [0] * 15 + [1])
    bands = calibration_bands(outcome, probability, (0.0, 0.05, 0.5, 1.0), minimum_cell_size=11)
    # [0, 5%) holds one row, so it joins [5%, 50%); the top band publishes on its own.
    assert [(band["predicted_low"], band["predicted_high"]) for band in bands] == [
        (0.0, 0.5),
        (0.5, 1.0),
    ]
    assert bands[0]["rows"] == 44
    assert bands[0]["observed"] == {"rate": 0.364, "numerator": 16, "denominator": 44}
    assert bands[1]["observed"]["numerator"] == 16  # 15 at 60% plus the one at exactly 100%

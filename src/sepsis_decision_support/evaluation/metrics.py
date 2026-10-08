"""Performance measures for probability predictions, with patient-level bootstrap intervals.

Measures and what they answer
    AUROC, AUPRC          does the model rank patients who progress above those who do not?
    Brier score           how far are predicted probabilities from what happened (lower is better)?
    calibration intercept is the average predicted risk right? (0 = right)
    calibration slope     are predictions too extreme (< 1) or too timid (> 1)?
    ICI                   average absolute gap between predicted and observed risk
                          (integrated calibration index, Austin & Steyerberg 2019)
    net benefit           is acting on the model better than acting on everyone or no one,
                          at a given risk threshold (decision curve analysis, Vickers 2006)?
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import SplineTransformer

from sepsis_decision_support.privacy.aggregate_guard import (
    count_cell,
    merge_sparse_groups,
    rate_cell,
)

PROBABILITY_FLOOR = 1e-6


def _logit(probability: np.ndarray) -> np.ndarray:
    clipped = np.clip(probability, PROBABILITY_FLOOR, 1 - PROBABILITY_FLOOR)
    return np.log(clipped / (1 - clipped))


def auroc(outcome: np.ndarray, probability: np.ndarray) -> float:
    return float(roc_auc_score(outcome, probability))


def auprc(outcome: np.ndarray, probability: np.ndarray) -> float:
    return float(average_precision_score(outcome, probability))


def brier_score(outcome: np.ndarray, probability: np.ndarray) -> float:
    return float(np.mean((probability - outcome) ** 2))


def calibration_slope(outcome: np.ndarray, probability: np.ndarray) -> float:
    """Slope of a logistic regression of the outcome on the predicted log-odds."""
    model = LogisticRegression(C=1e6, max_iter=1000).fit(
        _logit(probability).reshape(-1, 1), outcome
    )
    return float(model.coef_[0, 0])


def calibration_intercept(outcome: np.ndarray, probability: np.ndarray) -> float:
    """Calibration-in-the-large: intercept a with the predicted log-odds as a fixed offset.

    Solved by Newton's method on one parameter. Positive values mean the model
    under-predicts on average.
    """
    offset = _logit(probability)
    intercept = 0.0
    for _ in range(50):
        fitted = 1.0 / (1.0 + np.exp(-(offset + intercept)))
        gradient = np.sum(outcome - fitted)
        curvature = np.sum(fitted * (1 - fitted))
        step = gradient / max(curvature, 1e-12)
        intercept += step
        if abs(step) < 1e-10:
            break
    return float(intercept)


def integrated_calibration_index(outcome: np.ndarray, probability: np.ndarray) -> float:
    """Mean absolute difference between predicted and smoothed observed risk.

    The observed-risk curve is a logistic regression on a cubic spline of the predicted
    log-odds (a smooth calibration curve), which works without extra packages.
    """
    log_odds = _logit(probability).reshape(-1, 1)
    spline = SplineTransformer(n_knots=5, degree=3, extrapolation="linear")
    basis = spline.fit_transform(log_odds)
    model = LogisticRegression(C=1.0, max_iter=2000).fit(basis, outcome)
    smoothed = model.predict_proba(basis)[:, 1]
    return float(np.mean(np.abs(smoothed - probability)))


METRICS: dict[str, Callable[[np.ndarray, np.ndarray], float]] = {
    "auroc": auroc,
    "auprc": auprc,
    "brier": brier_score,
    "calibration_intercept": calibration_intercept,
    "calibration_slope": calibration_slope,
    "integrated_calibration_index": integrated_calibration_index,
}


def bootstrap_indices(groups: np.ndarray, replicates: int, seed: int) -> list[np.ndarray]:
    """Row indices for patient-level bootstrap samples (all rows of a patient move together)."""
    random = np.random.default_rng(seed)
    unique_groups, inverse = np.unique(groups, return_inverse=True)
    rows_by_group = [np.flatnonzero(inverse == index) for index in range(len(unique_groups))]
    samples = []
    for _ in range(replicates):
        chosen = random.integers(0, len(unique_groups), size=len(unique_groups))
        samples.append(np.concatenate([rows_by_group[index] for index in chosen]))
    return samples


def summarise_predictions(
    outcome: np.ndarray,
    probability: np.ndarray,
    groups: np.ndarray,
    bootstrap_samples: list[np.ndarray] | None,
    minimum_cell_size: int,
) -> dict:
    """All measures, with 95% patient-bootstrap intervals when samples are given.

    Args:
        bootstrap_samples: row indices from ``bootstrap_indices`` built on these same rows,
            or None to report estimates only (used for the many per-hour and subgroup
            tables, where intervals would multiply the run time).

    Returns a dictionary safe for the aggregate guard. Measures are omitted (set to
    None) when there are too few events or non-events to estimate them.
    """
    outcome = np.asarray(outcome, dtype=int)
    probability = np.asarray(probability, dtype=float)
    events = int(outcome.sum())
    summary: dict = {
        "rows": count_cell(len(outcome), minimum_cell_size),
        "patients": count_cell(len(np.unique(groups)), minimum_cell_size),
        "observed_rate": rate_cell(events, len(outcome), minimum_cell_size),
        "mean_predicted": round(float(probability.mean()), 4) if len(probability) else None,
    }
    if min(events, len(outcome) - events) < minimum_cell_size:
        summary["measures"] = None
        return summary

    measures = {}
    for name, function in METRICS.items():
        estimate = function(outcome, probability)
        replicates = []
        for rows in bootstrap_samples or []:
            sampled_outcome = outcome[rows]
            if 0 < sampled_outcome.sum() < len(sampled_outcome):
                replicates.append(function(sampled_outcome, probability[rows]))
        interval = (
            [
                round(float(np.quantile(replicates, 0.025)), 4),
                round(float(np.quantile(replicates, 0.975)), 4),
            ]
            if len(replicates) >= 20
            else None
        )
        measures[name] = {"estimate": round(estimate, 4), "interval_95": interval}
    summary["measures"] = measures
    return summary


def calibration_bins(
    outcome: np.ndarray, probability: np.ndarray, number_of_bins: int, minimum_cell_size: int
) -> list[dict]:
    """Observed versus predicted risk in bins of equal size along the predicted risk.

    Adjacent bins with fewer than ``minimum_cell_size`` events or non-events are merged
    (see ``merge_sparse_groups``), so the table may have fewer bins than requested.
    """
    outcome = np.asarray(outcome, dtype=int)
    probability = np.asarray(probability, dtype=float)
    order = np.argsort(probability, kind="stable")
    chunks = [chunk for chunk in np.array_split(order, number_of_bins) if len(chunk)]
    groups = merge_sparse_groups(
        [len(chunk) for chunk in chunks],
        [int(outcome[chunk].sum()) for chunk in chunks],
        minimum_cell_size,
    )
    bins = []
    for group in groups:
        rows = np.concatenate([chunks[index] for index in group])
        bins.append(
            {
                "predicted_low": round(float(probability[rows].min()), 4),
                "predicted_high": round(float(probability[rows].max()), 4),
                "predicted_mean": round(float(probability[rows].mean()), 4),
                "observed": rate_cell(int(outcome[rows].sum()), len(rows), minimum_cell_size),
            }
        )
    return bins


def calibration_bands(
    outcome: np.ndarray,
    probability: np.ndarray,
    edges: tuple[float, ...],
    minimum_cell_size: int,
) -> list[dict]:
    """Observed versus predicted risk in fixed bands of predicted risk, e.g. 30-40%.

    Fixed bands answer the bedside question "of patients given about this risk, how many
    progressed?" more directly than equal-count bins, whose top bin can span 35-97%.
    Bands are half-open, [low, high), except the last, which includes its upper edge.
    Adjacent bands too small to publish are merged (for example 50-70% and 70-100% become
    50-100%), so every band shown has an observed rate.
    """
    outcome = np.asarray(outcome, dtype=int)
    probability = np.asarray(probability, dtype=float)
    pairs = list(zip(edges[:-1], edges[1:], strict=True))
    members = []
    for position, (low, high) in enumerate(pairs):
        is_last = position == len(pairs) - 1
        members.append(
            (probability >= low) & ((probability <= high) if is_last else (probability < high))
        )
    groups = merge_sparse_groups(
        [int(inside.sum()) for inside in members],
        [int(outcome[inside].sum()) for inside in members],
        minimum_cell_size,
    )
    bands = []
    for group in groups:
        inside = np.logical_or.reduce([members[index] for index in group])
        rows = int(inside.sum())
        bands.append(
            {
                "predicted_low": pairs[group[0]][0],
                "predicted_high": pairs[group[-1]][1],
                "predicted_mean": round(float(probability[inside].mean()), 4)
                if rows >= minimum_cell_size
                else None,
                "rows": count_cell(rows, minimum_cell_size),
                "observed": rate_cell(int(outcome[inside].sum()), rows, minimum_cell_size),
            }
        )
    return bands


def decision_curve(
    outcome: np.ndarray, probability: np.ndarray, thresholds: tuple[float, ...]
) -> list[dict]:
    """Net benefit of acting on the model, on everyone, and on no one (always 0).

    Net benefit at threshold t = TP/n - FP/n * t/(1-t): true positives credited, false
    positives charged at the odds the threshold implies.
    """
    outcome = np.asarray(outcome, dtype=int)
    n = len(outcome)
    prevalence = outcome.mean()
    rows = []
    for threshold in thresholds:
        acted = probability >= threshold
        true_positive = np.sum(acted & (outcome == 1)) / n
        false_positive = np.sum(acted & (outcome == 0)) / n
        weight = threshold / (1 - threshold)
        rows.append(
            {
                "threshold": threshold,
                "net_benefit_model": round(float(true_positive - false_positive * weight), 5),
                "net_benefit_treat_all": round(float(prevalence - (1 - prevalence) * weight), 5),
            }
        )
    return rows

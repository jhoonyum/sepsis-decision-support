"""Predictors available at each landmark time, built only from data recorded up to then.

For every measured variable v the model sees four numbers:
    v_last                 most recent value at or before the landmark
    v_hours_since_last     how old that value is (large = stale)
    v_change               most recent value minus the most recent value taken at least
                           6 h earlier but within the last 24 h; missing if there is none
    v_count                number of measurements in the last 24 h

Why include counts and staleness?
    Clinicians measure sicker patients more often. How often something is measured is
    itself information, and a model that ignores it would be told only half the story.

Plus context: SOFA over the last 24 h, current treatments, age, sex, hours since ICU
admission and since time zero. Race is deliberately not a predictor; it is used only to
check performance by subgroup.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from sepsis_decision_support.config import FeatureSettings
from sepsis_decision_support.data.canonical_tables import CanonicalTables


def _as_of(
    queries: pd.DataFrame, query_time_column: str, events: pd.DataFrame, event_time_column: str
) -> pd.DataFrame:
    """Most recent event at or before each query time, within the same stay.

    ``queries`` must have a unique index; the result is aligned to that index.
    """
    left = queries[["stay_id", query_time_column]].reset_index().sort_values(query_time_column)
    right = events.sort_values(event_time_column)
    matched = pd.merge_asof(
        left,
        right,
        left_on=query_time_column,
        right_on=event_time_column,
        by="stay_id",
        direction="backward",
    )
    return matched.set_index("index").reindex(queries.index)


def _measurement_features(
    landmarks: pd.DataFrame, measurements: pd.DataFrame, variable: str, settings: FeatureSettings
) -> pd.DataFrame:
    series = measurements.loc[
        measurements["variable"] == variable, ["stay_id", "charttime", "value"]
    ]
    series = series.sort_values(["stay_id", "charttime"])
    # Running count within each stay lets a window count be computed as a difference.
    series = series.assign(running_count=series.groupby("stay_id").cumcount() + 1)

    queries = landmarks[["stay_id", "landmark_time"]].copy()
    queries["change_reference_time"] = queries["landmark_time"] - pd.Timedelta(
        hours=settings.change_window_hours
    )
    queries["count_window_start"] = queries["landmark_time"] - pd.Timedelta(
        hours=settings.count_window_hours
    )

    latest = _as_of(queries, "landmark_time", series, "charttime")
    earlier = _as_of(queries, "change_reference_time", series, "charttime")
    window_start = _as_of(queries, "count_window_start", series, "charttime")

    hours_since_last = (queries["landmark_time"] - latest["charttime"]).dt.total_seconds() / 3600
    # A reference value older than the count window says little about the recent trend.
    reference_is_recent = earlier["charttime"] >= queries["count_window_start"]
    change = latest["value"] - earlier["value"].where(reference_is_recent)
    count = latest["running_count"].fillna(0) - window_start["running_count"].fillna(0)
    return pd.DataFrame(
        {
            f"{variable}_last": latest["value"],
            f"{variable}_hours_since_last": hours_since_last,
            f"{variable}_change": change,
            f"{variable}_count": count,
        },
        index=landmarks.index,
    )


def _treatment_features(landmarks: pd.DataFrame, treatments: pd.DataFrame) -> pd.DataFrame:
    features = pd.DataFrame(index=landmarks.index)
    pairs = landmarks[["stay_id", "landmark_time"]].reset_index().merge(treatments, on="stay_id")
    running = pairs.loc[
        (pairs["start_time"] <= pairs["landmark_time"])
        & (pairs["landmark_time"] < pairs["end_time"])
    ]
    for treatment in ("vasopressor", "antibiotic", "supplemental_oxygen", "invasive_ventilation"):
        indices = running.loc[running["treatment"] == treatment, "index"].unique()
        features[f"on_{treatment}"] = landmarks.index.isin(indices).astype("float64")

    vasopressor_rates = (
        running.loc[running["treatment"] == "vasopressor"].groupby("index")["rate"].max()
    )
    features["vasopressor_rate"] = vasopressor_rates.reindex(landmarks.index).fillna(0.0)

    started = pairs.loc[
        (pairs["treatment"] == "antibiotic") & (pairs["start_time"] <= pairs["landmark_time"])
    ]
    first_start = started.groupby("index")["start_time"].min().reindex(landmarks.index)
    features["hours_since_first_antibiotic"] = (
        landmarks["landmark_time"] - first_start
    ).dt.total_seconds() / 3600
    return features


def build_landmark_features(
    landmarks: pd.DataFrame, tables: CanonicalTables, settings: FeatureSettings
) -> pd.DataFrame:
    """Predictor matrix aligned with ``landmarks`` (same index, one row per landmark).

    Missing values are left missing here; the model's preprocessing handles them so that
    imputation is fitted on training folds only.
    """
    if not landmarks.index.is_unique:
        raise ValueError("landmarks must have a unique index")
    blocks = [
        _measurement_features(landmarks, tables.measurements, variable, settings)
        for variable in settings.variables
    ]
    blocks.append(_treatment_features(landmarks, tables.treatments))

    sofa = _as_of(
        landmarks[["stay_id", "landmark_time"]],
        "landmark_time",
        tables.sofa_hourly,
        "hour_end_time",
    )
    context = pd.DataFrame(
        {
            "sofa_24h": sofa["sofa_24h"],
            "age_years": landmarks["age_years"],
            "male": (landmarks["sex"] == "M").astype("float64"),
            "hours_since_time_zero": landmarks["landmark_hour"],
            "hours_since_icu_admission": (
                landmarks["landmark_time"] - landmarks["icu_intime"]
            ).dt.total_seconds()
            / 3600,
        },
        index=landmarks.index,
    )
    blocks.append(context)
    features = pd.concat(blocks, axis=1)
    return features.astype("float64").replace([np.inf, -np.inf], np.nan)

"""Shock defined from physiology alone, so that it does not depend on treatment.

Why not the usual Sepsis-3 shock definition?
    Sepsis-3 shock requires a vasopressor. A model that predicts "shock" would then be
    partly predicting the clinician's decision to start a vasopressor, and a screen that
    offers "start a vasopressor now" would be offering the outcome itself. Here shock is:

        sustained hypotension   two or more mean arterial pressure readings below the
                                threshold, at least ``minimum_duration_minutes`` apart,
                                with no normal reading and no long gap in between
        and lactate > 2         a lactate above the threshold within a window around the
                                moment the hypotension became sustained

    The Sepsis-3 operational definition is kept as a sensitivity analysis.
"""

from __future__ import annotations

import pandas as pd

from sepsis_decision_support.config import OutcomeSettings, SustainedHypotensionSettings


def sustained_hypotension_times(
    measurements: pd.DataFrame, settings: SustainedHypotensionSettings
) -> pd.DataFrame:
    """Moments at which a run of low blood pressure readings first became sustained.

    Args:
        measurements: canonical measurements table.
        settings: thresholds for "low", "sustained" and the largest allowed gap.

    Returns:
        DataFrame with columns ``stay_id`` and ``hypotension_time``, one row per episode.
    """
    low_limit, high_limit = settings.plausible_map_range_mmhg
    blood_pressure = measurements.loc[
        (measurements["variable"] == "mean_arterial_pressure")
        & measurements["value"].between(low_limit, high_limit),
        ["stay_id", "charttime", "value"],
    ].sort_values(["stay_id", "charttime"])

    is_low = blood_pressure["value"] < settings.map_threshold_mmhg
    gap = blood_pressure.groupby("stay_id")["charttime"].diff()
    too_long_gap = gap > pd.Timedelta(minutes=settings.maximum_gap_between_readings_minutes)
    new_stay = blood_pressure["stay_id"] != blood_pressure["stay_id"].shift()
    # A new run starts whenever the low/normal status changes, the stay changes, or the
    # gap since the previous reading is too long to assume the pressure stayed low.
    run_number = (new_stay | (is_low != is_low.shift()) | too_long_gap).cumsum()

    low_readings = blood_pressure.loc[is_low].assign(run_number=run_number[is_low])
    run_start = low_readings.groupby("run_number")["charttime"].transform("min")
    long_enough = (low_readings["charttime"] - run_start) >= pd.Timedelta(
        minutes=settings.minimum_duration_minutes
    )
    episodes = (
        low_readings.loc[long_enough]
        .groupby("run_number", as_index=False)
        .first()[["stay_id", "charttime"]]
        .rename(columns={"charttime": "hypotension_time"})
    )
    return episodes.sort_values(["stay_id", "hypotension_time"]).reset_index(drop=True)


def shock_event_times(measurements: pd.DataFrame, settings: OutcomeSettings) -> pd.DataFrame:
    """Times at which the treatment-independent shock definition was met.

    The event time is the moment both parts are on record: the hypotension time if a
    high lactate was already measured in the window before it, otherwise the time of
    the first high lactate in the window after it.

    Returns:
        DataFrame with columns ``stay_id`` and ``shock_time``, sorted.
    """
    hypotension = sustained_hypotension_times(measurements, settings.sustained_hypotension)
    lactate_settings = settings.lactate
    high_lactate = (
        measurements.loc[
            (measurements["variable"] == "lactate")
            & (measurements["value"] > lactate_settings.threshold_mmol_per_l),
            ["stay_id", "charttime"],
        ]
        .rename(columns={"charttime": "lactate_time"})
        .sort_values("lactate_time")
    )
    if hypotension.empty or high_lactate.empty:
        return pd.DataFrame(
            {"stay_id": pd.Series(dtype="int64"), "shock_time": pd.Series(dtype="datetime64[ns]")}
        )

    hypotension = hypotension.sort_values("hypotension_time")
    lactate_before = pd.merge_asof(
        hypotension,
        high_lactate,
        left_on="hypotension_time",
        right_on="lactate_time",
        by="stay_id",
        direction="backward",
        tolerance=pd.Timedelta(hours=lactate_settings.window_hours_before),
    )
    lactate_after = pd.merge_asof(
        hypotension,
        high_lactate,
        left_on="hypotension_time",
        right_on="lactate_time",
        by="stay_id",
        direction="forward",
        tolerance=pd.Timedelta(hours=lactate_settings.window_hours_after),
    )
    shock_time = lactate_before["hypotension_time"].where(
        lactate_before["lactate_time"].notna(), lactate_after["lactate_time"]
    )
    events = pd.DataFrame(
        {"stay_id": hypotension["stay_id"].to_numpy(), "shock_time": shock_time.to_numpy()}
    )
    events = events.dropna(subset=["shock_time"]).drop_duplicates()
    events["shock_time"] = events["shock_time"].astype("datetime64[ns]")
    return events.sort_values(["stay_id", "shock_time"]).reset_index(drop=True)

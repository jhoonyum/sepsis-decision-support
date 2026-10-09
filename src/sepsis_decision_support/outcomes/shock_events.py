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

    The Sepsis-3 operational definition is kept as a sensitivity analysis
    (``outcomes.shock_definition: sepsis3_operational``): a vasopressor episode with a
    lactate above the threshold in the same window around the episode's start.
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


# Vasopressor intervals separated by no more than this belong to one episode: mimic-code
# splits an infusion into a new interval at every rate change.
VASOPRESSOR_EPISODE_GAP_MINUTES = 60


def vasopressor_episode_starts(treatments: pd.DataFrame) -> pd.DataFrame:
    """Start of each vasopressor episode.

    An episode is a run of vasopressor intervals in which each interval begins no more than
    ``VASOPRESSOR_EPISODE_GAP_MINUTES`` after the previous one ended.

    Returns:
        DataFrame with columns ``stay_id`` and ``episode_start``.
    """
    infusions = treatments.loc[
        treatments["treatment"] == "vasopressor", ["stay_id", "start_time", "end_time"]
    ].sort_values(["stay_id", "start_time"])
    latest_end_so_far = infusions.groupby("stay_id")["end_time"].cummax()
    previous_end = latest_end_so_far.groupby(infusions["stay_id"]).shift()
    gap = infusions["start_time"] - previous_end
    starts_episode = previous_end.isna() | (
        gap > pd.Timedelta(minutes=VASOPRESSOR_EPISODE_GAP_MINUTES)
    )
    return (
        infusions.loc[starts_episode, ["stay_id", "start_time"]]
        .rename(columns={"start_time": "episode_start"})
        .reset_index(drop=True)
    )


def _with_high_lactate(
    anchors: pd.DataFrame, anchor_column: str, measurements: pd.DataFrame, lactate_settings
) -> pd.DataFrame:
    """Event times for anchor moments (hypotension or vasopressor start) with high lactate.

    The event time is the moment both parts are on record: the anchor time if a high
    lactate was already measured in the window before it, otherwise the time of the
    first high lactate in the window after it.
    """
    empty = pd.DataFrame(
        {"stay_id": pd.Series(dtype="int64"), "shock_time": pd.Series(dtype="datetime64[ns]")}
    )
    high_lactate = (
        measurements.loc[
            (measurements["variable"] == "lactate")
            & (measurements["value"] > lactate_settings.threshold_mmol_per_l),
            ["stay_id", "charttime"],
        ]
        .rename(columns={"charttime": "lactate_time"})
        .sort_values("lactate_time")
    )
    if anchors.empty or high_lactate.empty:
        return empty

    anchors = anchors[["stay_id", anchor_column]].sort_values(anchor_column)
    lactate_before = pd.merge_asof(
        anchors,
        high_lactate,
        left_on=anchor_column,
        right_on="lactate_time",
        by="stay_id",
        direction="backward",
        tolerance=pd.Timedelta(hours=lactate_settings.window_hours_before),
    )
    lactate_after = pd.merge_asof(
        anchors,
        high_lactate,
        left_on=anchor_column,
        right_on="lactate_time",
        by="stay_id",
        direction="forward",
        tolerance=pd.Timedelta(hours=lactate_settings.window_hours_after),
    )
    shock_time = lactate_before[anchor_column].where(
        lactate_before["lactate_time"].notna(), lactate_after["lactate_time"]
    )
    events = pd.DataFrame(
        {"stay_id": anchors["stay_id"].to_numpy(), "shock_time": shock_time.to_numpy()}
    )
    events = events.dropna(subset=["shock_time"]).drop_duplicates()
    events["shock_time"] = events["shock_time"].astype("datetime64[ns]")
    return events.sort_values(["stay_id", "shock_time"]).reset_index(drop=True)


def shock_event_times(
    measurements: pd.DataFrame,
    settings: OutcomeSettings,
    treatments: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Times at which the configured shock definition was met.

    ``physiological`` (main analysis): sustained hypotension with a high lactate in the
    window around it. ``sepsis3_operational`` (sensitivity analysis): a vasopressor episode
    start with a high lactate in the same window; needs ``treatments``.

    Returns:
        DataFrame with columns ``stay_id`` and ``shock_time``, sorted.
    """
    if settings.shock_definition == "physiological":
        anchors = sustained_hypotension_times(measurements, settings.sustained_hypotension)
        return _with_high_lactate(anchors, "hypotension_time", measurements, settings.lactate)
    if treatments is None:
        raise ValueError("the sepsis3_operational shock definition needs the treatments table")
    anchors = vasopressor_episode_starts(treatments)
    return _with_high_lactate(anchors, "episode_start", measurements, settings.lactate)

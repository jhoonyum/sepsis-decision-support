"""Outcome labels for each landmark.

Two populations, because "the next stage" means different things:
    pre_shock     not on a vasopressor and no shock event so far. Next stage = shock
                  (treatment-independent definition) or death.
    shock_stage   on a vasopressor, or already had a shock event. Next stage = death.

Labels per landmark time L (H = horizon, 24 hours by default):
    event_within_horizon       next stage in (L, L + H]                     -> X on the screen
    event_within_next_hour     next stage in (L, L + 1 h]                  -> p1
    event_horizon_after_hour   next stage in (L + 1 h, L + 1 h + H], only for landmarks
                               with no event in the first hour (missing otherwise) -> Z
    death_within_28_days       death in (L, L + 28 days], any location

Deaths count wherever they happen. Shock can only be observed in the ICU; a patient
who leaves the ICU alive is counted as "no shock" for the rest of the window. This is
stated in the model card as a known limitation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from sepsis_decision_support.config import OutcomeSettings

PRE_SHOCK = "pre_shock"
SHOCK_STAGE = "shock_stage"


def vasopressor_running_at(landmarks: pd.DataFrame, treatments: pd.DataFrame) -> pd.Series:
    """True where a vasopressor infusion covers the landmark time."""
    infusions = treatments.loc[
        treatments["treatment"] == "vasopressor", ["stay_id", "start_time", "end_time"]
    ]
    paired = landmarks[["stay_id", "landmark_time"]].reset_index().merge(infusions, on="stay_id")
    covering = paired.loc[
        (paired["start_time"] <= paired["landmark_time"])
        & (paired["landmark_time"] < paired["end_time"]),
        "index",
    ]
    return pd.Series(landmarks.index.isin(covering.unique()), index=landmarks.index)


def died_within(landmarks: pd.DataFrame, start: pd.Series, end: pd.Series) -> pd.Series:
    """True where the patient died in (start, end].

    In-hospital deaths have an exact time. Deaths after hospital discharge only have a
    date, so they count when the date falls between the dates of ``start`` and ``end``.
    """
    exact = (
        landmarks["death_time"].notna()
        & (landmarks["death_time"] > start)
        & (landmarks["death_time"] <= end)
    )
    date_only = (
        landmarks["death_time"].isna()
        & landmarks["date_of_death"].notna()
        & (landmarks["date_of_death"] >= start.dt.normalize())
        & (landmarks["date_of_death"] <= end.dt.normalize())
    )
    return exact | date_only


def _next_and_previous_shock(landmarks: pd.DataFrame, shock_events: pd.DataFrame) -> pd.DataFrame:
    """For each landmark: time of the first shock event after it, and whether one came before."""
    ordered = landmarks[["stay_id", "landmark_time"]].reset_index().sort_values("landmark_time")
    events = shock_events.sort_values("shock_time")
    following = pd.merge_asof(
        ordered,
        events,
        left_on="landmark_time",
        right_on="shock_time",
        by="stay_id",
        direction="forward",
        allow_exact_matches=False,
    )
    preceding = pd.merge_asof(
        ordered,
        events,
        left_on="landmark_time",
        right_on="shock_time",
        by="stay_id",
        direction="backward",
        allow_exact_matches=True,
    )
    result = pd.DataFrame(
        {
            "next_shock_time": following["shock_time"].to_numpy(),
            "had_shock_before": preceding["shock_time"].notna().to_numpy(),
        },
        index=ordered["index"].to_numpy(),
    )
    return result.reindex(landmarks.index)


def label_landmarks(
    landmarks: pd.DataFrame,
    shock_events: pd.DataFrame,
    treatments: pd.DataFrame,
    settings: OutcomeSettings,
) -> pd.DataFrame:
    """Add population group, event times and the four labels to a landmark table."""
    labelled = landmarks.copy()
    labelled["on_vasopressor"] = vasopressor_running_at(labelled, treatments)
    shock = _next_and_previous_shock(labelled, shock_events)
    labelled["next_shock_time"] = shock["next_shock_time"].astype("datetime64[ns]")
    labelled["had_shock_before"] = shock["had_shock_before"].astype(bool)
    labelled["population"] = np.where(
        labelled["on_vasopressor"] | labelled["had_shock_before"], SHOCK_STAGE, PRE_SHOCK
    )

    landmark_time = labelled["landmark_time"]
    one_hour = pd.Timedelta(hours=1)
    horizon = pd.Timedelta(hours=settings.horizon_hours)
    is_pre_shock = labelled["population"] == PRE_SHOCK

    def event_between(start: pd.Series, end: pd.Series) -> pd.Series:
        """Next stage in (start, end]: shock (pre-shock patients only, while in ICU) or death."""
        shock_in_window = (
            is_pre_shock
            & labelled["next_shock_time"].notna()
            & (labelled["next_shock_time"] > start)
            & (labelled["next_shock_time"] <= end)
            & (labelled["next_shock_time"] <= labelled["icu_outtime"])
        )
        return shock_in_window | died_within(labelled, start, end)

    labelled["event_within_horizon"] = event_between(landmark_time, landmark_time + horizon)
    labelled["event_within_next_hour"] = event_between(landmark_time, landmark_time + one_hour)
    after_hour = event_between(landmark_time + one_hour, landmark_time + one_hour + horizon)
    labelled["event_horizon_after_hour"] = after_hour.astype("float64").where(
        ~labelled["event_within_next_hour"]
    )
    labelled["death_within_28_days"] = died_within(
        labelled, landmark_time, landmark_time + pd.Timedelta(days=settings.mortality_days)
    )

    # Time of the next stage, used for lead-time calculations.
    death_time_or_date = labelled["death_time"].fillna(labelled["date_of_death"])
    shock_time_if_relevant = labelled["next_shock_time"].where(
        is_pre_shock & (labelled["next_shock_time"] <= labelled["icu_outtime"])
    )
    labelled["next_stage_time"] = pd.concat(
        [shock_time_if_relevant, death_time_or_date], axis=1
    ).min(axis=1)
    return labelled

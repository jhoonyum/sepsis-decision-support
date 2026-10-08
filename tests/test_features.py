"""Features may only use data recorded up to the landmark time."""

import numpy as np
import pandas as pd

from sepsis_decision_support.cohort.cohort_builder import build_cohort
from sepsis_decision_support.cohort.landmarks import build_landmarks
from sepsis_decision_support.data.canonical_tables import CanonicalTables
from sepsis_decision_support.data.synthetic import generate_canonical_tables
from sepsis_decision_support.features.landmark_features import build_landmark_features

from .conftest import make_tables, recognition_row, stay_row


def features_for(tables, settings, landmark_hours):
    cohort = build_cohort(tables, settings.cohort)
    landmarks = build_landmarks(cohort.stays, landmark_hours)
    features = build_landmark_features(landmarks, tables, settings.features)
    return landmarks, features


def test_last_value_change_count_and_age_of_a_measurement(settings):
    measurements = [
        (1, 1.0, "heart_rate", 80.0),
        (1, 5.0, "heart_rate", 90.0),
        (1, 11.0, "heart_rate", 120.0),
        (1, 12.0, "heart_rate", 130.0),  # exactly at the landmark: included
        (1, 12.5, "heart_rate", 200.0),  # after the landmark: never used
    ]
    tables = make_tables([stay_row(1)], [recognition_row(1, 0.0)], measurements)
    _, features = features_for(tables, settings, [12])
    row = features.iloc[0]
    assert row["heart_rate_last"] == 130.0
    assert row["heart_rate_hours_since_last"] == 0.0
    assert row["heart_rate_change"] == 130.0 - 90.0  # value 6 h earlier was the 5 h reading
    assert row["heart_rate_count"] == 4
    assert np.isnan(row["lactate_last"])  # never measured


def test_change_needs_a_reference_value_from_the_last_day(settings):
    measurements = [(1, -20.0, "lactate", 1.0), (1, 11.0, "lactate", 4.0)]
    tables = make_tables([stay_row(1)], [recognition_row(1, 0.0)], measurements)
    _, features = features_for(tables, settings, [12])
    # The only value 6 h or more before the landmark is 32 h old: no change is reported.
    assert np.isnan(features.iloc[0]["lactate_change"])


def test_treatment_features(settings):
    treatments = [
        (1, "vasopressor", 2.0, 20.0, 0.15),
        (1, "antibiotic", -3.0, 30.0, np.nan),
        (1, "invasive_ventilation", 13.0, 40.0, np.nan),  # starts after the landmark
    ]
    tables = make_tables([stay_row(1)], [recognition_row(1, 0.0)], treatments=treatments)
    _, features = features_for(tables, settings, [12])
    row = features.iloc[0]
    assert row["on_vasopressor"] == 1.0
    assert row["vasopressor_rate"] == 0.15
    assert row["on_invasive_ventilation"] == 0.0
    assert row["hours_since_first_antibiotic"] == 15.0


def truncate_after(tables: CanonicalTables, cutoff_by_stay: pd.Series) -> CanonicalTables:
    """Copy of ``tables`` without anything recorded after each stay's cutoff."""

    def keep(frame: pd.DataFrame, time_column: str) -> pd.DataFrame:
        cutoff = frame["stay_id"].map(cutoff_by_stay)
        return frame.loc[frame[time_column] <= cutoff].reset_index(drop=True)

    treatments = keep(tables.treatments, "start_time")
    # An infusion still running at the cutoff is known to be running, not when it stops.
    cutoff = treatments["stay_id"].map(cutoff_by_stay)
    treatments["end_time"] = treatments["end_time"].where(
        treatments["end_time"] <= cutoff, cutoff + pd.Timedelta(seconds=1)
    )
    return CanonicalTables(
        stays=tables.stays,
        recognition=tables.recognition,
        measurements=keep(tables.measurements, "charttime"),
        treatments=treatments,
        sofa_hourly=keep(tables.sofa_hourly, "hour_end_time"),
    )


def test_features_do_not_change_when_the_future_is_removed(settings):
    """For every landmark, delete everything recorded after it: no feature may change."""
    tables = generate_canonical_tables(80, seed=7)
    cohort = build_cohort(tables, settings.cohort)
    time_zero = cohort.stays.set_index("stay_id")["time_zero"]
    checked = 0
    for landmark_hour in (0, 6, 12, 24):
        landmarks = build_landmarks(cohort.stays, [landmark_hour])
        full = build_landmark_features(landmarks, tables, settings.features)
        cutoff = time_zero + pd.Timedelta(hours=landmark_hour)
        truncated = build_landmark_features(
            landmarks, truncate_after(tables, cutoff), settings.features
        )
        pd.testing.assert_frame_equal(full, truncated, obj=f"landmark {landmark_hour} h")
        checked += len(landmarks)
    assert checked > 100

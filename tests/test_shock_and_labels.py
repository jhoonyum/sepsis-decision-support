import numpy as np
import pandas as pd

from sepsis_decision_support.cohort.cohort_builder import build_cohort
from sepsis_decision_support.cohort.landmarks import build_landmarks
from sepsis_decision_support.outcomes.labels import PRE_SHOCK, SHOCK_STAGE, label_landmarks
from sepsis_decision_support.outcomes.shock_events import (
    shock_event_times,
    sustained_hypotension_times,
)

from .conftest import hours, make_tables, recognition_row, stay_row

MINUTE = 1 / 60


def blood_pressure(stay_id, *readings):
    """(hour, value) pairs of mean arterial pressure for one stay."""
    return [(stay_id, hour, "mean_arterial_pressure", value) for hour, value in readings]


def lactate(stay_id, hour, value):
    return [(stay_id, hour, "lactate", value)]


def shock_tables():
    measurements = (
        # 1: low for 40 minutes, high lactate 2 h later -> shock at the lactate time
        blood_pressure(1, (10, 60), (10 + 40 * MINUTE, 62))
        + lactate(1, 12, 3.0)
        # 2: two low readings only 20 minutes apart -> not sustained
        + blood_pressure(2, (10, 60), (10 + 20 * MINUTE, 60))
        + lactate(2, 10.5, 3.0)
        # 3: a normal reading between the low ones -> not sustained
        + blood_pressure(3, (10, 60), (10 + 15 * MINUTE, 70), (10 + 45 * MINUTE, 60))
        + lactate(3, 10.5, 3.0)
        # 4: 150 minutes between the low readings -> too long a gap
        + blood_pressure(4, (10, 60), (12.5, 60))
        + lactate(4, 11, 3.0)
        # 5: high lactate 1 h before the hypotension became sustained -> shock at the hypotension
        + blood_pressure(5, (10, 60), (10 + 40 * MINUTE, 61))
        + lactate(5, 9, 2.5)
        # 6: sustained hypotension but lactate normal -> no shock
        + blood_pressure(6, (10, 60), (11, 58))
        + lactate(6, 10.5, 1.5)
        # 7: implausible pressures are ignored
        + blood_pressure(7, (10, 10), (11, 12))
        + lactate(7, 10.5, 4.0)
    )
    return make_tables([stay_row(stay) for stay in range(1, 8)], measurements=measurements)


def test_sustained_hypotension(settings):
    episodes = sustained_hypotension_times(
        shock_tables().measurements, settings.outcomes.sustained_hypotension
    )
    found = dict(zip(episodes["stay_id"], episodes["hypotension_time"], strict=True))
    assert set(found) == {1, 5, 6}
    assert found[1] == hours(10 + 40 * MINUTE)
    assert found[6] == hours(11)


def test_shock_needs_high_lactate_near_the_hypotension(settings):
    events = shock_event_times(shock_tables().measurements, settings.outcomes)
    found = dict(zip(events["stay_id"], events["shock_time"], strict=True))
    assert found == {1: hours(12), 5: hours(10 + 40 * MINUTE)}


def labelled(settings, stays, measurements=(), treatments=(), landmark_hours=(0, 6)):
    tables = make_tables(
        stays, [recognition_row(row["stay_id"], 0.0) for row in stays], measurements, treatments
    )
    cohort = build_cohort(tables, settings.cohort)
    landmarks = build_landmarks(cohort.stays, list(landmark_hours))
    shock = shock_event_times(tables.measurements, settings.outcomes)
    rows = label_landmarks(landmarks, shock, tables.treatments, settings.outcomes)
    return rows.set_index(["stay_id", "landmark_hour"])


def test_labels_around_a_shock_event(settings):
    # Shock at 3 h: at landmark 0 the patient is pre-shock and progresses within 24 h,
    # not within the first hour; at landmark 6 the patient is in the shock stage.
    measurements = blood_pressure(1, (2.5, 60), (3.0, 60)) + lactate(1, 2.0, 3.0)
    rows = labelled(settings, [stay_row(1)], measurements)
    first, later = rows.loc[(1, 0.0)], rows.loc[(1, 6.0)]
    assert first["population"] == PRE_SHOCK
    assert first["event_within_horizon"]
    assert not first["event_within_next_hour"]
    assert first["event_horizon_after_hour"] == 1.0
    assert first["next_stage_time"] == hours(3)
    assert later["population"] == SHOCK_STAGE
    assert not later["event_within_horizon"]  # for the shock stage only death counts


def test_a_running_vasopressor_puts_the_patient_in_the_shock_stage(settings):
    rows = labelled(settings, [stay_row(1)], treatments=[(1, "vasopressor", 4, 8, 0.1)])
    assert rows.loc[(1, 0.0), "population"] == PRE_SHOCK
    assert rows.loc[(1, 6.0), "population"] == SHOCK_STAGE
    assert rows.loc[(1, 6.0), "on_vasopressor"]


def test_death_in_the_first_hour_leaves_the_later_window_missing(settings):
    stay = stay_row(1, death_time=hours(0.5), icu_outtime=hours(0.5))
    rows = labelled(settings, [stay], landmark_hours=(0,))
    row = rows.loc[(1, 0.0)]
    assert row["event_within_next_hour"]
    assert np.isnan(row["event_horizon_after_hour"])


def test_death_after_discharge_counts_by_date(settings):
    stay = stay_row(1, date_of_death=pd.Timestamp("2150-01-11"))  # 10 days later, date only
    rows = labelled(settings, [stay], landmark_hours=(0,))
    row = rows.loc[(1, 0.0)]
    assert row["death_within_28_days"]
    assert not row["event_within_horizon"]


def test_shock_after_leaving_the_icu_is_not_an_event(settings):
    stay = stay_row(1, icu_outtime=hours(5))
    measurements = blood_pressure(1, (6, 60), (7, 60)) + lactate(1, 6.5, 3.0)
    rows = labelled(settings, [stay], measurements, landmark_hours=(0,))
    assert not rows.loc[(1, 0.0), "event_within_horizon"]


def test_vasopressor_episodes_join_intervals_split_by_rate_changes():
    from sepsis_decision_support.outcomes.shock_events import vasopressor_episode_starts

    tables = make_tables(
        [stay_row(1)],
        treatments=[
            (1, "vasopressor", 2.0, 3.0, 0.05),
            (1, "vasopressor", 3.0, 5.0, 0.10),  # rate change: same episode
            (1, "vasopressor", 5.5, 6.0, 0.10),  # 30 minutes off: same episode
            (1, "vasopressor", 8.0, 9.0, 0.10),  # 2 hours off: new episode
            (1, "norepinephrine", 20.0, 21.0, 0.10),  # counted through "vasopressor" only
        ],
    )
    starts = vasopressor_episode_starts(tables.treatments)
    assert list(starts["episode_start"]) == [hours(2.0), hours(8.0)]


def test_operational_shock_is_a_vasopressor_start_with_high_lactate(settings):
    operational = settings.outcomes.model_copy(update={"shock_definition": "sepsis3_operational"})
    measurements = (
        lactate(1, 1.0, 3.0)  # before the start: shock at the start
        + lactate(2, 9.0, 2.5)  # 5 h after the start: shock at the lactate time
        + lactate(3, 20.0, 4.0)  # 16 h after the start: outside the window
        + lactate(4, 4.0, 1.5)  # normal lactate
    )
    treatments = [(stay, "vasopressor", 4.0, 10.0, 0.1) for stay in (1, 2, 3, 4)]
    tables = make_tables(
        [stay_row(stay) for stay in (1, 2, 3, 4)],
        measurements=measurements,
        treatments=treatments,
    )
    events = shock_event_times(tables.measurements, operational, tables.treatments)
    found = dict(zip(events["stay_id"], events["shock_time"], strict=True))
    assert found == {1: hours(4.0), 2: hours(9.0)}
    # The main definition ignores vasopressors altogether.
    assert shock_event_times(tables.measurements, settings.outcomes, tables.treatments).empty

import pandas as pd

from sepsis_decision_support.cohort.cohort_builder import build_cohort
from sepsis_decision_support.cohort.landmarks import build_landmarks

from .conftest import hours, make_tables, recognition_row, stay_row


def cohort_tables():
    stays = [
        stay_row(1),  # included
        stay_row(2, age_years=17.0),  # child
        stay_row(3, is_first_icu_stay=False),  # later ICU stay of a patient
        stay_row(4),  # sepsis never recognised
        stay_row(5),  # recognised too late
        stay_row(6, hospital_service="CSURG"),  # cardiac surgery service
        stay_row(7),  # recognised before ICU admission: included, time zero = admission
    ]
    recognition = [
        recognition_row(1, 2.0),
        recognition_row(2, 2.0),
        recognition_row(3, 2.0),
        recognition_row(5, 30.0),
        recognition_row(6, 2.0),
        recognition_row(7, -3.0),
    ]
    return make_tables(stays, recognition)


def test_cohort_rules_and_flow(settings):
    cohort = build_cohort(cohort_tables(), settings.cohort)
    assert list(cohort.stays["stay_id"]) == [1, 7]
    assert [count for _, count in cohort.flow] == [7, 7, 6, 5, 4, 3, 2]


def test_time_zero_is_recognition_or_admission_whichever_is_later(settings):
    stays = build_cohort(cohort_tables(), settings.cohort).stays.set_index("stay_id")
    assert stays.loc[1, "time_zero"] == hours(2.0)
    assert stays.loc[7, "time_zero"] == hours(0.0)
    assert stays.loc[1, "administrative_end"] == hours(2.0 + 240)


def test_landmarks_only_include_patients_still_in_the_icu_and_alive(settings):
    tables = make_tables(
        [
            stay_row(1, icu_outtime=hours(10)),  # leaves the ICU at 10 h
            stay_row(2, death_time=hours(5), icu_outtime=hours(5)),  # dies at 5 h
            stay_row(3),  # stays 72 h
        ],
        [recognition_row(stay, 0.0) for stay in (1, 2, 3)],
    )
    cohort = build_cohort(tables, settings.cohort)
    landmarks = build_landmarks(cohort.stays, [0, 6, 12])
    hours_by_stay = landmarks.groupby("stay_id")["landmark_hour"].apply(list).to_dict()
    assert hours_by_stay == {1: [0.0, 6.0], 2: [0.0], 3: [0.0, 6.0, 12.0]}


def test_landmarks_stop_at_the_administrative_end(settings):
    tables = make_tables([stay_row(1, icu_outtime=hours(400))], [recognition_row(1, 0.0)])
    stays = build_cohort(tables, settings.cohort).stays
    landmarks = build_landmarks(stays, [0, 239, 240, 241])
    assert list(landmarks["landmark_hour"]) == [0.0, 239.0, 240.0]
    assert (
        landmarks["landmark_time"]
        == stays.loc[0, "time_zero"] + pd.to_timedelta(landmarks["landmark_hour"], unit="h")
    ).all()


def test_flow_names_the_infection_definition(settings):
    pair_settings = settings.cohort.model_copy(update={"definition": "culture_antibiotic_pair"})
    steps = [step for step, _ in build_cohort(cohort_tables(), pair_settings).flow]
    assert "IV antibiotic and culture within 1 h of each other in the ICU" in steps
    steps = [step for step, _ in build_cohort(cohort_tables(), settings.cohort).flow]
    assert "Sepsis-3 recognised during the ICU stay" in steps

"""The SQL files, run against tiny hand-made tables whose answers are known.

The open MIMIC-IV demo (tests marked ``demo``) checks that the SQL runs on real tables;
these tests check the rules themselves, boundary by boundary.
"""

import re

import pandas as pd

from sepsis_decision_support.data.mimic_extract import for_selected_stays, read_sql

from .conftest import hours
from .toy_mimic import (
    ARTERIAL_MEAN,
    CEFTRIAXONE,
    GENTAMICIN,
    NON_INVASIVE_MEAN,
    URINE_CULTURE,
    culture,
    icu_stay,
    infusion,
    patient,
    toy_database,
)

COURSE_ANTIBIOTICS = {
    225798, 225842, 225845, 225847, 225850, 225851, 225855, 225860, 225879,
    225881, 225883, 225884, 225886, 225892, 225893, 225899, 225902, 229061,
}  # fmt: skip
COURSE_CULTURES = {225401, 225437, 225444, 225451, 225454, 225816, 225817, 225818}


def test_both_course_definition_queries_use_the_prototype_item_lists():
    for name in ("recognition_culture_antibiotic_pair", "m1_course_funnel"):
        item_ids = {int(number) for number in re.findall(r"\b\d{6}\b", read_sql(name))}
        assert item_ids == COURSE_ANTIBIOTICS | COURSE_CULTURES, name


def pair_recognition(rows: dict) -> pd.DataFrame:
    connection = toy_database(rows)
    return (
        connection.execute(read_sql("recognition_culture_antibiotic_pair"))
        .df()
        .set_index("stay_id")
    )


def test_pair_recognition_is_the_later_event_of_the_first_complete_pair():
    stays = [icu_stay(stay, stay, 0, 72) for stay in range(1, 10)]
    rows = {
        "mimiciv_icu.icustays": stays,
        "mimiciv_icu.inputevents": [
            infusion(1, 2.0, 3.0),
            infusion(1, 10.0, 11.0, CEFTRIAXONE),  # a later pair does not move R
            infusion(2, 2.0, 3.0),
            infusion(3, 2.0, 3.0),
            infusion(4, -2.0, -1.0),  # before ICU admission
            infusion(5, 70.0, 80.0),  # still running at ICU discharge: kept
            infusion(6, 2.0, 3.0, GENTAMICIN),  # not on the course list
            infusion(7, 1.5, 2.5),
            infusion(8, 71.5, 72.0),
            infusion(9, 5.0, 6.0),
            infusion(9, 5.5, 6.0),
        ],
        "mimiciv_icu.procedureevents": [
            culture(1, 2.5),
            culture(1, 10.0),
            culture(2, 2.0 + 61 / 60),  # 61 minutes after the antibiotic
            culture(3, 1.0),  # exactly one hour before: inclusive
            culture(4, -1.5),
            culture(5, 70.5, URINE_CULTURE),
            culture(6, 2.0),
            culture(7, 1.0),
            culture(8, 72.5),  # after ICU discharge
            culture(9, 5.0 + 50 / 60),
            culture(9, 5.0 + 40 / 60),  # first complete pair: 5 h 40 min
        ],
    }
    result = pair_recognition(rows)
    assert sorted(result.index) == [1, 3, 5, 7, 9]
    expected_hours = {1: 2.5, 3: 2.0, 5: 70.5, 7: 1.5, 9: 5.0 + 40 / 60}
    for stay, hour in expected_hours.items():
        assert result.loc[stay, "recognition_time"] == hours(hour), stay
    # Two antibiotics complete a pair with the 5 h 40 min culture; the earlier one is kept.
    assert result.loc[9, "antibiotic_time"] == hours(5.0)
    assert result.loc[9, "culture_time"] == hours(5.0 + 40 / 60)
    assert result["organ_dysfunction_time"].isna().all()


def course_funnel(rows: dict) -> dict:
    connection = toy_database(rows)
    return connection.execute(read_sql("m1_course_funnel")).df().iloc[0].to_dict()


def test_course_funnel_follows_the_prototype_rule_for_rule():
    ten_days = 240.0
    rows = {
        "mimiciv_icu.icustays": [
            icu_stay(1, 11, 0, 48),  # first stay of patient 1
            icu_stay(1, 12, 100, 148),  # later stay of patient 1
            icu_stay(2, 21, 0, 12),  # exactly 12 hours: out (strictly longer needed)
            icu_stay(3, 31, 0, 12 + 1 / 60),  # 12 h 1 min: in
            icu_stay(4, 41, 0, ten_days),  # exactly 10 days: out (strictly shorter needed)
            icu_stay(5, 51, 0, 48),  # anchor_age 66: out
            icu_stay(6, 61, 0, 48),  # anchor_age 65: in
            icu_stay(7, 71, 0, 48),  # anchor_age 17: out
        ],
        "mimiciv_hosp.patients": [
            patient(1),
            patient(2),
            patient(3),
            patient(4),
            patient(5, anchor_age=66),
            patient(6, anchor_age=65),
            patient(7, anchor_age=17),
        ],
        "mimiciv_icu.inputevents": [
            infusion(11, 1.0, 2.0),
            infusion(12, 101.0, 102.0),  # later stay: not counted
            infusion(31, 1.0, 13.0),  # infusion ends after ICU discharge: out
            infusion(61, 0.25, 1.0),
        ],
        "mimiciv_icu.procedureevents": [
            culture(11, 1.5),
            culture(12, 101.0),
            culture(31, 1.0),
            culture(61, -0.5),  # charted before ICU admission, 45 minutes before the antibiotic
        ],
    }
    counts = course_funnel(rows)
    assert counts == {
        "icu_stays": 8,
        "first_icu_stays": 7,
        "stays_12_hours_to_10_days": 5,  # 11, 31, 51, 61, 71
        "anchor_age_18_to_65": 3,  # 11, 31, 61
        "culture_antibiotic_pair": 1,  # 11
        "culture_antibiotic_pair_any_culture_time": 2,  # 11, 61
    }


def test_database_counts_count_sepsis3_on_first_stays_only():
    rows = {
        "mimiciv_icu.icustays": [
            icu_stay(1, 11, 0, 48),
            icu_stay(1, 12, 100, 148),
            icu_stay(2, 21, 0, 48),
        ],
        "mimiciv_hosp.patients": [patient(1), patient(2), patient(3)],
        "mimiciv_hosp.admissions": [{"subject_id": 1, "hadm_id": 11}],
        "mimiciv_derived.sepsis3": [
            {"stay_id": 11, "sepsis3": True},
            {"stay_id": 12, "sepsis3": True},  # not a first stay
            {"stay_id": 21, "sepsis3": False},
        ],
    }
    connection = toy_database(rows)
    counts = connection.execute(read_sql("m1_database_counts")).df().iloc[0].to_dict()
    assert counts["patients"] == 3
    assert counts["icustays"] == 3
    assert counts["patients_with_icu_stay"] == 2
    assert counts["first_icu_stays"] == 2
    assert counts["first_stays_with_sepsis3"] == 1


def test_map_by_source_splits_arterial_and_cuff_readings():
    rows = {
        "mimiciv_icu.chartevents": [
            {"stay_id": 1, "charttime": 1.0, "itemid": ARTERIAL_MEAN, "valuenum": 60.0},
            {"stay_id": 1, "charttime": 1.0, "itemid": 225312, "valuenum": 64.0},
            {"stay_id": 1, "charttime": 1.0, "itemid": NON_INVASIVE_MEAN, "valuenum": 70.0},
            {"stay_id": 1, "charttime": 2.0, "itemid": NON_INVASIVE_MEAN, "valuenum": 0.0},
        ]
    }
    connection = toy_database(rows)
    readings = connection.execute(read_sql("m1_map_by_source")).df()
    by_source = readings.set_index("source")["value"].to_dict()
    assert by_source == {"arterial": 62.0, "non_invasive": 70.0}


def test_selected_stays_wrapper_survives_a_comment_on_the_last_line():
    connection = toy_database(
        {"mimiciv_icu.icustays": [icu_stay(1, 1, 0, 48), icu_stay(2, 2, 0, 48)]}
    )
    connection.register("selected_stays", pd.DataFrame({"stay_id": [2]}))
    sql = "SELECT stay_id FROM mimiciv_icu.icustays -- every stay"
    assert connection.execute(for_selected_stays(sql)).df()["stay_id"].tolist() == [2]

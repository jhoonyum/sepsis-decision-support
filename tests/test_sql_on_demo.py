"""The SQL extraction, run against the open MIMIC-IV demo (make demo-data).

Skipped unless MIMIC_DEMO_DUCKDB points to the demo's DuckDB file. The demo is open
access (ODbL); these tests never run on the credentialed MIMIC-IV database.
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from sepsis_decision_support.cohort.cohort_builder import build_cohort
from sepsis_decision_support.data.canonical_tables import MEASUREMENT_VARIABLES, validate_tables

DEMO_PATH = os.environ.get("MIMIC_DEMO_DUCKDB")

pytestmark = [
    pytest.mark.demo,
    pytest.mark.skipif(not DEMO_PATH, reason="MIMIC_DEMO_DUCKDB is not set"),
]


@pytest.fixture(scope="module")
def tables():
    from sepsis_decision_support.data.mimic_extract import extract_canonical_tables

    return extract_canonical_tables(Path(DEMO_PATH))


def test_tables_pass_validation(tables):
    assert validate_tables(tables) == []


def test_one_first_icu_stay_per_patient(tables):
    first = tables.stays.loc[tables.stays["is_first_icu_stay"]]
    assert first["subject_id"].is_unique
    assert set(first["subject_id"]) == set(tables.stays["subject_id"])


def test_recognition_time_is_when_every_criterion_was_on_record(tables):
    recognition = tables.recognition
    assert (recognition["recognition_time"] >= recognition["antibiotic_time"]).all()
    assert (recognition["recognition_time"] >= recognition["organ_dysfunction_time"]).all()
    with_culture = recognition["culture_time"].notna()
    assert (
        recognition.loc[with_culture, "recognition_time"]
        >= recognition.loc[with_culture, "culture_time"]
    ).all()


def test_measurements_are_plausible_and_known(tables):
    for variable, (_, low, high) in MEASUREMENT_VARIABLES.items():
        values = tables.measurements.loc[tables.measurements["variable"] == variable, "value"]
        assert values.between(low, high).all(), variable


def test_cohort_flow_only_shrinks(tables, settings):
    counts = [count for _, count in build_cohort(tables, settings.cohort).flow]
    assert counts == sorted(counts, reverse=True)
    assert counts[-1] > 0


def test_culture_antibiotic_pairs_on_the_demo():
    from sepsis_decision_support.data.mimic_extract import (
        extract_recognition,
        extract_stays,
        open_read_only,
    )

    with open_read_only(DEMO_PATH) as connection:
        stays = extract_stays(connection)
        recognition = extract_recognition(connection, "culture_antibiotic_pair", stays)
    assert len(recognition) > 0 and recognition["stay_id"].is_unique
    gap = (recognition["antibiotic_time"] - recognition["culture_time"]).abs()
    assert (gap <= pd.Timedelta(hours=1)).all()
    joined = recognition.merge(stays, on="stay_id")
    assert (joined["recognition_time"] >= joined["icu_intime"]).all()
    assert (joined["culture_time"] <= joined["icu_outtime"]).all()


def test_reading_only_cohort_stays_changes_nothing_for_those_stays(tables, settings):
    from sepsis_decision_support.data.mimic_extract import extract_canonical_tables

    restricted = extract_canonical_tables(Path(DEMO_PATH), settings.cohort)
    cohort_stays = set(build_cohort(tables, settings.cohort).stays["stay_id"])
    assert set(restricted.measurements["stay_id"]) <= cohort_stays
    for name in ("measurements", "treatments", "sofa_hourly"):
        full = tables.as_dictionary()[name]
        full = full.loc[full["stay_id"].isin(cohort_stays)]
        sort_columns = list(full.columns)
        pd.testing.assert_frame_equal(
            full.sort_values(sort_columns).reset_index(drop=True),
            restricted.as_dictionary()[name].sort_values(sort_columns).reset_index(drop=True),
            obj=name,
        )
    pd.testing.assert_frame_equal(tables.stays, restricted.stays)


def test_m1_check_runs_on_the_demo_and_is_aggregate_only(settings):
    from sepsis_decision_support.checks.m1 import build_m1_report, collect_m1_inputs
    from sepsis_decision_support.checks.m1_markdown import render_m1_markdown
    from sepsis_decision_support.privacy.aggregate_guard import assert_aggregate_only

    inputs = collect_m1_inputs(DEMO_PATH, settings)
    report = build_m1_report(inputs, settings)
    assert_aggregate_only(report)
    assert report["database_counts"]["demo"]
    # The flow starts from every ICU stay, the same count the database section reports.
    assert inputs.database_counts["icustays"] == len(inputs.stays)
    assert inputs.course_funnel["icu_stays"] == len(inputs.stays)
    assert inputs.course_funnel["first_icu_stays"] == int(inputs.stays["is_first_icu_stay"].sum())
    # Pressure readings by source exist only for the main cohort's stays.
    main_stays = set(report_main_stays(inputs, settings))
    assert set(inputs.map_by_source["stay_id"]) <= main_stays
    assert set(inputs.map_by_source["source"]) <= {"arterial", "non_invasive"}
    assert "# M1 check" in render_m1_markdown(report)


def report_main_stays(inputs, settings):
    from sepsis_decision_support.cohort.cohort_builder import select_cohort

    return select_cohort(inputs.stays, inputs.recognition["sepsis3"], settings.cohort).stays[
        "stay_id"
    ]

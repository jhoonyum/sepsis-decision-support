"""The SQL extraction, run against the open MIMIC-IV demo (make demo-data).

Skipped unless MIMIC_DEMO_DUCKDB points to the demo's DuckDB file. The demo is open
access (ODbL); these tests never run on the credentialed MIMIC-IV database.
"""

import os
from pathlib import Path

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

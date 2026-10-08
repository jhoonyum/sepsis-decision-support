import pandas as pd
import pytest
from pydantic import ValidationError

from sepsis_decision_support.config import load_settings
from sepsis_decision_support.data.canonical_tables import validate_tables
from sepsis_decision_support.data.synthetic import generate_canonical_tables
from sepsis_decision_support.decision_support.export_for_web import (
    _default_now,
    _longest_run,
    describe_feature,
    describe_missing_indicator,
)
from sepsis_decision_support.outcomes.labels import PRE_SHOCK, SHOCK_STAGE
from sepsis_decision_support.pipeline import prepare


def test_unknown_settings_are_errors():
    with pytest.raises(ValidationError):
        load_settings(overrides={"outcomes": {"horizon_hour": 12}})


def test_fingerprint_ignores_machine_specific_paths():
    here = load_settings(overrides={"runs": {"directory": "/tmp/a"}})
    there = load_settings(overrides={"runs": {"directory": "/tmp/b"}})
    changed = load_settings(overrides={"outcomes": {"horizon_hours": 12}})
    assert here.fingerprint() == there.fingerprint()
    assert here.fingerprint() != changed.fingerprint()


def test_synthetic_tables_are_valid_and_reproducible():
    first = generate_canonical_tables(60, seed=11)
    second = generate_canonical_tables(60, seed=11)
    assert validate_tables(first) == []
    for name, table in first.as_dictionary().items():
        pd.testing.assert_frame_equal(table, second.as_dictionary()[name], obj=name)


def test_prepare_produces_both_populations_and_binary_labels():
    settings = load_settings(overrides={"data": {"synthetic": {"number_of_stays": 250}}})
    prepared = prepare(settings)
    rows = prepared.training.rows
    assert set(rows["population"]) == {PRE_SHOCK, SHOCK_STAGE}
    assert set(rows["event_within_horizon"].unique()) <= {True, False}
    assert rows.index.equals(prepared.training.features.index)
    # Every landmark falls inside the stay and before any death.
    assert (rows["landmark_time"] < rows["icu_outtime"]).all()
    alive = rows["death_time"].isna() | (rows["landmark_time"] < rows["death_time"])
    assert alive.all()


def test_feature_descriptions_on_the_screen():
    assert describe_feature("temperature_last", 36.84) == "Temperature 36.8 °C"
    assert describe_feature("spo2_last", 94.0) == "SpO2 94%"
    assert describe_feature("lactate_change", -0.5) == "Lactate change over 6 h: -0.5 mmol/L"
    assert describe_feature("lactate_last_missing", None) == "Lactate not yet measured"
    assert describe_feature("vasopressor_rate", 0.0) == "No vasopressor dose"
    assert describe_missing_indicator("lactate_last", None) == "Lactate not yet measured"
    assert describe_missing_indicator("lactate_last", 2.4) == "Lactate measured"
    assert describe_missing_indicator("sofa_24h", None) == "sofa 24h unknown"
    assert describe_missing_indicator("lactate_change", None) == "Lactate change over 6 h unknown"


def test_longest_run_and_default_hour():
    assert _longest_run(pd.Series([False, True, True, False, True])) == (1, 2)
    assert _longest_run(pd.Series([False, False])) is None
    hourly = [
        {"hour": float(hour), "population": PRE_SHOCK if hour < 8 else SHOCK_STAGE,
         "on_vasopressor": 9 <= hour <= 13, "risk_interval": [0.1, 0.2 + 0.01 * (hour == 4)]}
        for hour in range(20)
    ]  # fmt: skip
    assert _default_now("deteriorating", hourly) == 5.0  # three hours before the shock stage
    assert _default_now("on_vasopressor", hourly) == 11.0  # middle of the vasopressor run
    assert _default_now("uncertain", hourly) == 4.0  # widest interval
    assert _default_now("steady", hourly) == 10.0

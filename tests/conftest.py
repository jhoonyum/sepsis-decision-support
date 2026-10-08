"""Shared helpers: small hand-written canonical tables and the default settings.

Tests build their own tiny tables so that every expected value can be checked by hand.
No test reads MIMIC-IV; tests marked ``demo`` read the open MIMIC-IV demo when the
environment variable MIMIC_DEMO_DUCKDB points to its DuckDB file.
"""

from __future__ import annotations

import pandas as pd
import pytest

from sepsis_decision_support.config import load_settings
from sepsis_decision_support.data.canonical_tables import (
    MEASUREMENTS,
    RECOGNITION,
    SOFA_HOURLY,
    STAYS,
    TREATMENTS,
    CanonicalTables,
    coerce_to_specification,
)

# MIMIC-IV shifts dates into the future; the tests do the same.
ADMISSION = pd.Timestamp("2150-01-01 08:00")


def hours(value: float) -> pd.Timestamp:
    """A time ``value`` hours after the reference ICU admission."""
    return ADMISSION + pd.Timedelta(hours=value)


def stay_row(stay_id: int, **changes) -> dict:
    """One stays row with ordinary defaults; pass keyword arguments to change any column."""
    row = {
        "stay_id": stay_id,
        "subject_id": stay_id,
        "hadm_id": stay_id,
        "age_years": 60.0,
        "sex": "F",
        "race_group": "White",
        "first_careunit": "Medical ICU",
        "era": "2014 - 2016",
        "hospital_service": "MED",
        "icu_intime": hours(0),
        "icu_outtime": hours(72),
        "hospital_dischtime": hours(120),
        "death_time": pd.NaT,
        "date_of_death": pd.NaT,
        "is_first_icu_stay": True,
    }
    row.update(changes)
    return row


def recognition_row(stay_id: int, recognised_at_hour: float) -> dict:
    time = hours(recognised_at_hour)
    return {
        "stay_id": stay_id,
        "recognition_time": time,
        "antibiotic_time": time,
        "culture_time": time - pd.Timedelta(hours=1),
        "organ_dysfunction_time": time,
    }


def make_tables(
    stays: list[dict],
    recognition: list[dict] = (),
    measurements: list[tuple] = (),
    treatments: list[tuple] = (),
    sofa: list[tuple] = (),
) -> CanonicalTables:
    """Canonical tables from plain rows.

    measurements: (stay_id, hour, variable, value)
    treatments:   (stay_id, treatment, start hour, end hour, rate)
    sofa:         (stay_id, hour, sofa_24h)
    """
    measurement_frame = pd.DataFrame(
        [(stay, hours(hour), variable, value) for stay, hour, variable, value in measurements],
        columns=["stay_id", "charttime", "variable", "value"],
    )
    treatment_frame = pd.DataFrame(
        [
            (stay, name, hours(start), hours(end), rate)
            for stay, name, start, end, rate in treatments
        ],
        columns=["stay_id", "treatment", "start_time", "end_time", "rate"],
    )
    sofa_frame = pd.DataFrame(
        [(stay, hours(hour), score, 0.0) for stay, hour, score in sofa],
        columns=["stay_id", "hour_end_time", "sofa_24h", "cardiovascular_24h"],
    )
    return CanonicalTables(
        stays=coerce_to_specification(pd.DataFrame(stays), STAYS),
        recognition=coerce_to_specification(
            pd.DataFrame(list(recognition), columns=list(RECOGNITION.all_columns)), RECOGNITION
        ),
        measurements=coerce_to_specification(measurement_frame, MEASUREMENTS),
        treatments=coerce_to_specification(treatment_frame, TREATMENTS),
        sofa_hourly=coerce_to_specification(sofa_frame, SOFA_HOURLY),
    )


@pytest.fixture(scope="session")
def settings():
    return load_settings()

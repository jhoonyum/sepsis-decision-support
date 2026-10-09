"""The five tables every later step reads, whatever the data source.

Why a fixed intermediate format?
    The pipeline has three data sources: generated (synthetic) patients, the open
    MIMIC-IV demo, and the credentialed full MIMIC-IV. Each source has its own
    extraction step, but all of them must produce exactly these tables. Everything
    downstream (cohort, outcomes, features, models, evaluation) is then written and
    tested once.

Tables (one pandas DataFrame each):
    stays         one row per ICU stay
    recognition   one row per ICU stay that meets the cohort's infection definition
                  (Sepsis-3, or the culture-antibiotic pair): time R and its parts
    measurements  long format: one row per measured value
    treatments    one row per treatment interval
    sofa_hourly   one row per stay-hour: SOFA over the preceding 24 hours

All times are naive timestamps on the source's own (shifted) clock.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Measured variables: name -> (unit, plausible minimum, plausible maximum).
# Values outside the plausible range are treated as recording errors and dropped.
MEASUREMENT_VARIABLES: dict[str, tuple[str, float, float]] = {
    "heart_rate": ("beats/min", 20, 250),
    "mean_arterial_pressure": ("mmHg", 20, 200),
    "systolic_blood_pressure": ("mmHg", 30, 300),
    "respiratory_rate": ("breaths/min", 3, 70),
    "spo2": ("%", 50, 100),
    "temperature": ("degrees C", 30, 43),
    "lactate": ("mmol/L", 0.2, 30),
    "creatinine": ("mg/dL", 0.1, 20),
    "platelets": ("10^3/uL", 1, 1500),
    "bilirubin": ("mg/dL", 0.1, 50),
    "white_blood_cells": ("10^3/uL", 0.1, 200),
    "gcs_total": ("points", 3, 15),
}

# Treatment intervals. "rate" is only meaningful for vasopressors
# (norepinephrine-equivalent dose, micrograms per kg per minute); otherwise it is missing.
TREATMENT_NAMES: tuple[str, ...] = (
    "vasopressor",
    "norepinephrine",
    "antibiotic",
    "supplemental_oxygen",
    "invasive_ventilation",
)

ERA_LABELS: tuple[str, ...] = (
    "2008 - 2010",
    "2011 - 2013",
    "2014 - 2016",
    "2017 - 2019",
    "2020 - 2022",
)


@dataclass(frozen=True)
class TableSpecification:
    """Required columns of one canonical table and the kind of values each holds."""

    name: str
    integer_columns: tuple[str, ...] = ()
    float_columns: tuple[str, ...] = ()
    text_columns: tuple[str, ...] = ()
    time_columns: tuple[str, ...] = ()
    boolean_columns: tuple[str, ...] = ()

    @property
    def all_columns(self) -> tuple[str, ...]:
        return (
            self.integer_columns
            + self.float_columns
            + self.text_columns
            + self.time_columns
            + self.boolean_columns
        )


STAYS = TableSpecification(
    name="stays",
    integer_columns=("stay_id", "subject_id", "hadm_id"),
    float_columns=("age_years",),
    text_columns=("sex", "race_group", "first_careunit", "era", "hospital_service"),
    time_columns=(
        "icu_intime",
        "icu_outtime",
        "hospital_dischtime",
        "death_time",  # in-hospital death time; missing if the patient left the hospital alive
        "date_of_death",  # date of death from linked state records (up to 1 year after discharge)
    ),
    boolean_columns=("is_first_icu_stay",),
)

RECOGNITION = TableSpecification(
    name="recognition",
    integer_columns=("stay_id",),
    time_columns=(
        "recognition_time",  # R: earliest time every criterion of the definition was on record
        "antibiotic_time",
        "culture_time",
        "organ_dysfunction_time",  # first SOFA >= 2 that qualified (Sepsis-3 only, else missing)
    ),
)

MEASUREMENTS = TableSpecification(
    name="measurements",
    integer_columns=("stay_id",),
    float_columns=("value",),
    text_columns=("variable",),
    time_columns=("charttime",),
)

TREATMENTS = TableSpecification(
    name="treatments",
    integer_columns=("stay_id",),
    float_columns=("rate",),
    text_columns=("treatment",),
    time_columns=("start_time", "end_time"),
)

SOFA_HOURLY = TableSpecification(
    name="sofa_hourly",
    integer_columns=("stay_id",),
    float_columns=("sofa_24h", "cardiovascular_24h"),
    time_columns=("hour_end_time",),
)

ALL_TABLES: tuple[TableSpecification, ...] = (
    STAYS,
    RECOGNITION,
    MEASUREMENTS,
    TREATMENTS,
    SOFA_HOURLY,
)


@dataclass
class CanonicalTables:
    """The five canonical tables, kept together so they travel as one object."""

    stays: pd.DataFrame
    recognition: pd.DataFrame
    measurements: pd.DataFrame
    treatments: pd.DataFrame
    sofa_hourly: pd.DataFrame

    def as_dictionary(self) -> dict[str, pd.DataFrame]:
        return {
            specification.name: getattr(self, specification.name) for specification in ALL_TABLES
        }


def coerce_to_specification(table: pd.DataFrame, specification: TableSpecification) -> pd.DataFrame:
    """Return a copy with exactly the specified columns, each converted to its kind.

    Raises:
        ValueError: if a required column is missing.
    """
    missing_columns = [
        column for column in specification.all_columns if column not in table.columns
    ]
    if missing_columns:
        raise ValueError(f"{specification.name}: missing columns {missing_columns}")

    result = table.loc[:, list(specification.all_columns)].copy()
    for column in specification.integer_columns:
        result[column] = result[column].astype("int64")
    for column in specification.float_columns:
        result[column] = result[column].astype("float64")
    for column in specification.text_columns:
        result[column] = result[column].astype("string")
    for column in specification.time_columns:
        result[column] = pd.to_datetime(result[column]).astype("datetime64[ns]")
    for column in specification.boolean_columns:
        result[column] = result[column].astype("bool")
    return result.reset_index(drop=True)


def validate_tables(tables: CanonicalTables) -> list[str]:
    """Check the tables against their specifications and against each other.

    Returns:
        A list of human-readable problems. An empty list means the tables are usable.
    """
    problems: list[str] = []
    for specification in ALL_TABLES:
        table = getattr(tables, specification.name)
        missing = [column for column in specification.all_columns if column not in table.columns]
        if missing:
            problems.append(f"{specification.name}: missing columns {missing}")

    if problems:
        return problems

    if tables.stays["stay_id"].duplicated().any():
        problems.append("stays: stay_id is not unique")
    if tables.recognition["stay_id"].duplicated().any():
        problems.append("recognition: stay_id is not unique")

    known_stays = set(tables.stays["stay_id"])
    for specification in (RECOGNITION, MEASUREMENTS, TREATMENTS, SOFA_HOURLY):
        table = getattr(tables, specification.name)
        unknown = set(table["stay_id"]) - known_stays
        if unknown:
            problems.append(f"{specification.name}: {len(unknown)} stay_id values not in stays")

    unknown_variables = set(tables.measurements["variable"].dropna()) - set(MEASUREMENT_VARIABLES)
    if unknown_variables:
        problems.append(f"measurements: unknown variables {sorted(unknown_variables)}")
    unknown_treatments = set(tables.treatments["treatment"].dropna()) - set(TREATMENT_NAMES)
    if unknown_treatments:
        problems.append(f"treatments: unknown treatments {sorted(unknown_treatments)}")

    stays = tables.stays
    if (stays["icu_outtime"] < stays["icu_intime"]).any():
        problems.append("stays: icu_outtime before icu_intime")
    unknown_eras = set(stays["era"].dropna()) - set(ERA_LABELS)
    if unknown_eras:
        problems.append(f"stays: unknown era labels {sorted(unknown_eras)}")
    return problems

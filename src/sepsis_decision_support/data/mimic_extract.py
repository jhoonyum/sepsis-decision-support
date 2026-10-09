"""Canonical tables from a DuckDB file built by mimic-code (full MIMIC-IV or the open demo).

The DuckDB file is opened read-only. The SQL lives in ``data/sql/`` so that it can be
read and reviewed on its own; this module only runs it and tidies the results.

Where this runs
    On the analyst's own computer, never on a cloud service: the extracted tables are
    record-level MIMIC-IV data covered by the PhysioNet data use agreement.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from sepsis_decision_support.cohort.cohort_builder import select_cohort
from sepsis_decision_support.config import CohortSettings
from sepsis_decision_support.data.canonical_tables import (
    ERA_LABELS,
    MEASUREMENT_VARIABLES,
    MEASUREMENTS,
    RECOGNITION,
    SOFA_HOURLY,
    STAYS,
    TREATMENTS,
    CanonicalTables,
    coerce_to_specification,
)


def read_sql(name: str) -> str:
    return (
        resources.files("sepsis_decision_support.data").joinpath("sql", f"{name}.sql").read_text()
    )


def race_group(race: pd.Series) -> pd.Series:
    """Collapse MIMIC-IV's detailed race and ethnicity labels into six groups.

    Used only for subgroup performance checks, never as a predictor.
    """
    text = race.fillna("UNKNOWN").str.upper()
    groups = np.select(
        [
            text.str.startswith("WHITE") | text.isin(["PORTUGUESE"]),
            text.str.startswith("BLACK"),
            text.str.startswith("HISPANIC") | text.str.startswith("SOUTH AMERICAN"),
            text.str.startswith("ASIAN"),
            text.str.contains("UNKNOWN|UNABLE|DECLINED", regex=True),
        ],
        ["White", "Black", "Hispanic", "Asian", "Unknown"],
        default="Other",
    )
    return pd.Series(groups, index=race.index)


def estimated_era(
    intime: pd.Series, anchor_year: pd.Series, anchor_year_group: pd.Series
) -> pd.Series:
    """Real-calendar era of each stay.

    MIMIC-IV shifts every patient's dates by a patient-specific number of years. Each
    patient's ``anchor_year`` corresponds to some real year inside ``anchor_year_group``
    (a three-year range). A stay's real year is estimated as the middle of that range
    plus the stay's distance in years from the anchor year, then mapped back to a range.
    """
    bounds = anchor_year_group.str.extract(r"(\d{4})\s*-\s*(\d{4})").astype("float64")
    middle = (bounds[0] + bounds[1]) / 2
    estimated_year = middle + (intime.dt.year - anchor_year)
    starts = np.array([int(re.match(r"\d{4}", label).group()) for label in ERA_LABELS])
    position = np.clip(
        np.searchsorted(starts, estimated_year.to_numpy(), side="right") - 1, 0, len(starts) - 1
    )
    labels = pd.Series(np.array(ERA_LABELS)[position], index=intime.index)
    return labels.where(estimated_year.notna())


def drop_implausible_values(measurements: pd.DataFrame) -> pd.DataFrame:
    """Remove values outside each variable's plausible range (recording errors)."""
    low = measurements["variable"].map(
        {name: limits[1] for name, limits in MEASUREMENT_VARIABLES.items()}
    )
    high = measurements["variable"].map(
        {name: limits[2] for name, limits in MEASUREMENT_VARIABLES.items()}
    )
    keep = measurements["value"].between(low, high)
    return measurements.loc[keep].reset_index(drop=True)


# Which recognition query belongs to which infection definition (``cohort.definition``).
RECOGNITION_QUERIES = {
    "sepsis3": "recognition",
    "culture_antibiotic_pair": "recognition_culture_antibiotic_pair",
}


def open_read_only(duckdb_path: Path | str) -> duckdb.DuckDBPyConnection:
    """Open a mimic-code DuckDB file read-only (temporary tables and views still work)."""
    return duckdb.connect(str(duckdb_path), read_only=True)


def extract_stays(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Every ICU stay, in the canonical stays format.

    Stays without a discharge time are kept here and removed by the cohort's first rule,
    so that the cohort flow starts from the full count of ICU stays.
    """
    stays = connection.execute(read_sql("stays")).df()
    stays["era"] = estimated_era(
        pd.to_datetime(stays["icu_intime"]), stays["anchor_year"], stays["anchor_year_group"]
    )
    stays["race_group"] = race_group(stays["race"])
    stays["sex"] = stays["sex"].str.upper().str[0]
    return coerce_to_specification(stays, STAYS)


def extract_recognition(
    connection: duckdb.DuckDBPyConnection, definition: str, stays: pd.DataFrame
) -> pd.DataFrame:
    """Recognition time R of every stay that meets the infection definition."""
    recognition = connection.execute(read_sql(RECOGNITION_QUERIES[definition])).df()
    recognition = recognition.loc[recognition["stay_id"].isin(set(stays["stay_id"]))]
    return coerce_to_specification(recognition, RECOGNITION)


def for_selected_stays(sql: str) -> str:
    """Wrap an extraction query so that it only returns rows of the stays in ``selected_stays``.

    ``selected_stays`` is a table with a ``stay_id`` column registered on the connection.
    The newlines keep a comment on the query's last line from swallowing the bracket.
    """
    body = sql.strip().rstrip(";")
    return (
        f"SELECT extracted.* FROM (\n{body}\n) AS extracted\n"
        "WHERE extracted.stay_id IN (SELECT stay_id FROM selected_stays)"
    )


def extract_event_tables(
    connection: duckdb.DuckDBPyConnection, stay_ids: pd.Series | list[int] | None
) -> dict[str, pd.DataFrame]:
    """Measurements, treatments and hourly SOFA, for the given stays or (None) for all."""

    def run(name: str) -> pd.DataFrame:
        sql = read_sql(name)
        if stay_ids is None:
            return connection.execute(sql).df()
        return connection.execute(for_selected_stays(sql)).df()

    if stay_ids is not None:
        connection.register("selected_stays", pd.DataFrame({"stay_id": list(stay_ids)}))
    measurements = run("measurements")
    treatments = run("treatments")
    sofa_hourly = run("sofa_hourly")
    treatments = treatments.loc[
        treatments["end_time"].notna() & (treatments["end_time"] > treatments["start_time"])
    ]
    return {
        "measurements": coerce_to_specification(
            drop_implausible_values(measurements), MEASUREMENTS
        ),
        "treatments": coerce_to_specification(treatments, TREATMENTS),
        "sofa_hourly": coerce_to_specification(sofa_hourly, SOFA_HOURLY),
    }


def extract_canonical_tables(
    duckdb_path: Path | str, cohort_settings: CohortSettings | None = None
) -> CanonicalTables:
    """Run the extraction SQL against a mimic-code DuckDB file and return canonical tables.

    Args:
        duckdb_path: the DuckDB file built by mimic-code (opened read-only).
        cohort_settings: when given, recognition follows ``cohort_settings.definition`` and
            measurements, treatments and SOFA are read only for the stays that pass the
            cohort rules. This keeps the full MIMIC-IV extraction to a few GB of memory.
            The stays table always holds every ICU stay, so the cohort flow is complete.
            When omitted, Sepsis-3 recognition is used and every stay's data is read.
    """
    definition = cohort_settings.definition if cohort_settings is not None else "sepsis3"
    with open_read_only(duckdb_path) as connection:
        stays = extract_stays(connection)
        recognition = extract_recognition(connection, definition, stays)
        selected = None
        if cohort_settings is not None:
            selected = select_cohort(stays, recognition, cohort_settings).stays["stay_id"]
        events = extract_event_tables(connection, selected)

    known_stays = set(stays["stay_id"])

    def only_known(table: pd.DataFrame) -> pd.DataFrame:
        return table.loc[table["stay_id"].isin(known_stays)].reset_index(drop=True)

    return CanonicalTables(
        stays=stays,
        recognition=recognition.reset_index(drop=True),
        measurements=only_known(events["measurements"]),
        treatments=only_known(events["treatments"]),
        sofa_hourly=only_known(events["sofa_hourly"]),
    )

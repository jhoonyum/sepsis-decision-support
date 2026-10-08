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


def extract_canonical_tables(duckdb_path: Path | str) -> CanonicalTables:
    """Run the extraction SQL against a mimic-code DuckDB file and return canonical tables."""
    with duckdb.connect(str(duckdb_path), read_only=True) as connection:
        stays = connection.execute(read_sql("stays")).df()
        recognition = connection.execute(read_sql("recognition")).df()
        measurements = connection.execute(read_sql("measurements")).df()
        treatments = connection.execute(read_sql("treatments")).df()
        sofa_hourly = connection.execute(read_sql("sofa_hourly")).df()

    stays = stays.loc[stays["icu_outtime"].notna()].copy()
    stays["intime_for_era"] = pd.to_datetime(stays["icu_intime"])
    stays["era"] = estimated_era(
        stays["intime_for_era"], stays["anchor_year"], stays["anchor_year_group"]
    )
    stays["race_group"] = race_group(stays["race"])
    stays["sex"] = stays["sex"].str.upper().str[0]
    known_stays = set(stays["stay_id"])

    def only_known(table: pd.DataFrame) -> pd.DataFrame:
        return table.loc[table["stay_id"].isin(known_stays)]

    treatments = treatments.loc[
        treatments["end_time"].notna() & (treatments["end_time"] > treatments["start_time"])
    ]
    return CanonicalTables(
        stays=coerce_to_specification(stays, STAYS),
        recognition=coerce_to_specification(only_known(recognition), RECOGNITION),
        measurements=coerce_to_specification(
            drop_implausible_values(only_known(measurements)), MEASUREMENTS
        ),
        treatments=coerce_to_specification(only_known(treatments), TREATMENTS),
        sofa_hourly=coerce_to_specification(only_known(sofa_hourly), SOFA_HOURLY),
    )

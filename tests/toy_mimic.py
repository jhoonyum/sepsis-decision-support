"""A tiny in-memory DuckDB database with the MIMIC-IV tables the SQL files read.

Only the columns the project's SQL uses are created. Tests insert a handful of rows whose
expected results can be worked out by hand, then run the real SQL files against them.
Times are hours after a reference moment, as in ``conftest.hours``.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from .conftest import hours

TABLE_COLUMNS: dict[str, dict[str, str]] = {
    "mimiciv_icu.icustays": {
        "subject_id": "INTEGER",
        "hadm_id": "INTEGER",
        "stay_id": "INTEGER",
        "first_careunit": "VARCHAR",
        "intime": "TIMESTAMP",
        "outtime": "TIMESTAMP",
    },
    "mimiciv_hosp.patients": {
        "subject_id": "INTEGER",
        "gender": "VARCHAR",
        "anchor_age": "INTEGER",
        "anchor_year": "INTEGER",
        "anchor_year_group": "VARCHAR",
        "dod": "DATE",
    },
    "mimiciv_hosp.admissions": {"subject_id": "INTEGER", "hadm_id": "INTEGER"},
    "mimiciv_icu.inputevents": {
        "subject_id": "INTEGER",
        "stay_id": "INTEGER",
        "starttime": "TIMESTAMP",
        "endtime": "TIMESTAMP",
        "itemid": "INTEGER",
    },
    "mimiciv_icu.procedureevents": {
        "subject_id": "INTEGER",
        "stay_id": "INTEGER",
        "starttime": "TIMESTAMP",
        "itemid": "INTEGER",
    },
    "mimiciv_icu.chartevents": {
        "stay_id": "INTEGER",
        "charttime": "TIMESTAMP",
        "itemid": "INTEGER",
        "valuenum": "DOUBLE",
    },
    "mimiciv_derived.sepsis3": {"stay_id": "INTEGER", "sepsis3": "BOOLEAN"},
}

VANCOMYCIN = 225798
CEFTRIAXONE = 225855
GENTAMICIN = 225875  # an antibiotic that is not on the course list
BLOOD_CULTURE = 225401
URINE_CULTURE = 225454
ARTERIAL_MEAN = 220052
NON_INVASIVE_MEAN = 220181


def toy_database(rows: dict[str, list[dict]]) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB connection with every table in ``TABLE_COLUMNS``.

    Args:
        rows: table name -> list of row dictionaries. Values of time columns may be given
            as hours (numbers); they are converted with ``conftest.hours``. Missing tables
            are created empty.
    """
    connection = duckdb.connect()
    for schema in sorted({name.split(".")[0] for name in TABLE_COLUMNS}):
        connection.execute(f"CREATE SCHEMA {schema}")
    for table, columns in TABLE_COLUMNS.items():
        definition = ", ".join(f"{column} {kind}" for column, kind in columns.items())
        connection.execute(f"CREATE TABLE {table} ({definition})")
        table_rows = rows.get(table, [])
        if not table_rows:
            continue
        frame = pd.DataFrame(table_rows).reindex(columns=list(columns))
        for column, kind in columns.items():
            if kind == "TIMESTAMP":
                frame[column] = [
                    hours(value) if isinstance(value, int | float) else value
                    for value in frame[column]
                ]
        connection.register("incoming_rows", frame)
        connection.execute(f"INSERT INTO {table} SELECT * FROM incoming_rows")
        connection.unregister("incoming_rows")
    return connection


def icu_stay(subject_id: int, stay_id: int, intime: float, outtime: float) -> dict:
    return {
        "subject_id": subject_id,
        "hadm_id": stay_id,
        "stay_id": stay_id,
        "first_careunit": "Medical Intensive Care Unit (MICU)",
        "intime": intime,
        "outtime": outtime,
    }


def patient(subject_id: int, anchor_age: int = 60) -> dict:
    return {
        "subject_id": subject_id,
        "gender": "F",
        "anchor_age": anchor_age,
        "anchor_year": 2150,
        "anchor_year_group": "2014 - 2016",
        "dod": None,
    }


def infusion(stay_id: int, start: float, end: float, itemid: int = VANCOMYCIN) -> dict:
    return {
        "subject_id": stay_id,
        "stay_id": stay_id,
        "starttime": start,
        "endtime": end,
        "itemid": itemid,
    }


def culture(stay_id: int, time: float, itemid: int = BLOOD_CULTURE) -> dict:
    return {"subject_id": stay_id, "stay_id": stay_id, "starttime": time, "itemid": itemid}

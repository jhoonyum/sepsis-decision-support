"""From raw source to model-ready landmark tables, in one place.

Steps (each lives in its own module and is tested separately):
    load tables      synthetic generator or SQL extraction from a mimic-code DuckDB file
    build cohort     inclusion rules and time zero
    landmarks        decision times at which the patient is still at risk
    shock events     treatment-independent shock definition
    labels           population group and outcome labels per landmark
    features         predictors from data recorded up to each landmark
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from sepsis_decision_support.cohort.cohort_builder import Cohort, build_cohort
from sepsis_decision_support.cohort.landmarks import build_landmarks
from sepsis_decision_support.config import Settings
from sepsis_decision_support.data.canonical_tables import CanonicalTables, validate_tables
from sepsis_decision_support.data.synthetic import generate_canonical_tables
from sepsis_decision_support.features.landmark_features import build_landmark_features
from sepsis_decision_support.outcomes.labels import label_landmarks
from sepsis_decision_support.outcomes.shock_events import shock_event_times


@dataclass
class LandmarkData:
    """Labelled landmark rows and their predictors (same index)."""

    rows: pd.DataFrame
    features: pd.DataFrame


@dataclass
class PreparedData:
    tables: CanonicalTables
    cohort: Cohort
    training: LandmarkData
    hourly: LandmarkData


def load_tables(settings: Settings) -> CanonicalTables:
    """Canonical tables from the configured data source."""
    if settings.data.source == "synthetic":
        tables = generate_canonical_tables(
            settings.data.synthetic.number_of_stays,
            seed=settings.seed,
            recognition_definition=settings.cohort.definition,
        )
    else:
        from sepsis_decision_support.data.mimic_extract import extract_canonical_tables

        if settings.data.duckdb_path is None:
            raise ValueError("data.duckdb_path must point to a mimic-code DuckDB file")
        # Measurements and treatments are read for the cohort's stays only.
        tables = extract_canonical_tables(settings.data.duckdb_path, settings.cohort)
    problems = validate_tables(tables)
    if problems:
        raise ValueError("canonical tables failed validation:\n  " + "\n  ".join(problems))
    return tables


def landmark_data(
    tables: CanonicalTables,
    cohort: Cohort,
    hours: list[float] | tuple[float, ...],
    settings: Settings,
) -> LandmarkData:
    landmarks = build_landmarks(cohort.stays, hours)
    shock_events = shock_event_times(tables.measurements, settings.outcomes, tables.treatments)
    rows = label_landmarks(landmarks, shock_events, tables.treatments, settings.outcomes)
    features = build_landmark_features(rows, tables, settings.features)
    return LandmarkData(rows=rows, features=features)


def prepare(settings: Settings, tables: CanonicalTables | None = None) -> PreparedData:
    """Run every step up to model-ready tables."""
    tables = tables if tables is not None else load_tables(settings)
    cohort = build_cohort(tables, settings.cohort)
    training = landmark_data(tables, cohort, settings.landmarks.training_hours, settings)
    hourly_hours = np.arange(0, settings.landmarks.alert_scoring_maximum_hours + 1, 1.0)
    hourly = landmark_data(tables, cohort, hourly_hours.tolist(), settings)
    return PreparedData(tables=tables, cohort=cohort, training=training, hourly=hourly)

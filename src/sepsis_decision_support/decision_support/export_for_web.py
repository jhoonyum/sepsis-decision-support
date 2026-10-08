"""Data for the decision-support screen: a real (or synthetic) model, always synthetic patients.

Why the patients are generated here and nowhere else
    The screen is public. This module never reads patient tables from a run; it creates
    its own synthetic patients with a fixed seed and scores them with the fitted model.
    The only things taken from the run are aggregates: model coefficients, their
    bootstrap spread, and the performance and calibration tables of the evaluation report
    (already checked by the privacy guard).

For each patient the screen gets, for every hour from sepsis recognition onwards:
    X    risk of the next stage within the horizon, with a 90% interval
    p1   risk of the next stage within the next hour
    Z    risk within the horizon starting one hour from now, if nothing happens in that hour
    the five predictors that move X most for this patient at that hour
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from sepsis_decision_support.cohort.cohort_builder import build_cohort
from sepsis_decision_support.config import Settings
from sepsis_decision_support.data.canonical_tables import MEASUREMENT_VARIABLES
from sepsis_decision_support.data.synthetic import generate_canonical_tables
from sepsis_decision_support.decision_support.evidence import EVIDENCE_TABLE
from sepsis_decision_support.models.landmark_model import FittedLandmarkModel
from sepsis_decision_support.outcomes.labels import PRE_SHOCK, SHOCK_STAGE
from sepsis_decision_support.pipeline import landmark_data
from sepsis_decision_support.privacy.aggregate_guard import (
    assert_aggregate_only,
    write_aggregate_json,
)

SCREEN_SEED = 4_401_017  # fixed and unrelated to any training seed
SCREEN_CANDIDATE_STAYS = 400
SCREEN_TARGETS = ("event_within_horizon", "event_within_next_hour", "event_horizon_after_hour")

VARIABLE_LABELS = {
    "heart_rate": "Heart rate",
    "mean_arterial_pressure": "Mean arterial pressure",
    "systolic_blood_pressure": "Systolic blood pressure",
    "respiratory_rate": "Respiratory rate",
    "spo2": "SpO2",
    "temperature": "Temperature",
    "lactate": "Lactate",
    "creatinine": "Creatinine",
    "platelets": "Platelets",
    "bilirubin": "Bilirubin",
    "white_blood_cells": "White blood cells",
    "gcs_total": "GCS",
}

# How units and values are written on the screen (the analysis keeps the plain-ASCII units).
DISPLAY_UNITS = {"degrees C": "°C", "10^3/uL": "×10³/µL", "points": "", "%": "%"}
DECIMALS = {
    "heart_rate": 0,
    "mean_arterial_pressure": 0,
    "systolic_blood_pressure": 0,
    "respiratory_rate": 0,
    "spo2": 0,
    "temperature": 1,
    "lactate": 1,
    "creatinine": 1,
    "platelets": 0,
    "bilirubin": 1,
    "white_blood_cells": 1,
    "gcs_total": 0,
}
# Bedside reference lines drawn on the screen's charts.
REFERENCE_VALUES = {"mean_arterial_pressure": 65.0, "lactate": 2.0}


def display_unit(variable: str) -> str:
    unit = MEASUREMENT_VARIABLES[variable][0]
    return DISPLAY_UNITS.get(unit, unit)


def format_value(variable: str, value: float, signed: bool = False) -> str:
    """A measurement written the way the screen shows it, e.g. ``36.8 °C`` or ``94%``."""
    number = f"{value:+.{DECIMALS[variable]}f}" if signed else f"{value:.{DECIMALS[variable]}f}"
    unit = display_unit(variable)
    if not unit:
        return number
    return f"{number}{unit}" if unit == "%" else f"{number} {unit}"


def describe_feature(name: str, value: float | None) -> str:
    """Plain-language description of one predictor and its current value."""
    missing = value is None or (isinstance(value, float) and np.isnan(value))
    for variable, label in VARIABLE_LABELS.items():
        if name == f"{variable}_last":
            return (
                f"{label} {format_value(variable, value)}"
                if not missing
                else f"{label} not measured"
            )
        if name == f"{variable}_change":
            if missing:
                return f"{label} change unknown"
            return f"{label} change over 6 h: {format_value(variable, value, signed=True)}"
        if name == f"{variable}_hours_since_last":
            return (
                f"{label} last measured {value:.1f} h ago"
                if not missing
                else f"{label} never measured"
            )
        if name == f"{variable}_count":
            return f"{label} measured {int(value)} times in 24 h"
        if name == f"{variable}_last_missing":
            return f"{label} not yet measured"
    fixed = {
        "sofa_24h": lambda v: f"SOFA (last 24 h) {v:g}",
        "age_years": lambda v: f"Age {v:.0f}",
        "male": lambda v: "Male" if v >= 0.5 else "Female",
        "vasopressor_rate": lambda v: (
            f"Vasopressor dose {v:.2f} µg/kg/min" if v > 0 else "No vasopressor dose"
        ),
        "hours_since_first_antibiotic": lambda v: f"Antibiotics started {v:.1f} h ago",
        "hours_since_time_zero": lambda v: f"{v:.0f} h since sepsis recognition",
        "hours_since_icu_admission": lambda v: f"{v:.0f} h since ICU admission",
        "on_vasopressor": lambda v: "On a vasopressor" if v >= 0.5 else "Not on a vasopressor",
        "on_antibiotic": lambda v: "Antibiotic running" if v >= 0.5 else "No antibiotic running",
        "on_supplemental_oxygen": lambda v: "On supplemental oxygen" if v >= 0.5 else "On room air",
        "on_invasive_ventilation": lambda v: (
            "Mechanically ventilated" if v >= 0.5 else "Not ventilated"
        ),
    }
    base = name.removesuffix("_missing")
    if name.endswith("_missing") and base in fixed:
        return f"{base.replace('_', ' ')} unknown"
    if name in fixed and not missing:
        return fixed[name](float(value))
    return name.replace("_", " ")


def describe_missing_indicator(raw_name: str, raw_value: float | None) -> str:
    """Wording for a "not measured" indicator, which can push the risk either way."""
    known = raw_value is not None and not (isinstance(raw_value, float) and np.isnan(raw_value))
    for variable, label in VARIABLE_LABELS.items():
        if raw_name == f"{variable}_change":
            return f"{label} change over 6 h known" if known else f"{label} change over 6 h unknown"
        if raw_name.startswith(variable):
            return f"{label} measured" if known else f"{label} not yet measured"
    readable = raw_name.replace("_", " ")
    return f"{readable} known" if known else f"{readable} unknown"


def _top_drivers(
    model: FittedLandmarkModel, features: pd.DataFrame, count: int = 5
) -> list[list[dict]]:
    contributions = model.contributions(features)
    design_inputs = features.reindex(columns=model.preprocessor.input_columns)
    drivers = []
    for row_position in range(len(features)):
        row = contributions.iloc[row_position]
        chosen = row.abs().sort_values(ascending=False).head(count).index
        entries = []
        for name in chosen:
            raw_name = name.removesuffix("_missing")
            raw_value = (
                design_inputs.iloc[row_position].get(raw_name)
                if raw_name in design_inputs
                else None
            )
            if name.endswith("_missing"):
                description = describe_missing_indicator(raw_name, raw_value)
            else:
                description = describe_feature(
                    name, None if raw_value is None else float(raw_value)
                )
            entries.append({"description": description, "contribution": round(float(row[name]), 3)})
        drivers.append(entries)
    return drivers


def _longest_run(flags: pd.Series) -> tuple[int, int] | None:
    """Start and end positions of the longest run of True values, or None."""
    best, start = None, None
    values = list(flags) + [False]
    for position, flag in enumerate(values):
        if flag and start is None:
            start = position
        elif not flag and start is not None:
            if best is None or position - start > best[1] - best[0] + 1:
                best = (start, position - 1)
            start = None
    return best


def _choose_patients(rows: pd.DataFrame, risk: pd.Series) -> dict[str, int]:
    """Pick four stays that show different situations, deterministically."""
    by_stay = rows.assign(risk=risk).groupby("stay_id")
    summary = pd.DataFrame(
        {
            "hours": by_stay["landmark_hour"].max(),
            "max_risk": by_stay["risk"].max(),
            "ever_shock_stage": by_stay["population"].apply(
                lambda values: (values == SHOCK_STAGE).any()
            ),
            "starts_pre_shock": by_stay["population"].first() == PRE_SHOCK,
            "event": by_stay["event_within_horizon"].max(),
            "vasopressor_hours": by_stay["on_vasopressor"].sum(),
            "first_shock_stage_hour": rows.loc[rows["population"] == SHOCK_STAGE]
            .groupby("stay_id")["landmark_hour"]
            .min(),
        }
    )
    long_enough = summary["hours"] >= 20
    choices: dict[str, int] = {}

    def take(label: str, mask: pd.Series, sort_by: str, ascending: bool) -> None:
        candidates = summary.loc[mask & long_enough & ~summary.index.isin(choices.values())]
        if not candidates.empty:
            choices[label] = int(
                candidates.sort_values([sort_by, "hours"], ascending=[ascending, False]).index[0]
            )

    take(
        "steady",
        summary["starts_pre_shock"] & ~summary["ever_shock_stage"] & (summary["event"] == 0),
        "max_risk",
        True,
    )
    take(
        "deteriorating",
        summary["starts_pre_shock"]
        & summary["ever_shock_stage"]
        & (summary["first_shock_stage_hour"] >= 8),
        "max_risk",
        False,
    )
    take("on_vasopressor", summary["vasopressor_hours"] >= 6, "vasopressor_hours", False)
    take(
        "uncertain",
        summary["starts_pre_shock"] & (summary["max_risk"].between(0.15, 0.45)),
        "hours",
        False,
    )
    return choices


def _default_now(scenario: str, hourly: list[dict]) -> float:
    """The hour the screen opens at: the moment that best shows the scenario."""
    hours = [entry["hour"] for entry in hourly]
    middle = hours[len(hours) // 2]
    if scenario == "deteriorating":
        shock_hours = [entry["hour"] for entry in hourly if entry["population"] == SHOCK_STAGE]
        return max(hours[0], shock_hours[0] - 3) if shock_hours else middle
    if scenario == "on_vasopressor":
        run = _longest_run(pd.Series([entry["on_vasopressor"] for entry in hourly]))
        return hours[(run[0] + run[1]) // 2] if run else middle
    if scenario == "uncertain":
        widths = [entry["risk_interval"][1] - entry["risk_interval"][0] for entry in hourly]
        return hours[int(np.argmax(widths))]
    return middle


def _performance(report: dict) -> dict:
    """Validation results for every number the screen shows, per population (aggregates only)."""
    table: dict = {}
    for population in (PRE_SHOCK, SHOCK_STAGE):
        for target in SCREEN_TARGETS:
            for split in ("development_cv", "temporal_holdout"):
                summary = report["results"].get(population, {}).get(target, {}).get(split)
                if not summary:
                    continue
                d1 = summary["models"]["d1"]
                measures = d1["measures"]
                table.setdefault(population, {}).setdefault(target, {})[split] = {
                    "rows": d1["rows"],
                    "patients": d1["patients"],
                    "observed_rate": d1["observed_rate"],
                    "measures": None
                    if measures is None
                    else {
                        name: measures[name]
                        for name in ("auroc", "calibration_slope", "calibration_intercept", "brier")
                    },
                }
    return table


def export_screen_data(run_directory: Path, output_directory: Path) -> list[Path]:
    """Write ``screen_data.json`` for ``web/index.html``; returns the written paths."""
    run_directory, output_directory = Path(run_directory), Path(output_directory)
    with open(run_directory / "models.pickle", "rb") as file:
        bundle = pickle.load(file)
    models: dict[tuple[str, str], FittedLandmarkModel] = bundle["models"]
    settings: Settings = bundle["settings"]
    report = json.loads((run_directory / "evaluation_report.json").read_text())
    # The report is public-facing input here, so check it again before reusing any of it.
    assert_aggregate_only(report)

    tables = generate_canonical_tables(SCREEN_CANDIDATE_STAYS, seed=SCREEN_SEED)
    cohort = build_cohort(tables, settings.cohort)
    hours = np.arange(0, settings.landmarks.alert_scoring_maximum_hours + 1, 1.0).tolist()
    data = landmark_data(tables, cohort, hours, settings)
    rows, features = data.rows, data.features

    scores = pd.DataFrame(index=rows.index)
    for population in (PRE_SHOCK, SHOCK_STAGE):
        in_population = rows["population"] == population
        if not in_population.any():
            continue
        for target, column in zip(SCREEN_TARGETS, ("risk", "next_hour", "after_hour"), strict=True):
            model = models[(population, target)]
            scores.loc[in_population, column] = model.predict_probability(
                features.loc[in_population]
            )
            if column in ("risk", "after_hour"):
                low, high = model.predict_interval(features.loc[in_population], coverage=0.9)
                scores.loc[in_population, f"{column}_low"] = low
                scores.loc[in_population, f"{column}_high"] = high
        drivers = _top_drivers(
            models[(population, "event_within_horizon")], features.loc[in_population]
        )
        scores.loc[in_population, "drivers"] = pd.Series(
            drivers, index=rows.index[in_population], dtype="object"
        )

    chosen = _choose_patients(rows, scores["risk"])
    patients = []
    for bed_number, (scenario, stay_id) in enumerate(chosen.items(), start=1):
        patients.append(
            _patient_payload(
                f"Bed {bed_number:02d}", scenario, stay_id, tables, cohort, rows, scores
            )
        )

    summary = report.get("model_summary", {})
    payload = {
        "schema_version": 2,
        "model_source": "MIMIC-IV" if report["data_source"] == "mimic_duckdb" else "synthetic data",
        "patients_source": "synthetic",
        "horizon_hours": settings.outcomes.horizon_hours,
        "code_revision": report.get("code_revision", "unknown"),
        "settings_fingerprint": report["settings_fingerprint"],
        "minimum_cell_size": report["minimum_cell_size"],
        "interval_bootstrap_replicates": settings.model.landmark_logistic.bootstrap_replicates,
        "models": {
            key: {
                name: entry[name]
                for name in ("inverse_regularization", "training_rows", "training_events")
            }
            for key, entry in summary.items()
            if key.split("__")[1] in SCREEN_TARGETS
        },
        "performance": _performance(report),
        "calibration_evidence": {
            population: report["results"][population]["event_within_horizon"]["development_cv"][
                "calibration_bands_d1"
            ]
            for population in (PRE_SHOCK, SHOCK_STAGE)
            if report["results"].get(population, {}).get("event_within_horizon")
        },
        "variables": {
            variable: {
                "label": VARIABLE_LABELS[variable],
                "unit": display_unit(variable),
                "decimals": DECIMALS[variable],
                "reference": REFERENCE_VALUES.get(variable),
            }
            for variable in VARIABLE_LABELS
        },
        "evidence": EVIDENCE_TABLE,
        "patients": patients,
    }
    path = output_directory / "screen_data.json"
    write_aggregate_json(
        payload,
        path,
        run_directory / "privacy_log.jsonl",
        "screen data (model aggregates and synthetic patients)",
        compact=True,
    )
    return [path]


def _hours_since(times: pd.Series, origin: pd.Timestamp) -> pd.Series:
    return ((times - origin).dt.total_seconds() / 3600).round(2)


def _patient_payload(
    label: str,
    scenario: str,
    stay_id: int,
    tables,
    cohort,
    rows: pd.DataFrame,
    scores: pd.DataFrame,
) -> dict:
    stay = cohort.stays.set_index("stay_id").loc[stay_id]
    time_zero = stay["time_zero"]
    patient_rows = rows["stay_id"] == stay_id
    hourly = []
    for index in rows.index[patient_rows]:
        score = scores.loc[index]
        hourly.append(
            {
                "hour": float(rows.at[index, "landmark_hour"]),
                "population": rows.at[index, "population"],
                "on_vasopressor": bool(rows.at[index, "on_vasopressor"]),
                "risk": round(float(score["risk"]), 4),
                "risk_interval": [
                    round(float(score["risk_low"]), 4),
                    round(float(score["risk_high"]), 4),
                ],
                "next_hour": round(float(score["next_hour"]), 4),
                "after_hour": round(float(score["after_hour"]), 4),
                "after_hour_interval": [
                    round(float(score["after_hour_low"]), 4),
                    round(float(score["after_hour_high"]), 4),
                ],
                "drivers": score["drivers"],
            }
        )
    measurements = tables.measurements.loc[tables.measurements["stay_id"] == stay_id]
    shown_end = time_zero + pd.Timedelta(hours=max(entry["hour"] for entry in hourly) + 1)
    measurements = measurements.loc[measurements["charttime"] <= shown_end]
    series = {
        variable: [
            [float(hour), round(float(value), 2)]
            for hour, value in zip(
                _hours_since(group["charttime"], time_zero), group["value"], strict=True
            )
        ]
        for variable, group in measurements.groupby("variable")
    }
    treatments = tables.treatments.loc[
        (tables.treatments["stay_id"] == stay_id)
        & (tables.treatments["treatment"] != "norepinephrine")
    ]
    intervals = [
        {
            "treatment": treatment,
            "start": round(float((start - time_zero).total_seconds() / 3600), 2),
            "end": round(float((min(end, shown_end) - time_zero).total_seconds() / 3600), 2),
        }
        for treatment, start, end in treatments[["treatment", "start_time", "end_time"]].itertuples(
            index=False
        )
        if start <= shown_end
    ]
    return {
        "label": label,
        "scenario": scenario,
        "age": int(round(float(stay["age_years"]))),
        "sex": "Female" if stay["sex"] == "F" else "Male",
        "care_unit": str(stay["first_careunit"]),
        "hours_from_icu_admission_to_recognition": round(
            float((time_zero - stay["icu_intime"]).total_seconds() / 3600), 1
        ),
        "default_now_hour": _default_now(scenario, hourly),
        "scores": hourly,
        "measurements": series,
        "treatments": sorted(
            intervals, key=lambda interval: (interval["treatment"], interval["start"])
        ),
    }

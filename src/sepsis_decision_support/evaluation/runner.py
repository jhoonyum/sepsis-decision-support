"""Fit, validate and summarise D1 and the bedside baselines.

Validation design
    development set   all eras except the most recent one
    temporal hold-out the most recent era (2020 - 2022), untouched until the final model
                      is fixed; it plays the role of "next year's patients"
    within development, 5-fold cross-validation grouped by patient: every prediction
    used for development metrics comes from a model that never saw that patient.

Outputs
    report        aggregate-only dictionary (passes the privacy guard)
    models        final models fitted on the whole development set, for the screen
    predictions   patient-level out-of-fold and hold-out predictions; these stay in the
                  run directory on the analyst's machine and are never published
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedGroupKFold

from sepsis_decision_support.config import Settings
from sepsis_decision_support.evaluation.alerts import alert_burden
from sepsis_decision_support.evaluation.metrics import (
    bootstrap_indices,
    calibration_bands,
    calibration_bins,
    decision_curve,
    summarise_predictions,
)
from sepsis_decision_support.models.baselines import RecalibratedScore
from sepsis_decision_support.models.landmark_model import (
    TARGET_DESCRIPTIONS,
    FittedLandmarkModel,
    fit_landmark_model,
)
from sepsis_decision_support.outcomes.labels import PRE_SHOCK, SHOCK_STAGE
from sepsis_decision_support.pipeline import PreparedData
from sepsis_decision_support.privacy.aggregate_guard import (
    complementary_suppression,
    count_cell,
    publishable_flow,
    rate_cell,
    suppressed_label,
)

POPULATIONS = (PRE_SHOCK, SHOCK_STAGE)
BASELINES = ("sofa", "news2")
PRIMARY_TARGET = "event_within_horizon"
AGE_BANDS = [(18, 45, "18-44"), (45, 65, "45-64"), (65, 80, "65-79"), (80, 200, "80+")]


@dataclass
class EvaluationResult:
    report: dict
    models: dict[tuple[str, str], FittedLandmarkModel]
    predictions: pd.DataFrame
    baselines: dict[tuple[str, str, str], RecalibratedScore] = field(default_factory=dict)


def assign_folds(rows: pd.DataFrame, number_of_folds: int, seed: int) -> pd.Series:
    """Fold number per row; all rows of a patient share a fold.

    Folds are stratified on whether the patient ever has the primary event, so that
    each fold has a similar share of patients who progress.
    """
    by_patient = rows.groupby("subject_id")[PRIMARY_TARGET].max().astype(int)
    splitter = StratifiedGroupKFold(n_splits=number_of_folds, shuffle=True, random_state=seed)
    fold_of_patient = pd.Series(-1, index=by_patient.index)
    for fold, (_, test_index) in enumerate(
        splitter.split(
            by_patient.index.to_numpy().reshape(-1, 1),
            by_patient.to_numpy(),
            by_patient.index.to_numpy(),
        )
    ):
        fold_of_patient.iloc[test_index] = fold
    return rows["subject_id"].map(fold_of_patient).astype(int)


def _age_band(age: pd.Series) -> pd.Series:
    labels = pd.Series("unknown", index=age.index, dtype="object")
    for low, high, label in AGE_BANDS:
        labels[(age >= low) & (age < high)] = label
    return labels


def run_evaluation(prepared: PreparedData, settings: Settings) -> EvaluationResult:
    """Cross-validate, fit final models, score the hold-out era and build the report."""
    minimum = settings.privacy.minimum_cell_size
    model_settings = settings.model.landmark_logistic
    rows, features = prepared.training.rows, prepared.training.features

    is_holdout = rows["era"] == settings.evaluation.temporal_holdout_era
    is_development = ~is_holdout
    fold = pd.Series(-1, index=rows.index)
    fold.loc[is_development] = assign_folds(
        rows.loc[is_development], settings.evaluation.folds, settings.seed
    )
    fold_by_patient = (
        fold.loc[is_development].groupby(rows.loc[is_development, "subject_id"]).first()
    )

    predictions = rows[["subject_id", "stay_id", "landmark_hour", "population", "era"]].copy()
    predictions["split"] = np.where(is_holdout, "temporal_holdout", "development_cv")
    models: dict[tuple[str, str], FittedLandmarkModel] = {}
    baselines: dict[tuple[str, str, str], RecalibratedScore] = {}
    fold_models: dict[int, FittedLandmarkModel] = {}
    fold_news2: dict[int, RecalibratedScore] = {}
    results: dict = {}

    for population in POPULATIONS:
        in_population = rows["population"] == population
        results[population] = {}
        for target in TARGET_DESCRIPTIONS:
            outcome = rows[target].astype("float64")
            usable = in_population & outcome.notna()
            development = usable & is_development
            if outcome[development].nunique() < 2:
                continue
            column = f"{population}__{target}"
            predictions[f"{column}__d1"] = np.nan
            for baseline in BASELINES:
                predictions[f"{column}__{baseline}"] = np.nan

            for fold_number in range(settings.evaluation.folds):
                train = development & (fold != fold_number)
                test = development & (fold == fold_number)
                if not test.any() or outcome[train].nunique() < 2:
                    continue
                model = fit_landmark_model(
                    features.loc[train],
                    outcome.loc[train],
                    rows.loc[train, "subject_id"],
                    population,
                    target,
                    model_settings.inverse_regularization_grid,
                    model_settings.inner_folds,
                    bootstrap_replicates=0,
                    seed=settings.seed,
                )
                predictions.loc[test, f"{column}__d1"] = model.predict_probability(
                    features.loc[test]
                )
                for baseline in BASELINES:
                    score = RecalibratedScore.fit(baseline, features.loc[train], outcome.loc[train])
                    predictions.loc[test, f"{column}__{baseline}"] = score.predict_probability(
                        features.loc[test]
                    )
                    if population == PRE_SHOCK and target == PRIMARY_TARGET and baseline == "news2":
                        fold_news2[fold_number] = score
                if population == PRE_SHOCK and target == PRIMARY_TARGET:
                    fold_models[fold_number] = model

            final = fit_landmark_model(
                features.loc[development],
                outcome.loc[development],
                rows.loc[development, "subject_id"],
                population,
                target,
                model_settings.inverse_regularization_grid,
                model_settings.inner_folds,
                bootstrap_replicates=model_settings.bootstrap_replicates,
                seed=settings.seed,
            )
            models[(population, target)] = final
            holdout = usable & is_holdout
            if holdout.any():
                predictions.loc[holdout, f"{column}__d1"] = final.predict_probability(
                    features.loc[holdout]
                )
            for baseline in BASELINES:
                score = RecalibratedScore.fit(
                    baseline, features.loc[development], outcome.loc[development]
                )
                baselines[(population, target, baseline)] = score
                if holdout.any():
                    predictions.loc[holdout, f"{column}__{baseline}"] = score.predict_probability(
                        features.loc[holdout]
                    )

            results[population][target] = _summarise_target(
                rows, outcome, predictions, column, usable, is_development, is_holdout, settings
            )

    report = {
        "schema_version": 1,
        "data_source": settings.data.source,
        "settings_fingerprint": settings.fingerprint(),
        "horizon_hours": settings.outcomes.horizon_hours,
        "minimum_cell_size": minimum,
        "cohort_flow": publishable_flow(prepared.cohort.flow, minimum),
        "landmark_counts": _landmark_counts(rows, settings),
        "results": results,
        "alert_burden": _alert_results(
            prepared, fold_by_patient, fold_models, fold_news2, models, baselines, settings
        ),
        "subgroups": _subgroup_results(rows, predictions, settings),
        "drift_by_era": _era_results(rows, predictions, settings),
        "model_summary": {
            f"{population}__{target}": {
                "description": TARGET_DESCRIPTIONS[target],
                "inverse_regularization": model.inverse_regularization,
                "training_rows": count_cell(model.training_rows, minimum),
                "training_events": count_cell(model.training_events, minimum)
                if model.training_rows - model.training_events >= minimum
                else suppressed_label(minimum),
                "largest_coefficients": [
                    {"feature": name, "coefficient_per_sd": round(float(value), 3)}
                    for name, value in model.coefficients.reindex(
                        model.coefficients.abs().sort_values(ascending=False).index
                    )
                    .head(12)
                    .items()
                ],
            }
            for (population, target), model in models.items()
        },
    }
    return EvaluationResult(
        report=report, models=models, predictions=predictions, baselines=baselines
    )


def _summarise_target(
    rows: pd.DataFrame,
    outcome: pd.Series,
    predictions: pd.DataFrame,
    column: str,
    usable: pd.Series,
    is_development: pd.Series,
    is_holdout: pd.Series,
    settings: Settings,
) -> dict:
    minimum = settings.privacy.minimum_cell_size
    summary: dict = {}
    for split_name, split_mask in (
        ("development_cv", is_development),
        ("temporal_holdout", is_holdout),
    ):
        mask = usable & split_mask & predictions[f"{column}__d1"].notna()
        if not mask.any():
            summary[split_name] = None
            continue
        y = outcome[mask].astype(int).to_numpy()
        groups = rows.loc[mask, "subject_id"].to_numpy()
        samples = bootstrap_indices(groups, settings.evaluation.bootstrap_replicates, settings.seed)
        split_summary: dict = {"models": {}, "by_landmark_hour": {}}
        for model_name in ("d1", *BASELINES):
            probability = predictions.loc[mask, f"{column}__{model_name}"].to_numpy()
            split_summary["models"][model_name] = summarise_predictions(
                y, probability, groups, samples, minimum
            )
        hours = rows.loc[mask, "landmark_hour"]
        for hour in settings.landmarks.reporting_hours:
            at_hour = (hours == hour).to_numpy()
            if not at_hour.any():
                continue
            split_summary["by_landmark_hour"][f"{hour:g}"] = {
                model_name: summarise_predictions(
                    y[at_hour],
                    predictions.loc[mask, f"{column}__{model_name}"].to_numpy()[at_hour],
                    groups[at_hour],
                    None,
                    minimum,
                )
                for model_name in ("d1", *BASELINES)
            }
        d1_probability = predictions.loc[mask, f"{column}__d1"].to_numpy()
        split_summary["calibration_bins_d1"] = calibration_bins(
            y, d1_probability, settings.evaluation.calibration_bins, minimum
        )
        split_summary["calibration_bands_d1"] = calibration_bands(
            y, d1_probability, settings.evaluation.calibration_band_edges, minimum
        )
        split_summary["decision_curve"] = {
            model_name: decision_curve(
                y,
                predictions.loc[mask, f"{column}__{model_name}"].to_numpy(),
                settings.evaluation.decision_curve_thresholds,
            )
            for model_name in ("d1", *BASELINES)
        }
        summary[split_name] = split_summary
    return summary


def _landmark_counts(rows: pd.DataFrame, settings: Settings) -> dict:
    minimum = settings.privacy.minimum_cell_size
    counts: dict = {}
    for population in POPULATIONS:
        counts[population] = {}
        for hour in settings.landmarks.reporting_hours:
            selected = rows.loc[
                (rows["population"] == population) & (rows["landmark_hour"] == hour)
            ]
            counts[population][f"{hour:g}"] = {
                "rows": count_cell(len(selected), minimum),
                "event_within_horizon": rate_cell(
                    int(selected[PRIMARY_TARGET].sum()), len(selected), minimum
                ),
            }
    return counts


def _alert_results(
    prepared: PreparedData,
    fold_by_patient: pd.Series,
    fold_models: dict[int, FittedLandmarkModel],
    fold_news2: dict[int, RecalibratedScore],
    models: dict[tuple[str, str], FittedLandmarkModel],
    baselines: dict[tuple[str, str, str], RecalibratedScore],
    settings: Settings,
) -> dict:
    """Hourly scoring of pre-shock patients with D1 and with recalibrated NEWS2."""
    rows, features = prepared.hourly.rows, prepared.hourly.features
    pre_shock = rows["population"] == PRE_SHOCK
    rows, features = rows.loc[pre_shock], features.loc[pre_shock]
    is_holdout = rows["era"] == settings.evaluation.temporal_holdout_era

    scored = rows[["stay_id", "subject_id", "landmark_time", "next_stage_time"]].copy()
    scored["risk_d1"] = np.nan
    scored["risk_news2"] = np.nan
    # Development patients are scored by the models of the fold that held them out, so
    # neither D1 nor the NEWS2 recalibration has seen them.
    for fold_number, model in fold_models.items():
        in_fold = (~is_holdout) & rows["subject_id"].map(fold_by_patient).eq(fold_number)
        if in_fold.any():
            scored.loc[in_fold, "risk_d1"] = model.predict_probability(features.loc[in_fold])
            if fold_number in fold_news2:
                scored.loc[in_fold, "risk_news2"] = fold_news2[fold_number].predict_probability(
                    features.loc[in_fold]
                )
    final = models.get((PRE_SHOCK, PRIMARY_TARGET))
    news2 = baselines.get((PRE_SHOCK, PRIMARY_TARGET, "news2"))
    if is_holdout.any():
        if final is not None:
            scored.loc[is_holdout, "risk_d1"] = final.predict_probability(features.loc[is_holdout])
        if news2 is not None:
            scored.loc[is_holdout, "risk_news2"] = news2.predict_probability(
                features.loc[is_holdout]
            )

    results: dict = {}
    for split_name, mask in (("development_cv", ~is_holdout), ("temporal_holdout", is_holdout)):
        split_rows = scored.loc[mask & scored["risk_d1"].notna()]
        if split_rows.empty:
            results[split_name] = None
            continue
        results[split_name] = {
            model_name: alert_burden(
                split_rows,
                f"risk_{model_name}",
                settings.evaluation.alert_thresholds,
                settings.outcomes.horizon_hours,
                settings.privacy.minimum_cell_size,
            )
            for model_name in ("d1", "news2")
        }
    return results


def _subgroup_results(rows: pd.DataFrame, predictions: pd.DataFrame, settings: Settings) -> dict:
    """Primary target, pre-shock population, development cross-validation predictions."""
    minimum = settings.privacy.minimum_cell_size
    column = f"{PRE_SHOCK}__{PRIMARY_TARGET}__d1"
    if column not in predictions:
        return {}
    mask = (
        (rows["population"] == PRE_SHOCK)
        & (rows["era"] != settings.evaluation.temporal_holdout_era)
        & predictions[column].notna()
    )
    selected = rows.loc[mask]
    groupings = {
        "sex": selected["sex"].astype("object"),
        "age_band": _age_band(selected["age_years"]),
        "race_group": selected["race_group"].astype("object"),
        "care_unit": selected["first_careunit"].astype("object"),
    }
    outcome = selected[PRIMARY_TARGET].astype(int).to_numpy()
    probability = predictions.loc[mask, column].to_numpy()
    patients = selected["subject_id"].to_numpy()
    results: dict = {}
    for grouping, labels in groupings.items():
        members = {
            str(label): (labels == label).to_numpy() for label in sorted(labels.dropna().unique())
        }
        results[grouping] = _summaries_by_group(members, outcome, probability, patients, minimum)
    return results


def _summaries_by_group(
    members: dict[str, np.ndarray],
    outcome: np.ndarray,
    probability: np.ndarray,
    patients: np.ndarray,
    minimum: int,
) -> dict:
    """Point-estimate summaries for the groups of one partition, with complementary
    suppression so that no hidden group can be recovered from the others and the total."""
    cells = {
        name: (int(inside.sum()), len(np.unique(patients[inside])), int(outcome[inside].sum()))
        for name, inside in members.items()
    }
    hidden = complementary_suppression(cells, minimum)
    label = suppressed_label(minimum)
    summaries = {}
    for name, inside in members.items():
        if name in hidden:
            summaries[name] = {
                "rows": label,
                "patients": label,
                "observed_rate": label,
                "mean_predicted": None,
                "measures": None,
            }
        else:
            summaries[name] = summarise_predictions(
                outcome[inside], probability[inside], patients[inside], None, minimum
            )
    return summaries


def _era_results(rows: pd.DataFrame, predictions: pd.DataFrame, settings: Settings) -> dict:
    minimum = settings.privacy.minimum_cell_size
    column = f"{PRE_SHOCK}__{PRIMARY_TARGET}__d1"
    if column not in predictions:
        return {}
    mask = (rows["population"] == PRE_SHOCK) & predictions[column].notna()
    eras = rows.loc[mask, "era"]
    members = {str(era): (eras == era).to_numpy() for era in sorted(eras.dropna().unique())}
    return _summaries_by_group(
        members,
        rows.loc[mask, PRIMARY_TARGET].astype(int).to_numpy(),
        predictions.loc[mask, column].to_numpy(),
        rows.loc[mask, "subject_id"].to_numpy(),
        minimum,
    )

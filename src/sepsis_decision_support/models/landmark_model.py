"""D1: penalised logistic regression on stacked landmarks.

One model per (population, target). A landmark row is one patient at one decision time,
so a patient contributes several rows; every split and bootstrap therefore works on
patients (``subject_id``), never on rows.

Why logistic regression first?
    It is fast, its coefficients can be read, and each prediction can be broken into
    per-variable contributions for the "what is driving the risk" panel. The Bayesian
    continuous-time HMM (B1) is compared against it under a pre-registered rule.

Preprocessing, fitted on training data only:
    1. every feature with any missing values gets a 0/1 "missing" indicator;
    2. missing values are replaced by the training median;
    3. features are standardised (mean 0, standard deviation 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.model_selection import GroupKFold

TARGET_DESCRIPTIONS: dict[str, str] = {
    "event_within_horizon": "next stage within the horizon (X)",
    "event_within_next_hour": "next stage within the next hour (p1)",
    "event_horizon_after_hour": "next stage within the horizon, starting one hour from now (Z)",
    "death_within_28_days": "death within 28 days",
}


@dataclass
class Preprocessor:
    """Median imputation with missing indicators, then standardisation."""

    input_columns: list[str]
    indicator_columns: list[str]
    medians: pd.Series
    means: pd.Series = field(default_factory=pd.Series)
    scales: pd.Series = field(default_factory=pd.Series)

    @classmethod
    def fit(cls, features: pd.DataFrame) -> Preprocessor:
        usable = [column for column in features.columns if features[column].notna().any()]
        indicators = [column for column in usable if features[column].isna().any()]
        preprocessor = cls(
            input_columns=usable,
            indicator_columns=indicators,
            medians=features[usable].median(),
        )
        imputed = preprocessor._impute(features)
        means = imputed.mean()
        scales = imputed.std(ddof=0)
        # Columns that never vary in the training data carry no information.
        keep = scales[scales > 1e-12].index
        preprocessor.means = means[keep]
        preprocessor.scales = scales[keep]
        return preprocessor

    def _impute(self, features: pd.DataFrame) -> pd.DataFrame:
        values = features.reindex(columns=self.input_columns)
        indicators = values[self.indicator_columns].isna().astype("float64")
        indicators.columns = [f"{column}_missing" for column in self.indicator_columns]
        return pd.concat([values.fillna(self.medians), indicators], axis=1)

    def transform(self, features: pd.DataFrame) -> pd.DataFrame:
        imputed = self._impute(features)[self.means.index]
        return (imputed - self.means) / self.scales

    @property
    def output_columns(self) -> list[str]:
        return list(self.means.index)


@dataclass
class FittedLandmarkModel:
    """A fitted model for one population and one target."""

    population: str
    target: str
    preprocessor: Preprocessor
    intercept: float
    coefficients: pd.Series
    inverse_regularization: float
    training_rows: int
    training_events: int
    bootstrap_intercepts: np.ndarray = field(default_factory=lambda: np.empty(0))
    bootstrap_coefficients: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))

    def linear_predictor(self, features: pd.DataFrame) -> np.ndarray:
        design = self.preprocessor.transform(features)
        return self.intercept + design.to_numpy() @ self.coefficients.to_numpy()

    def predict_probability(self, features: pd.DataFrame) -> np.ndarray:
        """Predicted probability of the target, shape (rows,)."""
        return 1.0 / (1.0 + np.exp(-self.linear_predictor(features)))

    def predict_interval(
        self, features: pd.DataFrame, coverage: float = 0.9
    ) -> tuple[np.ndarray, np.ndarray]:
        """Interval of predicted probabilities across bootstrap refits, shape (rows,) each."""
        if self.bootstrap_coefficients.size == 0:
            point = self.predict_probability(features)
            return point, point
        design = self.preprocessor.transform(features).to_numpy()
        logits = self.bootstrap_intercepts[:, None] + self.bootstrap_coefficients @ design.T
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        tail = (1.0 - coverage) / 2.0
        return np.quantile(probabilities, tail, axis=0), np.quantile(
            probabilities, 1.0 - tail, axis=0
        )

    def contributions(self, features: pd.DataFrame) -> pd.DataFrame:
        """Each feature's contribution to the log-odds, relative to an average patient.

        Shape (rows, model features). Positive values push the risk up. Because the
        design matrix is standardised, zero means "at the training average".
        """
        design = self.preprocessor.transform(features)
        return design * self.coefficients


def _fit_logistic(
    design: pd.DataFrame, outcome: np.ndarray, inverse_regularization: float
) -> LogisticRegression:
    model = LogisticRegression(C=inverse_regularization, max_iter=2000)
    model.fit(design.to_numpy(), outcome)
    return model


def fit_landmark_model(
    features: pd.DataFrame,
    outcome: pd.Series,
    groups: pd.Series,
    population: str,
    target: str,
    inverse_regularization_grid: tuple[float, ...],
    inner_folds: int,
    bootstrap_replicates: int,
    seed: int,
) -> FittedLandmarkModel:
    """Fit D1 for one population and target.

    Args:
        features: predictor matrix (rows = landmark rows of this population).
        outcome: 0/1 labels aligned with ``features``; rows with missing labels are dropped.
        groups: patient identifier per row, used for all resampling.
        inverse_regularization_grid: candidate values of C (smaller = stronger penalty),
            chosen by grouped cross-validated log loss.
        bootstrap_replicates: number of patient-level bootstrap refits used for the
            uncertainty interval shown on the screen (0 to skip).
    """
    usable = outcome.notna()
    features, outcome, groups = (
        features.loc[usable],
        outcome.loc[usable].astype(int),
        groups.loc[usable],
    )
    if outcome.nunique() < 2:
        raise ValueError(f"{population}/{target}: outcome has a single value; cannot fit")

    best_c, best_loss = inverse_regularization_grid[0], np.inf
    folds = min(inner_folds, groups.nunique())
    for candidate in inverse_regularization_grid:
        losses = []
        for train_index, test_index in GroupKFold(n_splits=folds).split(features, outcome, groups):
            train_y, test_y = outcome.iloc[train_index], outcome.iloc[test_index]
            if train_y.nunique() < 2:
                continue
            preprocessor = Preprocessor.fit(features.iloc[train_index])
            model = _fit_logistic(
                preprocessor.transform(features.iloc[train_index]), train_y.to_numpy(), candidate
            )
            predicted = model.predict_proba(
                preprocessor.transform(features.iloc[test_index]).to_numpy()
            )[:, 1]
            losses.append(log_loss(test_y, predicted, labels=[0, 1]))
        if losses and np.mean(losses) < best_loss:
            best_c, best_loss = candidate, float(np.mean(losses))

    preprocessor = Preprocessor.fit(features)
    design = preprocessor.transform(features)
    model = _fit_logistic(design, outcome.to_numpy(), best_c)
    fitted = FittedLandmarkModel(
        population=population,
        target=target,
        preprocessor=preprocessor,
        intercept=float(model.intercept_[0]),
        coefficients=pd.Series(model.coef_[0], index=design.columns),
        inverse_regularization=best_c,
        training_rows=len(outcome),
        training_events=int(outcome.sum()),
    )

    if bootstrap_replicates > 0:
        random = np.random.default_rng(seed)
        unique_groups = groups.unique()
        rows_by_group = (
            pd.Series(np.arange(len(groups)), index=groups.to_numpy()).groupby(level=0).apply(list)
        )
        intercepts, coefficient_rows = [], []
        for _ in range(bootstrap_replicates):
            sampled = random.choice(unique_groups, size=len(unique_groups), replace=True)
            rows = np.concatenate([rows_by_group[group] for group in sampled])
            sampled_y = outcome.to_numpy()[rows]
            if len(np.unique(sampled_y)) < 2:
                continue
            refit = _fit_logistic(design.iloc[rows], sampled_y, best_c)
            intercepts.append(refit.intercept_[0])
            coefficient_rows.append(refit.coef_[0])
        fitted.bootstrap_intercepts = np.asarray(intercepts)
        fitted.bootstrap_coefficients = np.asarray(coefficient_rows)
    return fitted

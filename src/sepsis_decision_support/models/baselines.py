"""Reference scores a new model has to beat: SOFA and NEWS2.

Both are bedside scores that clinicians already know. Each is turned into a probability
by a one-variable logistic regression fitted on the training folds ("recalibration"),
so that Brier scores and calibration can be compared with D1 on equal terms. AUROC is
unchanged by this step.

NEWS2 (Royal College of Physicians, 2017) uses: respiratory rate, SpO2 (scale 1),
supplemental oxygen, systolic blood pressure, pulse, consciousness and temperature.
Consciousness is approximated by GCS < 15 ("new confusion or worse"), because AVPU is not
recorded consistently in ICU data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression


def _points(values: pd.Series, bins: list[float], points: list[int]) -> pd.Series:
    """Score values by interval: ``bins`` are the right edges (inclusive) of each band."""
    scored = pd.Series(
        np.select([values <= edge for edge in bins], points[: len(bins)], default=points[-1]),
        index=values.index,
    )
    return scored.where(values.notna())


def news2_score(features: pd.DataFrame) -> pd.Series:
    """NEWS2 from the landmark features. Missing components count as 0 points."""
    respiratory = _points(features["respiratory_rate_last"], [8, 11, 20, 24], [3, 1, 0, 2, 3])
    oxygen_saturation = _points(features["spo2_last"], [91, 93, 95], [3, 2, 1, 0])
    on_oxygen = (
        (features["on_supplemental_oxygen"] > 0) | (features["on_invasive_ventilation"] > 0)
    ) * 2
    systolic = _points(
        features["systolic_blood_pressure_last"], [90, 100, 110, 219], [3, 2, 1, 0, 3]
    )
    pulse = _points(features["heart_rate_last"], [40, 50, 90, 110, 130], [3, 1, 0, 1, 2, 3])
    consciousness = (features["gcs_total_last"] < 15).astype(float) * 3
    temperature = _points(features["temperature_last"], [35.0, 36.0, 38.0, 39.0], [3, 1, 0, 1, 2])
    components = [
        respiratory,
        oxygen_saturation,
        on_oxygen,
        systolic,
        pulse,
        consciousness,
        temperature,
    ]
    return sum(component.fillna(0) for component in components)


BASELINE_SCORES = {
    "sofa": lambda features: features["sofa_24h"].fillna(0),
    "news2": news2_score,
}


@dataclass
class RecalibratedScore:
    """A bedside score mapped to a probability by logistic regression on training data."""

    name: str
    intercept: float
    slope: float

    @classmethod
    def fit(cls, name: str, features: pd.DataFrame, outcome: pd.Series) -> RecalibratedScore:
        score = BASELINE_SCORES[name](features).to_numpy().reshape(-1, 1)
        model = LogisticRegression(C=1e6, max_iter=1000).fit(score, outcome.astype(int).to_numpy())
        return cls(name=name, intercept=float(model.intercept_[0]), slope=float(model.coef_[0, 0]))

    def predict_probability(self, features: pd.DataFrame) -> np.ndarray:
        score = BASELINE_SCORES[self.name](features).to_numpy()
        return 1.0 / (1.0 + np.exp(-(self.intercept + self.slope * score)))

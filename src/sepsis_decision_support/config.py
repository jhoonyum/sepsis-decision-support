"""Settings for every pipeline step, loaded from YAML and checked before anything runs.

Why validate settings up front?
    A typo such as ``horizon_hour: 24`` would otherwise be silently ignored and the
    default used instead. Pydantic models with ``extra="forbid"`` turn such typos into
    an immediate, readable error.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPOSITORY_ROOT / "configs" / "default.yaml"


class StrictModel(BaseModel):
    """Base class: unknown keys are errors, values are immutable after loading."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SyntheticDataSettings(StrictModel):
    number_of_stays: int = Field(gt=0)


class DataSettings(StrictModel):
    source: Literal["synthetic", "mimic_duckdb"]
    duckdb_path: Path | None = None
    synthetic: SyntheticDataSettings

    @field_validator("duckdb_path")
    @classmethod
    def expand_user_home(cls, path: Path | None) -> Path | None:
        return path.expanduser() if path is not None else None


class RunSettings(StrictModel):
    directory: Path

    @field_validator("directory")
    @classmethod
    def expand_user_home(cls, path: Path) -> Path:
        return path.expanduser()


class CohortSettings(StrictModel):
    name: str
    # How infection is identified and when the stay enters the cohort (time R):
    #   sepsis3                  Sepsis-3 from mimic-code's suspicion-of-infection and SOFA
    #   culture_antibiotic_pair  an ICU antibiotic infusion and an ICU culture within 1 hour
    #                            (the course prototype's definition; sensitivity cohort)
    definition: Literal["sepsis3", "culture_antibiotic_pair"]
    minimum_age_years: float
    first_icu_stay_only: bool
    maximum_hours_from_icu_admission_to_recognition: float
    excluded_hospital_services: tuple[str, ...]
    administrative_censoring_hours: float


class LandmarkSettings(StrictModel):
    training_hours: tuple[float, ...]
    reporting_hours: tuple[float, ...]
    alert_scoring_maximum_hours: float


class SustainedHypotensionSettings(StrictModel):
    map_threshold_mmhg: float
    minimum_duration_minutes: float
    maximum_gap_between_readings_minutes: float
    plausible_map_range_mmhg: tuple[float, float]


class LactateSettings(StrictModel):
    threshold_mmol_per_l: float
    window_hours_before: float
    window_hours_after: float


class OutcomeSettings(StrictModel):
    horizon_hours: float
    # physiological        sustained hypotension with high lactate, no vasopressor in the rule
    # sepsis3_operational  a vasopressor episode with high lactate (sensitivity analysis)
    shock_definition: Literal["physiological", "sepsis3_operational"]
    sustained_hypotension: SustainedHypotensionSettings
    lactate: LactateSettings
    mortality_days: int


class FeatureSettings(StrictModel):
    variables: tuple[str, ...]
    change_window_hours: float
    count_window_hours: float


class LandmarkLogisticSettings(StrictModel):
    inverse_regularization_grid: tuple[float, ...]
    inner_folds: int = Field(ge=2)
    bootstrap_replicates: int = Field(ge=0)


class ModelSettings(StrictModel):
    landmark_logistic: LandmarkLogisticSettings


class EvaluationSettings(StrictModel):
    folds: int = Field(ge=2)
    temporal_holdout_era: str
    bootstrap_replicates: int = Field(ge=0)
    alert_thresholds: tuple[float, ...]
    decision_curve_thresholds: tuple[float, ...]
    calibration_bins: int = Field(ge=2)
    calibration_band_edges: tuple[float, ...]


class PrivacySettings(StrictModel):
    minimum_cell_size: int = Field(ge=1)


class Settings(StrictModel):
    seed: int
    data: DataSettings
    runs: RunSettings
    cohort: CohortSettings
    landmarks: LandmarkSettings
    outcomes: OutcomeSettings
    features: FeatureSettings
    model: ModelSettings
    evaluation: EvaluationSettings
    privacy: PrivacySettings

    def fingerprint(self) -> str:
        """Short hash of the settings that shape the results, stored with every run.

        Machine-specific paths (the run folder and the DuckDB file location) are left out,
        so the same analysis has the same fingerprint on any computer.
        """
        values = self.model_dump(mode="json", exclude={"runs": True, "data": {"duckdb_path"}})
        canonical_json = json.dumps(values, sort_keys=True)
        return hashlib.sha256(canonical_json.encode()).hexdigest()[:12]


def _merge_dictionaries(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base``; values in ``override`` win."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dictionaries(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(*override_paths: Path | str, overrides: dict[str, Any] | None = None) -> Settings:
    """Load ``configs/default.yaml``, apply override files in order, then a dictionary.

    Example:
        >>> settings = load_settings(overrides={"outcomes": {"horizon_hours": 12}})
    """
    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as file:
        values = yaml.safe_load(file)
    for path in override_paths:
        with open(path, encoding="utf-8") as file:
            values = _merge_dictionaries(values, yaml.safe_load(file) or {})
    if overrides:
        values = _merge_dictionaries(values, overrides)
    return Settings.model_validate(values)

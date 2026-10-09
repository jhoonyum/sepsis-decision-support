"""Select the study cohort and set each stay's time zero.

The main cohort ("sepsis3_early", called F-prime in the planning documents):
    adult, first ICU stay of the patient, Sepsis-3 recognised within 24 hours of ICU
    admission, not on a cardiac or thoracic surgery service.

The sensitivity cohort ("culture_antibiotic_pair") uses the same rules with the course
prototype's infection definition instead of Sepsis-3: an ICU antibiotic infusion and an
ICU culture within one hour of each other. Which definition applies is set by
``cohort.definition``; the recognition table holds the matching time R.

Why time zero is the recognition time R and not ICU admission
    A patient only enters the cohort once every criterion is on record. Using an
    earlier time zero would mean predicting for patients we could not yet have known to
    be in the cohort, which is selection on the future. The same reasoning removes the
    course prototype's length-of-stay filter (12 hours to 10 days): stay length is only
    known at discharge.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from sepsis_decision_support.config import CohortSettings
from sepsis_decision_support.data.canonical_tables import CanonicalTables

# How the recognition step reads in the cohort flow, for each infection definition.
RECOGNITION_STEP = {
    "sepsis3": "Sepsis-3 recognised during the ICU stay",
    "culture_antibiotic_pair": "IV antibiotic and culture within 1 h of each other in the ICU",
}


@dataclass
class Cohort:
    """Selected stays and the counts left after each selection step.

    Attributes:
        stays: one row per selected stay with ``time_zero`` and ``administrative_end``.
        flow: (step description, number of stays remaining), in order. These counts are
            record-level-free but still pass through the privacy guard before publishing.
        recognised: the stays left after the recognition step, before the time limit and
            the service rule (the population of the M1 check's time-to-recognition table).
    """

    stays: pd.DataFrame
    flow: list[tuple[str, int]]
    recognised: pd.DataFrame | None = None


def build_cohort(tables: CanonicalTables, settings: CohortSettings) -> Cohort:
    """Apply the cohort rules in ``settings`` to the canonical tables."""
    return select_cohort(tables.stays, tables.recognition, settings)


def select_cohort(
    stays: pd.DataFrame, recognition: pd.DataFrame, settings: CohortSettings
) -> Cohort:
    """Apply the cohort rules to the stays and recognition tables alone.

    The extraction step uses this before reading measurements and treatments, so that
    those large tables are read for the selected stays only.
    """
    stays = stays.merge(recognition[["stay_id", "recognition_time"]], on="stay_id", how="left")
    flow: list[tuple[str, int]] = [("ICU stays", len(stays))]

    def keep(mask: pd.Series, description: str) -> None:
        nonlocal stays
        stays = stays.loc[mask].copy()
        flow.append((description, len(stays)))

    # Without a discharge time there is no way to tell when the patient stopped being at risk.
    keep(stays["icu_outtime"].notna(), "ICU discharge time recorded")
    keep(stays["age_years"] >= settings.minimum_age_years, f"age >= {settings.minimum_age_years:g}")
    if settings.first_icu_stay_only:
        keep(stays["is_first_icu_stay"], "first ICU stay of the patient")
    keep(stays["recognition_time"].notna(), RECOGNITION_STEP[settings.definition])
    recognised = stays.copy()

    hours_to_recognition = (
        stays["recognition_time"] - stays["icu_intime"]
    ).dt.total_seconds() / 3600
    limit = settings.maximum_hours_from_icu_admission_to_recognition
    keep(hours_to_recognition <= limit, f"recognised within {limit:g} h of ICU admission")

    excluded = set(settings.excluded_hospital_services)
    not_excluded = ~stays["hospital_service"].fillna("").isin(excluded)
    keep(not_excluded, f"not on services {', '.join(sorted(excluded))}")

    stays["time_zero"] = stays[["recognition_time", "icu_intime"]].max(axis=1)
    stays["administrative_end"] = stays["time_zero"] + pd.Timedelta(
        hours=settings.administrative_censoring_hours
    )
    # Patients who die or leave the ICU at the very moment of recognition have no
    # decision time left; they stay in the flow table but cannot contribute landmarks.
    stays = stays.sort_values("stay_id").reset_index(drop=True)
    return Cohort(stays=stays, flow=flow, recognised=recognised)

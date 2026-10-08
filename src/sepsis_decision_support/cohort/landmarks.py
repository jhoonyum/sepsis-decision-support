"""Decision times ("landmarks") at which a prediction is made.

A landmark is a (stay, hours after time zero) pair. At landmark s the model may use
everything recorded up to time_zero + s, and nothing after it.

Who is at risk at a landmark?
    Only patients who are still alive and still in the ICU at that moment, and whose
    follow-up has not reached the administrative end. This is the population a bedside
    tool would actually see at that time.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def build_landmarks(cohort_stays: pd.DataFrame, landmark_hours: Sequence[float]) -> pd.DataFrame:
    """One row per (stay, landmark hour) for patients still at risk.

    Args:
        cohort_stays: output of ``build_cohort(...).stays``; needs ``time_zero``,
            ``icu_outtime``, ``death_time``, ``administrative_end`` and identifiers.
        landmark_hours: hours after time zero, for example ``[0, 6, 12, 24, 48]``.

    Returns:
        DataFrame with columns ``stay_id``, ``subject_id``, ``landmark_hour`` and
        ``landmark_time`` plus the stay columns needed later.
    """
    columns = [
        "stay_id",
        "subject_id",
        "time_zero",
        "icu_intime",
        "icu_outtime",
        "death_time",
        "date_of_death",
        "administrative_end",
        "age_years",
        "sex",
        "race_group",
        "first_careunit",
        "era",
    ]
    stays = cohort_stays.loc[:, columns]
    hours = pd.DataFrame({"landmark_hour": np.asarray(landmark_hours, dtype="float64")})
    landmarks = stays.merge(hours, how="cross")
    landmarks["landmark_time"] = landmarks["time_zero"] + pd.to_timedelta(
        landmarks["landmark_hour"], unit="h"
    )

    still_in_icu = landmarks["landmark_time"] < landmarks["icu_outtime"]
    still_alive = landmarks["death_time"].isna() | (
        landmarks["landmark_time"] < landmarks["death_time"]
    )
    within_follow_up = landmarks["landmark_time"] <= landmarks["administrative_end"]
    landmarks = landmarks.loc[still_in_icu & still_alive & within_follow_up]
    return landmarks.sort_values(["stay_id", "landmark_hour"]).reset_index(drop=True)

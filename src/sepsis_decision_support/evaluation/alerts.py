"""What would it be like to run the model as an alert? Burden, yield and lead time.

Health systems judge a deterioration model less by AUROC than by what it asks of staff:
how many alerts per patient-day, how many of them are followed by the event, how many
events are caught, and how early.

Setting
    Every pre-shock patient is scored once an hour from time zero up to
    ``alert_scoring_maximum_hours`` (or until they leave the pre-shock population).
    An alert fires when the hourly risk rises to or above the threshold. While the risk
    stays above the threshold, the alert repeats once per horizon (a patient who stays
    high is re-flagged daily, as a real system would), and it fires again at once if the
    risk falls below the threshold and rises again.

Measures per threshold
    alerts per 100 patient-days   alerts / scored patient-days * 100
    positive predictive value     share of alerts followed by the next stage within the horizon
    sensitivity                   share of next-stage events preceded by an alert within the
                                  horizon before them (events inside the scored period only)
    workup-to-detection ratio     alerts per detected event
    lead time                     hours from the first alert in the horizon to the event
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from sepsis_decision_support.privacy.aggregate_guard import count_cell, rate_cell


def alert_burden(
    hourly: pd.DataFrame,
    risk_column: str,
    thresholds: tuple[float, ...],
    horizon_hours: float,
    minimum_cell_size: int,
) -> list[dict]:
    """Alert measures for each threshold.

    Args:
        hourly: one row per pre-shock patient-hour with ``stay_id``, ``landmark_time``,
            ``next_stage_time`` and the risk column.
        risk_column: name of the predicted-risk column.
        thresholds: risk thresholds to evaluate.
        horizon_hours: an alert counts as a true alert if the next stage follows within this.
    """
    hourly = hourly.sort_values(["stay_id", "landmark_time"]).reset_index(drop=True)
    horizon = pd.Timedelta(hours=horizon_hours)
    patient_days = len(hourly) / 24.0
    time_to_event = hourly["next_stage_time"] - hourly["landmark_time"]
    followed_by_event = (time_to_event > pd.Timedelta(0)) & (time_to_event <= horizon)

    last_scored = hourly.groupby("stay_id")["landmark_time"].max()
    events = hourly.groupby("stay_id")["next_stage_time"].first().dropna()
    first_scored = hourly.groupby("stay_id")["landmark_time"].min()
    # Only events that happen while the patient is being scored (or within the horizon
    # after the last score) can be caught.
    catchable = events[
        (events > first_scored.reindex(events.index))
        & (events <= last_scored.reindex(events.index) + horizon)
    ]

    results = []
    for threshold in thresholds:
        alert_rows = hourly.loc[
            _alert_times(hourly, hourly[risk_column] >= threshold, horizon),
            ["stay_id", "landmark_time"],
        ]
        number_of_alerts = len(alert_rows)
        true_alerts = int(followed_by_event.loc[alert_rows.index].sum())

        paired = alert_rows.merge(
            catchable.rename("event_time"), left_on="stay_id", right_index=True
        )
        lead = paired["event_time"] - paired["landmark_time"]
        in_window = paired.loc[(lead > pd.Timedelta(0)) & (lead <= horizon)]
        first_alert_lead = (
            in_window.groupby("stay_id")["event_time"].first()
            - in_window.groupby("stay_id")["landmark_time"].min()
        ).dt.total_seconds() / 3600
        detected = len(first_alert_lead)
        lead_quartiles = (
            [round(float(value), 1) for value in np.quantile(first_alert_lead, [0.25, 0.5, 0.75])]
            if detected >= minimum_cell_size
            else None
        )
        results.append(
            {
                "threshold": threshold,
                "alerts": count_cell(number_of_alerts, minimum_cell_size),
                "alerts_per_100_patient_days": round(100 * number_of_alerts / patient_days, 1)
                if number_of_alerts >= minimum_cell_size
                else None,
                "positive_predictive_value": rate_cell(
                    true_alerts, number_of_alerts, minimum_cell_size
                ),
                "sensitivity": rate_cell(detected, len(catchable), minimum_cell_size),
                "workup_to_detection_ratio": round(number_of_alerts / detected, 1)
                if detected >= minimum_cell_size
                else None,
                "lead_time_hours_quartiles": lead_quartiles,
            }
        )
    return results


def _alert_times(hourly: pd.DataFrame, above: pd.Series, repeat_after: pd.Timedelta) -> np.ndarray:
    """Boolean mask of rows where an alert fires (rows sorted by stay and time)."""
    fires = np.zeros(len(hourly), dtype=bool)
    stay_ids = hourly["stay_id"].to_numpy()
    times = hourly["landmark_time"].to_numpy()
    above_values = above.to_numpy()
    last_alert_time = None
    was_above = False
    for row in range(len(hourly)):
        if row == 0 or stay_ids[row] != stay_ids[row - 1]:
            last_alert_time, was_above = None, False
        if above_values[row]:
            rising = not was_above
            due_again = last_alert_time is not None and times[row] - last_alert_time >= repeat_after
            if rising or due_again:
                fires[row] = True
                last_alert_time = times[row]
        was_above = bool(above_values[row])
    return fires

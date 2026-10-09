"""Figures drawn only from the aggregate report, never from patient-level data.

Because every figure is rebuilt from ``evaluation_report.json``, a figure cannot show
anything the privacy guard did not already allow. Each figure has a table twin in the
Markdown report, so no value is available only as colour.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from sepsis_decision_support.visualization.style import (
    BASELINE,
    REFERENCE_COLOR,
    apply_style,
    series_line,
)

PRIMARY = ("pre_shock", "event_within_horizon")


def _save(figure, directory: Path, name: str) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for extension in ("png", "svg"):
        path = directory / f"{name}.{extension}"
        figure.savefig(path, dpi=160, bbox_inches="tight")
        paths.append(path)
    plt.close(figure)
    return paths


def _primary(report: dict) -> dict:
    population, target = PRIMARY
    return report["results"][population][target]


def calibration_figure(report: dict, directory: Path) -> list[Path]:
    """Observed against predicted risk in bins, development and temporal hold-out."""
    figure, axis = plt.subplots(figsize=(5.2, 4.6))
    largest = 0.0
    for split in ("development_cv", "temporal_holdout"):
        summary = _primary(report).get(split)
        if not summary:
            continue
        points = [
            (bin_["predicted_mean"], bin_["observed"]["rate"])
            for bin_ in summary["calibration_bins_d1"]
            if isinstance(bin_["observed"], dict)
        ]
        if not points:
            continue
        x, y = zip(*points, strict=True)
        series_line(axis, x, y, split)
        largest = max(largest, max(x), max(y))
    limit = min(1.0, largest * 1.1 + 0.02)
    axis.plot(
        [0, limit],
        [0, limit],
        color=REFERENCE_COLOR,
        linewidth=1.0,
        zorder=1,
        label="Perfect calibration",
    )
    axis.set_xlim(0, limit)
    axis.set_ylim(0, limit)
    axis.set_xlabel("Predicted risk of progression within 24 h")
    axis.set_ylabel("Observed share who progressed")
    axis.set_title("Calibration, pre-shock patients")
    axis.legend(loc="upper left")
    return _save(figure, directory, "calibration")


def decision_curve_figure(report: dict, directory: Path) -> list[Path]:
    """Net benefit of the model, the bedside scores and treating everyone."""
    summary = _primary(report)["development_cv"]
    curves = summary["decision_curve"]
    figure, axis = plt.subplots(figsize=(6.0, 4.4))
    thresholds = [row["threshold"] for row in curves["d1"]]
    for key in ("d1", "news2", "sofa"):
        values = [row["net_benefit_model"] for row in curves[key]]
        # Lines converge at high thresholds, so the legend carries identity instead of end labels.
        series_line(axis, thresholds, values, key)
    treat_all = [row["net_benefit_treat_all"] for row in curves["d1"]]
    axis.plot(
        thresholds,
        treat_all,
        color=REFERENCE_COLOR,
        linewidth=1.2,
        label="Treat everyone",
        zorder=2,
    )
    axis.axhline(0, color=BASELINE, linewidth=1.0, label="Treat no one", zorder=1)
    lowest = min(
        min(row["net_benefit_model"] for row in curves[key]) for key in ("d1", "news2", "sofa")
    )
    axis.set_ylim(min(lowest, 0) - 0.01, None)
    axis.set_xlabel("Risk threshold for acting")
    axis.set_ylabel("Net benefit")
    axis.set_title("Decision curve, development (cross-validated)")
    axis.legend(loc="upper right")
    return _save(figure, directory, "decision_curve")


def alert_tradeoff_figure(report: dict, directory: Path) -> list[Path]:
    """Alerts per 100 patient-days against the share of events caught, per threshold."""
    alerts = report["alert_burden"].get("development_cv")
    if not alerts:
        return []
    figure, axis = plt.subplots(figsize=(6.0, 4.4))
    for key in ("d1", "news2"):
        points = [
            (row["alerts_per_100_patient_days"], row["sensitivity"]["rate"], row["threshold"])
            for row in alerts[key]
            if row["alerts_per_100_patient_days"] is not None
            and isinstance(row["sensitivity"], dict)
        ]
        if not points:
            continue
        x, y, thresholds = zip(*points, strict=True)
        series_line(axis, x, y, key)
        if key == "d1":
            for x_value, y_value, threshold in points:
                axis.annotate(
                    f"{threshold:.0%}",
                    (x_value, y_value),
                    xytext=(0, 8),
                    textcoords="offset points",
                    ha="center",
                    fontsize=8,
                    color="#52514e",
                )
    axis.set_xlabel("Alerts per 100 patient-days")
    axis.set_ylabel("Share of events preceded by an alert within 24 h")
    axis.set_ylim(0, 1.05)
    axis.set_title("Alert burden against events caught (labels: D1 threshold)")
    axis.legend(loc="lower right")
    return _save(figure, directory, "alert_tradeoff")


def performance_by_hour_figure(report: dict, directory: Path) -> list[Path]:
    """AUROC at each reporting landmark hour for the model and the bedside scores."""
    by_hour = _primary(report)["development_cv"]["by_landmark_hour"]
    hours = sorted(by_hour, key=float)
    figure, axis = plt.subplots(figsize=(6.0, 4.0))
    for key in ("d1", "news2", "sofa"):
        points = [
            (float(hour), by_hour[hour][key]["measures"]["auroc"]["estimate"])
            for hour in hours
            if by_hour[hour][key]["measures"] is not None
        ]
        if points:
            x, y = zip(*points, strict=True)
            series_line(axis, x, y, key)
    axis.axhline(0.5, color=BASELINE, linewidth=1.0, zorder=1)
    axis.set_ylim(0.45, 1.0)
    axis.set_xlabel("Hours after sepsis recognition")
    axis.set_ylabel("AUROC")
    axis.set_title("Discrimination by decision time, development (cross-validated)")
    axis.legend(loc="lower left")
    return _save(figure, directory, "performance_by_hour")


def subgroup_figure(report: dict, directory: Path) -> list[Path]:
    """AUROC by subgroup (one series, so no legend; suppressed groups are listed as such)."""
    labels, values = [], []
    for grouping, groups in report["subgroups"].items():
        for name, summary in groups.items():
            rows = summary["rows"]
            size = f"n = {rows:,} landmarks" if isinstance(rows, int) else str(rows)
            labels.append(f"{grouping.replace('_', ' ')}: {name} ({size})")
            measures = summary["measures"]
            values.append(measures["auroc"]["estimate"] if measures else None)
    if not labels:
        return []
    figure, axis = plt.subplots(figsize=(6.0, 0.32 * len(labels) + 1.2))
    positions = range(len(labels))
    shown = [
        (position, value)
        for position, value in zip(positions, values, strict=True)
        if value is not None
    ]
    if shown:
        y, x = zip(*shown, strict=True)
        axis.scatter(x, y, s=64, color="#2a78d6", edgecolors="#ffffff", linewidths=1.5, zorder=3)
    for position, value in zip(positions, values, strict=True):
        if value is None:
            axis.annotate(
                "not reported (too few events, or hidden with a small group)",
                (0.5, position),
                fontsize=8,
                color="#898781",
                va="center",
            )
    axis.set_yticks(list(positions), labels)
    axis.invert_yaxis()
    axis.set_xlim(0.5, 1.0)
    axis.set_xlabel("AUROC (development, cross-validated)")
    axis.set_title("Discrimination by subgroup, pre-shock patients (estimates; small groups vary)")
    return _save(figure, directory, "subgroups")


def drift_figure(report: dict, directory: Path) -> list[Path]:
    """Discrimination and calibration-in-the-large by era, as two small charts (never a dual axis)."""
    eras = [era for era, summary in report["drift_by_era"].items() if summary["measures"]]
    if not eras:
        return []
    auroc = [report["drift_by_era"][era]["measures"]["auroc"]["estimate"] for era in eras]
    intercept = [
        report["drift_by_era"][era]["measures"]["calibration_intercept"]["estimate"] for era in eras
    ]
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(6.0, 5.2), sharex=True)
    positions = list(range(len(eras)))
    series_line(top, positions, auroc, "d1", label="AUROC")
    top.set_ylabel("AUROC")
    top.set_ylim(0.5, 1.0)
    top.set_title("Performance by era (last era = temporal hold-out)")
    series_line(bottom, positions, intercept, "d1", label="Calibration intercept")
    bottom.axhline(0, color=BASELINE, linewidth=1.0)
    bottom.set_ylabel("Calibration intercept\n(> 0: model under-predicts)")
    bottom.set_xticks(positions, eras)
    return _save(figure, directory, "drift_by_era")


def draw_all(report: dict, directory: Path) -> list[Path]:
    """Draw every report figure into ``directory``; returns the written paths."""
    apply_style()
    paths: list[Path] = []
    for function in (
        calibration_figure,
        decision_curve_figure,
        alert_tradeoff_figure,
        performance_by_hour_figure,
        subgroup_figure,
        drift_figure,
    ):
        paths.extend(function(report, Path(directory)))
    return paths

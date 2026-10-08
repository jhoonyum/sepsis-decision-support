"""A readable Markdown version of the aggregate report, with the table twin of every figure.

Every number here is read from ``evaluation_report.json``; nothing is typed by hand.
"""

from __future__ import annotations

from sepsis_decision_support.models.landmark_model import TARGET_DESCRIPTIONS
from sepsis_decision_support.visualization.style import SERIES_LABELS

POPULATION_TITLES = {
    "pre_shock": "Pre-shock patients (next stage = shock or death)",
    "shock_stage": "Patients on a vasopressor or after shock (next stage = death)",
}


def _cell(value) -> str:
    if value is None:
        return "–"
    if isinstance(value, dict) and "rate" in value:
        return f"{value['rate']:.1%} ({value['numerator']:,}/{value['denominator']:,})"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _measure(summary: dict | None, name: str) -> str:
    if not summary or not summary.get("measures"):
        return "too few events"
    measure = summary["measures"][name]
    interval = measure["interval_95"]
    if interval:
        return f"{measure['estimate']:.3f} ({interval[0]:.3f}–{interval[1]:.3f})"
    return f"{measure['estimate']:.3f}"


def render_markdown(report: dict, figure_directory_name: str = "figures") -> str:
    lines: list[str] = []
    add = lines.append
    source = (
        "synthetic patients (no real data)" if report["data_source"] == "synthetic" else "MIMIC-IV"
    )
    add("# Evaluation report")
    add("")
    add(f"- Data: {source}")
    add(f"- Settings fingerprint: `{report['settings_fingerprint']}`")
    add(f"- Horizon: {report['horizon_hours']:g} hours")
    add(
        f"- Cells below {report['minimum_cell_size']} are shown as `<{report['minimum_cell_size']}`; "
        "small bins and cohort steps are merged with their neighbours instead."
    )
    add(
        "- Overall measures carry 95% patient-bootstrap intervals; results by decision time, "
        "subgroup and era are point estimates."
    )
    add("")
    add("## Cohort")
    add("")
    add("| Step | ICU stays remaining |")
    add("|---|---|")
    for step in report["cohort_flow"]:
        add(f"| {step['step']} | {_cell(step['stays'])} |")
    add("")
    add("## Main result: pre-shock patients, next stage within the horizon")
    add("")
    add(f"![Calibration]({figure_directory_name}/calibration.png)")
    add("")
    main = report["results"]["pre_shock"]["event_within_horizon"]
    _calibration_tables(main, add)
    for population, targets in report["results"].items():
        add(f"## {POPULATION_TITLES.get(population, population)}")
        add("")
        add(
            "| Target | Split | Model | AUROC | AUPRC | Brier | Calibration slope | "
            "Calibration intercept | ICI | Observed rate |"
        )
        add("|---|---|---|---|---|---|---|---|---|---|")
        for target, splits in targets.items():
            for split_name, split in splits.items():
                if not split:
                    continue
                for model_name, summary in split["models"].items():
                    add(
                        f"| {TARGET_DESCRIPTIONS[target]} | {split_name.replace('_', ' ')} | "
                        f"{SERIES_LABELS.get(model_name, model_name)} | {_measure(summary, 'auroc')} | "
                        f"{_measure(summary, 'auprc')} | {_measure(summary, 'brier')} | "
                        f"{_measure(summary, 'calibration_slope')} | "
                        f"{_measure(summary, 'calibration_intercept')} | "
                        f"{_measure(summary, 'integrated_calibration_index')} | "
                        f"{_cell(summary['observed_rate'])} |"
                    )
        add("")
    add("## Discrimination by decision time")
    add("")
    add(f"![AUROC by hour]({figure_directory_name}/performance_by_hour.png)")
    add("")
    primary = report["results"]["pre_shock"]["event_within_horizon"]["development_cv"]
    add("| Hours after recognition | D1 | NEWS2 | SOFA |")
    add("|---|---|---|---|")
    for hour, models in sorted(
        primary["by_landmark_hour"].items(), key=lambda item: float(item[0])
    ):
        add(
            f"| {hour} | {_measure(models['d1'], 'auroc')} | {_measure(models['news2'], 'auroc')} | {_measure(models['sofa'], 'auroc')} |"
        )
    add("")
    add("## Decision curve")
    add("")
    add(f"![Decision curve]({figure_directory_name}/decision_curve.png)")
    add("")
    add("| Threshold | D1 | NEWS2 | SOFA | Treat everyone |")
    add("|---|---|---|---|---|")
    curves = primary["decision_curve"]
    for index, row in enumerate(curves["d1"]):
        add(
            f"| {row['threshold']:.0%} | {row['net_benefit_model']:.4f} | {curves['news2'][index]['net_benefit_model']:.4f} | "
            f"{curves['sofa'][index]['net_benefit_model']:.4f} | {row['net_benefit_treat_all']:.4f} |"
        )
    add("")
    add("## Alert burden (hourly scoring of pre-shock patients)")
    add("")
    add(f"![Alert trade-off]({figure_directory_name}/alert_tradeoff.png)")
    add("")
    for split_name, split in report["alert_burden"].items():
        if not split:
            continue
        add(f"### {split_name.replace('_', ' ').capitalize()}")
        add("")
        add(
            "| Model | Threshold | Alerts per 100 patient-days | PPV | Sensitivity | Alerts per detected event | Lead time, h (quartiles) |"
        )
        add("|---|---|---|---|---|---|---|")
        for model_name, rows in split.items():
            for row in rows:
                lead = row["lead_time_hours_quartiles"]
                add(
                    f"| {SERIES_LABELS.get(model_name, model_name)} | {row['threshold']:.0%} | "
                    f"{_cell(row['alerts_per_100_patient_days'])} | {_cell(row['positive_predictive_value'])} | "
                    f"{_cell(row['sensitivity'])} | {_cell(row['workup_to_detection_ratio'])} | "
                    f"{' / '.join(f'{value:g}' for value in lead) if lead else '–'} |"
                )
        add("")
    add("## Subgroups")
    add("")
    add(f"![Subgroups]({figure_directory_name}/subgroups.png)")
    add("")
    add("| Group | Rows | Observed rate | AUROC | Calibration intercept |")
    add("|---|---|---|---|---|")
    for grouping, groups in report["subgroups"].items():
        for name, summary in groups.items():
            add(
                f"| {grouping.replace('_', ' ')}: {name} | {_cell(summary['rows'])} | {_cell(summary['observed_rate'])} | "
                f"{_measure(summary, 'auroc')} | {_measure(summary, 'calibration_intercept')} |"
            )
    add("")
    add("## Drift by era")
    add("")
    add(f"![Drift]({figure_directory_name}/drift_by_era.png)")
    add("")
    add("| Era | Rows | Observed rate | AUROC | Calibration intercept |")
    add("|---|---|---|---|---|")
    for era, summary in report["drift_by_era"].items():
        add(
            f"| {era} | {_cell(summary['rows'])} | {_cell(summary['observed_rate'])} | {_measure(summary, 'auroc')} | "
            f"{_measure(summary, 'calibration_intercept')} |"
        )
    add("")
    return "\n".join(lines)


def _calibration_tables(main: dict, add) -> None:
    """Table twins of the calibration figure (deciles) and of the screen's risk bands."""
    splits = [(name, main.get(name)) for name in ("development_cv", "temporal_holdout")]
    splits = [(name, split) for name, split in splits if split]
    add("Calibration by tenth of predicted risk (the figure above):")
    add("")
    add("| Split | Predicted range | Mean predicted | Observed |")
    add("|---|---|---|---|")
    for name, split in splits:
        for bin_ in split["calibration_bins_d1"]:
            add(
                f"| {name.replace('_', ' ')} | {bin_['predicted_low']:.1%}–{bin_['predicted_high']:.1%} | "
                f"{bin_['predicted_mean']:.1%} | {_cell(bin_['observed'])} |"
            )
    add("")
    add("Calibration by fixed risk band (used on the screen):")
    add("")
    add("| Split | Band | Decision points | Mean predicted | Observed |")
    add("|---|---|---|---|---|")
    for name, split in splits:
        for band in split.get("calibration_bands_d1", []):
            mean = band["predicted_mean"]
            add(
                f"| {name.replace('_', ' ')} | {band['predicted_low']:.0%}–{band['predicted_high']:.0%} | "
                f"{_cell(band['rows'])} | {f'{mean:.1%}' if mean is not None else '–'} | "
                f"{_cell(band['observed'])} |"
            )
    add("")

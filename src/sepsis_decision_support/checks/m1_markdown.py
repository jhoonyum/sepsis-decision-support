"""A readable Markdown version of the M1 check, built only from ``m1_check.json``."""

from __future__ import annotations

DEFINITION_TITLES = {
    "sepsis3": "Main cohort: Sepsis-3",
    "culture_antibiotic_pair": "Sensitivity cohort: antibiotic and culture within 1 h",
}


def _number(value) -> str:
    if isinstance(value, str) and value.startswith("withheld"):
        return "withheld"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _signed(value) -> str:
    return f"{value:+,}" if isinstance(value, int) else _number(value)


def _exact_rate(cell) -> str:
    if isinstance(cell, str):
        return _number(cell)
    if isinstance(cell, dict):
        return f"{cell['rate']:.1%} ({cell['numerator']:,} of {cell['denominator']:,})"
    return str(cell)


def _rounded_rate(cell) -> str:
    if isinstance(cell, dict):
        numerator, denominator = cell["numerator_rounded"], cell["denominator_rounded"]
        return f"{cell['rate']:.1%} (~{numerator:,} of ~{denominator:,})"
    return str(cell)


def _rounded(value) -> str:
    return f"~{value:,}" if isinstance(value, int) else str(value)


def render_m1_markdown(report: dict) -> str:
    lines: list[str] = []
    add = lines.append
    minimum = report["minimum_cell_size"]
    database = report["database_counts"]
    demo = database["demo"]

    add("# M1 check")
    add("")
    add(f"- Database file: `{report['database']}`" + (" (the open MIMIC-IV demo)" if demo else ""))
    add(
        f"- Code revision `{report.get('code_revision', 'unknown')}`, settings fingerprint `{report['settings_fingerprint']}`, created {report.get('created_utc', 'unknown')}"
    )
    add(
        f"- Counts below {minimum} are shown as `<{minimum}`; in the criterion tables, `hidden` "
        f"marks groups below {minimum} and the groups hidden with them so that a small group "
        "cannot be worked out from the total. Flows and time bins are merged "
        f"instead of hidden. Counts marked `~` are rounded to the nearest {report['rounding_base']}, "
        "and their rates are computed from the rounded counts."
    )
    if demo:
        add(
            "- This is the 100-patient demo: the comparisons with full-database figures are "
            "expected to fail and say nothing about the build."
        )
    add("")

    reference = report["sepsis3_reference"]
    funnel = report["course_funnel"]
    add("## Gate summary")
    add("")
    add("| Criterion | Result |")
    add("|---|---|")
    add(
        f"| Table sizes match mimic-code's expected counts for MIMIC-IV 3.1 | {_number(database['all_match'])} |"
    )
    add(f"| Course prototype's funnel reproduced exactly | {_number(funnel['all_match'])} |")
    add(
        "| Sepsis-3 among first ICU stays, against the published count | "
        f"{_number(reference['first_stays_with_sepsis3'])} here, "
        f"{reference['reference']['sepsis3']:,} published "
        f"(difference {_signed(reference['difference_from_reference'])}); explain in the analysis plan |"
    )
    add("")

    add("## Database")
    add("")
    add("| Table | Rows | Expected for 3.1 | Match |")
    add("|---|---|---|---|")
    for row in database["tables"]:
        add(
            f"| {row['table']} | {_number(row['rows'])} | {row['expected_for_mimic_iv_3_1']:,} | "
            f"{_number(row['matches'])} |"
        )
    add("")
    add(f"Patients with at least one ICU stay: {_number(database['patients_with_icu_stay'])}.")
    add("")

    add("## Sepsis-3 among first ICU stays")
    add("")
    published = reference["reference"]
    add("| | This database | Published |")
    add("|---|---|---|")
    add(
        f"| First ICU stays | {_number(reference['first_icu_stays'])} | {published['first_icu_stays']:,} |"
    )
    add(
        f"| With Sepsis-3 | {_number(reference['first_stays_with_sepsis3'])} | {published['sepsis3']:,} |"
    )
    add(
        f"| Share | {_exact_rate(reference['share_of_first_stays'])} | "
        f"{published['share_of_first_stays']:.1%} |"
    )
    add("")
    add(f"- Published figure: {published['source']}.")
    add(f"- Counted from {reference['counted_from']}.")
    agreement = reference["project_minus_mimic_code"]
    if agreement == 0:
        add(
            "- The project's recognition rule selects the same stays as mimic-code's sepsis3 table."
        )
    elif isinstance(agreement, str) and agreement.startswith("withheld"):
        add("- Agreement between the project's recognition table and mimic-code's: withheld.")
    else:
        add(
            f"- The project's recognition table minus mimic-code's sepsis3 table: {_signed(agreement)} stays."
        )
    add("")

    add("## Course prototype funnel")
    add("")
    add("| Step | This database | Prototype | Match |")
    add("|---|---|---|---|")
    for step in funnel["steps"]:
        add(
            f"| {step['step']} | {_number(step['stays'])} | {step['prototype']:,} | {_number(step['matches'])} |"
        )
    add("")
    variant = funnel["culture_at_any_time_minus_culture_in_icu"]
    add(
        "- Without the rule that the culture is charted inside the ICU stay (the prototype's "
        f"script): {_signed(variant)} stays compared with the last step."
    )
    add("")

    cohorts = report["cohorts"]
    rules = cohorts["rules"]
    add("## Cohorts")
    add("")
    add(
        f"Both cohorts: age at least {rules['minimum_age_years']:g}, "
        + ("first ICU stay of the patient, " if rules["first_icu_stay_only"] else "")
        + f"recognised within {rules['maximum_hours_from_icu_admission_to_recognition']:g} h of ICU admission, "
        f"services {', '.join(rules['excluded_hospital_services'])} excluded."
    )
    add("")
    for definition, section in cohorts["definitions"].items():
        add(f"### {DEFINITION_TITLES.get(definition, definition)}")
        add("")
        add("| Step | ICU stays remaining |")
        add("|---|---|")
        for step in section["flow"]:
            add(f"| {step['step']} | {_number(step['stays'])} |")
        add("")
        add("Hours from ICU admission to recognition (before the time limit):")
        add("")
        bins = section["hours_from_icu_admission_to_recognition"]
        if isinstance(bins, str):
            add(f"{bins}.")
        else:
            add("| Hours | Stays |")
            add("|---|---|")
            for row in bins:
                add(f"| {row['hours']} | {_number(row['stays'])} |")
        add("")
        add("Criterion met last, at recognition (final cohort):")
        add("")
        criteria = section["criterion_that_completed_recognition"]
        if isinstance(criteria, str):
            add(f"{_number(criteria)}.")
        else:
            add("| Criterion | Stays |")
            add("|---|---|")
            for criterion, count in criteria.items():
                add(f"| {criterion} | {_number(count)} |")
        add("")
    add("### Overlap of the final cohorts")
    add("")
    overlap = cohorts["overlap"]
    if isinstance(overlap, dict):
        add("| | Stays |")
        add("|---|---|")
        for group, count in overlap.items():
            add(f"| {group} | {count:,} |")
    else:
        add(f"{_number(overlap) if overlap.startswith('withheld') else overlap}.")
    add("")

    outcomes = report["outcome_definitions"]
    add("## Outcome definitions")
    add("")
    add(
        f"Population: {outcomes['population']}. Horizon {outcomes['horizon_hours']:g} h. "
        "Each row changes one thing in the first row's definition."
    )
    add("")
    add(
        "| Definition | Patients at time zero | In shock stage at time zero | Pre-shock at time zero: "
        "progressed (shock or death) | Pre-shock at time zero: shock | Pre-shock decision times | "
        "Progressed per decision time |"
    )
    add("|---|---|---|---|---|---|---|")
    for row in outcomes["variants"]:
        add(
            f"| {row['description']} | {_rounded(row['patients_at_time_zero'])} | "
            f"{_rounded_rate(row['in_shock_stage_at_time_zero'])} | "
            f"{_rounded_rate(row['progressed_within_horizon'])} | "
            f"{_rounded_rate(row['shock_within_horizon'])} | "
            f"{_rounded(row['pre_shock_decision_times'])} | "
            f"{_rounded_rate(row['progressed_per_pre_shock_decision_time'])} |"
        )
    add("")
    availability = outcomes["measurement_availability"]
    add("### How often the outcome's ingredients are measured (main cohort)")
    add("")
    add("| Measure | Value |")
    add("|---|---|")
    labels = {
        "lactate_within_window_of_time_zero": "Lactate measured within the lactate window of time zero",
        "lactate_in_24_h_before_time_zero": "Lactate measured in the 24 h before time zero",
        "sustained_hypotension_in_first_24_h": "Pre-shock at time zero: sustained hypotension in the next 24 h",
        "of_those_lactate_measured_within_window": "of those, lactate measured within the window",
        "of_those_lactate_above_threshold_within_window": "of those, lactate above the threshold within the window",
        "arterial_share_of_map_readings": "Share of MAP readings from an arterial line, first 24 h",
        "stays_with_arterial_map_in_first_24_h": "Stays with an arterial-line MAP in the first 24 h",
    }
    add(f"| Stays | {_rounded(availability['stays'])} |")
    add(f"| Pre-shock at time zero | {_rounded(availability['pre_shock_at_time_zero'])} |")
    add(
        f"| MAP readings in the first 24 h | {_rounded(availability['map_readings_in_first_24_h'])} |"
    )
    for key, label in labels.items():
        add(f"| {label} | {_rounded_rate(availability[key])} |")
    add("")

    add("## Before sharing or publishing")
    add("")
    if report.get("withheld"):
        add(
            "- Withheld because they were within "
            f"{minimum - 1} of another published count: " + "; ".join(report["withheld"]) + "."
        )
    add("- Every number above passed the aggregate guard when `m1_check.json` was written.")
    add(
        "- Check links between tables by hand before publishing: a published number minus "
        "another must not leave a group of 1 to 10 patients."
    )
    add("- Record-level files: none are written by this check.")
    return "\n".join(lines)

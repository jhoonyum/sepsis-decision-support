"""The M1 gate check: is the database built right, and which outcome definition to register?

Run on the analyst's computer against the full MIMIC-IV DuckDB file (CI runs it on the open
demo)::

    sepsis-support m1-check --duckdb ~/physionet/mimic4.db

Everything it returns is aggregate and goes through the privacy guard: counts below the
minimum cell size are suppressed, cohort flows and time bins are merged so that no small
group can be worked out from the rest, and tables that count the same patients under
alternative definitions publish counts rounded to the nearest 10.

Sections
    database             table sizes against the row counts mimic-code expects for 3.1
    sepsis3_reference    first ICU stays with Sepsis-3, against a published count
    course_funnel        the course prototype's cohort funnel, reproduced rule for rule
    cohorts              flows of the main and sensitivity cohorts, their overlap, hours
                         from ICU admission to recognition, the criterion that completed R
    outcome_definitions  how often the progression outcome occurs under alternative
                         definitions, and how often lactate and arterial pressure are measured
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from sepsis_decision_support.cohort.cohort_builder import RECOGNITION_STEP, Cohort, select_cohort
from sepsis_decision_support.cohort.landmarks import build_landmarks
from sepsis_decision_support.config import CohortSettings, OutcomeSettings, Settings
from sepsis_decision_support.data.canonical_tables import CanonicalTables
from sepsis_decision_support.outcomes.labels import PRE_SHOCK, label_landmarks
from sepsis_decision_support.outcomes.shock_events import (
    shock_event_times,
    sustained_hypotension_times,
)
from sepsis_decision_support.privacy.aggregate_guard import (
    count_cell,
    count_partition_cells,
    merge_sparse_counts,
    publishable_flow,
    rate_cell,
    rounded_count_cell,
    rounded_rate_cell,
    suppressed_label,
)

# Row counts mimic-code expects for MIMIC-IV 3.1
# (mimic-iv/buildmimic/postgres/validate.sql at the pinned commit 303d26c).
EXPECTED_ROW_COUNTS_MIMIC_IV_3_1 = {
    "patients": 364_627,
    "admissions": 546_028,
    "icustays": 94_458,
    "inputevents": 10_953_713,
    "procedureevents": 808_706,
}

# A published count of Sepsis-3 among first ICU stays, built from mimic-code's sepsis3
# table on MIMIC-IV 3.0 (one version earlier than the one used here).
SEPSIS3_REFERENCE = {
    "source": "Yang P et al. Front Pharmacol 2025;16:1615618 (MIMIC-IV 3.0)",
    "first_icu_stays": 65_366,
    "sepsis3": 28_087,
}

# The course prototype's funnel: (column of m1_course_funnel.sql, description, its count).
COURSE_FUNNEL = (
    ("icu_stays", "ICU stays", 94_458),
    ("first_icu_stays", "first ICU stay of the patient", 65_366),
    ("stays_12_hours_to_10_days", "stay longer than 12 h and shorter than 10 days", 58_506),
    ("anchor_age_18_to_65", "anchor_age 18 to 65", 29_421),
    (
        "culture_antibiotic_pair",
        "antibiotic infusion started and ended in the ICU, culture charted in the ICU within 1 h",
        1_502,
    ),
)

# Hours from ICU admission to recognition, right-closed bins; the last bin is open.
RECOGNITION_HOUR_EDGES = (0.0, 1.0, 3.0, 6.0, 12.0, 24.0, 48.0, 72.0, 168.0)

DEFINITIONS = ("sepsis3", "culture_antibiotic_pair")


@dataclass
class M1Inputs:
    """Everything the check needs, read once from the database (record-level, in memory only).

    Attributes:
        database_counts: one row of ``m1_database_counts.sql`` as a dictionary.
        course_funnel: one row of ``m1_course_funnel.sql`` as a dictionary.
        stays: canonical stays table (every ICU stay).
        recognition: canonical recognition table for each definition in ``DEFINITIONS``.
        main_tables: canonical tables with measurements and treatments for the main
            cohort's stays only.
        map_by_source: mean arterial pressure readings of the main cohort's stays with a
            ``source`` column ("arterial" or "non_invasive").
        database_name: file name of the DuckDB database (no folders).
    """

    database_counts: dict[str, int]
    course_funnel: dict[str, int]
    stays: pd.DataFrame
    recognition: dict[str, pd.DataFrame]
    main_tables: CanonicalTables
    map_by_source: pd.DataFrame
    database_name: str


def cohort_settings_for(settings: CohortSettings, definition: str) -> CohortSettings:
    """The configured cohort rules with the given infection definition."""
    name = "sepsis3_early" if definition == "sepsis3" else "culture_antibiotic_pair"
    return settings.model_copy(update={"definition": definition, "name": name})


def collect_m1_inputs(duckdb_path: Path | str, settings: Settings) -> M1Inputs:
    """Run the M1 queries against a mimic-code DuckDB file (read-only)."""
    from sepsis_decision_support.data.mimic_extract import (
        extract_event_tables,
        extract_recognition,
        extract_stays,
        for_selected_stays,
        open_read_only,
        read_sql,
    )

    with open_read_only(duckdb_path) as connection:
        database_counts = _first_row_as_integers(
            connection.execute(read_sql("m1_database_counts")).df()
        )
        course_funnel = _first_row_as_integers(
            connection.execute(read_sql("m1_course_funnel")).df()
        )
        stays = extract_stays(connection)
        recognition = {
            definition: extract_recognition(connection, definition, stays)
            for definition in DEFINITIONS
        }
        main = select_cohort(
            stays, recognition["sepsis3"], cohort_settings_for(settings.cohort, "sepsis3")
        )
        events = extract_event_tables(connection, main.stays["stay_id"])
        # extract_event_tables registered ``selected_stays`` for the main cohort.
        map_by_source = connection.execute(for_selected_stays(read_sql("m1_map_by_source"))).df()

    main_tables = CanonicalTables(
        stays=stays,
        recognition=recognition["sepsis3"],
        measurements=events["measurements"],
        treatments=events["treatments"],
        sofa_hourly=events["sofa_hourly"],
    )
    map_by_source["charttime"] = pd.to_datetime(map_by_source["charttime"]).astype("datetime64[ns]")
    return M1Inputs(
        database_counts=database_counts,
        course_funnel=course_funnel,
        stays=stays,
        recognition=recognition,
        main_tables=main_tables,
        map_by_source=map_by_source,
        database_name=Path(duckdb_path).name,
    )


def _first_row_as_integers(frame: pd.DataFrame) -> dict[str, int]:
    return {column: int(value) for column, value in frame.iloc[0].items()}


# ---------------------------------------------------------------------------
# Database and published references
# ---------------------------------------------------------------------------


def is_demo_database(database_counts: dict[str, int]) -> bool:
    """The open demo has exactly 100 patients (the same test mimic-code's build uses)."""
    return database_counts["patients"] == 100


def database_section(database_counts: dict[str, int], minimum: int) -> dict:
    """Table sizes against mimic-code's expected counts for MIMIC-IV 3.1."""
    tables = [
        {
            "table": table,
            "rows": count_cell(database_counts[table], minimum),
            "expected_for_mimic_iv_3_1": expected,
            "matches": database_counts[table] == expected,
        }
        for table, expected in EXPECTED_ROW_COUNTS_MIMIC_IV_3_1.items()
    ]
    return {
        "demo": is_demo_database(database_counts),
        "tables": tables,
        "all_match": all(row["matches"] for row in tables),
        "patients_with_icu_stay": count_cell(database_counts["patients_with_icu_stay"], minimum),
    }


def _difference_cell(first: int, second: int, minimum: int) -> int | str:
    """``first - second`` when it is 0 or at least the minimum in size; otherwise suppressed."""
    difference = int(first) - int(second)
    if difference != 0 and abs(difference) < minimum:
        return suppressed_label(minimum)
    return difference


def sepsis3_reference_section(
    database_counts: dict[str, int], project_first_stays_with_recognition: int, minimum: int
) -> dict:
    """First ICU stays with Sepsis-3 by mimic-code, against the published reference.

    The project's own recognition rule selects exactly the stays of mimic-code's sepsis3
    table (it only computes a different time R), so the two counts should agree. If they
    differ by fewer than the minimum, mimic-code's count is withheld: the project's count
    appears in the cohort flow, and both together would reveal the small difference.
    """
    first_stays = database_counts["first_icu_stays"]
    mimic_code = database_counts["first_stays_with_sepsis3"]
    project = int(project_first_stays_with_recognition)
    agreement = _difference_cell(project, mimic_code, minimum)
    withheld = agreement == suppressed_label(minimum)
    shown = project if withheld else mimic_code
    reference = SEPSIS3_REFERENCE
    return {
        "first_icu_stays": count_cell(first_stays, minimum),
        "first_stays_with_sepsis3": count_cell(shown, minimum),
        "counted_from": "the project's recognition table" if withheld else "mimic-code's sepsis3",
        "share_of_first_stays": rate_cell(shown, first_stays, minimum),
        "project_minus_mimic_code": agreement,
        "reference": {
            **reference,
            "share_of_first_stays": round(reference["sepsis3"] / reference["first_icu_stays"], 3),
        },
        "difference_from_reference": int(shown) - reference["sepsis3"],
        "first_stays_difference_from_reference": int(first_stays) - reference["first_icu_stays"],
    }


def course_funnel_section(course_funnel: dict[str, int], minimum: int) -> dict:
    """The prototype's funnel reproduced, step by step, against its published counts."""
    steps = [
        {
            "step": description,
            "stays": count_cell(course_funnel[column], minimum),
            "prototype": prototype,
            "matches": course_funnel[column] == prototype,
        }
        for column, description, prototype in COURSE_FUNNEL
    ]
    # The prototype's script did not restrict the culture to the ICU stay. The difference
    # between the two versions is usually a handful of stays, so it is only reported
    # exactly when it is at least the minimum cell size.
    variant = _difference_cell(
        course_funnel["culture_antibiotic_pair_any_culture_time"],
        course_funnel["culture_antibiotic_pair"],
        minimum,
    )
    return {
        "steps": steps,
        "all_match": all(step["matches"] for step in steps),
        "culture_at_any_time_minus_culture_in_icu": variant,
    }


# ---------------------------------------------------------------------------
# Cohorts
# ---------------------------------------------------------------------------


def _bin_labels(edges: tuple[float, ...]) -> list[tuple[float, float]]:
    """(lower, upper] hour ranges, starting with 'at or before admission'."""
    bounds = [(-np.inf, edges[0])]
    bounds += list(zip(edges[:-1], edges[1:], strict=True))
    bounds.append((edges[-1], np.inf))
    return bounds


def _describe_hours(lower: float, upper: float) -> str:
    if np.isinf(lower) and np.isinf(upper):
        return "any time"
    if np.isinf(lower):
        if upper == 0:
            return "at or before ICU admission"
        return f"up to {upper:g} h (including at or before admission)"
    if np.isinf(upper):
        return f"more than {lower:g} h"
    return f"more than {lower:g} h, up to {upper:g} h"


def recognition_hour_bins(
    hours_after_admission: pd.Series,
    limit_hours: float,
    minimum: int,
    split_at_limit: bool = True,
) -> list[dict]:
    """Histogram of hours from ICU admission to R, with sparse bins merged.

    Args:
        split_at_limit: True when the cohort flow publishes the number of stays recognised
            within the limit (24 h). Bins on either side of the limit are then merged
            separately, so that this published count is also a sum of whole bins. When the
            flow does not publish it (because the next step excluded fewer than the
            minimum and was merged), the bins on both sides of the limit are joined, so that
            no sum of bins gives it away. If one side holds fewer stays than the minimum,
            it is merged into the other side either way.
    """
    bounds = _bin_labels(RECOGNITION_HOUR_EDGES)
    if limit_hours not in RECOGNITION_HOUR_EDGES:
        raise ValueError("the cohort's recognition limit must be one of the bin edges")
    counts = []
    for lower, upper in bounds:
        inside = (hours_after_admission > lower) & (hours_after_admission <= upper)
        counts.append(int(inside.sum()))
    within = [index for index, (_, upper) in enumerate(bounds) if upper <= limit_hours]
    beyond = [index for index, (_, upper) in enumerate(bounds) if upper > limit_hours]

    def merge(units: list[list[int]]) -> list[list[int]]:
        """Merge consecutive units (lists of bin positions) until each group can be shown."""
        groups = merge_sparse_counts(
            [sum(counts[index] for index in unit) for unit in units], minimum
        )
        return [[index for position in group for index in units[position]] for group in groups]

    one_side_small = (
        sum(counts[index] for index in beyond) < minimum
        or sum(counts[index] for index in within) < minimum
    )
    if split_at_limit and not one_side_small:
        groups = merge([[index] for index in within]) + merge([[index] for index in beyond])
    elif split_at_limit:
        groups = merge([[index] for index in within + beyond])
    else:
        # The last bin before the limit and the first after it form one unit.
        units = [[index] for index in within[:-1]] + [[within[-1], beyond[0]]]
        units += [[index] for index in beyond[1:]]
        groups = merge(units)

    published = []
    for group in groups:
        lower, upper = bounds[group[0]][0], bounds[group[-1]][1]
        published.append(
            {
                "hours": _describe_hours(lower, upper),
                "stays": count_cell(sum(counts[index] for index in group), minimum),
            }
        )
    return published


def completing_criterion(recognition: pd.DataFrame) -> pd.Series:
    """Which criterion was the last to be met at R (the one that 'completed' recognition)."""
    columns = {
        "antibiotic": "antibiotic_time",
        "culture": "culture_time",
        "organ dysfunction (SOFA >= 2)": "organ_dysfunction_time",
    }
    matches = pd.DataFrame(
        {
            name: recognition[column].eq(recognition["recognition_time"])
            for name, column in columns.items()
        }
    )
    number_matching = matches.sum(axis=1)
    labels = matches.idxmax(axis=1).where(number_matching == 1, "two or more at the same moment")
    return labels.where(number_matching > 0, "not determined")


def cohort_section(
    stays: pd.DataFrame,
    recognition: dict[str, pd.DataFrame],
    cohort_settings: CohortSettings,
    minimum: int,
) -> tuple[dict, dict[str, Cohort]]:
    """Flows of both cohorts, their overlap, hours to recognition, completing criterion."""
    limit = cohort_settings.maximum_hours_from_icu_admission_to_recognition
    cohorts: dict[str, Cohort] = {}
    by_definition: dict[str, dict] = {}
    for definition in DEFINITIONS:
        settings = cohort_settings_for(cohort_settings, definition)
        cohort = select_cohort(stays, recognition[definition], settings)
        cohorts[definition] = cohort

        # The population of the flow's recognition step, before the time limit.
        recognised = cohort.recognised
        hours = (
            recognised["recognition_time"] - recognised["icu_intime"]
        ).dt.total_seconds() / 3600
        published_flow = publishable_flow(cohort.flow, minimum)
        descriptions = [step for step, _ in cohort.flow]
        limit_step = descriptions[descriptions.index(RECOGNITION_STEP[definition]) + 1]
        within_limit_published = any(
            step["step"].split("; ")[-1] == limit_step for step in published_flow
        )

        final = cohort.stays.merge(
            recognition[definition].drop(columns="recognition_time"), on="stay_id"
        )
        criterion = completing_criterion(final).value_counts().to_dict()
        by_definition[definition] = {
            "name": settings.name,
            "flow": published_flow,
            "hours_from_icu_admission_to_recognition": recognition_hour_bins(
                hours, limit, minimum, split_at_limit=within_limit_published
            ),
            "criterion_that_completed_recognition": count_partition_cells(
                {key: int(value) for key, value in criterion.items()}, minimum
            ),
        }

    main_ids = set(cohorts["sepsis3"].stays["stay_id"])
    pair_ids = set(cohorts["culture_antibiotic_pair"].stays["stay_id"])
    overlap = {
        "in both cohorts": len(main_ids & pair_ids),
        "main cohort only": len(main_ids - pair_ids),
        "sensitivity cohort only": len(pair_ids - main_ids),
    }
    # Both cohort sizes are published, so any one overlap cell gives away the other two:
    # the cells are shown only when all three can be.
    if min(overlap.values()) >= minimum:
        published_overlap: dict | str = overlap
    else:
        published_overlap = f"not shown: a cell is below {minimum}"
    return {
        "rules": {
            "minimum_age_years": cohort_settings.minimum_age_years,
            "first_icu_stay_only": cohort_settings.first_icu_stay_only,
            "maximum_hours_from_icu_admission_to_recognition": limit,
            "excluded_hospital_services": list(cohort_settings.excluded_hospital_services),
        },
        "definitions": by_definition,
        "overlap": published_overlap,
    }, cohorts


# ---------------------------------------------------------------------------
# Outcome definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutcomeVariant:
    """One alternative definition of the progression outcome in the M1 grid."""

    key: str
    description: str
    outcomes: OutcomeSettings
    map_source: str = "any"  # "any" (mimic-code vitalsign.mbp) or "arterial"
    lactate_required: bool = True


def outcome_variants(base: OutcomeSettings) -> list[OutcomeVariant]:
    """The main (physiological) definition with the configured values, and one-at-a-time
    changes to it. The first row is physiological even when the configured outcome is the
    operational one, so every other row differs from it in exactly one respect."""
    hypotension = base.sustained_hypotension
    lactate = base.lactate

    def changed(hypotension_changes=None, lactate_changes=None, **outcome_changes):
        return base.model_copy(
            update={
                "sustained_hypotension": hypotension.model_copy(update=hypotension_changes or {}),
                "lactate": lactate.model_copy(update=lactate_changes or {}),
                **outcome_changes,
            }
        )

    physiological = changed(shock_definition="physiological")
    return [
        OutcomeVariant("main", "main definition, configured values", physiological),
        OutcomeVariant(
            "duration_15",
            "low readings at least 15 min apart",
            changed({"minimum_duration_minutes": 15.0}, shock_definition="physiological"),
        ),
        OutcomeVariant(
            "duration_60",
            "low readings at least 60 min apart",
            changed({"minimum_duration_minutes": 60.0}, shock_definition="physiological"),
        ),
        OutcomeVariant(
            "gap_60",
            "no more than 60 min between low readings",
            changed(
                {"maximum_gap_between_readings_minutes": 60.0}, shock_definition="physiological"
            ),
        ),
        OutcomeVariant(
            "map_below_60",
            "MAP below 60 mmHg",
            changed({"map_threshold_mmhg": 60.0}, shock_definition="physiological"),
        ),
        OutcomeVariant(
            "arterial_only", "arterial-line MAP only", physiological, map_source="arterial"
        ),
        OutcomeVariant(
            "lactate_window_3",
            "lactate within 3 h of the hypotension",
            changed(
                lactate_changes={"window_hours_before": 3.0, "window_hours_after": 3.0},
                shock_definition="physiological",
            ),
        ),
        OutcomeVariant(
            "lactate_window_12",
            "lactate within 12 h of the hypotension",
            changed(
                lactate_changes={"window_hours_before": 12.0, "window_hours_after": 12.0},
                shock_definition="physiological",
            ),
        ),
        OutcomeVariant(
            "lactate_above_4",
            "lactate above 4 mmol/L",
            changed(
                lactate_changes={"threshold_mmol_per_l": 4.0}, shock_definition="physiological"
            ),
        ),
        OutcomeVariant(
            "hypotension_alone",
            "sustained hypotension alone, no lactate needed",
            physiological,
            lactate_required=False,
        ),
        OutcomeVariant(
            "sepsis3_operational",
            "Sepsis-3 operational: vasopressor episode with high lactate",
            changed(shock_definition="sepsis3_operational"),
        ),
    ]


def measurements_with_map_source(
    measurements: pd.DataFrame, map_by_source: pd.DataFrame, source: str
) -> pd.DataFrame:
    """The measurements table with mean arterial pressure from one source only."""
    if source == "any":
        return measurements
    readings = map_by_source.loc[
        map_by_source["source"] == source, ["stay_id", "charttime", "value"]
    ]
    replacement = readings.assign(variable="mean_arterial_pressure")[list(measurements.columns)]
    others = measurements.loc[measurements["variable"] != "mean_arterial_pressure"]
    combined = pd.concat([others, replacement.astype(others.dtypes.to_dict())], ignore_index=True)
    return combined


def _shock_events(variant: OutcomeVariant, measurements: pd.DataFrame, treatments: pd.DataFrame):
    if not variant.lactate_required:
        episodes = sustained_hypotension_times(measurements, variant.outcomes.sustained_hypotension)
        return episodes.rename(columns={"hypotension_time": "shock_time"})
    return shock_event_times(measurements, variant.outcomes, treatments)


def _shock_within(rows: pd.DataFrame, horizon_hours: float) -> pd.Series:
    """Pre-shock rows whose next shock event falls in (L, L + H] while in the ICU."""
    end = rows["landmark_time"] + pd.Timedelta(hours=horizon_hours)
    return (
        (rows["population"] == PRE_SHOCK)
        & rows["next_shock_time"].notna()
        & (rows["next_shock_time"] > rows["landmark_time"])
        & (rows["next_shock_time"] <= end)
        & (rows["next_shock_time"] <= rows["icu_outtime"])
    )


def outcome_grid(
    tables: CanonicalTables,
    cohort: Cohort,
    settings: Settings,
    map_by_source: pd.DataFrame,
    minimum: int,
    base: int = 10,
) -> tuple[list[dict], pd.Series]:
    """Event counts of each outcome variant in the main cohort (rounded to ``base``).

    Returns:
        The grid rows, and the stays that are pre-shock at time zero under the main
        definition (used by ``measurement_availability``).
    """
    landmark_hours = sorted(set(settings.landmarks.reporting_hours) | {0.0})
    landmarks = build_landmarks(cohort.stays, landmark_hours)
    horizon = settings.outcomes.horizon_hours
    rows_out = []
    measurement_tables: dict[str, pd.DataFrame] = {}
    for variant in outcome_variants(settings.outcomes):
        if variant.map_source not in measurement_tables:
            measurement_tables[variant.map_source] = measurements_with_map_source(
                tables.measurements, map_by_source, variant.map_source
            )
        measurements = measurement_tables[variant.map_source]
        events = _shock_events(variant, measurements, tables.treatments)
        labelled = label_landmarks(landmarks, events, tables.treatments, variant.outcomes)
        at_zero = labelled.loc[labelled["landmark_hour"] == 0.0]
        pre_shock_zero = at_zero["population"] == PRE_SHOCK
        if variant.key == "main":
            pre_shock_stays = at_zero.loc[pre_shock_zero, "stay_id"]
        pre_shock_rows = labelled["population"] == PRE_SHOCK
        rows_out.append(
            {
                "variant": variant.key,
                "description": variant.description,
                "patients_at_time_zero": rounded_count_cell(len(at_zero), minimum, base),
                "in_shock_stage_at_time_zero": rounded_rate_cell(
                    int((~pre_shock_zero).sum()), len(at_zero), minimum, base
                ),
                "progressed_within_horizon": rounded_rate_cell(
                    int(at_zero.loc[pre_shock_zero, "event_within_horizon"].sum()),
                    int(pre_shock_zero.sum()),
                    minimum,
                    base,
                ),
                "shock_within_horizon": rounded_rate_cell(
                    int(_shock_within(at_zero, horizon).loc[pre_shock_zero].sum()),
                    int(pre_shock_zero.sum()),
                    minimum,
                    base,
                ),
                "pre_shock_decision_times": rounded_count_cell(
                    int(pre_shock_rows.sum()), minimum, base
                ),
                "progressed_per_pre_shock_decision_time": rounded_rate_cell(
                    int(labelled.loc[pre_shock_rows, "event_within_horizon"].sum()),
                    int(pre_shock_rows.sum()),
                    minimum,
                    base,
                ),
            }
        )
    return rows_out, pre_shock_stays


def _any_within(
    anchors: pd.DataFrame, anchor_column: str, events: pd.DataFrame, before: float, after: float
) -> pd.Series:
    """For each anchor row, whether an event of the same stay falls in [anchor - before, anchor + after]."""
    paired = anchors[["stay_id", anchor_column]].reset_index().merge(events, on="stay_id")
    inside = (paired["charttime"] >= paired[anchor_column] - pd.Timedelta(hours=before)) & (
        paired["charttime"] <= paired[anchor_column] + pd.Timedelta(hours=after)
    )
    hits = set(paired.loc[inside, "index"])
    return pd.Series(anchors.index.isin(hits), index=anchors.index)


def measurement_availability(
    tables: CanonicalTables,
    cohort: Cohort,
    settings: Settings,
    map_by_source: pd.DataFrame,
    pre_shock_stay_ids: pd.Series,
    minimum: int,
    base: int = 10,
) -> dict:
    """How often lactate and arterial pressure are measured around the decision points.

    The hypotension items are for patients who are pre-shock at time zero (main
    definition), the population in which shock is the outcome.
    """
    outcome = settings.outcomes
    stays = cohort.stays[["stay_id", "time_zero", "icu_outtime"]]
    lactate = tables.measurements.loc[
        tables.measurements["variable"] == "lactate", ["stay_id", "charttime"]
    ]
    window_before = outcome.lactate.window_hours_before
    window_after = outcome.lactate.window_hours_after
    lactate_near_zero = _any_within(stays, "time_zero", lactate, window_before, window_after)
    lactate_day_before = _any_within(stays, "time_zero", lactate, 24.0, 0.0)

    # First sustained hypotension in the 24 hours after time zero (configured rule).
    pre_shock = stays.loc[stays["stay_id"].isin(set(pre_shock_stay_ids))]
    episodes = sustained_hypotension_times(tables.measurements, outcome.sustained_hypotension)
    episodes = episodes.merge(pre_shock, on="stay_id")
    after_zero = (episodes["hypotension_time"] > episodes["time_zero"]) & (
        episodes["hypotension_time"] <= episodes["time_zero"] + pd.Timedelta(hours=24)
    )
    first_episodes = (
        episodes.loc[after_zero].sort_values("hypotension_time").groupby("stay_id").head(1)
    ).reset_index(drop=True)
    lactate_near_episode = _any_within(
        first_episodes, "hypotension_time", lactate, window_before, window_after
    )
    high_lactate = tables.measurements.loc[
        (tables.measurements["variable"] == "lactate")
        & (tables.measurements["value"] > outcome.lactate.threshold_mmol_per_l),
        ["stay_id", "charttime"],
    ]
    high_near_episode = _any_within(
        first_episodes, "hypotension_time", high_lactate, window_before, window_after
    )

    # Mean arterial pressure readings in the 24 hours after time zero, by source.
    readings = map_by_source.merge(stays, on="stay_id")
    first_day = (readings["charttime"] > readings["time_zero"]) & (
        readings["charttime"] <= readings["time_zero"] + pd.Timedelta(hours=24)
    )
    readings = readings.loc[first_day]
    arterial = readings["source"] == "arterial"
    stays_with_arterial = readings.loc[arterial, "stay_id"].nunique()

    total_stays = len(stays)
    return {
        "stays": rounded_count_cell(total_stays, minimum, base),
        "lactate_within_window_of_time_zero": rounded_rate_cell(
            int(lactate_near_zero.sum()), total_stays, minimum, base
        ),
        "lactate_in_24_h_before_time_zero": rounded_rate_cell(
            int(lactate_day_before.sum()), total_stays, minimum, base
        ),
        "pre_shock_at_time_zero": rounded_count_cell(len(pre_shock), minimum, base),
        "sustained_hypotension_in_first_24_h": rounded_rate_cell(
            len(first_episodes), len(pre_shock), minimum, base
        ),
        "of_those_lactate_measured_within_window": rounded_rate_cell(
            int(lactate_near_episode.sum()), len(first_episodes), minimum, base
        ),
        "of_those_lactate_above_threshold_within_window": rounded_rate_cell(
            int(high_near_episode.sum()), len(first_episodes), minimum, base
        ),
        "map_readings_in_first_24_h": rounded_count_cell(len(readings), minimum, base),
        "arterial_share_of_map_readings": rounded_rate_cell(
            int(arterial.sum()), len(readings), minimum, base
        ),
        "stays_with_arterial_map_in_first_24_h": rounded_rate_cell(
            int(stays_with_arterial), total_stays, minimum, base
        ),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _close_to_any(value: int, published: list[int], minimum: int) -> bool:
    return any(0 < abs(value - other) < minimum for other in published)


def printed_reference_figures() -> list[int]:
    """Counts of stays or patients that the report prints from outside this database."""
    return [
        EXPECTED_ROW_COUNTS_MIMIC_IV_3_1["patients"],
        EXPECTED_ROW_COUNTS_MIMIC_IV_3_1["icustays"],
        *(count for _, _, count in COURSE_FUNNEL),
        SEPSIS3_REFERENCE["first_icu_stays"],
        SEPSIS3_REFERENCE["sepsis3"],
    ]


def withhold_close_counts(report: dict, minimum: int) -> list[str]:
    """Withhold exact counts that differ from a printed or published count by 1 to minimum - 1.

    Counts of nested groups of stays appear in several sections, next to reference figures
    from outside the database (mimic-code's expected row counts, the prototype's funnel, the
    published Sepsis-3 count). Two counts that differ by a small number would reveal a small
    group: for example "first ICU stays" in a paper minus "first ICU stays of adults with a
    discharge time" here. Every exact count of stays or patients, including cohort-flow
    steps, counts that can be rebuilt from a printed difference, and running sums of the
    time bins, is compared in report order with the reference figures and the counts
    published before it. A count that lands within the gap is withheld together with
    anything that would reveal it (match flags, rates, differences). Rounded counts are
    exempt: rounding already hides small differences.

    Returns:
        Descriptions of the withheld items, also stored in ``report["withheld"]``.
    """
    label = f"withheld: within {minimum - 1} of another published count"
    published = printed_reference_figures()
    withheld: list[str] = []

    def publishable(description: str, value) -> bool:
        if not isinstance(value, int) or isinstance(value, bool):
            return True
        if _close_to_any(value, published, minimum):
            withheld.append(description)
            return False
        published.append(value)
        return True

    definitions = report["cohorts"]["definitions"]
    for definition, section in definitions.items():
        for step in section["flow"]:
            if not publishable(f"{definition} flow: {step['step']}", step["stays"]):
                step["stays"] = label

    database = report["database_counts"]
    for row in database["tables"]:
        if row["table"] in ("patients", "icustays") and not publishable(
            f"database: {row['table']}", row["rows"]
        ):
            row["rows"] = row["matches"] = database["all_match"] = label
    if not publishable("database: patients with an ICU stay", database["patients_with_icu_stay"]):
        database["patients_with_icu_stay"] = label

    funnel = report["course_funnel"]
    for step in funnel["steps"]:
        if not publishable(f"prototype funnel: {step['step']}", step["stays"]):
            step["stays"] = step["matches"] = funnel["all_match"] = label
    variant = funnel["culture_at_any_time_minus_culture_in_icu"]
    last_step = funnel["steps"][-1]["stays"] if funnel["steps"] else None
    if (
        isinstance(variant, int)
        and variant != 0
        and isinstance(last_step, int)
        and not publishable("prototype funnel: culture at any time", last_step + variant)
    ):
        funnel["culture_at_any_time_minus_culture_in_icu"] = label

    reference = report["sepsis3_reference"]
    if not publishable("Sepsis-3 reference: first ICU stays", reference["first_icu_stays"]):
        for key in (
            "first_icu_stays",
            "share_of_first_stays",
            "first_stays_difference_from_reference",
        ):
            reference[key] = label
    if not publishable(
        "Sepsis-3 reference: first ICU stays with Sepsis-3", reference["first_stays_with_sepsis3"]
    ):
        for key in (
            "first_stays_with_sepsis3",
            "share_of_first_stays",
            "difference_from_reference",
            "project_minus_mimic_code",
        ):
            reference[key] = label
    agreement = reference.get("project_minus_mimic_code")
    shown = reference["first_stays_with_sepsis3"]
    if (
        isinstance(agreement, int)
        and agreement != 0
        and isinstance(shown, int)
        and not publishable("Sepsis-3 reference: the project's count", shown + agreement)
    ):
        reference["project_minus_mimic_code"] = label

    # Tables whose cells add up to a cohort's final size: if every cell is shown, their
    # sum is that size, so it is checked like any other count.
    for definition, section in definitions.items():
        cells = section.get("criterion_that_completed_recognition")
        all_shown = (
            isinstance(cells, dict)
            and bool(cells)
            and all(isinstance(count, int) for count in cells.values())
        )
        if all_shown and not publishable(
            f"{definition}: criterion completed last (total)", sum(cells.values())
        ):
            section["criterion_that_completed_recognition"] = label
    overlap = report["cohorts"].get("overlap")
    if isinstance(overlap, dict):
        main_total = overlap["in both cohorts"] + overlap["main cohort only"]
        pair_total = overlap["in both cohorts"] + overlap["sensitivity cohort only"]
        if not (
            publishable("overlap: main cohort (total)", main_total)
            and publishable("overlap: sensitivity cohort (total)", pair_total)
        ):
            report["cohorts"]["overlap"] = label

    for definition, section in definitions.items():
        bins = section["hours_from_icu_admission_to_recognition"]
        if isinstance(bins, list) and all(isinstance(row["stays"], int) for row in bins):
            running_sums = np.cumsum([row["stays"] for row in bins]).tolist()
            # Check every running sum; keep the histogram only if all of them can be shown.
            if any(_close_to_any(int(total), published, minimum) for total in running_sums):
                withheld.append(f"{definition}: hours from ICU admission to recognition")
                section["hours_from_icu_admission_to_recognition"] = label
            else:
                published.extend(int(total) for total in running_sums)

    report["withheld"] = withheld
    return withheld


def build_m1_report(inputs: M1Inputs, settings: Settings, rounding_base: int = 10) -> dict:
    """Assemble every section into one aggregate dictionary (checked when written)."""
    minimum = settings.privacy.minimum_cell_size
    cohorts_report, cohorts = cohort_section(
        inputs.stays, inputs.recognition, settings.cohort, minimum
    )
    first_stays = inputs.stays.loc[inputs.stays["is_first_icu_stay"], "stay_id"]
    project_count = int(inputs.recognition["sepsis3"]["stay_id"].isin(set(first_stays)).sum())
    main = cohorts["sepsis3"]
    variants, pre_shock_stays = outcome_grid(
        inputs.main_tables, main, settings, inputs.map_by_source, minimum, rounding_base
    )
    report = {
        "check": "M1",
        "database": inputs.database_name,
        "minimum_cell_size": minimum,
        "rounding_base": rounding_base,
        "settings_fingerprint": settings.fingerprint(),
        "database_counts": database_section(inputs.database_counts, minimum),
        "sepsis3_reference": sepsis3_reference_section(
            inputs.database_counts, project_count, minimum
        ),
        "course_funnel": course_funnel_section(inputs.course_funnel, minimum),
        "cohorts": cohorts_report,
        "outcome_definitions": {
            "population": "main cohort (Sepsis-3), decision times "
            + ", ".join(f"{hour:g}" for hour in sorted(set(settings.landmarks.reporting_hours)))
            + " h after time zero",
            "horizon_hours": settings.outcomes.horizon_hours,
            "variants": variants,
            "measurement_availability": measurement_availability(
                inputs.main_tables,
                main,
                settings,
                inputs.map_by_source,
                pre_shock_stays,
                minimum,
                rounding_base,
            ),
        },
    }
    withhold_close_counts(report, minimum)
    return report

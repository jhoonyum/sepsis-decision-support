"""The M1 check, section by section, on hand-made inputs and on synthetic patients."""

import pandas as pd
import pytest

from sepsis_decision_support.checks.m1 import (
    COURSE_FUNNEL,
    EXPECTED_ROW_COUNTS_MIMIC_IV_3_1,
    M1Inputs,
    build_m1_report,
    completing_criterion,
    course_funnel_section,
    measurements_with_map_source,
    outcome_variants,
    recognition_hour_bins,
    sepsis3_reference_section,
    withhold_close_counts,
)
from sepsis_decision_support.checks.m1_markdown import render_m1_markdown
from sepsis_decision_support.cohort.cohort_builder import select_cohort
from sepsis_decision_support.data.synthetic import generate_canonical_tables
from sepsis_decision_support.privacy.aggregate_guard import assert_aggregate_only, publishable_flow

from .conftest import hours, make_tables, stay_row


def test_hour_bins_are_merged_on_each_side_of_the_cohort_limit():
    stay_hours = pd.Series([0.5] * 12 + [2.0] * 5 + [10.0] * 20 + [30.0] * 15 + [100.0] * 3)
    assert recognition_hour_bins(stay_hours, 24.0, 11) == [
        {"hours": "up to 1 h (including at or before admission)", "stays": 12},
        {"hours": "more than 1 h, up to 24 h", "stays": 25},
        {"hours": "more than 24 h", "stays": 18},
    ]


def test_hour_bins_merge_across_the_limit_when_one_side_is_small():
    # Only 3 stays beyond 24 h: the cohort flow merges its own 24 h step, and so do the bins.
    stay_hours = pd.Series([0.5] * 12 + [30.0] * 3)
    assert recognition_hour_bins(stay_hours, 24.0, 11) == [{"hours": "any time", "stays": 15}]


def test_completing_criterion_names_the_last_criterion_met():
    recognition = pd.DataFrame(
        {
            "recognition_time": [hours(3), hours(3), hours(3), hours(3), hours(3)],
            "antibiotic_time": [hours(3), hours(1), hours(1), hours(3), hours(1)],
            "culture_time": [hours(2), hours(3), hours(1), hours(3), hours(1)],
            "organ_dysfunction_time": [hours(1), hours(1), hours(3), hours(1), pd.NaT],
        }
    )
    assert list(completing_criterion(recognition)) == [
        "antibiotic",
        "culture",
        "organ dysfunction (SOFA >= 2)",
        "two or more at the same moment",
        "not determined",
    ]


def counts_for_reference(first_stays: int, with_sepsis3: int) -> dict:
    return {"first_icu_stays": first_stays, "first_stays_with_sepsis3": with_sepsis3}


def test_reference_comparison_when_both_rules_agree():
    section = sepsis3_reference_section(counts_for_reference(65_000, 28_000), 28_000, 11)
    assert section["first_stays_with_sepsis3"] == 28_000
    assert section["project_minus_mimic_code"] == 0
    assert section["difference_from_reference"] == 28_000 - 28_087
    assert section["counted_from"] == "mimic-code's sepsis3"


def test_reference_comparison_withholds_a_small_disagreement():
    section = sepsis3_reference_section(counts_for_reference(65_000, 28_000), 28_004, 11)
    assert section["project_minus_mimic_code"] == "<11"
    assert section["first_stays_with_sepsis3"] == 28_004  # the project's count, as in the flow
    assert section["counted_from"] == "the project's recognition table"


def prototype_counts(**changes) -> dict:
    counts = {column: count for column, _, count in COURSE_FUNNEL}
    counts["culture_antibiotic_pair_any_culture_time"] = counts["culture_antibiotic_pair"]
    counts.update(changes)
    return counts


def test_funnel_reproduction_and_the_culture_variant():
    section = course_funnel_section(prototype_counts(), 11)
    assert section["all_match"]
    assert section["culture_at_any_time_minus_culture_in_icu"] == 0
    section = course_funnel_section(
        prototype_counts(culture_antibiotic_pair_any_culture_time=1_503), 11
    )
    assert section["culture_at_any_time_minus_culture_in_icu"] == "<11"
    section = course_funnel_section(prototype_counts(anchor_age_18_to_65=29_420), 11)
    assert not section["all_match"]


def fake_report(flow_counts, patients_with_icu_stay, first_stays, with_sepsis3, bins):
    flow = [{"step": f"step {index}", "stays": count} for index, count in enumerate(flow_counts)]
    return {
        "database_counts": {
            "tables": [{"table": "icustays", "rows": flow_counts[0], "matches": True}],
            "all_match": True,
            "patients_with_icu_stay": patients_with_icu_stay,
        },
        "course_funnel": {
            "steps": [],
            "all_match": True,
            "culture_at_any_time_minus_culture_in_icu": 0,
        },
        "sepsis3_reference": {
            "first_icu_stays": first_stays,
            "first_stays_with_sepsis3": with_sepsis3,
            "share_of_first_stays": {"rate": 0.5},
            "difference_from_reference": with_sepsis3 - 28_087,
            "first_stays_difference_from_reference": first_stays - 65_366,
            "project_minus_mimic_code": 0,
        },
        "cohorts": {
            "definitions": {
                "sepsis3": {
                    "flow": flow,
                    "hours_from_icu_admission_to_recognition": [
                        {"hours": "bin", "stays": count} for count in bins
                    ],
                }
            }
        },
    }


def test_counts_close_to_a_published_count_are_withheld_with_what_reveals_them():
    report = fake_report(
        flow_counts=[1_000, 800, 500, 400],
        patients_with_icu_stay=805,  # 5 more than the flow's 800
        first_stays=805,
        with_sepsis3=503,  # 3 more than the flow's 500
        bins=[300, 200],  # adds up to the flow's 500: fine
    )
    withheld = withhold_close_counts(report, 11)
    assert withheld == [
        "database: patients with an ICU stay",
        "Sepsis-3 reference: first ICU stays",
        "Sepsis-3 reference: first ICU stays with Sepsis-3",
    ]
    reference = report["sepsis3_reference"]
    for key in (
        "first_icu_stays",
        "first_stays_with_sepsis3",
        "share_of_first_stays",
        "difference_from_reference",
        "first_stays_difference_from_reference",
        "project_minus_mimic_code",
    ):
        assert str(reference[key]).startswith("withheld"), key
    assert report["database_counts"]["tables"][0]["rows"] == 1_000
    bins = report["cohorts"]["definitions"]["sepsis3"]["hours_from_icu_admission_to_recognition"]
    assert sum(row["stays"] for row in bins) == 500


def test_a_histogram_whose_total_is_close_to_a_published_count_is_withheld():
    report = fake_report([1_000, 800, 500, 400], 800, 800, 500, bins=[300, 196])
    assert withhold_close_counts(report, 11) == ["sepsis3: hours from ICU admission to recognition"]


def test_a_flow_step_close_to_a_printed_reference_figure_is_withheld():
    # Three first ICU stays without a discharge time: 65,363 next to the paper's 65,366.
    report = fake_report([94_458, 65_363, 28_000, 20_000], 65_366, 65_366, 28_000, bins=[28_000])
    withheld = withhold_close_counts(report, 11)
    assert "sepsis3 flow: step 1" in withheld
    flow = report["cohorts"]["definitions"]["sepsis3"]["flow"]
    assert str(flow[1]["stays"]).startswith("withheld")
    # The copies of 65,366 equal the printed figure, so they stay.
    assert report["sepsis3_reference"]["first_icu_stays"] == 65_366


def test_a_near_miss_of_the_prototype_funnel_is_withheld_with_its_match_flags():
    report = fake_report([94_458, 65_366, 28_000, 20_000], 65_366, 65_366, 28_000, bins=[28_000])
    report["course_funnel"]["steps"] = [
        {"step": "ICU stays", "stays": 94_458, "prototype": 94_458, "matches": True},
        {"step": "last", "stays": 1_499, "prototype": 1_502, "matches": False},
    ]
    report["course_funnel"]["all_match"] = False
    withheld = withhold_close_counts(report, 11)
    assert withheld == ["prototype funnel: last"]
    last = report["course_funnel"]["steps"][-1]
    assert str(last["stays"]).startswith("withheld") and str(last["matches"]).startswith("withheld")
    assert str(report["course_funnel"]["all_match"]).startswith("withheld")


def test_a_count_rebuilt_from_a_printed_difference_is_checked():
    report = fake_report([94_458, 65_366, 28_000, 20_000], 65_366, 65_366, 27_985, bins=[28_000])
    # mimic-code's count 27,985 is shown; the project's count 27,985 + 12 = 27,997 would sit
    # 3 below the flow's 28,000.
    report["sepsis3_reference"]["project_minus_mimic_code"] = 12
    withheld = withhold_close_counts(report, 11)
    assert withheld == ["Sepsis-3 reference: the project's count"]
    assert str(report["sepsis3_reference"]["project_minus_mimic_code"]).startswith("withheld")


def test_hour_bins_do_not_split_at_the_limit_when_the_flow_hides_that_count():
    stay_hours = pd.Series([0.5] * 12 + [10.0] * 20 + [30.0] * 15)
    assert recognition_hour_bins(stay_hours, 24.0, 11, split_at_limit=False) == [
        {"hours": "up to 1 h (including at or before admission)", "stays": 12},
        {"hours": "more than 1 h, up to 12 h", "stays": 20},
        {"hours": "more than 12 h", "stays": 15},
    ]


def test_hour_bins_follow_the_flow_when_a_few_stays_are_excluded_after_the_limit(settings):
    # 500 recognised, 400 within 24 h, 5 of those on a cardiac surgery service: the flow
    # merges the last two steps, so no sum of bins may equal the 400 within 24 h.
    from sepsis_decision_support.checks.m1 import cohort_section

    stays, recognition = [], []
    for stay in range(1, 501):
        service = "CSURG" if stay <= 5 else "MED"
        stays.append(stay_row(stay, hospital_service=service, icu_outtime=hours(200)))
        recognised_at = 2.0 if stay <= 400 else 30.0
        recognition.append(
            {
                "stay_id": stay,
                "recognition_time": hours(recognised_at),
                "antibiotic_time": hours(recognised_at),
                "culture_time": hours(recognised_at - 1),
                "organ_dysfunction_time": hours(recognised_at - 1),
            }
        )
    tables = make_tables(stays, recognition)
    section, _ = cohort_section(
        tables.stays,
        {"sepsis3": tables.recognition, "culture_antibiotic_pair": tables.recognition},
        settings.cohort,
        11,
    )
    main = section["definitions"]["sepsis3"]
    assert [step["stays"] for step in main["flow"]][-1] == 395
    assert 400 not in [step["stays"] for step in main["flow"]]
    # No bin edge at 24 h: the bins cannot be summed to the stays recognised within 24 h.
    labels = [row["hours"] for row in main["hours_from_icu_admission_to_recognition"]]
    assert not any("up to 24 h" in label or "more than 24 h" in label for label in labels)
    # Here no stay falls between 3 and 24 h, so the first bin happens to hold the same 400;
    # the cross-section check withholds the table because 400 is 5 above the flow's 395.
    report = {
        "cohorts": {"definitions": {"sepsis3": main}},
        "database_counts": {"tables": [], "all_match": True, "patients_with_icu_stay": 500},
        "course_funnel": {
            "steps": [],
            "all_match": True,
            "culture_at_any_time_minus_culture_in_icu": 0,
        },
        "sepsis3_reference": {
            "first_icu_stays": 500,
            "first_stays_with_sepsis3": 500,
            "project_minus_mimic_code": 0,
        },
    }
    assert withhold_close_counts(report, 11) == ["sepsis3: hours from ICU admission to recognition"]


def test_each_outcome_variant_changes_one_thing(settings):
    base = settings.outcomes

    def leaves(outcomes, variant):
        values = outcomes.model_dump()
        flat = {
            f"{group}.{key}": value
            for group, nested in values.items()
            if isinstance(nested, dict)
            for key, value in nested.items()
        }
        flat.update({key: value for key, value in values.items() if not isinstance(value, dict)})
        flat["map_source"] = variant.map_source
        flat["lactate_required"] = variant.lactate_required
        # The window is one setting with two sides.
        flat["lactate.window"] = (
            flat.pop("lactate.window_hours_before"),
            flat.pop("lactate.window_hours_after"),
        )
        return flat

    variants = outcome_variants(base)
    reference = leaves(base, variants[0])
    assert variants[0].key == "main"
    for variant in variants[1:]:
        changed = {
            key
            for key, value in leaves(variant.outcomes, variant).items()
            if reference[key] != value
        }
        assert len(changed) == 1, (variant.key, changed)


def test_arterial_only_measurements_replace_the_mixed_pressure_series():
    measurements = make_tables(
        [stay_row(1)],
        measurements=[
            (1, 1.0, "mean_arterial_pressure", 70.0),
            (1, 2.0, "mean_arterial_pressure", 60.0),
            (1, 1.0, "heart_rate", 90.0),
        ],
    ).measurements
    by_source = pd.DataFrame(
        {
            "stay_id": [1, 1],
            "charttime": [hours(1.0), hours(2.0)],
            "source": ["arterial", "non_invasive"],
            "value": [72.0, 58.0],
        }
    )
    arterial = measurements_with_map_source(measurements, by_source, "arterial")
    pressure = arterial.loc[arterial["variable"] == "mean_arterial_pressure"]
    assert pressure["value"].tolist() == [72.0]
    assert (arterial["variable"] == "heart_rate").sum() == 1
    assert list(arterial.columns) == list(measurements.columns)
    assert measurements_with_map_source(measurements, by_source, "any") is measurements


@pytest.fixture(scope="module")
def synthetic_inputs():
    tables = generate_canonical_tables(400, seed=3)
    pairs = generate_canonical_tables(400, seed=3, recognition_definition="culture_antibiotic_pair")
    pressure = tables.measurements.loc[
        tables.measurements["variable"] == "mean_arterial_pressure",
        ["stay_id", "charttime", "value"],
    ]
    map_by_source = pressure.assign(
        source=["arterial" if index % 2 else "non_invasive" for index in range(len(pressure))]
    )
    stays = len(tables.stays)
    database_counts = {table: count for table, count in EXPECTED_ROW_COUNTS_MIMIC_IV_3_1.items()}
    database_counts.update(
        icustays=stays,
        patients_with_icu_stay=int(tables.stays["is_first_icu_stay"].sum()),
        first_icu_stays=int(tables.stays["is_first_icu_stay"].sum()),
        first_stays_with_sepsis3=int(
            tables.recognition["stay_id"]
            .isin(tables.stays.loc[tables.stays["is_first_icu_stay"], "stay_id"])
            .sum()
        ),
    )
    return M1Inputs(
        database_counts=database_counts,
        course_funnel=prototype_counts(icu_stays=stays),
        stays=tables.stays,
        recognition={"sepsis3": tables.recognition, "culture_antibiotic_pair": pairs.recognition},
        main_tables=tables,
        map_by_source=map_by_source,
        database_name="synthetic.db",
    )


def test_m1_report_on_synthetic_patients_is_aggregate_and_consistent(synthetic_inputs, settings):
    report = build_m1_report(synthetic_inputs, settings)
    assert_aggregate_only(report)
    for definition, recognition in synthetic_inputs.recognition.items():
        cohort_settings = settings.cohort.model_copy(update={"definition": definition})
        cohort = select_cohort(synthetic_inputs.stays, recognition, cohort_settings)
        section = report["cohorts"]["definitions"][definition]
        assert section["flow"] == publishable_flow(cohort.flow, 11)
        # The bins add up to the stays recognised before the time limit.
        recognised = dict(cohort.flow)[
            "Sepsis-3 recognised during the ICU stay"
            if definition == "sepsis3"
            else "IV antibiotic and culture within 1 h of each other in the ICU"
        ]
        bins = section["hours_from_icu_admission_to_recognition"]
        if not isinstance(bins, str):
            assert sum(row["stays"] for row in bins) == recognised
    variants = report["outcome_definitions"]["variants"]
    assert [row["variant"] for row in variants][0] == "main"
    assert report["sepsis3_reference"]["project_minus_mimic_code"] == 0
    markdown = render_m1_markdown(report)
    assert "## Outcome definitions" in markdown and "## Course prototype funnel" in markdown


def test_m1_grid_matches_the_pipeline_labels_at_time_zero(synthetic_inputs, settings):
    from sepsis_decision_support.cohort.landmarks import build_landmarks
    from sepsis_decision_support.outcomes.labels import PRE_SHOCK, label_landmarks
    from sepsis_decision_support.outcomes.shock_events import shock_event_times
    from sepsis_decision_support.privacy.aggregate_guard import rounded_rate_cell

    tables = synthetic_inputs.main_tables
    cohort = select_cohort(tables.stays, tables.recognition, settings.cohort)
    landmarks = build_landmarks(cohort.stays, [0.0])
    events = shock_event_times(tables.measurements, settings.outcomes, tables.treatments)
    rows = label_landmarks(landmarks, events, tables.treatments, settings.outcomes)
    pre_shock = rows.loc[rows["population"] == PRE_SHOCK]
    expected = rounded_rate_cell(int(pre_shock["event_within_horizon"].sum()), len(pre_shock), 11)
    report = build_m1_report(synthetic_inputs, settings)
    configured = report["outcome_definitions"]["variants"][0]
    assert configured["progressed_within_horizon"] == expected


def test_tables_that_add_up_to_a_withheld_cohort_size_are_withheld_too():
    # The sensitivity cohort ends at 1,507, within 10 of the prototype's 1,502.
    report = fake_report([94_458, 65_366, 28_000, 20_000], 65_366, 65_366, 28_000, bins=[28_000])
    report["cohorts"]["definitions"]["sepsis3"]["criterion_that_completed_recognition"] = {
        "antibiotic": 15_000,
        "culture": 5_000,
    }
    report["cohorts"]["definitions"]["culture_antibiotic_pair"] = {
        "flow": [{"step": "ICU stays", "stays": 94_458}, {"step": "final", "stays": 1_507}],
        "hours_from_icu_admission_to_recognition": "withheld",
        "criterion_that_completed_recognition": {"antibiotic": 1_107, "culture": 400},
    }
    report["cohorts"]["overlap"] = {
        "in both cohorts": 1_000,
        "main cohort only": 19_000,
        "sensitivity cohort only": 507,
    }
    withheld = withhold_close_counts(report, 11)
    assert "culture_antibiotic_pair flow: final" in withheld
    pair = report["cohorts"]["definitions"]["culture_antibiotic_pair"]
    assert str(pair["criterion_that_completed_recognition"]).startswith("withheld")
    assert str(report["cohorts"]["overlap"]).startswith("withheld")
    # The main cohort's criterion table adds up to its published size and stays.
    main = report["cohorts"]["definitions"]["sepsis3"]
    assert main["criterion_that_completed_recognition"] == {"antibiotic": 15_000, "culture": 5_000}

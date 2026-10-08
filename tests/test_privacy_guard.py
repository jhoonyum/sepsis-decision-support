import hashlib
import json

import numpy as np
import pytest

from sepsis_decision_support.privacy.aggregate_guard import (
    PrivacyViolationError,
    assert_aggregate_only,
    complementary_suppression,
    count_cell,
    merge_sparse_groups,
    publishable_flow,
    rate_cell,
    write_aggregate_json,
)


def test_count_cell_keeps_counts_at_the_minimum_and_suppresses_smaller_ones():
    assert count_cell(11, 11) == 11
    assert count_cell(10, 11) == "<11"
    assert count_cell(0, 11) == "<11"


def test_rate_cell_suppresses_small_numerators_complements_and_denominators():
    assert rate_cell(5, 100, 11) == "<11"  # few events
    assert rate_cell(95, 100, 11) == "<11"  # few non-events: 5 could be back-calculated
    assert rate_cell(5, 5, 1) == "<1"  # complement of zero
    assert rate_cell(20, 100, 11) == {"rate": 0.2, "numerator": 20, "denominator": 100}


@pytest.mark.parametrize(
    "content",
    [
        {"stay_id": 1},
        {"results": [{"charttime": "2150-01-01"}]},
        {"values": list(range(401))},
        {"value": float("nan")},
        {"values": np.array([1.0, 2.0])},
        {"value": np.float32(0.5)},
    ],
)
def test_record_level_or_unsafe_content_is_refused(content):
    with pytest.raises(PrivacyViolationError):
        assert_aggregate_only(content)


def test_aggregate_content_passes():
    assert_aggregate_only({"rows": 1200, "rate": {"rate": 0.2, "numerator": 240}, "bins": [1, 2]})


def test_write_aggregate_json_records_the_file_hash_in_the_run_log(tmp_path):
    report_path = tmp_path / "report.json"
    log_path = tmp_path / "privacy_log.jsonl"
    digest = write_aggregate_json({"auroc": 0.77}, report_path, log_path, "test report")

    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == digest
    entry = json.loads(log_path.read_text().splitlines()[-1])
    assert entry["sha256"] == digest
    assert entry["file"] == "report.json"


def test_write_aggregate_json_refuses_before_writing(tmp_path):
    with pytest.raises(PrivacyViolationError):
        write_aggregate_json({"subject_id": 3}, tmp_path / "r.json", tmp_path / "log", "bad")
    assert not (tmp_path / "r.json").exists()


def test_a_hidden_group_cannot_be_recovered_from_the_others():
    # (rows, patients, events): "Other" is small, so the next-smallest group is hidden too.
    cells = {"White": (900, 300, 120), "Black": (150, 50, 20), "Other": (8, 3, 1)}
    assert complementary_suppression(cells, 11) == {"Other", "Black"}
    assert complementary_suppression({"F": (500, 200, 60), "M": (520, 210, 70)}, 11) == set()


def test_hidden_groups_together_reach_the_minimum():
    cells = {"A": (1000, 400, 100), "B": (5, 2, 1), "C": (6, 3, 1), "D": (300, 120, 40)}
    hidden = complementary_suppression(cells, 11)
    assert {"B", "C"} <= hidden
    assert sum(cells[key][2] for key in hidden) >= 11  # events hidden together


def test_sparse_bins_are_merged_left_to_right():
    assert merge_sparse_groups([50, 50, 50], [12, 3, 20], 11) == [[0], [1, 2]]
    assert merge_sparse_groups([50, 50, 5], [12, 20, 1], 11) == [[0], [1, 2]]  # short tail


def test_cohort_flow_never_shows_a_small_exclusion():
    flow = [("ICU stays", 2000), ("adults", 2000), ("first stay", 1729), ("recognised", 1722),
            ("within 24 h", 1600), ("not surgical", 1595)]  # fmt: skip
    published = publishable_flow(flow, 11)
    counts = [step["stays"] for step in published]
    assert counts == [2000, 2000, 1729, 1595]  # the final count is always the cohort size
    assert published[3]["step"] == "recognised; within 24 h; not surgical"
    exclusions = [before - after for before, after in zip(counts, counts[1:], strict=False)]
    assert all(value == 0 or value >= 11 for value in exclusions)

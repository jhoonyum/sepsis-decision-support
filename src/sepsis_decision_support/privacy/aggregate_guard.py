"""Guards for anything that leaves the machine: only aggregates, no small cells.

Why this module exists
    MIMIC-IV may only be used under the PhysioNet data use agreement: no record-level
    data may be shared, and results are published as aggregates. This project also
    follows a common disclosure rule: no count below 11 is published, and no rate is
    published if the count behind it (or its complement) could be back-calculated
    below 11.

    Every report, figure input and web export goes through ``write_aggregate_json``,
    which checks the content and writes a line to a run log.

Cells that could be worked out from other cells
    Hiding one small cell is not enough when the other cells of the same table and its
    total are published: the hidden value is the total minus the rest. Three tools close
    that gap: ``complementary_suppression`` hides a second cell in a partition (subgroups,
    eras), ``merge_sparse_groups`` merges adjacent bins of a calibration table instead of
    hiding them, and ``publishable_flow`` merges cohort-flow steps that exclude fewer than
    the minimum. Links between different tables are reviewed by hand before each release.

How to use it
    Build report dictionaries with ``count_cell`` and ``rate_cell`` instead of raw
    numbers, then save them with ``write_aggregate_json``.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Keys that would only appear in record-level output. Their presence anywhere in an
# output is treated as a leak, whatever the value.
RECORD_LEVEL_KEYS = frozenset(
    {
        "subject_id",
        "hadm_id",
        "stay_id",
        "charttime",
        "icu_intime",
        "icu_outtime",
        "death_time",
        "date_of_death",
        "recognition_time",
        "time_zero",
        "landmark_time",
    }
)
# A list longer than this is assumed to be per-patient values rather than a summary.
MAXIMUM_LIST_LENGTH = 400


class PrivacyViolationError(ValueError):
    """Raised when an output looks record-level or contains a small cell."""


def suppressed_label(minimum_cell_size: int) -> str:
    return f"<{minimum_cell_size}"


def count_cell(count: int, minimum_cell_size: int) -> int | str:
    """A count, or the suppression label if it is below the minimum cell size.

    A count of zero is also suppressed: "none" can be as revealing as "one".
    """
    count = int(count)
    return count if count >= minimum_cell_size else suppressed_label(minimum_cell_size)


def rate_cell(
    numerator: int, denominator: int, minimum_cell_size: int, decimals: int = 3
) -> dict[str, Any] | str:
    """A proportion with its counts, or the suppression label.

    Suppressed when the numerator, the complement (denominator minus numerator), or the
    denominator is below the minimum cell size: any of these would let a reader
    recover a small count from the published rate.
    """
    numerator, denominator = int(numerator), int(denominator)
    complement = denominator - numerator
    if min(numerator, complement, denominator) < minimum_cell_size:
        return suppressed_label(minimum_cell_size)
    return {
        "rate": round(numerator / denominator, decimals),
        "numerator": numerator,
        "denominator": denominator,
    }


def assert_aggregate_only(content: Any, path: str = "$") -> None:
    """Raise ``PrivacyViolationError`` if ``content`` looks like record-level data.

    Checks, recursively:
        * no key from ``RECORD_LEVEL_KEYS``;
        * no list longer than ``MAXIMUM_LIST_LENGTH``;
        * only JSON-compatible types (no DataFrames or arrays slipping through);
        * no non-finite numbers.
    """
    if isinstance(content, dict):
        for key, value in content.items():
            if str(key) in RECORD_LEVEL_KEYS:
                raise PrivacyViolationError(f"record-level key '{key}' at {path}")
            assert_aggregate_only(value, f"{path}.{key}")
    elif isinstance(content, list | tuple):
        if len(content) > MAXIMUM_LIST_LENGTH:
            raise PrivacyViolationError(
                f"list of length {len(content)} at {path} looks record-level"
            )
        for index, value in enumerate(content):
            assert_aggregate_only(value, f"{path}[{index}]")
    elif isinstance(content, bool) or content is None or isinstance(content, str):
        return
    elif isinstance(content, int | float):
        if isinstance(content, float) and not math.isfinite(content):
            raise PrivacyViolationError(f"non-finite number at {path}")
    else:
        raise PrivacyViolationError(f"unsupported type {type(content).__name__} at {path}")


def write_aggregate_json(
    content: dict[str, Any],
    output_path: Path,
    run_log_path: Path,
    description: str,
    compact: bool = False,
) -> str:
    """Check ``content``, write it as JSON, and append a line to the run log.

    Args:
        compact: write without indentation (for files served to browsers).

    Returns:
        SHA-256 of the bytes written, also recorded in the run log.
    """
    assert_aggregate_only(content)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(content, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    else:
        text = json.dumps(content, indent=2, ensure_ascii=False, allow_nan=False)
    data = (text + "\n").encode("utf-8")
    output_path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()

    run_log_path = Path(run_log_path)
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(run_log_path, "a", encoding="utf-8") as log:
        log.write(
            json.dumps(
                {
                    "written_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                    "file": output_path.name,
                    "sha256": digest,
                    "description": description,
                }
            )
            + "\n"
        )
    return digest


def _is_small(rows: int, events: int, minimum_cell_size: int, patients: int | None = None) -> bool:
    values = [rows, events, rows - events] + ([patients] if patients is not None else [])
    return min(values) < minimum_cell_size


def complementary_suppression(
    cells: dict[str, tuple[int, int, int]], minimum_cell_size: int
) -> set[str]:
    """Keys of a partition to hide so that no small cell can be worked out from the rest.

    Args:
        cells: for each group of a partition (for example each sex), the number of rows,
            patients and events. The groups add up to a total that is published elsewhere.

    Returns:
        The groups to hide: every small group (rows, patients, events or non-events below
        the minimum), plus the next-smallest groups until at least two are hidden and the
        hidden groups together hold at least the minimum of rows, events and non-events.
    """
    hidden = {
        key
        for key, (rows, patients, events) in cells.items()
        if _is_small(rows, events, minimum_cell_size, patients)
    }
    if not hidden:
        return hidden

    def hidden_totals_too_small() -> bool:
        rows = sum(cells[key][0] for key in hidden)
        events = sum(cells[key][2] for key in hidden)
        return _is_small(rows, events, minimum_cell_size)

    others = sorted((key for key in cells if key not in hidden), key=lambda key: cells[key][0])
    while others and (len(hidden) < 2 or hidden_totals_too_small()):
        hidden.add(others.pop(0))
    return hidden


def merge_sparse_groups(
    rows: list[int], events: list[int], minimum_cell_size: int
) -> list[list[int]]:
    """Merge adjacent bins (ordered, e.g. by predicted risk) until each can be published.

    Merging instead of hiding means no bin is missing, so no bin can be recovered by
    subtracting the others from the total. Works left to right; a short last group is
    merged into the one before it.

    Returns:
        Lists of original bin positions, one list per merged group.
    """
    groups: list[list[int]] = []
    current: list[int] = []
    for position in range(len(rows)):
        current.append(position)
        group_rows = sum(rows[index] for index in current)
        group_events = sum(events[index] for index in current)
        if not _is_small(group_rows, group_events, minimum_cell_size):
            groups.append(current)
            current = []
    if current:
        if groups:
            groups[-1].extend(current)
        else:
            groups.append(current)
    return groups


def publishable_flow(flow: list[tuple[str, int]], minimum_cell_size: int) -> list[dict]:
    """Cohort flow in which no step excludes between 1 and ``minimum_cell_size - 1`` stays.

    A step that excludes too few stays is merged with the following step, and their
    descriptions are joined; a short last step is merged into the one before it.
    """
    if not flow:
        return []
    groups: list[tuple[list[str], int]] = []  # (descriptions, count after the group)
    pending: list[str] = []
    start = flow[0][1]
    for description, count in flow[1:]:
        pending.append(description)
        excluded = start - count
        if excluded == 0 or excluded >= minimum_cell_size:
            groups.append((pending, count))
            pending, start = [], count
    if pending:
        final_count = flow[-1][1]
        while pending and groups:
            descriptions, _ = groups.pop()
            pending = descriptions + pending
            before = groups[-1][1] if groups else flow[0][1]
            excluded = before - final_count
            if excluded == 0 or excluded >= minimum_cell_size:
                break
        groups.append((pending, final_count))
    published = [{"step": flow[0][0], "stays": count_cell(flow[0][1], minimum_cell_size)}]
    for descriptions, count in groups:
        published.append(
            {"step": "; ".join(descriptions), "stays": count_cell(count, minimum_cell_size)}
        )
    return published

"""Refuse files that must never enter the repository.

Run on the files about to be committed (pre-commit) and on every tracked file (CI):

    python scripts/guard_data_files.py $(git ls-files)

What is refused, and why
    * Data files of any kind (CSV, Parquet, DuckDB, NumPy, pickles, spreadsheets):
      MIMIC-IV extracts are covered by the PhysioNet data use agreement and must stay
      on the analyst's computer. Synthetic test data is generated in code instead.
    * Jupyter notebooks: their saved outputs can carry patient-level tables.
    * Files larger than 2 MB: nothing in this project needs to be that large.
    * JSON under reports/ or web/data/ that is not aggregate-only, checked with the same
      guard the pipeline uses before writing any report.

Exit code 1 lists every refused file; 0 means all files are allowed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REFUSED_SUFFIXES = {
    ".csv",
    ".gz",
    ".parquet",
    ".feather",
    ".db",
    ".duckdb",
    ".sqlite",
    ".npy",
    ".npz",
    ".pkl",
    ".pickle",
    ".h5",
    ".hdf5",
    ".xlsx",
    ".xls",
    ".rds",
    ".sav",
    ".dta",
    ".ipynb",
}
REFUSED_DIRECTORIES = {"runs", "data_cache", "physionet", "mimic-iv", "mimiciv"}
MAXIMUM_BYTES = 2 * 1024 * 1024


def problems_with(path: Path) -> list[str]:
    problems = []
    if path.suffix.lower() in REFUSED_SUFFIXES:
        problems.append(f"{path}: data files and notebooks are not allowed ({path.suffix})")
    if REFUSED_DIRECTORIES.intersection(part.lower() for part in path.parts[:-1]):
        problems.append(f"{path}: lies in a folder reserved for local data or run outputs")
    if path.is_file() and path.stat().st_size > MAXIMUM_BYTES:
        problems.append(f"{path}: larger than {MAXIMUM_BYTES // 1024 // 1024} MB")
    if path.suffix == ".json" and path.is_file() and _is_published_data(path):
        problems.extend(_aggregate_problems(path))
    return problems


def _is_published_data(path: Path) -> bool:
    """Reports and the screen's data are published; both must be aggregate-only."""
    parts = path.parts
    return (len(parts) > 0 and parts[0] == "reports") or parts[:2] == ("web", "data")


def _aggregate_problems(path: Path) -> list[str]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from sepsis_decision_support.privacy.aggregate_guard import (
        PrivacyViolationError,
        assert_aggregate_only,
    )

    try:
        assert_aggregate_only(json.loads(path.read_text(encoding="utf-8")))
    except PrivacyViolationError as error:
        return [f"{path}: not aggregate-only ({error})"]
    return []


def main(arguments: list[str]) -> int:
    problems = [problem for name in arguments for problem in problems_with(Path(name))]
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

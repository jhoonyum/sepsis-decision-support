"""Command-line entry point: ``sepsis-support <command>``.

Commands
    run            prepare data, fit and validate D1, write the aggregate report and figures
    extract-check  extract the canonical tables from a mimic-code DuckDB file and print
                   aggregate counts (a smoke test for the SQL; nothing record-level is printed)
    m1-check       the M1 gate check on a mimic-code DuckDB file: database counts, published
                   and prototype cohort counts, both cohorts, outcome-definition grid
                   (aggregate-only JSON and Markdown)
    web-export     write the decision-support screen's data (synthetic patients only)
    check-privacy  check that a JSON file is aggregate-only
"""

from __future__ import annotations

import json
import pickle
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from sepsis_decision_support.config import REPOSITORY_ROOT, load_settings
from sepsis_decision_support.evaluation.markdown_report import render_markdown
from sepsis_decision_support.evaluation.runner import run_evaluation
from sepsis_decision_support.pipeline import prepare
from sepsis_decision_support.privacy.aggregate_guard import (
    assert_aggregate_only,
    write_aggregate_json,
)
from sepsis_decision_support.visualization.report_figures import draw_all

app = typer.Typer(add_completion=False, help="Sepsis decision support: build, validate and export.")

ConfigFiles = Annotated[
    list[Path] | None, typer.Option("--config", help="Extra YAML files, applied in order.")
]
DuckdbFile = Annotated[
    Path | None, typer.Option("--duckdb", help="mimic-code DuckDB file, opened read-only.")
]


REPORTED_PACKAGES = (
    "duckdb",
    "jax",
    "matplotlib",
    "numpy",
    "numpyro",
    "pandas",
    "pydantic",
    "scikit-learn",
    "scipy",
)


def _package_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    versions = {}
    for package in REPORTED_PACKAGES:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def _git_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


@app.command()
def run(
    config: ConfigFiles = None,
    source: Annotated[
        str | None, typer.Option(help="synthetic or mimic_duckdb (overrides the config).")
    ] = None,
    duckdb_path: DuckdbFile = None,
    stays: Annotated[
        int | None, typer.Option(help="Number of synthetic stays (synthetic source only).")
    ] = None,
    run_name: Annotated[
        str | None, typer.Option(help="Name of the run folder; defaults to a timestamp.")
    ] = None,
    quick: Annotated[
        bool, typer.Option(help="Few bootstrap replicates: for smoke tests, not for results.")
    ] = False,
) -> None:
    """Prepare data, fit and validate the landmark model, and write the run folder."""
    overrides: dict = {"data": {}}
    if source:
        overrides["data"]["source"] = source
    if duckdb_path:
        overrides["data"]["duckdb_path"] = str(duckdb_path)
    if stays:
        overrides["data"]["synthetic"] = {"number_of_stays": stays}
    if quick:
        overrides["evaluation"] = {"bootstrap_replicates": 20}
        overrides["model"] = {"landmark_logistic": {"bootstrap_replicates": 5}}
    settings = load_settings(*(config or []), overrides=overrides)

    name = run_name or datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + f"-{settings.data.source}"
    run_directory = settings.runs.directory / name
    run_directory.mkdir(parents=True, exist_ok=True)
    typer.echo(f"Run folder: {run_directory}")

    prepared = prepare(settings)
    typer.echo(
        f"Cohort: {len(prepared.cohort.stays):,} stays; "
        f"{len(prepared.training.rows):,} training landmarks; "
        f"{len(prepared.hourly.rows):,} hourly rows"
    )
    result = run_evaluation(prepared, settings)
    result.report["code_revision"] = _git_revision()
    result.report["created_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    result.report["python"] = platform.python_version()
    result.report["package_versions"] = _package_versions()

    log_path = run_directory / "privacy_log.jsonl"
    write_aggregate_json(
        result.report, run_directory / "evaluation_report.json", log_path, "evaluation report"
    )
    settings_text = json.dumps(settings.model_dump(mode="json"), indent=2) + "\n"
    (run_directory / "settings.json").write_text(settings_text)
    figure_paths = draw_all(result.report, run_directory / "figures")
    markdown = render_markdown(result.report) + "\n"
    (run_directory / "evaluation_report.md").write_text(markdown, encoding="utf-8")

    # Patient-level outputs: kept in the run folder on this machine, never published.
    result.predictions.to_parquet(run_directory / "predictions_patient_level.parquet")
    with open(run_directory / "models.pickle", "wb") as file:
        bundle = {"models": result.models, "baselines": result.baselines, "settings": settings}
        pickle.dump(bundle, file)
    typer.echo(
        f"Wrote report, {len(figure_paths)} figure files and patient-level files (local only)."
    )


@app.command("extract-check")
def extract_check(duckdb_path: DuckdbFile = None, config: ConfigFiles = None) -> None:
    """Extract and validate the canonical tables, then print aggregate counts only.

    Used by CI on the open MIMIC-IV demo, and on the full database as the first check
    after a build. Counts below the minimum cell size are printed as "<11", and cohort-flow
    steps that exclude fewer than the minimum are merged with the next step.
    """
    from sepsis_decision_support.cohort.cohort_builder import build_cohort
    from sepsis_decision_support.pipeline import landmark_data, load_tables
    from sepsis_decision_support.privacy.aggregate_guard import count_cell, publishable_flow

    if duckdb_path is None:
        raise typer.BadParameter("--duckdb is required")
    overrides = {"data": {"source": "mimic_duckdb", "duckdb_path": str(duckdb_path)}}
    settings = load_settings(*(config or []), overrides=overrides)
    minimum = settings.privacy.minimum_cell_size

    tables = load_tables(settings)  # raises if a table fails validation
    cohort = build_cohort(tables, settings.cohort)
    training = landmark_data(tables, cohort, settings.landmarks.training_hours, settings)
    summary = {
        "source": str(duckdb_path.name),
        "table_rows": {
            name: count_cell(len(table), minimum) for name, table in tables.as_dictionary().items()
        },
        "cohort_flow": publishable_flow(cohort.flow, minimum),
        "training_landmarks": count_cell(len(training.rows), minimum),
        "training_landmarks_by_population": {
            population: count_cell(count, minimum)
            for population, count in training.rows["population"].value_counts().items()
        },
    }
    assert_aggregate_only(summary)
    typer.echo(json.dumps(summary, indent=2))


@app.command("m1-check")
def m1_check(
    duckdb_path: DuckdbFile = None,
    config: ConfigFiles = None,
    run_name: Annotated[
        str | None, typer.Option(help="Name of the run folder; defaults to m1-check-<date>.")
    ] = None,
) -> None:
    """Run the aggregate-only M1 gate check and print its Markdown summary.

    Writes ``m1_check.json`` (checked by the aggregate guard) and ``m1_check.md`` to the
    run folder. Nothing record-level is written or printed. Takes a few minutes on the
    full database.
    """
    from sepsis_decision_support.checks.m1 import build_m1_report, collect_m1_inputs
    from sepsis_decision_support.checks.m1_markdown import render_m1_markdown

    if duckdb_path is None:
        raise typer.BadParameter("--duckdb is required")
    overrides = {"data": {"source": "mimic_duckdb", "duckdb_path": str(duckdb_path)}}
    settings = load_settings(*(config or []), overrides=overrides)
    name = run_name or "m1-check-" + datetime.now(UTC).strftime("%Y%m%d")
    run_directory = settings.runs.directory / name
    run_directory.mkdir(parents=True, exist_ok=True)

    inputs = collect_m1_inputs(duckdb_path, settings)
    report = build_m1_report(inputs, settings)
    report["code_revision"] = _git_revision()
    report["created_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    report["package_versions"] = _package_versions()
    write_aggregate_json(
        report, run_directory / "m1_check.json", run_directory / "privacy_log.jsonl", "M1 check"
    )
    markdown = render_m1_markdown(report) + "\n"
    (run_directory / "m1_check.md").write_text(markdown, encoding="utf-8")
    typer.echo(markdown)
    typer.echo(f"Wrote {run_directory / 'm1_check.json'} and m1_check.md")


@app.command("web-export")
def web_export(
    run_directory: Annotated[Path, typer.Argument(help="Run folder written by `run`.")],
    output_directory: Annotated[
        Path, typer.Option(help="Where the screen reads its data.")
    ] = REPOSITORY_ROOT / "web" / "data",
) -> None:
    """Write the screen's data: model summaries plus synthetic patients scored by the model."""
    from sepsis_decision_support.decision_support.export_for_web import export_screen_data

    paths = export_screen_data(run_directory, output_directory)
    for path in paths:
        typer.echo(f"Wrote {path}")


@app.command("check-privacy")
def check_privacy(path: Path) -> None:
    """Exit with an error if a JSON file looks record-level."""
    assert_aggregate_only(json.loads(path.read_text()))
    typer.echo(f"{path}: aggregate-only")


if __name__ == "__main__":
    app()

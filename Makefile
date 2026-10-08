# Common tasks. `make help` lists them.
#
# Real MIMIC-IV data is only ever read on the analyst's own computer; the targets that
# touch it (mimic-check, mimic) print or write aggregates only. See docs/data_governance.md.

SHELL := /bin/bash
PYTHON ?= python
RUNS ?= $(HOME)/sepsis_runs
RUN ?= synthetic-default
DEMO_DIR ?= $(HOME)/physionet/mimic-iv-demo
MIMIC_DB ?= $(HOME)/physionet/mimic4.db

.DEFAULT_GOAL := help
.PHONY: help setup lint format test guard synth demo-data demo-check mimic-check mimic screen serve check-screen

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-13s %s\n", $$1, $$2}'

setup: ## Install the pinned packages and this package into the active environment
	$(PYTHON) -m pip install -r requirements-lock.txt
	$(PYTHON) -m pip install -e . --no-deps

lint: ## Lint and check formatting
	ruff check .
	ruff format --check .

format: ## Format the code
	ruff format .

test: ## Unit tests (generated data only)
	pytest

guard: ## Refuse data files, notebooks and large files among tracked files
	$(PYTHON) scripts/guard_data_files.py $$(git ls-files)

synth: ## Full run on synthetic data (a few minutes); writes $(RUNS)/synthetic-default
	sepsis-support run --run-name synthetic-default

demo-data: ## Download the open MIMIC-IV demo and build its DuckDB file (needs the DuckDB CLI)
	scripts/build_mimic_demo.sh $(DEMO_DIR)

demo-check: ## Extract and validate the canonical tables from the demo; run the SQL tests
	sepsis-support extract-check --duckdb $(DEMO_DIR)/mimic4_demo.db
	MIMIC_DEMO_DUCKDB=$(DEMO_DIR)/mimic4_demo.db pytest -m demo

mimic-check: ## Same check on the full MIMIC-IV file (aggregate output only)
	sepsis-support extract-check --duckdb $(MIMIC_DB)

mimic: ## Full run on MIMIC-IV; the run folder stays in $(RUNS)
	sepsis-support run --source mimic_duckdb --duckdb $(MIMIC_DB) --run-name mimic-$$(date +%Y%m%d)

screen: ## Write the screen data from a run folder: make screen RUN=<run name>
	sepsis-support web-export $(RUNS)/$(RUN)

serve: ## Serve the screen at http://localhost:8000
	cd web && $(PYTHON) -m http.server 8000

check-screen: ## Drive every control of the screen and compare it with its data
	$(PYTHON) scripts/check_screen.py

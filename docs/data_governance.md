# Data governance

MIMIC-IV is credentialed data. This page states the rules the project follows, how they are enforced, and how to obtain and build the data.

## Rules

1. **Record-level data stays on the analyst's computer.** Raw files, the DuckDB database, extracted tables, patient-level predictions and fitted-model pickles never enter the repository and are never sent to an online service, including AI assistants. The PhysioNet credentialed data use agreement says "I will not share access to PhysioNet restricted data with anyone else", and PhysioNet's notice on large language models and online services (24 September 2025) asks users not to send the data to such services.
2. **Only aggregates are published**, and no cell is smaller than 11: no count below 11, and no rate whose numerator or complement (denominator minus numerator) is below 11, since either would let a reader recover a small count.
3. **The code that produced published results is published with them**, as the agreement asks ("If I openly disseminate my results, I will also contribute the code used to produce those results ...").
4. **The screen shows synthetic patients only.** From v0.2 the model behind it is fitted on MIMIC-IV; its coefficients and bootstrap spread are aggregates. Patient-level outputs and full posterior draws are not published.
5. **No notebooks.** Saved notebook outputs can carry patient-level tables.

## How the rules are enforced

| Mechanism | What it does |
|---|---|
| `privacy/aggregate_guard.py` | Every report and the screen's data go through `write_aggregate_json`, which refuses record identifiers (`subject_id`, `stay_id`, `charttime`, ...), lists longer than 400 entries, non-finite numbers and non-JSON types, then logs the SHA-256 of the written file to `privacy_log.jsonl` in the run folder. `count_cell` and `rate_cell` apply the small-cell rule. |
| Linked cells | A hidden cell could be worked out as a published total minus the other cells. Calibration bins and risk bands that are too small are merged with their neighbours (`merge_sparse_groups`); cohort-flow steps that exclude 1 to 10 stays are merged with the next step (`publishable_flow`); in subgroup and era tables, a second group is hidden whenever one is (`complementary_suppression`). Links between different tables are checked by hand before each release. |
| Run folders outside the repository | Runs are written to `~/sepsis_runs/<run name>/` (configurable). Patient-level files stay there. |
| `scripts/guard_data_files.py` | Refuses data files (CSV, Parquet, DuckDB, NumPy, pickles, spreadsheets), notebooks, files over 2 MB and non-aggregate JSON under `reports/` and `web/data/`. Runs on every tracked file in CI, and before each commit once the pre-commit hook is installed (`pre-commit install`). |
| `.gitignore` | Ignores the same file types and the local data folders. |
| `sepsis-support extract-check` | The first check after a build prints table sizes and the cohort flow only, already suppressed. |

## Where things run

- **On the analyst's Mac:** download, DuckDB build, extraction, model fitting and evaluation. Results are written to the run folder; the aggregate report is what gets shared.
- **AI assistance** sees code, documentation and aggregate outputs that have passed the guard. Short aggregate checks are run only through reviewed scripts that pass the same guard.
- **CI** uses synthetic data and the open MIMIC-IV demo only.

## Getting and building MIMIC-IV v3.1

Requirements: a credentialed PhysioNet account, completed CITI "Data or Specimens Only Research" training, and the signed data use agreement for MIMIC-IV. The project page is https://physionet.org/content/mimiciv/3.1/.

Disk space: about 10 GB for the compressed download and about 25 GB for the database plus a few GB of derived concepts (mimic-code's DuckDB notes, DuckDB 1.4). Keep the files compressed; DuckDB reads `.csv.gz` directly.

Keep the data out of folders synced to a cloud service (Desktop and Documents are often synced by iCloud), do not connect the data folder to any AI tool, and keep disk encryption (FileVault) on.

```bash
# 1. Environment (Miniforge / conda-forge); environment-mimic-build.yml is a smaller alternative
mamba env create -f environment.yml
conda activate sepsis_hmm
duckdb --version                      # v1.4.4

# 2. Download (resumable: re-run the same command if interrupted)
mkdir -p ~/physionet/raw && cd ~/physionet/raw
caffeinate -i wget -r -N -c -np --user <PhysioNet username> --ask-password \
    https://physionet.org/files/mimiciv/3.1/
cd ~/physionet/raw/physionet.org/files/mimiciv/3.1
shasum -a 256 -c SHA256SUMS.txt | grep -v ': OK$'      # no output means every file matches

# 3. Build with mimic-code at the pinned commit (resumes if interrupted)
cd ~/physionet
git clone https://github.com/MIT-LCP/mimic-code.git
cd mimic-code && git checkout 303d26c
cd mimic-iv/buildmimic/duckdb
caffeinate -i ./build_mimic.sh ~/physionet/raw/physionet.org/files/mimiciv/3.1 ~/physionet/mimic4.db \
    2>&1 | tee ~/physionet/build_log.txt

# 4. Check, from the repository
make setup
make mimic-check
```

mimic-code's `download_data.sh` is not used: its `--cut-dirs=4` drops the `hosp/` and `icu/` folder level that `build_mimic.sh` expects. The plain `wget` above keeps PhysioNet's folder structure.

The build log ends with a row-count check of every table against mimic-code's expected counts. That table holds no patient data and can be shared.

## The open demo

The MIMIC-IV Clinical Database Demo (version 2.2, 100 patients) is open access under the Open Database License. `scripts/build_mimic_demo.sh` downloads and builds it the same way; CI and `make demo-check` run the project's SQL on it. Demo results are never reported as findings.

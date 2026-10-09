# M1 runbook: build MIMIC-IV and run the M1 check on your Mac

M1 is the gate before the first real results (v0.2). It passes when:

1. the database build passes mimic-code's row-count check;
2. the count of Sepsis-3 among first ICU stays is compared with the published count (Yang et al. 2025: 28,087 of 65,366 in MIMIC-IV 3.0) and any difference is explained;
3. the course prototype's funnel (94,458 / 65,366 / 58,506 / 29,421 / 1,502) is reproduced exactly;
4. the outcome definition is fixed from the M1 check's aggregates and written into [analysis_plan.md](analysis_plan.md), which is then registered on OSF.

Everything below runs in Terminal on your Mac. Nothing here sends data anywhere. The outputs you may share (with a person or an AI assistant) are listed in step 6.

## 0. Before you start

- PhysioNet: credentialed account, CITI training, and the MIMIC-IV 3.1 data use agreement signed on the project page.
- Disk: about 40 GB free (10 GB download, 25 to 30 GB database). Keep `~/physionet` out of iCloud-synced folders (Desktop, Documents) and keep FileVault on.
- Do not connect `~/physionet` or `~/sepsis_runs` to any AI tool.
- Do not run `make mimic` (model fitting and the temporal hold-out) until the analysis plan is registered.

## 1. Environment

```bash
cd ~/path/to/sepsis-decision-support
git pull
mamba env create -f environment.yml        # first time; later: mamba env update -f environment.yml --prune
conda activate sepsis_hmm
make setup
duckdb --version                           # v1.4.4
```

## 2. Download (several hours)

```bash
mkdir -p ~/physionet/raw && cd ~/physionet/raw
caffeinate -i wget -r -N -c -np --user <PhysioNet username> --ask-password \
    https://physionet.org/files/mimiciv/3.1/
```

The command resumes where it stopped if you run it again. wget fetches one file at a time; if that is too slow, stop it and download the two large folders in two Terminal windows at once (same starting folder, so the layout is the same):

```bash
# window 1
cd ~/physionet/raw
caffeinate -i wget -r -N -c -np --user <PhysioNet username> --ask-password https://physionet.org/files/mimiciv/3.1/hosp/
# window 2
cd ~/physionet/raw
caffeinate -i wget -r -N -c -np --user <PhysioNet username> --ask-password https://physionet.org/files/mimiciv/3.1/icu/
```

When both have finished, run the first command once more: it skips the finished files and fetches the small files in the top folder, including `SHA256SUMS.txt`.

Check every file:

```bash
cd ~/physionet/raw/physionet.org/files/mimiciv/3.1
shasum -a 256 -c SHA256SUMS.txt | grep -v ': OK$'      # no output means every file matches
```

## 3. Build the database (an hour or more)

```bash
cd ~/physionet
git clone https://github.com/MIT-LCP/mimic-code.git
cd mimic-code && git checkout 303d26c
cd mimic-iv/buildmimic/duckdb
caffeinate -i ./build_mimic.sh ~/physionet/raw/physionet.org/files/mimiciv/3.1 ~/physionet/mimic4.db \
    2>&1 | tee ~/physionet/build_log.txt
tail -60 ~/physionet/build_log.txt
```

The end of the log is mimic-code's row-count check (table names and counts, no patient data). Every table should match. If one does not, re-check the download (step 2) and rebuild; the build resumes where it stopped.

## 4. Quick check (several minutes)

```bash
cd ~/path/to/sepsis-decision-support
make mimic-check
```

This extracts the project's tables and prints their sizes and the main cohort's flow, with small counts suppressed and small steps merged. It fails loudly if a table does not have the expected columns.

## 5. The M1 check (probably under 15 minutes)

```bash
make m1-check
```

It prints a Markdown report and writes `m1_check.json` and `m1_check.md` to `~/sepsis_runs/m1-check-<date>/`. What each section is for:

| Section | What to look for | If it fails |
|---|---|---|
| Gate summary, Database | every table matches the 3.1 counts | wrong version or incomplete build: steps 2 and 3 |
| Sepsis-3 among first ICU stays | first ICU stays 65,366; Sepsis-3 close to 28,087. Differences are expected: 3.1 corrected `icustays` and `microbiologyevents`, and mimic-code has changed since the paper | a difference of more than a few percent needs an explanation before v0.2; if none is found, v0.2 uses the sensitivity cohort (analysis plan, section 3) |
| Course prototype funnel | every step matches | compare step by step; the first two steps depend only on `icustays`, the last on the item lists and time rules in `src/sepsis_decision_support/data/sql/m1_course_funnel.sql` |
| Cohorts | both flows, hours from admission to recognition, the criterion completed last, the overlap | decides nothing on its own; it goes into the v0.2 report |
| Outcome definitions | how the event rate moves when one part of the definition changes, how often lactate is drawn around hypotension, how much pressure data come from an arterial line | fix the values marked [M1] in the analysis plan by its section 4 |

The check never writes record-level files. Counts below 11 are shown as `<11`; groups hidden together in a partition are marked `hidden`; counts in the outcome-definition tables are rounded to the nearest 10. A count of stays or patients that would differ by 1 to 10 from another count or reference figure in the report is withheld and listed at the end. If a funnel step is withheld or does not match, look at the exact counts on your Mac only: `duckdb -readonly ~/physionet/mimic4.db < src/sepsis_decision_support/data/sql/m1_course_funnel.sql` (do not share that output).

## 6. What you may share

- `tail -60 ~/physionet/build_log.txt`
- the output of `make mimic-check`
- `~/sepsis_runs/m1-check-<date>/m1_check.md` (read it once before sharing)

Never share anything else from `~/physionet` or `~/sepsis_runs`, in particular `predictions_patient_level.parquet` and `models.pickle`, which a full run writes.

## 7. After M1

1. Fill in the [M1] values and the [decide] items in [analysis_plan.md](analysis_plan.md), commit, and register the plan on OSF (the registration freezes that commit's text).
2. Add the OSF link to the README and the model card.
3. Then run `make mimic` for v0.2.

# Decision log

Each entry records the choice, the alternatives, why, and what result would change it. Newest entries are added at the end.

## 1. An independent rewrite, not a fork (2026-10-08)

- **Choice:** a new repository with a package and command-line steps, no notebooks; the course prototype is not linked.
- **Alternatives:** clean up the prototype's notebooks; fork the team repository.
- **Why:** the prototype's problems were structural (see prototype_lessons.md), and a reviewer should be able to run every step from a fresh clone.
- **Would change if:** never; this is the premise of the project.

## 2. The screen keeps the two-by-two question but shows numbers only where the data support them (2026-10-08)

- **Choice:** "decide now / wait one hour" against "usual care / start a treatment". The usual-care column shows validated predictions (X, p1, Z). The treatment column shows the evidence status of the selected treatment, with no model number.
- **Alternatives:** drop the treatment column; show observational treatment estimates with a warning.
- **Why:** observational ICU data cannot separate the effect of starting a treatment from the reason it was started (design_rationale.md, section 3). A warning does not stop a number from being read as an effect.
- **Would change if:** the planned target-trial emulation of norepinephrine timing passes its pre-specified checks; then that one row gets an estimate.

## 3. Cohort: Sepsis-3 recognised within 24 hours of ICU admission; time zero at recognition (2026-10-08)

- **Choice:** adults, first ICU stay, Sepsis-3 recognised within 24 h of admission, cardiac and thoracic surgery services excluded. Time zero is the recognition time R, or ICU admission if R came first.
- **Alternatives:** the course definition (an IV antibiotic and a culture within one hour, stays of 12 hours to 10 days), planned as a sensitivity cohort without its length-of-stay filter; ICU admission as time zero.
- **Why:** R is the first moment the patient could have been known to belong to the cohort, so nothing is selected on the future, and mimic-code's Sepsis-3 concepts make the cohort comparable with published work.
- **Would change if:** the full-data cohort count differs from published MIMIC-IV Sepsis-3 counts in a way the definitions do not explain; v0.2 would then use the sensitivity cohort.

## 4. Outcome: shock defined without vasopressors, or death, within 24 hours (2026-10-08)

- **Choice:** sustained hypotension with lactate above 2 mmol/L, or death; death only for patients already in the shock stage. Death within 28 days as a secondary outcome.
- **Alternatives:** Sepsis-3 septic shock, which needs a vasopressor (planned as a sensitivity analysis); death alone.
- **Why:** an outcome defined by a vasopressor would make the model predict the treatment decision, and the screen offers that decision.
- **Would change if:** lactate is measured too rarely for the definition to be stable; the thresholds and windows are fixed from aggregate distributions before the analysis plan is registered.

## 5. Penalised logistic regression first, Bayesian continuous-time HMM second (2026-10-07)

- **Choice:** D1, one penalised logistic regression per stage and per number on stacked decision times. B1, a continuous-time HMM, is compared with D1 under a rule written before the comparison: B1 becomes the engine if its Brier score is no more than 0.005 worse than D1's and it meets the calibration criteria.
- **Alternatives:** gradient boosting; B1 alone.
- **Why:** D1 is fast, calibrates well, and its predictions split exactly into per-input contributions for the "what moves this risk" panel. B1 adds a model of how patients move between stages but costs hours per fit.
- **Would change if:** B1 meets the rule (then B1 drives the screen), or its evaluation is not finished in time for v1.0 (then B1 is released after v1.0).

## 6. DuckDB with mimic-code instead of reading CSV files with pandas (2026-10-08)

- **Choice:** build MIMIC-IV into DuckDB with mimic-code (commit 303d26c, DuckDB CLI 1.4.4); extract five small canonical tables with SQL; do the rest in pandas.
- **Alternatives:** read the compressed CSV files in chunks with pandas, as the prototype did.
- **Why:** mimic-code supplies reviewed definitions of SOFA, suspected infection, vasopressor equivalents and ventilation, so they are not re-implemented. The largest table, chartevents, has about 433 million rows; DuckDB reads only the columns a query needs, uses every core, and handles time-window joins without chunk boundaries.
- **Would change if:** a mimic-code concept turns out wrong for version 3.1; that concept would then be replaced by project SQL with its own tests.

## 7. Where real data runs (2026-10-08)

- **Choice:** heavy work on the analyst's Mac; short aggregate checks only through reviewed scripts that pass the aggregate guard; AI assistance sees code and aggregates only.
- **Alternatives:** run everything in a cloud workspace.
- **Why:** the data use agreement and PhysioNet's notice on online services.
- **Would change if:** never loosened; may be tightened.

## 8. The public screen uses the real model with synthetic patients (2026-10-08)

- **Choice:** from v0.2, synthetic patients scored by the model fitted on MIMIC-IV. Coefficients and their bootstrap spread are published; B1 parameter summaries from v0.3, not full posterior draws.
- **Alternatives:** keep a synthetic model on the screen.
- **Why:** coefficients are aggregates, like a regression table in a paper, and a real model makes the demo meaningful.
- **Would change if:** PhysioNet guidance on sharing trained models changes.

## 9. Antibiotics are shown as already started instead of hidden (2026-10-08)

- **Choice:** the antibiotic row stays in the treatment table with the status "Already started" and the note that timing evidence applies only before the first dose.
- **Alternatives:** hide the row for patients who have started antibiotics, which in this cohort is every patient.
- **Why:** the Sepsis-3 definition requires antibiotics, so hiding the row would remove antibiotics from the screen entirely and leave the reader wondering why.
- **Would change if:** the cohort is widened to patients before antibiotic start (for example infections that begin in the ICU).

## 10. The treatment column follows the selected option (2026-10-08)

- **Choice:** selecting a row in the treatment table changes the header and both status cells of the two-by-two table.
- **Alternatives:** one fixed summary across all treatments.
- **Why:** the original question is about choosing one action; the cells should answer for that action.

## 11. Alert rule for the alert-burden evaluation (2026-10-08)

- **Choice:** scoring every hour, an alert fires when the risk first reaches the threshold, repeats once per 24-hour horizon while the risk stays above it, and fires again after the risk falls below and rises.
- **Alternatives:** fire only on rising edges.
- **Why:** with rising edges only, a patient who stays high is flagged once, which made sensitivity fall as the threshold dropped. Re-alerting daily is how deployed deterioration alerts usually behave.

## 12. Environments (2026-10-08)

- **Choice:** Miniforge (conda-forge only) supplies Python and command-line tools; pip installs exact versions from `requirements-lock.txt`. A separate environment holds the DuckDB CLI for building MIMIC-IV.
- **Why:** the same pinned versions on macOS (arm64) and in CI (Linux).

## 13. Settings fingerprint ignores machine paths (2026-10-08)

- **Choice:** the fingerprint stored with every run hashes all settings except the run folder and the DuckDB file location.
- **Why:** the same analysis should have the same fingerprint on any computer.

## 14. AI assistance is disclosed (2026-10-08)

- **Choice:** commits keep their AI co-author lines, and the README says the project was built with AI pair programming.
- **Why:** it is better to explain how the tool was used and what was checked by hand than to hide it.

## 15. Release plan (2026-10-08)

- **Choice:** four releases: v0.1 skeleton and screen on synthetic data; v0.2 first MIMIC-IV results; v0.3 the Bayesian model; v1.0 a technical report and a demo video.
- **Would change if:** v0.2 slips by more than a week; then B1 is released after v1.0.

## 16. No hidden cell can be worked out from the others (2026-10-08)

- **Choice:** calibration bins and risk bands too small to publish are merged with their neighbours; cohort-flow steps that exclude 1 to 10 stays are merged with the next step; in subgroup and era tables, a second group is hidden whenever one is, until the hidden groups together reach the minimum.
- **Alternatives:** hide small cells only; round every count to the nearest 10.
- **Why:** a review found that hiding one cell was not enough: the total minus the published cells gave it back (for example, the events in a hidden risk band).
- **Would change if:** the tables grow enough that a dedicated statistical disclosure control tool is worth adding.

## 17. What "change over 6 hours" means (2026-10-08)

- **Choice:** the latest value minus the latest value taken at least 6 hours earlier, provided that one was taken within the last 24 hours; otherwise missing, with a "not measured" indicator.
- **Alternatives:** the earlier version used a reference value of any age and wrote 0 when there was none.
- **Why:** a 30-hour-old value says little about the recent trend, and 0 claimed "no change" when nothing was known.

## 18. The sensitivity cohort changes the infection definition only (2026-10-08)

- **Choice:** the `culture_antibiotic_pair` cohort uses the course prototype's infection rule (an ICU antibiotic infusion and an ICU culture within one hour; R is the later of the two in the first complete pair) and every other rule of the main cohort: adults, first ICU stay, R within 24 h of ICU admission, cardiac and thoracic surgery services excluded.
- **Alternatives:** the prototype's cohort as it was, without only its length-of-stay filter (which would keep its 18 to 65 age limit and its infusion-end rule).
- **Why:** a difference between the two cohorts' results should come from the infection definition alone. The infusion-end rule selects on the future like the length-of-stay filter. An upper age limit of 65 would remove roughly half of the sepsis population (about half are 65 or older in other MIMIC-IV pipelines) and every patient the 65 trial row is about.
- **Would change if:** the M1 check shows the pair rule finds a population so different (for example in time to recognition) that the 24-hour window means something else for it.

## 19. Measurements and treatments are read for cohort stays only (2026-10-08)

- **Choice:** the extraction selects the cohort from the stays and recognition tables first, then reads measurements, treatments and hourly SOFA for those stays only. The stays table still holds every ICU stay, so the cohort flow is complete.
- **Why:** on the full database the unrestricted measurement table would be several times larger than needed in memory; the cohort is expected to be roughly a fifth of ICU stays. A demo test checks that the restricted tables equal the unrestricted ones for cohort stays.

## 20. The cohort flow starts from every ICU stay (2026-10-08)

- **Choice:** stays without a discharge time are kept in the stays table and removed by the first cohort rule, "ICU discharge time recorded".
- **Why:** the M1 check publishes the database's count of ICU stays next to the flow. If the flow started after dropping stays without a discharge time, the two counts could differ by a handful of stays, a small group anyone could work out.

## 21. What the M1 check publishes, and how (2026-10-08)

- **Choice:** one command (`sepsis-support m1-check`, `make m1-check`) writes an aggregate JSON and Markdown report: table sizes against mimic-code's expected 3.1 counts, Sepsis-3 among first ICU stays against the published count, the course funnel reproduced rule for rule, both cohorts' flows, hours from admission to recognition, the criterion completed last, the cohorts' overlap, an outcome-definition table changing one part of the definition at a time, and how often lactate and arterial pressure are measured.
- **Disclosure control beyond the usual rules:** counts in tables of alternative definitions are rounded to the nearest 10 (two rows counting the same patients differ by a group that could be small). Every exact count of stays or patients, including the cohort-flow steps, is compared with the reference figures the report prints (mimic-code's expected counts, the prototype's funnel, the published Sepsis-3 count) and with the counts published before it; one within 10 of them is withheld together with whatever would reveal it. The time bins are not split at the 24-hour limit unless the flow publishes the count within it.
- **Why:** the outcome definition must be fixed before registration from how the data behave, not from model performance, and these numbers are the evidence for that choice.

## 22. Sepsis-3 operational shock as a sensitivity outcome (2026-10-08)

- **Choice:** a vasopressor episode (intervals no more than 60 minutes apart joined) with lactate above the threshold in the same window around its start (`configs/outcomes/sepsis3_operational_shock.yaml`).
- **Why:** it is the definition most published work uses. Reporting it next to the main outcome shows how much of the "shock" signal is the decision to start a vasopressor.

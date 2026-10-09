# Analysis plan (draft for OSF registration)

**Status:** draft, not yet registered. It is registered on OSF after the M1 check and before any model is fitted on MIMIC-IV or the temporal hold-out is evaluated. Items marked **[M1]** are fixed from the M1 check's aggregate counts (outcome frequencies and measurement availability, never model performance). The three choices left open in the first draft (the order of the principles in section 4, what counts as a material difference for the arterial-line series, and the calibration criteria in section 8) were confirmed on 2026-10-08. The registered version replaces this draft, and any later change is listed under "Deviations" with its reason and date.

Structure: OSF's template for secondary data analysis (van den Akker et al. 2021), with the TRIPOD+AI items for prediction-model development (Collins et al. 2024).

## 1. Aims

1. **Primary.** Develop and validate a model that gives, at each decision time after sepsis recognition in the ICU, the probability that the patient reaches the next stage within 24 hours if care continues as usual. For pre-shock patients the next stage is shock or death; for patients on a vasopressor or after shock it is death.
2. **Secondary.** The same for the next hour (p1) and for 24 hours starting one hour later (Z), and for death within 28 days.
3. **Comparison.** Compare a Bayesian continuous-time hidden Markov model (B1, v0.3) with the landmark model (D1) under the rule in section 8, written before either is fitted on MIMIC-IV.

There are no hypothesis tests. Every result is an estimate with an interval.

## 2. Data

- **Source:** MIMIC-IV version 3.1 (Beth Israel Deaconess Medical Center; ICU admissions 2008 to 2022), built into DuckDB with mimic-code at commit 303d26c. Access under the PhysioNet credentialed data use agreement.
- **What has been seen before registration:** the course prototype's notebooks on MIMIC-IV (a different cohort and outcome; see docs/prototype_lessons.md), and the M1 check's aggregate output. No model in this plan has been fitted on MIMIC-IV, and no outcome rate has been looked at by era.
- **Code:** this repository; every run records the code revision, settings fingerprint and package versions.

## 3. Participants

- **Main cohort (Sepsis-3, "sepsis3_early"):** ICU stays with a discharge time; age 18 or older at admission; the patient's first ICU stay; Sepsis-3 recognised during the stay (mimic-code's suspicion of infection with an antibiotic started in the ICU, and SOFA of 2 or more from 48 h before to 24 h after the suspicion time); recognition within 24 h of ICU admission; hospital service at ICU admission not cardiac or thoracic surgery (CSURG, TSURG).
- **Sensitivity cohort ("culture_antibiotic_pair"):** the same rules with the course prototype's infection definition instead of Sepsis-3: an IV antibiotic infusion and a culture, both charted in the ICU, within one hour of each other. The prototype's stay-length filter (12 hours to 10 days), infusion-end rule and age limit (18 to 65) are not used.
- **Time zero:** the recognition time R, the earliest moment every criterion of the definition was on record (or ICU admission, if later). In the main cohort, a culture recorded with a date only (microbiology records) counts at the end of that day; the sensitivity cohort's cultures are ICU chart entries with a time and are used as charted.
- **Fallback (decided before M1):** if the main cohort's size differs from the published Sepsis-3 count in a way the definitions do not explain, v0.2 is released with the sensitivity cohort as the main analysis.

## 4. Outcomes

| | Definition | Status |
|---|---|---|
| Primary, pre-shock patients | Shock or death within 24 h. Shock: sustained hypotension (two or more MAP readings below **65** mmHg at least **30** min apart, no normal reading between them, no gap over **120** min; readings outside 20 to 200 mmHg ignored) with lactate above **2** mmol/L from **6** h before to **6** h after the moment the hypotension became sustained. Event time: when both parts are on record. Shock only counts while the patient is in the ICU; death counts anywhere (exact time in hospital, date after discharge). | **[M1]** the bold values, and the MAP source: mimic-code's combined arterial and cuff series, or arterial line only |
| Primary, shock stage | Death within 24 h | fixed |
| Secondary | The same events within the next hour, and within 24 h starting 1 h later; death within 28 days | fixed |
| Sensitivity | Sepsis-3 operational shock: a vasopressor episode (intervals no more than 60 min apart joined) with lactate above 2 mmol/L in the same window around its start | fixed |

**How M1 fixes the bold values.** The M1 check reports, for the main cohort, how often the outcome occurs at time zero and across decision times when each value is changed on its own, how often lactate is measured around time zero and around the first sustained hypotension, and how much of the pressure record comes from an arterial line. The values are fixed by these principles, in this order (confirmed 2026-10-08):

1. Keep the Sepsis-3 thresholds (MAP 65 mmHg, lactate 2 mmol/L) unless lactate is measured so rarely around hypotension that the outcome mostly reflects whether lactate was drawn; then report that as a limitation rather than change the threshold.
2. Choose the duration and gap rules for which the event rate is stable against neighbouring values, so that the outcome does not hinge on the charting interval.
3. Use the combined MAP series unless the arterial-only series changes the shock rate materially: shock within 24 h among patients who are pre-shock at time zero, changed by a fifth or more of its value under the combined series, in either direction. Then the arterial-only definition becomes a further sensitivity analysis.

## 5. Predictors

At each decision time, from data recorded up to that moment only: for heart rate, mean and systolic blood pressure, respiratory rate, SpO2, temperature, GCS, lactate, creatinine, platelets, bilirubin and white cells, the latest value, its age, its change from the latest value at least 6 h older (taken within the last 24 h; missing otherwise) and the number of measurements in the last 24 h; vasopressor running and its norepinephrine-equivalent dose; antibiotics running and hours since the first; supplemental oxygen; invasive ventilation; SOFA over the last 24 h; age; sex; hours since ICU admission and since time zero. Race and ethnicity are not predictors. Missing values: median imputation with "not measured" indicators, fitted on training folds only.

## 6. Decision times and follow-up

Decision times at 0, 3, 6, 9, 12, 15, 18, 21, 24, 30, 36, 42, 48, 60 and 72 h after time zero, for patients alive and in the ICU at that moment. Follow-up ends 240 h after time zero for the choice of decision times; outcome windows are not cut. Results are reported at 0, 6, 12, 24 and 48 h. The alert-burden evaluation scores every hour up to 72 h.

## 7. Sample size

The number of patients and events is fixed by the cohort. After M1, the expected events per candidate parameter for each population and number are recorded here **[M1]**, with the minimum sample size for a model of this size (Riley et al. 2020). If a model falls short, its penalty is chosen as below and the shortfall is stated as a limitation; the number of predictors is not reduced after seeing results.

## 8. Analysis

- **D1.** One L2-penalised logistic regression per population (pre-shock, shock stage) and per number (24 h now, next hour, 24 h from one hour on), on the stacked decision times. Penalty from the grid in `configs/default.yaml`, chosen by patient-grouped cross-validated log loss inside the training data. Uncertainty for the screen: 50 patient-level bootstrap refits, 90% intervals.
- **Internal validation.** Patient-grouped 5-fold cross-validation, stratified on whether the patient ever has the primary event, with every preprocessing step fitted inside the training folds.
- **Temporal validation.** Stays from the era 2020 to 2022 (estimated real years) are held out from fitting and evaluated once, after registration.
- **Measures.** AUROC, AUPRC, Brier score, calibration intercept and slope, integrated calibration index, each with 95% patient-bootstrap intervals (200 replicates); calibration tables by tenth of predicted risk and by fixed risk band; decision curves; alert burden by threshold (alerts per 100 patient-days, positive predictive value, events caught, alerts per detected event, lead time); results by decision time, sex, age band, race and ethnicity group, first care unit and era.
- **Comparators.** SOFA and NEWS2, each recalibrated by logistic regression on the same folds.
- **B1 against D1 (v0.3).** B1 becomes the screen's engine if, on the temporal hold-out, its Brier score is at most 0.005 worse than D1's (point estimate; the interval is reported) and its calibration slope lies between **0.8 and 1.25** with an integrated calibration index no more than **1.5 times** D1's (criteria confirmed 2026-10-08). Otherwise D1 remains the engine and B1 is reported as an explanatory model.
- **Small cells.** Every published table follows docs/data_governance.md: no count below 11, no rate whose count or complement is below 11, merged bins and flow steps, rounded counts for tables of alternative definitions.

## 9. Sensitivity analyses

1. The sensitivity cohort (`configs/cohort/culture_antibiotic_pair.yaml`).
2. The Sepsis-3 operational shock outcome (`configs/outcomes/sepsis3_operational_shock.yaml`).
3. The arterial-only MAP definition, if M1 shows the series differ materially (section 4, principle 3).

Each is a full re-run with one setting file changed, reported next to the main analysis.

## 10. Known limitations, stated in advance

- Predictions describe risk under the care in the data, not risk without treatment; calibration can drift as practice changes.
- Shock is observable only in the ICU, and its definition depends on how often lactate is measured.
- mimic-code's hourly SOFA scores a component with no measurement as 0.
- Single centre; no external validation in this plan (eICU is planned separately).

## 11. Deviations

None yet. Each deviation from the registered plan is listed here with its date, what changed and why.

## References

- van den Akker OR, Weston S, Campbell L, et al. Preregistration of secondary data analysis: a template and tutorial. Meta-Psychology 2021;5.
- Collins GS, Moons KGM, Dhiman P, et al. TRIPOD+AI statement: updated guidance for reporting clinical prediction models that use regression or machine learning methods. BMJ 2024;385:e078378. [doi:10.1136/bmj-2023-078378](https://doi.org/10.1136/bmj-2023-078378)
- Riley RD, Ensor J, Snell KIE, et al. Calculating the sample size required for developing a clinical prediction model. BMJ 2020;368:m441. [doi:10.1136/bmj.m441](https://doi.org/10.1136/bmj.m441)
- Singer M, et al. The Third International Consensus Definitions for Sepsis and Septic Shock (Sepsis-3). JAMA 2016;315(8):801-810. [doi:10.1001/jama.2016.0287](https://doi.org/10.1001/jama.2016.0287)
- Yang P, et al. Front Pharmacol 2025;16:1615618. [doi:10.3389/fphar.2025.1615618](https://doi.org/10.3389/fphar.2025.1615618) (published count of Sepsis-3 among first ICU stays in MIMIC-IV 3.0, used in the M1 check)

# Lessons from the prototype, as requirements

This project began as a course team project: a hidden Markov model of sepsis progression on MIMIC-IV, written in notebooks. Rebuilding it started with a line-by-line review of that prototype. This page lists what the review found, grouped by the kind of failure, and the safeguard each lesson became here. It is a requirements document, not an audit: most of these mistakes are easy to make with ICU data, and several are documented pitfalls.

## Failure types and safeguards

| Failure type | What happened in the prototype | Safeguard in this implementation |
|---|---|---|
| A variable is not what its name says | An item ID used as FiO2 (220235) is arterial CO2 pressure in MIMIC-IV; FiO2 is 223835. Urine output was looked for in the wrong table, so the renal SOFA score used creatinine only. Two inotropes were in the vasopressor list. | Clinical concepts come from mimic-code (SOFA, blood gas, vasopressor equivalents, ventilation) instead of being re-derived. Every measurement has a declared unit and plausible range, and values outside it are dropped before use. |
| Missing values counted as evidence again and again | Laboratory values were carried forward without limit and then scaled with statistics from all patients, so one old value acted as fresh evidence at every step. | Features record how old the last value is and how often it was measured. Imputation and scaling are fitted on training folds only. |
| Information from the future | Patients were stratified using their whole stay; alerts used smoothed (forward-backward) state estimates; the cohort was filtered on length of stay and on when treatments ended. | Time zero is the moment Sepsis-3 was on record. Decision times include only patients still at risk. Features are built as of each decision time, and a test deletes all later data and checks that no feature changes. No length-of-stay filter. |
| Treatment mixed into the outcome | Shock was defined by vasopressor use; the SOFA score, which gives points for vasopressors, was an observed variable; treatments recorded at the same time as a change were used to explain it. | Shock is defined from blood pressure and lactate only. Treatments are predictors, never presented as effects. The screen shows no model-estimated treatment effect. |
| Results from different runs mixed | Two runs with different state orderings fed different documents, and hidden-state labels switched between runs. | Every number in a report or on the screen is written by code from one run's JSON output, which records the settings fingerprint and code revision. |
| Evaluation that was not a prediction test | Risk groups were assessed on the training patients at their last observation before discharge or death; no patients were held out. | Patient-grouped 5-fold cross-validation and a temporal hold-out, with calibration, decision curves, alert burden, subgroups and drift. |
| A name that promised more than the model did | The model was described as continuous-time, but time entered as a linear term in a discrete-time transition model. | Names follow the mathematics. The planned Bayesian model (B1) is a continuous-time HMM with a generator matrix and matrix exponentials, tested against exact enumeration. |

## Smaller lessons that became rules

- **One cohort, one implementation.** The prototype had two cohort builders that disagreed by one patient. Here there is one cohort module with tests, and the cohort flow is printed by code.
- **Ages.** MIMIC-IV's `anchor_age` is the age in the anchor year, not at admission; age is computed from the admission date.
- **Hidden-state labels need an anchor.** Without one, "mild" and "shock" can swap between runs. B1 will order its stages by death intensity.
- **A posterior is not one draw.** Decisions were computed from a single posterior sample without a fixed seed. Intervals here come from many refits or draws, with seeds recorded.
- **Diagnostics must match the method.** An effective sample size computed on independent draws from a variational approximation says nothing about convergence. B1 will report R-hat and effective sample size only from NUTS chains, and compare them with the variational fit.
- **"Risk at 48 hours" must mean what it says.** The prototype reported the probability of being in shock at hour 48, which misses patients who were in shock earlier and left it. Here every outcome is an event within a window.

# Design rationale: what the screen says and what it does not

## 1. The question

The project set out to tell a clinician, for a patient with sepsis:

> This patient currently has a __% chance of progressing to the next stage. If you take one action now (one of the combinations of antibiotics, IV fluids and a vasopressor), that chance falls to __%. If you wait one hour, the chance at that point will be __%, and if you then take one action, it falls to __%.

That sentence asks for four kinds of number. Two are predictions and can be checked against what happened. Two are statements about what *would* happen under a choice that may not have been made, and observational ICU data cannot support them for a single patient at an hourly scale. The screen keeps the sentence's two-by-two shape and fills each cell only with what can be supported.

| Cell | In the sentence | On the screen | Checked by |
|---|---|---|---|
| Now, usual care | chance of progressing now | **X**: risk of the next stage within 24 h if care continues as it usually does in these data | calibration and discrimination on held-out patients |
| In 1 h, usual care | chance after waiting an hour | **p1**: chance of the next stage within the hour, and **Z**: the 24-hour risk from one hour on, if nothing happens in that hour | the same |
| Now, start a treatment | chance if acting now | evidence status for the selected treatment | (no model number) |
| In 1 h, start a treatment | chance if acting in an hour | timing evidence for the selected treatment | (no model number) |

The risk of the next stage over the coming 25 hours follows from the two left-hand numbers: p1 + (1 − p1) × Z.

## 2. Why X is "if care continues as usual"

The outcome in the data happened under the care the patient actually received. A model trained on it predicts risk *under that care*, including treatments the team would start later. That is a legitimate and checkable prediction: if the model says 30%, about 30% of such patients should progress, and the calibration panel shows whether they did.

It is not:

- the risk without treatment, because treatment is part of usual care;
- the risk if the current treatment is kept exactly as it is, because nobody keeps treatment fixed for 24 hours, so that quantity is never observed.

The screen says "if care continues as usual" for this reason. One consequence is written in the limits: if a screen like this changed how clinicians act, the care pattern would change and the calibration with it.

## 3. Why there is no number for "start a treatment"

Estimating what would happen if a treatment were started now rather than in an hour needs three things that these data do not give:

1. **No unmeasured confounding.** Clinicians start fluids or a vasopressor because of what they see, including things never charted (skin, mental state, the trend over the last few minutes). Patients treated earlier look different in ways the data cannot adjust for.
2. **Positivity.** For many states, almost everyone receives the treatment (a vasopressor at a mean arterial pressure of 50), or almost no one does. Some combinations are almost always given together. A comparison needs both choices to occur among similar patients.
3. **A well-defined treatment.** "Give fluids" covers volumes from a bolus to a maintenance drip. In MIMIC-IV, two reasonable fluid definitions have led to opposite conclusions in another study (see docs/sources.md).

So each treatment row shows a status instead of a number:

- **External evidence only**: what randomised trials (and, labelled as such, observational studies) found, on their own outcomes and time frames. These cannot be subtracted from the 24-hour risk above.
- **Already started**: antibiotics. Every patient in this cohort received them by sepsis recognition, which is part of the Sepsis-3 definition, so evidence on antibiotic timing applies only before the first dose.
- **Not estimable**: the four combinations. No trial compares them at these times, and the data cannot separate their effect from how ill the patients looked.

A target-trial emulation of norepinephrine timing in sustained hypotension (clone, censor and weight) is planned after v1.0. A model estimate will appear on the screen only if that analysis passes its pre-specified checks.

## 4. Time zero is the moment Sepsis-3 was on record

A patient can only enter a sepsis cohort once every criterion is on record: suspected infection (antibiotic and culture) and organ dysfunction (SOFA of 2 or more). Using ICU admission as time zero would mean predicting for patients nobody could yet have known to be in the cohort. Time zero is therefore the recognition time R, or ICU admission if R came first:

- for every qualifying combination of suspected infection and organ dysfunction, the time it was complete is the latest of the antibiotic start, the culture and the first qualifying SOFA;
- R is the earliest such time for the stay;
- suspected infection is taken from mimic-code, which counts an antibiotic towards an ICU stay only when it was started during that stay, so in MIMIC-IV R is never before ICU admission (the "or ICU admission" rule matters only for synthetic data);
- a culture recorded with a date but no time counts at the end of that day, because it may have been taken at any time that day.

For the same reason there is no length-of-stay filter: how long a stay lasts is only known when it ends.

## 5. Shock is defined without vasopressors

The Sepsis-3 definition of septic shock requires a vasopressor. A model that predicted it would partly predict the clinician's decision to start one, and a screen offering "start a vasopressor now" would be offering the outcome itself. Here shock is:

- **sustained hypotension**: two or more mean arterial pressure readings below 65 mmHg, at least 30 minutes apart, with no normal reading between them and no gap over 2 hours; and
- **lactate above 2 mmol/L** within 6 hours before or after.

The Sepsis-3 operational definition is planned as a sensitivity analysis for v0.2. Known weaknesses: the definition depends on how often lactate is measured, and shock can only be observed in the ICU. Deaths count wherever they happen.

## 6. Two stages, two sets of models

"The next stage" means different things before and after shock:

- **pre-shock** patients (no vasopressor, no shock so far): the next stage is shock or death;
- **shock-stage** patients (on a vasopressor, or met the shock definition earlier): the next stage is death.

A patient can move between the groups during a stay, for example when a vasopressor is stopped. Each group has its own models, and the screen changes its wording when the group changes.

## 7. Decision times (landmarks)

A prediction is made at fixed decision times after time zero: every 3 hours during the first day, then at 30, 36, 42, 48, 60 and 72 hours. At each one, only patients still alive and in the ICU are at risk, which is the population a bedside tool would actually see. Features use only what was recorded up to that moment. The screen scores every hour, which is also how the alert-burden evaluation scores patients.

A patient contributes several rows, so every split, bootstrap and cross-validation fold works on patients, never on rows.

## 8. What the uncertainty means

- The **90% interval** is the spread of the prediction across 50 refits of the model on patients resampled with replacement. It describes uncertainty in the model's coefficients, not the range of outcomes for this patient.
- **"Predicted 30–40%: x% progressed, n of N"** comes from the development data: out-of-fold predictions grouped into fixed risk bands (0–5%, 5–10%, 10–15%, 15–20%, 20–30%, 30–40%, 40–50%, 50–70%, 70–100%), and the share of each band that progressed. Bands with fewer than 11 events or non-events are not shown.
- The **checks table** lists AUROC and calibration slope for each number on the screen, in cross-validation and in the temporal hold-out, and flags values below 0.6 or slopes outside 0.8 to 1.25.

## 9. Risk drivers

Each bar is one input's contribution to the log-odds of this prediction, relative to an average decision point in the training data (the standardised value times the coefficient), written as a multiplier on the odds. The five largest are shown. They describe what the model reacts to. They are not effects: a low blood pressure raises the predicted risk, but raising the blood pressure with a drug would not necessarily lower the true risk by the same amount.

## 10. Display rules

- Only data recorded up to the decision time are shown or used. Later hours are hatched and labelled "not yet known".
- Norepinephrine is hidden while a vasopressor is running, with a one-line note saying why.
- The 65 trial appears only for patients aged 65 or older, the trial's population.
- Inputs older than 24 hours are flagged.
- Chart colours come from a palette checked for colour-vision deficiency; every colour is paired with a label.

## 11. What the screen is not

- It is not an alarm. The evaluation report shows what alerting on it would cost (alerts per 100 patient-days, positive predictive value, lead time), because that is how a hospital would judge it.
- It is not for patient care. Under the FDA's clinical decision support guidance (January 2026), software that detects sepsis and alerts a clinician is a device function, and support for time-critical decisions does not meet Criterion 4. The screen still shows what Criterion 4 lists: intended use, inputs, how the model was built and checked, and missing inputs.

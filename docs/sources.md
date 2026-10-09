# Sources

## Evidence in the treatment table

Each number shown on the screen was checked against the trial's abstract or full text before it was written into `decision_support/evidence.py`. Observational studies are labelled as such on the screen. Trial outcomes and follow-up differ from the screen's 24-hour risk, which the screen states above the table.

| Source | Design | Population | What the screen quotes |
|---|---|---|---|
| Permpikul C, et al. Early use of norepinephrine in septic shock resuscitation (CENSER). Am J Respir Crit Care Med 2019;199(9):1097-1105. [doi:10.1164/rccm.201806-1034OC](https://doi.org/10.1164/rccm.201806-1034OC) | Randomised, single centre, phase II, placebo-controlled | 310 emergency-department patients with sepsis-related hypotension | Shock control at 6 h 76.1% vs 48.4%; 28-day death 15.5% vs 21.9% (P = 0.15); norepinephrine started at a median 93 vs 192 minutes |
| National Heart, Lung, and Blood Institute PETAL Clinical Trials Network. Early restrictive or liberal fluid management for sepsis-induced hypotension (CLOVERS). N Engl J Med 2023;388(6):499-510. [doi:10.1056/NEJMoa2212663](https://doi.org/10.1056/NEJMoa2212663) | Randomised | 1,563 patients with sepsis-induced hypotension after 1-3 L of fluid | Death before discharge home by day 90 14.0% vs 14.9%; difference -0.9 points (95% CI -4.4 to 2.6) |
| Meyhoff TS, et al. Restriction of intravenous fluid in ICU patients with septic shock (CLASSIC). N Engl J Med 2022;386(26):2459-2470. [doi:10.1056/NEJMoa2202707](https://doi.org/10.1056/NEJMoa2202707) | Randomised | 1,554 ICU patients with septic shock after at least 1 L of fluid | 90-day death 42.3% vs 42.1%; adjusted difference 0.1 points (95% CI -4.7 to 4.9) |
| Lamontagne F, et al. Effect of reduced exposure to vasopressors on 90-day mortality in older critically ill patients with vasodilatory hypotension (65 trial). JAMA 2020;323(10):938-949. [doi:10.1001/jama.2020.0930](https://doi.org/10.1001/jama.2020.0930) | Randomised, 65 UK ICUs | 2,463 analysed patients aged 65 or older | MAP target 60-65 vs usual care: 90-day death 41.0% vs 43.8% (95% CI for the difference -6.75 to 1.05 points). Shown only for patients aged 65 or older |
| Alam N, et al. Prehospital antibiotics in the ambulance for sepsis (PHANTASi). Lancet Respir Med 2018;6(1):40-50. [doi:10.1016/S2213-2600(17)30469-1](https://doi.org/10.1016/S2213-2600(17)30469-1) | Randomised | 2,672 analysed patients with suspected sepsis | Antibiotics a median 26 minutes earlier; 28-day death 8% vs 8% |
| Pak TR, et al. Clin Infect Dis 2023;77(11):1534-1543. [doi:10.1093/cid/ciad450](https://doi.org/10.1093/cid/ciad450) | Observational | 104,248 adults with suspected infection at 5 hospitals | Among patients treated within 6 h, each hour of delay: odds ratio 1.07 (1.04-1.11) in septic shock, 1.03 (0.98-1.09) in sepsis without shock |
| Li M, et al. Clin Epidemiol 2026;18:1-13. [doi:10.2147/clep.s588212](https://doi.org/10.2147/clep.s588212) | Observational (target-trial emulation with inverse probability weighting), MIMIC-IV and two Chinese ICU cohorts | Adults with sepsis | Vasopressors at 0-1 h vs 1-3 h after instability: 28-day death hazard ratio 1.07 (0.89-1.29). Fluids started within 1 h with at least 30 mL/kg by 3 h vs started at 1-3 h: 0.72 (0.53-0.97) |
| Dai Q, et al. iScience 2026;29(7):116584. [doi:10.1016/j.isci.2026.116584](https://doi.org/10.1016/j.isci.2026.116584) | Observational, MIMIC-IV with eICU check | 42,883 MIMIC-IV patients | Balanced crystalloid vs saline: two reasonable exposure definitions gave opposite estimates for major adverse kidney events at 30 days (odds ratio 0.49 vs 2.51) |

## Methods

- Hernán MA, Robins JM. Using big data to emulate a target trial when a randomized trial is not available. Am J Epidemiol 2016;183(8):758-764. [doi:10.1093/aje/kwv254](https://doi.org/10.1093/aje/kwv254)
- Maringe C, et al. Reflection on modern methods: trial emulation in the presence of immortal-time bias. Int J Epidemiol 2020;49(5):1719-1729. [doi:10.1093/ije/dyaa057](https://doi.org/10.1093/ije/dyaa057)
- Austin PC, Steyerberg EW. The Integrated Calibration Index (ICI) and related metrics for quantifying the calibration of logistic regression models. Stat Med 2019;38(21):4051-4065. [doi:10.1002/sim.8281](https://doi.org/10.1002/sim.8281)
- Vickers AJ, Elkin EB. Decision curve analysis: a novel method for evaluating prediction models. Med Decis Making 2006;26(6):565-574. [doi:10.1177/0272989X06295361](https://doi.org/10.1177/0272989X06295361)
- Royal College of Physicians. National Early Warning Score (NEWS) 2. London: RCP; 2017.
- Singer M, et al. The Third International Consensus Definitions for Sepsis and Septic Shock (Sepsis-3). JAMA 2016;315(8):801-810. [doi:10.1001/jama.2016.0287](https://doi.org/10.1001/jama.2016.0287)
- Collins GS, et al. TRIPOD+AI statement: updated guidance for reporting clinical prediction models that use regression or machine learning methods. BMJ 2024;385:e078378. [doi:10.1136/bmj-2023-078378](https://doi.org/10.1136/bmj-2023-078378)
- Riley RD, et al. Calculating the sample size required for developing a clinical prediction model. BMJ 2020;368:m441. [doi:10.1136/bmj.m441](https://doi.org/10.1136/bmj.m441)
- van den Akker OR, et al. Preregistration of secondary data analysis: a template and tutorial. Meta-Psychology 2021;5.

## Cohort counts used by the M1 check

- Yang P, et al. Front Pharmacol 2025;16:1615618. [doi:10.3389/fphar.2025.1615618](https://doi.org/10.3389/fphar.2025.1615618): 28,087 Sepsis-3 patients among 65,366 first ICU stays in MIMIC-IV 3.0.
- mimic-code, `mimic-iv/buildmimic/postgres/validate.sql` at commit 303d26c: expected row counts of MIMIC-IV 3.1 (patients 364,627; admissions 546,028; icustays 94,458; inputevents 10,953,713; procedureevents 808,706).
- The course prototype's cohort funnel (94,458 / 65,366 / 58,506 / 29,421 / 1,502): the first four counts from its code, the last from its notebook, which restricted the culture to the ICU stay. The rules are restated in `src/sepsis_decision_support/data/sql/m1_course_funnel.sql`.

## Data and software

- Johnson A, Bulgarelli L, Pollard T, Gow B, Moody B, Horng S, Celi LA, Mark R. MIMIC-IV (version 3.1). PhysioNet; 2024. [doi:10.13026/kpb9-mt58](https://doi.org/10.13026/kpb9-mt58)
- Johnson AEW, Bulgarelli L, Shen L, et al. MIMIC-IV, a freely accessible electronic health record dataset. Sci Data 2023;10:1. [doi:10.1038/s41597-022-01899-x](https://doi.org/10.1038/s41597-022-01899-x)
- Johnson A, Bulgarelli L, Pollard T, Horng S, Celi LA, Mark R. MIMIC-IV Clinical Database Demo (version 2.2). PhysioNet; 2023. [doi:10.13026/dp1f-ex47](https://doi.org/10.13026/dp1f-ex47)
- MIT Laboratory for Computational Physiology. mimic-code, commit 303d26c. https://github.com/MIT-LCP/mimic-code

## Regulation and data use

- U.S. Food and Drug Administration. Clinical Decision Support Software: Guidance for Industry and Food and Drug Administration Staff. January 2026.
- PhysioNet Credentialed Health Data Use Agreement 1.5.0. https://physionet.org/content/mimiciv/view-dua/3.1/
- PhysioNet. Use of MIMIC data with large language models and online services. 24 September 2025. https://physionet.org/news/post/llm-responsible-use/

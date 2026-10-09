-- The course prototype's cohort funnel, rule for rule, for the M1 reproduction check.
-- Returns one row of counts and nothing record-level.
--
-- The prototype (DuckDB over the CSV files) took, in order:
--   1. every ICU stay;
--   2. the first ICU stay of each patient (by ICU admission time; two stays admitted at
--      the same moment are ordered by stay_id here, where the prototype left it to chance);
--   3. stays longer than 12 hours and shorter than 10 days (both strict);
--   4. anchor_age from 18 to 65 (inclusive). anchor_age is the age in the patient's
--      anchor year, not the age at this admission; it is kept here to reproduce the count;
--   5. an antibiotic infusion (inputevents) that started at or after ICU admission and
--      ended at or before ICU discharge, and a culture (procedureevents) of the same stay
--      charted within one hour of the antibiotic start (inclusive).
-- The prototype's script did not restrict the culture time to the ICU stay; its notebook
-- did. Both versions are counted; the notebook version is the reference figure.
WITH ranked AS (
    SELECT
        stay_id,
        subject_id,
        intime,
        outtime,
        ROW_NUMBER() OVER (PARTITION BY subject_id ORDER BY intime, stay_id) AS stay_number
    FROM mimiciv_icu.icustays
),
first_stays AS (
    SELECT * FROM ranked WHERE stay_number = 1
),
stays_12_hours_to_10_days AS (
    SELECT * FROM first_stays
    WHERE EXTRACT(EPOCH FROM (outtime - intime)) > 12 * 3600
      AND EXTRACT(EPOCH FROM (outtime - intime)) < 10 * 24 * 3600
),
aged_18_to_65 AS (
    SELECT stays.*
    FROM stays_12_hours_to_10_days AS stays
    JOIN mimiciv_hosp.patients AS patients ON patients.subject_id = stays.subject_id
    WHERE patients.anchor_age >= 18 AND patients.anchor_age <= 65
),
antibiotics AS (
    SELECT stays.stay_id, inputs.starttime AS antibiotic_time
    FROM aged_18_to_65 AS stays
    JOIN mimiciv_icu.inputevents AS inputs
        ON inputs.stay_id = stays.stay_id
        AND inputs.starttime >= stays.intime
        AND inputs.endtime <= stays.outtime
    WHERE inputs.itemid IN (
        225798, -- Vancomycin
        225842, -- Ampicillin
        225845, -- Azithromycin
        225847, -- Aztreonam
        225850, -- Cefazolin
        225851, -- Cefepime
        225855, -- Ceftriaxone
        225860, -- Clindamycin
        225879, -- Levofloxacin
        225881, -- Linezolid
        225883, -- Meropenem
        225884, -- Metronidazole
        225886, -- Moxifloxacin
        225892, -- Piperacillin
        225893, -- Piperacillin/Tazobactam (Zosyn)
        225899, -- Bactrim (SMX/TMP)
        225902, -- Tobramycin
        229061  -- Ertapenem sodium (Invanz)
    )
),
cultures AS (
    SELECT
        stays.stay_id,
        procedures.starttime AS culture_time,
        procedures.starttime >= stays.intime AND procedures.starttime <= stays.outtime
            AS culture_in_icu
    FROM aged_18_to_65 AS stays
    JOIN mimiciv_icu.procedureevents AS procedures ON procedures.stay_id = stays.stay_id
    WHERE procedures.itemid IN (
        225401, -- Blood Cultured
        225437, -- CSF Culture
        225444, -- Pan Culture
        225451, -- Sputum Culture
        225454, -- Urine Culture
        225816, -- Wound Culture
        225817, -- BAL Fluid Culture
        225818  -- Pleural Fluid Culture
    )
),
pairs AS (
    SELECT antibiotics.stay_id, cultures.culture_in_icu
    FROM antibiotics
    JOIN cultures
        ON cultures.stay_id = antibiotics.stay_id
        AND ABS(EXTRACT(EPOCH FROM (cultures.culture_time - antibiotics.antibiotic_time))) <= 3600
)
SELECT
    (SELECT COUNT(*) FROM mimiciv_icu.icustays) AS icu_stays,
    (SELECT COUNT(*) FROM first_stays) AS first_icu_stays,
    (SELECT COUNT(*) FROM stays_12_hours_to_10_days) AS stays_12_hours_to_10_days,
    (SELECT COUNT(*) FROM aged_18_to_65) AS anchor_age_18_to_65,
    (SELECT COUNT(DISTINCT stay_id) FROM pairs WHERE culture_in_icu) AS culture_antibiotic_pair,
    (SELECT COUNT(DISTINCT stay_id) FROM pairs) AS culture_antibiotic_pair_any_culture_time

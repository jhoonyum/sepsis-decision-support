-- Recognition time R for the sensitivity cohort "culture_antibiotic_pair".
--
-- The course prototype's definition of suspected infection: an IV antibiotic infusion
-- (inputevents) and a culture (procedureevents) charted in the ICU within one hour of
-- each other. The item lists are the prototype's.
--
-- Two of the prototype's rules are left out because they select on the future:
--   * stay length between 12 hours and 10 days (known only at discharge);
--   * the infusion ending before ICU discharge.
--
-- R is the moment the first pair was complete: for every qualifying pair, the later of
-- the antibiotic start and the culture time; R is the earliest of these. Both events must
-- fall inside the ICU stay (admission and discharge times included). There is no
-- organ-dysfunction criterion, so that column is empty.
WITH stays AS (
    SELECT stay_id, intime, outtime FROM mimiciv_icu.icustays
),
antibiotics AS (
    SELECT inputs.stay_id, inputs.starttime AS antibiotic_time
    FROM mimiciv_icu.inputevents AS inputs
    JOIN stays ON stays.stay_id = inputs.stay_id
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
    AND inputs.starttime >= stays.intime
    AND inputs.starttime <= stays.outtime
),
cultures AS (
    SELECT procedures.stay_id, procedures.starttime AS culture_time
    FROM mimiciv_icu.procedureevents AS procedures
    JOIN stays ON stays.stay_id = procedures.stay_id
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
    AND procedures.starttime >= stays.intime
    AND procedures.starttime <= stays.outtime
),
pairs AS (
    SELECT
        antibiotics.stay_id,
        antibiotics.antibiotic_time,
        cultures.culture_time,
        GREATEST(antibiotics.antibiotic_time, cultures.culture_time) AS ready_time
    FROM antibiotics
    JOIN cultures
        ON cultures.stay_id = antibiotics.stay_id
        AND cultures.culture_time >= antibiotics.antibiotic_time - INTERVAL 1 HOUR
        AND cultures.culture_time <= antibiotics.antibiotic_time + INTERVAL 1 HOUR
),
earliest AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY stay_id ORDER BY ready_time, antibiotic_time, culture_time
        ) AS position
    FROM pairs
)
SELECT
    stay_id,
    ready_time AS recognition_time,
    antibiotic_time,
    culture_time,
    CAST(NULL AS TIMESTAMP) AS organ_dysfunction_time
FROM earliest
WHERE position = 1
ORDER BY stay_id

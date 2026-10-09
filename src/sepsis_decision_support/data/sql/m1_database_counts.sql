-- Database-level counts for the M1 check. Returns one row of counts.
--
-- The table sizes are compared with the row counts mimic-code expects for MIMIC-IV 3.1
-- (mimic-iv/buildmimic/postgres/validate.sql at the pinned commit).
--
-- first_stays_with_sepsis3 counts first ICU stays that have a row in mimic-code's sepsis3
-- table, the way published MIMIC-IV Sepsis-3 cohorts are usually counted (for example
-- 28,087 of 65,366 first ICU stays in MIMIC-IV 3.0, Yang et al. 2025).
WITH ranked AS (
    SELECT
        stay_id,
        ROW_NUMBER() OVER (PARTITION BY subject_id ORDER BY intime, stay_id) AS stay_number
    FROM mimiciv_icu.icustays
),
first_stays AS (
    SELECT stay_id FROM ranked WHERE stay_number = 1
)
SELECT
    (SELECT COUNT(*) FROM mimiciv_hosp.patients) AS patients,
    (SELECT COUNT(*) FROM mimiciv_hosp.admissions) AS admissions,
    (SELECT COUNT(*) FROM mimiciv_icu.icustays) AS icustays,
    (SELECT COUNT(*) FROM mimiciv_icu.inputevents) AS inputevents,
    (SELECT COUNT(*) FROM mimiciv_icu.procedureevents) AS procedureevents,
    (SELECT COUNT(DISTINCT subject_id) FROM mimiciv_icu.icustays) AS patients_with_icu_stay,
    (SELECT COUNT(*) FROM first_stays) AS first_icu_stays,
    (
        SELECT COUNT(*)
        FROM first_stays
        JOIN mimiciv_derived.sepsis3 AS sepsis ON sepsis.stay_id = first_stays.stay_id
        WHERE sepsis.sepsis3
    ) AS first_stays_with_sepsis3

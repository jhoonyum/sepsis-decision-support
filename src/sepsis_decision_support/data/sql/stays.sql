-- One row per ICU stay, with everything the cohort rules and the outcome labels need.
-- Reads mimic-code's DuckDB build (schemas mimiciv_hosp, mimiciv_icu, mimiciv_derived).
WITH ordered_stays AS (
    SELECT
        icu.subject_id,
        icu.hadm_id,
        icu.stay_id,
        icu.intime,
        icu.outtime,
        icu.first_careunit,
        -- First ICU stay of the patient across all hospital admissions.
        ROW_NUMBER() OVER (PARTITION BY icu.subject_id ORDER BY icu.intime, icu.stay_id) AS stay_number_of_patient
    FROM mimiciv_icu.icustays AS icu
),
service_in_effect AS (
    -- Hospital service in effect at ICU admission: the latest service change at or before it.
    SELECT
        stays.stay_id,
        services.curr_service,
        ROW_NUMBER() OVER (PARTITION BY stays.stay_id ORDER BY services.transfertime DESC) AS recency
    FROM ordered_stays AS stays
    JOIN mimiciv_hosp.services AS services
        ON services.hadm_id = stays.hadm_id AND services.transfertime <= stays.intime
),
first_service AS (
    -- Fallback when no service change is recorded before ICU admission.
    SELECT
        stays.stay_id,
        services.curr_service,
        ROW_NUMBER() OVER (PARTITION BY stays.stay_id ORDER BY services.transfertime) AS position
    FROM ordered_stays AS stays
    JOIN mimiciv_hosp.services AS services ON services.hadm_id = stays.hadm_id
)
SELECT
    stays.stay_id,
    stays.subject_id,
    stays.hadm_id,
    stays.intime AS icu_intime,
    stays.outtime AS icu_outtime,
    admissions.dischtime AS hospital_dischtime,
    admissions.deathtime AS death_time,
    patients.dod AS date_of_death,
    age.age AS age_years,
    patients.gender AS sex,
    admissions.race AS race,
    stays.first_careunit,
    patients.anchor_year,
    patients.anchor_year_group,
    COALESCE(in_effect.curr_service, first_seen.curr_service) AS hospital_service,
    stays.stay_number_of_patient = 1 AS is_first_icu_stay
FROM ordered_stays AS stays
JOIN mimiciv_hosp.admissions AS admissions ON admissions.hadm_id = stays.hadm_id
JOIN mimiciv_hosp.patients AS patients ON patients.subject_id = stays.subject_id
LEFT JOIN mimiciv_derived.age AS age ON age.hadm_id = stays.hadm_id
LEFT JOIN service_in_effect AS in_effect ON in_effect.stay_id = stays.stay_id AND in_effect.recency = 1
LEFT JOIN first_service AS first_seen ON first_seen.stay_id = stays.stay_id AND first_seen.position = 1
ORDER BY stays.stay_id

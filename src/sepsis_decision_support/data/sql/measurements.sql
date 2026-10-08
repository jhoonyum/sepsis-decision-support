-- Measured values in long format (stay_id, charttime, variable, value).
-- Vital signs and GCS are charted per ICU stay. Laboratory values are recorded per hospital
-- admission, so they are attached to the stay when taken between 24 hours before ICU
-- admission and ICU discharge: a value measured before admission is legitimately known.
WITH stays AS (
    SELECT stay_id, hadm_id, intime, outtime FROM mimiciv_icu.icustays
),
vital_signs AS (
    SELECT stay_id, charttime, 'heart_rate' AS variable, heart_rate AS value
    FROM mimiciv_derived.vitalsign WHERE heart_rate IS NOT NULL
    UNION ALL
    SELECT stay_id, charttime, 'mean_arterial_pressure', mbp
    FROM mimiciv_derived.vitalsign WHERE mbp IS NOT NULL
    UNION ALL
    SELECT stay_id, charttime, 'systolic_blood_pressure', sbp
    FROM mimiciv_derived.vitalsign WHERE sbp IS NOT NULL
    UNION ALL
    SELECT stay_id, charttime, 'respiratory_rate', resp_rate
    FROM mimiciv_derived.vitalsign WHERE resp_rate IS NOT NULL
    UNION ALL
    SELECT stay_id, charttime, 'spo2', spo2
    FROM mimiciv_derived.vitalsign WHERE spo2 IS NOT NULL
    UNION ALL
    SELECT stay_id, charttime, 'temperature', temperature
    FROM mimiciv_derived.vitalsign WHERE temperature IS NOT NULL
    UNION ALL
    SELECT stay_id, charttime, 'gcs_total', gcs
    FROM mimiciv_derived.gcs WHERE gcs IS NOT NULL
),
laboratory AS (
    SELECT hadm_id, charttime, 'lactate' AS variable, lactate AS value
    FROM mimiciv_derived.bg WHERE lactate IS NOT NULL
    UNION ALL
    SELECT hadm_id, charttime, 'creatinine', creatinine
    FROM mimiciv_derived.chemistry WHERE creatinine IS NOT NULL
    UNION ALL
    SELECT hadm_id, charttime, 'platelets', platelet
    FROM mimiciv_derived.complete_blood_count WHERE platelet IS NOT NULL
    UNION ALL
    SELECT hadm_id, charttime, 'white_blood_cells', wbc
    FROM mimiciv_derived.complete_blood_count WHERE wbc IS NOT NULL
    UNION ALL
    SELECT hadm_id, charttime, 'bilirubin', bilirubin_total
    FROM mimiciv_derived.enzyme WHERE bilirubin_total IS NOT NULL
),
laboratory_by_stay AS (
    SELECT stays.stay_id, laboratory.charttime, laboratory.variable, laboratory.value
    FROM laboratory
    JOIN stays
        ON stays.hadm_id = laboratory.hadm_id
        AND laboratory.charttime >= stays.intime - INTERVAL 24 HOUR
        AND laboratory.charttime <= stays.outtime
)
SELECT stay_id, charttime, variable, CAST(value AS DOUBLE) AS value FROM vital_signs
UNION ALL
SELECT stay_id, charttime, variable, CAST(value AS DOUBLE) AS value FROM laboratory_by_stay

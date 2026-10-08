-- Treatment intervals (stay_id, treatment, start_time, end_time, rate).
-- "vasopressor" is any vasopressor, as a norepinephrine-equivalent dose (micrograms/kg/min);
-- "norepinephrine" is norepinephrine alone. Inotropes (dobutamine, milrinone) are not counted.
SELECT stay_id, 'vasopressor' AS treatment, starttime AS start_time, endtime AS end_time,
       CAST(norepinephrine_equivalent_dose AS DOUBLE) AS rate
FROM mimiciv_derived.norepinephrine_equivalent_dose
WHERE norepinephrine_equivalent_dose > 0
UNION ALL
SELECT stay_id, 'norepinephrine', starttime, endtime, CAST(vaso_rate AS DOUBLE)
FROM mimiciv_derived.norepinephrine
UNION ALL
-- Antibiotic orders that started inside the ICU stay. Orders without a stop time are
-- assumed to run for one day, which only matters for the "on antibiotic now" flag.
SELECT stay_id, 'antibiotic', starttime, COALESCE(stoptime, starttime + INTERVAL 24 HOUR), CAST(NULL AS DOUBLE)
FROM mimiciv_derived.antibiotic
WHERE stay_id IS NOT NULL
UNION ALL
SELECT stay_id, 'supplemental_oxygen', starttime, endtime, CAST(NULL AS DOUBLE)
FROM mimiciv_derived.ventilation
WHERE ventilation_status IN ('SupplementalOxygen', 'HFNC', 'NonInvasiveVent', 'InvasiveVent', 'Tracheostomy')
UNION ALL
SELECT stay_id, 'invasive_ventilation', starttime, endtime, CAST(NULL AS DOUBLE)
FROM mimiciv_derived.ventilation
WHERE ventilation_status IN ('InvasiveVent', 'Tracheostomy')

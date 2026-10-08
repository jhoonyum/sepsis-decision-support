-- SOFA over the 24 hours ending at each hour of the ICU stay (mimic-code concept).
-- Missing components score 0 there; this is a known limitation recorded in the model card.
SELECT
    stay_id,
    endtime AS hour_end_time,
    CAST(sofa_24hours AS DOUBLE) AS sofa_24h,
    CAST(cardiovascular_24hours AS DOUBLE) AS cardiovascular_24h
FROM mimiciv_derived.sofa

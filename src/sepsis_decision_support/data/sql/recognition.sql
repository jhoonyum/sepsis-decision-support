-- Recognition time R: the earliest moment at which every Sepsis-3 criterion was on record.
--
-- mimic-code's sepsis3 table keeps one (suspicion, SOFA) combination per stay: the one
-- with the earliest suspected-infection time. That is not always the combination that
-- was complete first, so R is recomputed here over all qualifying combinations:
--   for each suspicion of infection inside the ICU (antibiotic started in the ICU),
--   ready time = max(antibiotic start, culture time, first SOFA >= 2 within -48 h/+24 h);
--   R = the smallest ready time of the stay.
WITH suspicion AS (
    SELECT
        stay_id,
        antibiotic_time,
        suspected_infection_time,
        -- A culture recorded with a date but no time appears at midnight. It may have been
        -- taken at any time that day, so it is treated as known only at the end of the day.
        CASE
            WHEN culture_time IS NOT NULL AND CAST(culture_time AS TIME) = TIME '00:00:00'
                THEN culture_time + INTERVAL 1 DAY - INTERVAL 1 SECOND
            ELSE culture_time
        END AS culture_time
    FROM mimiciv_derived.suspicion_of_infection
    WHERE stay_id IS NOT NULL AND suspected_infection = 1
),
qualifying AS (
    SELECT
        suspicion.stay_id,
        suspicion.antibiotic_time,
        suspicion.culture_time,
        MIN(sofa.endtime) AS organ_dysfunction_time
    FROM suspicion
    JOIN mimiciv_derived.sofa AS sofa
        ON sofa.stay_id = suspicion.stay_id
        AND sofa.sofa_24hours >= 2
        AND sofa.endtime >= suspicion.suspected_infection_time - INTERVAL 48 HOUR
        AND sofa.endtime <= suspicion.suspected_infection_time + INTERVAL 24 HOUR
    GROUP BY suspicion.stay_id, suspicion.antibiotic_time, suspicion.culture_time
),
ready AS (
    SELECT
        stay_id,
        antibiotic_time,
        culture_time,
        organ_dysfunction_time,
        GREATEST(antibiotic_time, COALESCE(culture_time, antibiotic_time), organ_dysfunction_time) AS ready_time
    FROM qualifying
),
earliest AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY stay_id ORDER BY ready_time, antibiotic_time) AS position
    FROM ready
)
SELECT
    stay_id,
    ready_time AS recognition_time,
    antibiotic_time,
    culture_time,
    organ_dysfunction_time
FROM earliest
WHERE position = 1
ORDER BY stay_id

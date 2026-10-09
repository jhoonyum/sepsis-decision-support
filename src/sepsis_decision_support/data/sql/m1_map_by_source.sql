-- Mean arterial pressure readings split by how they were measured (M1 check only).
--
-- The main pipeline uses mimic-code's vitalsign.mbp, which averages arterial-line and
-- cuff (non-invasive) readings charted at the same time. The M1 check compares the
-- hypotension rule on that series with an arterial-line-only series, so the readings
-- are returned per source, with mimic-code's plausibility filter (0 < value < 300).
--   arterial:      220052 Arterial Blood Pressure mean, 225312 ART BP Mean
--   non_invasive:  220181 Non Invasive Blood Pressure mean
SELECT
    stay_id,
    charttime,
    CASE WHEN itemid = 220181 THEN 'non_invasive' ELSE 'arterial' END AS source,
    AVG(valuenum) AS value
FROM mimiciv_icu.chartevents
WHERE itemid IN (220052, 225312, 220181)
  AND stay_id IS NOT NULL
  AND valuenum > 0
  AND valuenum < 300
GROUP BY stay_id, charttime, source

-- Test query: daily count of GDELT GKG articles mentioning "Hormuz" via the
-- extracted V2Locations field (a full-text location mention, closer to what
-- the DOC API's timelinevolraw does than event-level geocoding is).
--
-- Run this in the BigQuery console (console.cloud.google.com/bigquery) —
-- click "More" -> "Query settings" -> check "Dry run" first to see the
-- estimated bytes processed before actually running it.
--
-- Validate against src/data/doc_cache/hormuz__threat__*.csv before trusting
-- this method for the other 5 corridors: if the daily counts here are in
-- the same ballpark (not identical — different method — but correlated and
-- similarly scaled) as what the DOC API already gave us for Hormuz, the
-- method transfers. If they look wildly different, something about the
-- V2Locations matching needs adjusting before we rely on it.

SELECT
  PARSE_DATE('%Y%m%d', SUBSTR(CAST(DATE AS STRING), 1, 8)) AS event_date,
  COUNT(*) AS article_count
FROM `gdelt-bq.gdeltv2.gkg`
WHERE DATE >= 20170101000000 AND DATE < 20180101000000  -- one year first, to
                                                          -- check the cost/shape
                                                          -- before scaling up
  AND REGEXP_CONTAINS(V2Locations, r'(?i)Hormuz')
GROUP BY event_date
ORDER BY event_date;

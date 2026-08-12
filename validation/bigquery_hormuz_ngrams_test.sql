-- Test 2: same validation goal as bigquery_hormuz_test.sql, but against the
-- Web News NGrams 3.0 table instead of GKG's V2Locations (which undercounted
-- by ~72x against the known DOC API ground truth for Hormuz 2017).
--
-- ngrams are real words as they appeared in article text, with the source
-- url per row, so COUNT(DISTINCT url) approximates "how many articles
-- mentioned this word" -- much closer to what the DOC API measures than a
-- geocoded location tag is.
--
-- Compare the total / shape here against src/data/doc_cache/hormuz__threat__2020.csv
-- (note: that DOC API pull is Hormuz AND a threat-word -- this query is just
-- Hormuz alone, so expect this count to be HIGHER, not identical. What we're
-- checking is whether it's the same ORDER OF MAGNITUDE as the DOC ground
-- truth, not an exact match -- if it's back in the tens-of-thousands range
-- instead of ~150, the method is usable.)

SELECT
  DATE(date) AS event_date,
  COUNT(DISTINCT url) AS article_count
FROM `gdelt-bq.gdeltv2.webngrams`
WHERE date >= TIMESTAMP('2020-01-01') AND date < TIMESTAMP('2020-02-01')  -- one month first
  AND LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) = 'hormuz'
GROUP BY event_date
ORDER BY event_date;

-- If BigQuery errors on the `date` column (wrong type assumed above), tell
-- me the exact error text -- I'll adjust based on the schema it reports.

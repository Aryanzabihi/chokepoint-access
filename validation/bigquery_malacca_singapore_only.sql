-- Standalone, minimal version -- just the malacca/singapore diagnostic,
-- nothing else, to rule out a paste error truncating the query.
-- Same window as before: +/-90/60 days around the 2019-08-01 Singapore
-- Strait robbery wave. 'singapore' should NOT come back empty -- it's a
-- common word. If it does again, tell me the exact error text (if any)
-- shown below the editor after running.

SELECT
  DATE(date) AS event_date,
  LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) AS word,
  COUNT(DISTINCT url) AS article_count
FROM `gdelt-bq.gdeltv2.webngrams`
WHERE date BETWEEN TIMESTAMP('2019-05-03') AND TIMESTAMP('2019-09-30')
  AND LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) IN ('malacca', 'singapore')
GROUP BY event_date, word
ORDER BY word, event_date;

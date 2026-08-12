-- One representative event per remaining corridor, narrow ±90/+30-day
-- windows only (not full history) -- matching PRE_DAYS=90/POST_DAYS=30 from
-- 19c_attribution_recall.py so the response-ratio numbers this produces are
-- directly comparable to that script's own already-validated methodology.
--
-- Two corridors need phrase reconstruction since webngrams is unigram-only:
-- taiwan_strait matches ngram='taiwan' with 'strait' in the following-word
-- context; black_sea matches ngram='black' with 'sea' in the following-word
-- context. suez / malacca / turkish_straits match directly as single words
-- (turkish_straits via Bosphorus/Bosporus/Dardanelles, same as doc_queries.csv).

WITH tagged AS (
  SELECT
    DATE(date) AS event_date,
    url,
    CASE
      WHEN LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) = 'suez'
           AND date BETWEEN TIMESTAMP('2020-12-23') AND TIMESTAMP('2021-04-23')
        THEN 'suez'
      WHEN LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) = 'malacca'
           AND date BETWEEN TIMESTAMP('2019-05-03') AND TIMESTAMP('2019-09-01')
        THEN 'malacca'
      WHEN LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) IN ('bosphorus', 'bosporus', 'dardanelles')
           AND date BETWEEN TIMESTAMP('2022-04-23') AND TIMESTAMP('2022-08-22')
        THEN 'turkish_straits'
      WHEN LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) = 'taiwan'
           AND REGEXP_CONTAINS(LOWER(post), r'strait')
           AND date BETWEEN TIMESTAMP('2022-05-06') AND TIMESTAMP('2022-09-04')
        THEN 'taiwan_strait'
      WHEN LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) = 'black'
           AND REGEXP_CONTAINS(LOWER(post), r'sea')
           AND date BETWEEN TIMESTAMP('2021-11-26') AND TIMESTAMP('2022-03-27')
        THEN 'black_sea'
      ELSE NULL
    END AS corridor
  FROM `gdelt-bq.gdeltv2.webngrams`
  WHERE
       (date BETWEEN TIMESTAMP('2020-12-23') AND TIMESTAMP('2021-04-23'))
    OR (date BETWEEN TIMESTAMP('2019-05-03') AND TIMESTAMP('2019-09-01'))
    OR (date BETWEEN TIMESTAMP('2022-04-23') AND TIMESTAMP('2022-08-22'))
    OR (date BETWEEN TIMESTAMP('2022-05-06') AND TIMESTAMP('2022-09-04'))
    OR (date BETWEEN TIMESTAMP('2021-11-26') AND TIMESTAMP('2022-03-27'))
)
SELECT corridor, event_date, COUNT(DISTINCT url) AS article_count
FROM tagged
WHERE corridor IS NOT NULL
GROUP BY corridor, event_date
ORDER BY corridor, event_date;

-- Check the "will process X" estimate shown below the editor BEFORE running.
-- Expected order of magnitude: 5 windows x ~120 days x ~39GB/day =~ 20-25TB
-- (well under the trial credit, but tell me the number before you hit Run).

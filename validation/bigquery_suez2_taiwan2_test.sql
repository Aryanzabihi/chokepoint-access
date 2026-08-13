-- Second known event for the two corridors currently sitting on a single
-- tested incident. Same +/-90/30-day methodology as every prior test.
-- suez: Red Sea diversion wave (2023-12-18) -- pre 2023-09-19, post 2024-01-17
-- taiwan_strait: Joint Sword 2024A (2024-05-23) -- pre 2024-02-23, post 2024-06-22

WITH tagged AS (
  SELECT
    DATE(date) AS event_date,
    url,
    CASE
      WHEN LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) = 'suez'
           AND date BETWEEN TIMESTAMP('2023-09-19') AND TIMESTAMP('2024-01-17')
        THEN 'suez_event2'
      WHEN LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) = 'taiwan'
           AND REGEXP_CONTAINS(LOWER(post), r'strait')
           AND date BETWEEN TIMESTAMP('2024-02-23') AND TIMESTAMP('2024-06-22')
        THEN 'taiwan_strait_event2'
      ELSE NULL
    END AS corridor
  FROM `gdelt-bq.gdeltv2.webngrams`
  WHERE
       (date BETWEEN TIMESTAMP('2023-09-19') AND TIMESTAMP('2024-01-17'))
    OR (date BETWEEN TIMESTAMP('2024-02-23') AND TIMESTAMP('2024-06-22'))
)
SELECT corridor, event_date, COUNT(DISTINCT url) AS article_count
FROM tagged
WHERE corridor IS NOT NULL
GROUP BY corridor, event_date
ORDER BY corridor, event_date;

-- Expected cost: ~120 days x 2 windows =~ 240 days x ~39GB/day =~ 9-10TB.
-- Check the "will process" estimate before running.

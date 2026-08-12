-- Two follow-ups from the first smoketest:
-- 1. black_sea's SECOND known event (Grain deal collapse, 2023-07-17) --
--    confirms or overturns the borderline 2.89x read from event 1.
-- 2. malacca -- widened post-window (+60d instead of +30d) in case coverage
--    of the 2019 robbery wave was delayed, PLUS a diagnostic 'singapore'
--    column (same event was widely reported as "Singapore Strait robbery",
--    not "Malacca") -- this tells us whether the event just wasn't covered
--    under that name, vs the method missing real signal. 'singapore' is
--    NOT a usable production search term (too broad/generic) -- diagnostic
--    only, to distinguish those two explanations.

WITH tagged AS (
  SELECT
    DATE(date) AS event_date,
    url,
    CASE
      WHEN LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) = 'black'
           AND REGEXP_CONTAINS(LOWER(post), r'sea')
           AND date BETWEEN TIMESTAMP('2023-04-18') AND TIMESTAMP('2023-08-16')
        THEN 'black_sea_event2'
      WHEN LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) = 'malacca'
           AND date BETWEEN TIMESTAMP('2019-05-03') AND TIMESTAMP('2019-09-30')
        THEN 'malacca_widened'
      WHEN LOWER(REGEXP_REPLACE(ngram, r'[^a-zA-Z]', '')) = 'singapore'
           AND date BETWEEN TIMESTAMP('2019-05-03') AND TIMESTAMP('2019-09-30')
        THEN 'singapore_diagnostic'
      ELSE NULL
    END AS corridor
  FROM `gdelt-bq.gdeltv2.webngrams`
  WHERE
       (date BETWEEN TIMESTAMP('2023-04-18') AND TIMESTAMP('2023-08-16'))
    OR (date BETWEEN TIMESTAMP('2019-05-03') AND TIMESTAMP('2019-09-30'))
)
SELECT corridor, event_date, COUNT(DISTINCT url) AS article_count
FROM tagged
WHERE corridor IS NOT NULL
GROUP BY corridor, event_date
ORDER BY corridor, event_date;

-- Expected cost: ~120 days (black_sea) + ~150 days (malacca/singapore window,
-- shared) =~ 270 unique days x ~39GB/day =~ 10-11TB. Check the "will
-- process" estimate before running, same as before.

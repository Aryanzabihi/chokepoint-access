# Per-chokepoint evidence grade — reconciliation outcome

**Date:** 2026-08-12
**Question this answers:** for each of the 7 corridors `tar_ingest.py`'s `CORRIDORS`
actually covers, is there real, validated, corridor-specific signal to build a
non-fabricated threshold/time-window on — and if so, from what evidence?

**Method:** response ratio = mean daily mention volume 30 days after a known,
dated incident / mean daily volume 90 days before it (identical methodology to
`19c_attribution_recall.py`, `v06_validate.py`). ≥3.0 = usable, 1.5–3.0 = weak,
<1.5 = blind. Source is phrase/word-matched full-text mentions (DOC API where
already fetched; `gdelt-bq.gdeltv2.webngrams` via BigQuery otherwise) — **not**
the geographic-radius GDELT attribution the original 2026-08-05 report used,
which this session's recall test showed is blind for most corridors (Hormuz
median 1.27, Bab-el-Mandeb 1.21 under the old method).

---

## Result

| Corridor | Grade | Evidence | Confidence |
|---|---|---|---|
| **Hormuz** | `DIRECT_ESTIMATE` | 7 events (DOC API, already fetched): 5 usable (4.0–7.7×), 1 weak, 1 blind. Median ≈4.9×. | High |
| **Bab-el-Mandeb** | `DIRECT_ESTIMATE` | 4 testable events (1 predates coverage): 2 strongly usable (9.8×, 33.8×), 2 blind. | Moderate — bimodal, not uniform |
| **Suez** | `DIRECT_ESTIMATE` | 1 event (webngrams): Ever Given, 14.0×. | Moderate — single event, but very clean signal |
| **Taiwan Strait** | `DIRECT_ESTIMATE` | 1 event (webngrams): Post-Pelosi exercises, 3.6×. | Moderate — single event, at the usable threshold |
| **Turkish Straits / Black Sea** | `DIRECT_ESTIMATE`, flagged weak | 3 events across both sub-regions (webngrams): 2.9×, 1.7×, 2.3× — consistently weak, none clear the 3.0 bar. | Low — real signal exists but is weaker than the corridor's neighbors |
| **Adriatic** | `STRUCTURAL_ESTIMATE` | No testable event: only known onsets (Bosnia 1992, Kosovo 1999) predate every available data source (GDELT 2.0 from 2015, webngrams from 2020). | N/A — not a data gap, a coverage-era gap |
| **Malacca** | `STRUCTURAL_ESTIMATE` | No testable event: only known incident (2019-08-01) predates webngrams (2020-01-01+); no post-2020 incident on record. | N/A — same class of gap as Adriatic |

## What changed from the 2026-08-05 report

That report concluded corridor-specific thresholds were not estimable at all,
largely on the strength of geo-radius GDELT attribution scoring near chance
everywhere. This session found that conclusion was itself partly an artifact
(the `tar_share_z` bug, §Aug-6 retest) and, more importantly, that phrase/word-
based attribution — which the original report's own `TASK.md` follow-up
correctly identified as the real fix — works well where it's been tested: 5 of
7 corridors now have direct, validated evidence, two of them (Hormuz,
Bab-el-Mandeb) from a real multi-event sample, not a single data point.

## What this doesn't change

Turkish Straits/Black Sea and, to a lesser extent, Suez and Taiwan Strait still
rest on thin samples (1–3 events). This is evidence *of a method that works*,
not evidence of a *statistically fit threshold* — fitting an actual
bootstrapped threshold (the way `v06_validate.py` does for the corridors that
clear estimability) needs more events per corridor than are currently tested,
though the fetch/build machinery to get there (`v16_fetch_doc_tar.py`,
`21_build_ctar_doc.py` not yet written) now has a validated attribution method
to build on rather than an untested hope.

## Next step

This table is the input to the chokepoint-profile/threshold feature itself —
not yet designed. `DIRECT_ESTIMATE` corridors can carry a real, sourced
threshold; `STRUCTURAL_ESTIMATE` corridors (Adriatic, Malacca) need a
descriptive-characteristics-based approach instead, per the earlier plan.

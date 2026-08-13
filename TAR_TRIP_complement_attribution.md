# A note on corridor-level attribution recall, following on Section 5.6

**Status:** unreviewed supplementary note, not part of the manuscript. Prepared
after the manuscript's analysis was complete, using data collected for a
downstream product built on the same corridor registry.

## What this is, and what it is not

This note extends one specific diagnostic from Section 5.6's second negative
result — "A machine-coded reconstruction recovers part of the signal and does
not reproduce it" — using a different GDELT data product than the one tested
there. It reports evidence that a word/phrase-level source may not share the
attribution weakness the manuscript found in geographic-radius attribution.

It does **not** bear on the manuscript's other findings. In particular:

- It says nothing about escalation-onset anticipation (Sections 5.1–5.5). That
  result is unaffected and unaddressed here.
- It does **not** establish corridor-specific action thresholds. Section
  5.6's first negative result — pooled AUC 0.466 against real transit
  episodes, thresholds indistinguishable from noise on a chance-centred score
  — is a test of whether the *signal* discriminates future onsets. Nothing
  below tests that. This note is a test of whether text data can correctly
  *detect and attribute* a known, already-dated incident to its corridor,
  which is a precondition for building a corridor-specific signal, not a
  demonstration that one exists.
- It does not bear on Section 5.6's third negative result (rerouting is not
  anticipated). That used a different outcome (AIS-derived transit diversion)
  entirely.

In short: the manuscript asked "does a corridor-level signal predict the
future," found no, and separately asked "can a machine-coded reconstruction
even see the past correctly," and found only partially. This note stays
inside the second question, on a different data source.

## The manuscript's own diagnostic

Section 5.6 reports, for the GDELT event-archive reconstruction attributed by
geographic radius and littoral state:

> "the ratio of attributed volume in the thirty days after a known maritime
> crisis to the ninety days before has a median of 1.27 at Hormuz, 1.21 at
> Bab-el-Mandeb, 1.17 at Suez, 0.94 at Malacca and 0.73 at the Turkish
> Straits, and each corridor's highest-volume month corresponds to a violent
> event in a nearby capital rather than a maritime one."

A ratio near 1.0 means the attributed series barely moves when a real
maritime incident occurs — the geo-radius method is picking up regional
political volume in general, not the specific event. The manuscript's stated
remedy: "Separating these requires a corridor-filtered text corpus with a
threat-and-act dictionary rather than a reconstruction from an event
database" (Section 5.6), repeated in the Conclusions as the named next step
for the research programme (Section 8).

## What was tested here

Same formula, different source and attribution method: mean daily mention
volume in the 30 days after a known, dated incident, divided by mean daily
volume in the 90 days before it. Two sources, disclosed per corridor rather
than pooled:

- **Hormuz and Bab-el-Mandeb**: GDELT DOC 2.0 API, phrase query plus a
  threat-or-act word group, already-fetched cache (`src/data/doc_cache/`,
  fetched 2026-08-05/06).
- **Suez, Taiwan Strait, Turkish Straits/Black Sea**: `gdelt-bq.gdeltv2.webngrams`
  via BigQuery — word-level n-grams with pre/post context fields, allowing
  phrase reconstruction (fetched 2026-08-12/13).
- **Adriatic, Malacca**: not testable by either method. Both corridors'
  only known incidents predate the coverage start of every available source
  (GDELT 2.0 from 2015, webngrams from 2020) — a coverage-era gap, not a
  result of any kind.

This is a mixed pipeline, not a single controlled comparison, and that is
disclosed rather than smoothed over — see Limitations below.

## Results

| Corridor | Date | Incident | Pre-mean | Post-mean | Ratio | Status | Source |
|---|---|---|---:|---:|---:|---|---|
| Hormuz | 2019-05-12 | Fujairah tanker sabotage | 101.8 | 409.5 | 4.02× | usable | DOC API |
| Hormuz | 2019-06-13 | Gulf of Oman attacks | 225.7 | 1745.8 | 7.73× | usable | DOC API |
| Hormuz | 2019-07-19 | Stena Impero seizure | 885.2 | 1573.1 | 1.78× | weak | DOC API |
| Hormuz | 2020-01-03 | Soleimani strike | 95.9 | 509.8 | 5.32× | usable | DOC API |
| Hormuz | 2021-07-29 | Mercer Street attack | 29.2 | 39.2 | 1.34× | blind | DOC API |
| Hormuz | 2024-04-13 | MSC Aries seizure | 16.8 | 91.1 | 5.41× | usable | DOC API |
| Hormuz | 2025-06-13 | Israel-Iran war onset | 21.2 | 104.6 | 4.93× | usable | DOC API |
| Bab-el-Mandeb | 2016-10-09 | Houthi attacks on USS Mason | — | — | — | predates coverage | — |
| Bab-el-Mandeb | 2018-07-25 | Saudi tanker attack | 26.3 | 258.4 | 9.84× | usable | DOC API |
| Bab-el-Mandeb | 2023-11-19 | Galaxy Leader seizure | 3.1 | 105.5 | 33.77× | usable | DOC API |
| Bab-el-Mandeb | 2024-02-18 | Rubymar struck | 89.1 | 53.4 | 0.60× | blind | DOC API |
| Bab-el-Mandeb | 2025-07-06 | Magic Seas / Eternity C | 11.0 | 7.0 | 0.64× | blind | DOC API |
| Suez | 2021-03-23 | Ever Given grounding | 108.2 | 1518.6 | 14.04× | usable | webngrams |
| Suez | 2023-12-18 | Red Sea diversion wave | 173.4 | 716.8 | 4.13× | usable | webngrams |
| Taiwan Strait | 2022-08-04 | Post-Pelosi exercises | 167.2 | 595.8 | 3.56× | usable | webngrams |
| Taiwan Strait | 2024-05-23 | Joint Sword 2024A | 94.7 | 151.8 | 1.60× | weak | webngrams |
| Turkish Straits/Black Sea | 2022-02-24 | Invasion of Ukraine | 553.6 | 1602.4 | 2.89× | weak | webngrams |
| Turkish Straits/Black Sea | 2022-07-22 | Grain corridor agreement | 62.8 | 143.5 | 2.29× | weak | webngrams |
| Turkish Straits/Black Sea | 2023-07-17 | Grain deal collapse | 720.0 | 1213.2 | 1.68× | weak | webngrams |

Thresholds: ≥3.0× usable, 1.5–3.0× weak, <1.5× blind — the same convention
the manuscript uses for the corridor-month score, applied here to a recall
diagnostic rather than a forecast.

## Comparison against the manuscript's geo-radius figures

| Corridor | Geo-radius median (manuscript) | Word/phrase-level median (here) | n incidents |
|---|---:|---:|---:|
| Hormuz | 1.27 | 4.93 | 7 |
| Bab-el-Mandeb | 1.21 | 2.30* | 4 (1 untestable) |
| Suez | 1.17 | 9.09 | 2 |
| Turkish Straits | 0.73 | 2.29 | 3 |

\* Bab-el-Mandeb's median across 4 usable/blind observations sits between the
0.60–0.64× blind pair and the 9.84–33.77× usable pair — a bimodal
distribution poorly summarised by a single median (see Limitations).

Every corridor with a direct comparison shows a higher point estimate under
word/phrase-level attribution than under geo-radius. That is directional
evidence, not a tested result — see the next section before reading anything
stronger into it.

## Limitations, stated at the same standard the manuscript uses

- **No null.** The manuscript never reports a discrimination or recall
  figure without a permutation or rotation null, and warns explicitly that
  "wherever a specification, horizon or threshold grid is searched, the null
  is the distribution of the maximum" (Section 4.4). No null has been run
  here. The comparison above is a point-estimate comparison across methods
  that were tried informally (geo-radius, DOC API, webngrams) — exactly the
  kind of search the manuscript's own rule is written to guard against. It
  should be read as suggestive, not as a demonstrated improvement.
- **Not a matched comparison.** The manuscript's five geo-radius medians are
  not disaggregated to the incident level in the main text, so it cannot be
  confirmed here that the same incidents underlie both sides of the table
  above. Method and incident selection may both differ.
- **Small, uneven samples.** Two to seven incidents per corridor is far
  below what either this note or the manuscript treats as sufficient for a
  threshold; the manuscript's own corridor-threshold attempt failed in part
  for this reason, at a much larger scale (twelve corridors, real transit
  episodes).
- **Mixed pipeline.** Hormuz and Bab-el-Mandeb use a different API and word
  list than the other three corridors. A single, consistent pipeline across
  all corridors would be needed before treating cross-corridor differences
  as meaningful rather than as an artifact of two different tools.
- **Recall, not anticipation.** Every incident above is scored using
  30 days of coverage *after* a date that is already known. This says
  nothing about whether elevated coverage precedes the incident, which is
  the manuscript's actual subject.

## What would make this rigorous

The manuscript names the fix already, and this note is a small, informal
step toward it, not a substitute for it: "a corridor-filtered text corpus
with a threat-and-act dictionary... would supply the corridor variation the
published index lacks, separate the explanations for the reconstruction's
failure, and permit the leakage test a count-based index forecloses"
(Section 8). Concretely, that means: a single consistent source across all
seven corridors, a purpose-built threat/act dictionary applied per corridor
rather than a generic query, an incident sample large enough to bootstrap a
confidence interval per corridor, and a permutation or rotation null on the
recall statistic itself before any cross-method claim is made.

## Provenance

- `validation/report/doc_api_hormuz_babelmandeb_recall.txt`
- `validation/report/webngrams_5corridor_smoketest.txt`
- `validation/report/webngrams_suez2_taiwan2.txt`
- `validation/data/webngrams_blacksea_event2.csv`
- `validation/report/phase_d_corridor_grades.md`
- `validation/report/response_ratio_pattern.md`
- `src/chokepoint_profiles.py` (`PROFILES`, same figures, duplicated as
  literal data per that module's own convention)

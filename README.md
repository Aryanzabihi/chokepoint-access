# TAR corridor validation — data collection stage

Drop this folder inside `tar_phase1/` as `tar_phase1/validation/`. It writes only
under `validation/`. Nothing touches `data/`, `output/` or `final_output/`.

---

## Read this before running anything

**The corridor-threshold programme cannot be run on your current panel, and the
reason is not fixable by adding corridors.**

Your manuscript's panel (`final_output/panel_final.csv::tar`) is *global*: one
TAR value per month, repeated identically across all seven units. A threshold
estimated on it is by construction the same for every corridor. "Do corridors
differ?" is not unanswered on that data — it is unanswerable.

And the event side is worse than it looks. Table 3 of v5 gives eight onsets over
seven units:

| unit | onsets |
|---|---|
| Hormuz | 4 |
| Adriatic | 2 |
| Black Sea | 1 |
| Bab-el-Mandeb | 1 |
| Suez, Malacca, Taiwan Strait | 0 |

A per-corridor ROC needs positives *in that corridor*. Three units have none, so
their AUC is undefined. The largest has four. Adding the other eight chokepoints
from your framework document adds eight more zeros. Phase 11 (threshold
estimation), Phase 12 (threshold comparison) and Phase 14
(threshold ~ corridor characteristics, 15 predictors on 7 units) are all
unidentified on this design, and no amount of bootstrapping rescues them.

So the data collection stage has one job: **build a corridor-attributed TAR
series and a corridor-specific continuous outcome.** Two free sources do it.

### GDELT 1.0 → the corridor-attributed TAR

GPR ships as counts over 44 published country indices. Iran, Yemen and the
Balkan states are not among them, which is why three of your seven units are
proxy-attributed and why only 1 of 8 onsets sits in a directly-covered corridor.
That coverage gap is a property of the input, not of your method.

GDELT 1.0 gives one row per event, 1979-01-01 to present, each carrying a
latitude and longitude and a CAMEO code that separates coercive *speech*
(root 13, 15) from coercive *action* (18, 19, 20). That is the TAR numerator and
denominator, geolocated, over your full sample. Attribution stops being a proxy
argument and becomes a distance calculation.

It also closes §8.2: you currently have to state that article-level leakage is
untestable because GPR ships as counts, not corpus. GDELT ships the corpus.

### IMF PortWatch → the corridor-specific outcome

Daily AIS transit calls for 28 chokepoints since 2019. This converts the outcome
from 8 binary onsets to a continuous per-corridor series. The Red Sea diversion
alone puts hundreds of disruption-days on Bab-el-Mandeb and Suez.

**But be honest about what that buys.** It is 2019+, and it is *episodes*, not
days, that set the effective sample size — the ~300 days of the Red Sea
diversion are one draw, not 300. Realistically you get one or two episodes each
at Bab-el-Mandeb, Suez, Panama and Hormuz, and zero almost everywhere else.
That is enough for a hierarchical model with partial pooling. It is not enough
for sixteen independent thresholds, and `v05` will tell you so per corridor
rather than letting you find out after the fact.

My working expectation is that **the threshold-heterogeneity test will not
reject**, and the defensible output is a global threshold with corridor-specific
*baseline rates*. That is a genuinely publishable result, and it is exactly what
step 6 of your own scientific validation order points at. It is also the result
that best fits the paper: a chokepoint-restricted sample already discriminates
better (0.842 vs 0.753) without any per-corridor cut.

---

## Run order

```bash
cd tar_phase1/validation

python _smoke_test.py                    # 2 min, synthetic — verifies the chain
                                         # before you commit to a long download

python v01_fetch_portwatch.py            # ~5 min
python v02_fetch_gdelt.py --start 2015 --workers 8    # ~15 min, test slice
python v02_fetch_gdelt.py --workers 8    # 1-3 h, full archive, RESUMABLE
python v02_fetch_gdelt.py --merge
python v03_attribute.py                  # then READ report/attribution_audit.txt
python v04_build_ctar.py
python v05_outcomes_audit.py             # then READ report/estimability.txt
```

`v02` is interruptible. Kill it, rerun it, it skips what it already has. It
downloads ~50 GB but keeps only the filtered rows, so the disk footprint is a
few hundred MB.

---

## Two stop-and-check points

**After `v03`** — `report/attribution_audit.txt` gives direct-tier share per
corridor. Any corridor below ~20% direct has a geometry problem in
`corridors.csv`, not a data problem: fix the polyline or radius and rerun v03.
The coordinates in that file are my first pass and should be checked against a
chart before anything is published on them.

**After `v04`** — the script prints the mean cross-corridor correlation of the
primary series. If it is above ~0.9 the series is still effectively global and
nothing downstream will separate. The likely cause is the `C_proxy` tier, which
shares actors (USA, CHN, RUS) across corridors and pools them back together.
Rebuild on `tier_stack == "direct"` if so.

---

## Files

| file | what it does |
|---|---|
| `corridors.csv` | corridor registry — geometry, littoral FIPS codes, proxy CAMEO actors, PortWatch name. **Edit this, not the code.** |
| `vconfig.py` | paths, CAMEO sets, TAR specs, the 8/4/6 event set from Table 3 |
| `v01_fetch_portwatch.py` | PortWatch metadata + daily transit calls |
| `v02_fetch_gdelt.py` | streaming download + filter of GDELT 1.0, resumable |
| `v03_attribute.py` | 3-tier corridor attribution → corridor-day threat/act counts |
| `v04_build_ctar.py` | corridor TAR panel, 4 specs × 3 tier stacks, daily + monthly |
| `v05_outcomes_audit.py` | transit-based disruption labels + estimability report |
| `_smoke_test.py` | synthetic end-to-end run |

---

## Traps already handled in the code

- **Two code alphabets in adjacent columns.** `ActionGeo_CountryCode` is
  FIPS 10-4 (Iran `IR`, Oman `MU`, China `CH`, Turkey `TU`);
  `Actor1CountryCode` is CAMEO/ISO-3-like (`IRN`, `OMN`, `CHN`, `TUR`).
  `corridors.csv` carries both lists separately. Mixing them silently drops
  most of a corridor's events.
- **No look-ahead.** Normalisation is within-corridor and uses only months ≤ t.
  Rolling-120 is the default because expanding broke on structural breaks in
  the historical extension.
- **PortWatch's 2021 receiver expansion** produced a sustained level shift at
  Malacca and elsewhere. The outcome uses a trailing 365-day median baseline,
  so a coverage change is not read as a trade collapse.
- **Known blackout days** (2022-05-12, 2023-02-14, 2024-01-09) are dropped, not
  read as drops.
- **The superseded build.** Nothing here reads `output/tar_monthly.csv`, whose
  `TAR_mw` column is actually the published `TAR_sharez` spec (AUC 0.549).
  Rename that directory to `output_phase1_superseded/`.

## Traps NOT handled, that the validation stage has to face

- **Instrument correlation.** AIS goes dark under exactly the jamming and
  spoofing that accompany escalation, while news coverage surges. Both push
  toward a spurious TAR-disruption association. The Panama drought and Ever
  Given placebos are the control: physical disruption, no escalation, TAR must
  not fire.
- **Episode clustering.** Every interval must be a block bootstrap clustered on
  episode. Treating disruption days as independent would manufacture corridor
  thresholds that do not exist, and it would be invisible in the output — the
  intervals would simply look reassuringly narrow.
- **Two different samples.** GDELT-only gives 1979-2026 with binary onsets;
  GDELT+PortWatch gives 2019-2026 with a continuous outcome. They answer
  different questions and their thresholds are not comparable. Do not pool them.

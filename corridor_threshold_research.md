# Corridor-Specific Thresholds — Research Path (not started)

## Why this document exists

The paper's own Section 5.6 tested a corridor-specific TAR threshold and
got a pooled AUC of 0.466 against real transit episodes — worse than
chance. That result is real and the product respects it: no corridor gets
its own fitted numeric TAR cutoff today.

But that finding is narrower than "corridor-specific thresholds are
impossible." It specifically killed *one* approach: fit a separate raw
TAR cutoff per corridor from the current 8-onset historical sample. It
does not prove a better-designed, better-resourced attempt could never
work. The paper is a limitation to design around, not a ceiling on what
this product can ever become — this document scopes what a real attempt
would require, honestly, so a future decision to pursue it (or not) is
made with the real cost in view.

## The evidence that a naive attempt would fail again

Computed directly from `docs/readings.json` + `tar_ingest.ONSETS`, 2026-08-15:

| Chokepoint | TAR the month before each recorded onset |
|---|---|
| Strait of Hormuz | 1.122 (1987) · 0.834 (1990) · 4.029 (2003) · 4.522 (2026) |
| Adriatic | 1.834 (1992) · 0.983 (1999) |
| Bab-el-Mandeb | 1.827 (2023) — n=1 |
| Turkish Straits / Black Sea | 6.178 (2022) — n=1 |
| Suez Canal, Strait of Malacca, Taiwan Strait | no recorded onset at all |

Hormuz's own 4 pre-onset readings span nearly the entire band scale
(Routine to Critical transition). Two corridors have exactly one recorded
onset. Fitting *anything* — a raw threshold, a multi-factor cutoff, a
composite susceptibility score — to samples this size and this noisy
reproduces the 0.466 problem, just with more free parameters and even less
data per parameter. More knobs on the same eight events is not a fix.

## Path A — extend the historical window before 1985

**Status: done, 2026-08-15 — checked and implemented.**

Confirmed against the live release (`data/gpr_monthly.dta`): the
country-level series aren't named `GPRT`/`GPRA` per country as this
document originally guessed — they're a separate family, `GPRC_<code>`
(current release) and `GPRHC_<code>` (historical release), 44 countries
each. `GPRC_*` starts exactly 1985-01 for every one of the 44 (a hard wall,
not a coincidence — it's the actual reason `BENCHMARK_START` is 1985-01).
`GPRHC_*` has full 1900-01–2026-07 coverage for all 44, including 11 of the
16 corridor-relevant codes (`SAU`/`EGY`/`ITA`/`TUR`/`HUN`/`RUS`/`UKR`/`MYS`/
`IDN`/`CHN`/`TWN`). The 5 small Gulf states used as Hormuz's GCC proxy
(`ARE`/`KWT`/`QAT`/`OMN`/`BHR`) are absent from both families, current and
historical — that ceiling doesn't move regardless. In the 1985–2026
overlap, `GPRC_` and `GPRHC_` correlate 0.89–0.98 per country — closely
related, but not confirmed here to share a newspaper corpus/methodology
with the modern index, so the implementation always prefers `GPRC_` where
it has a value and only uses `GPRHC_` to fill months `GPRC_` has none for.

**Implemented**: `tar_ingest._extend_country_columns()`, wired into
`load()`/`build_readings()`. As anticipated, this is free information, not
a fix for the n=1–4-per-corridor problem — TAR itself and `ONSETS` are
untouched, still anchored to 1985 (the paper's own calibration), and
nothing here adds a single onset event. What it does do: widen the
long-run average/z-score baseline each corridor's live salience reading is
judged against, from ~41 years to up to 126. Today's own `share` value is
unchanged for every corridor (proven in `tar_ingest.py`'s selftest, not
just eyeballed); `share_avg`/`salience_z` move to reflect the longer
baseline.

**Found as a byproduct, not part of the original scope**: `salience_parts()`
had a live bug — `endswith()` column matching counted `GPRHC_<code>`
alongside `GPRC_<code>` in the numerator while the denominator only ever
summed `GPRC_*`, inflating every corridor's shipped `share`/`salience_z`
by roughly 2x in the currently-committed `docs/readings.json` (e.g. Hormuz
13.04% shown vs. 6.29% correct — every corridor, same ~2x, since every
country code has both columns). Fixed in the same pass (tightened to exact
`GPRC_<code>` match). Checked whether this needed a `--amend` against
`docs/record.jsonl`'s hash chain — it doesn't: the ledger only ever hashed
headline fields (`tar`/`band`/`alarm_now`/etc.), never per-corridor
salience. The fix is in `src/tar_ingest.py`, selftest-verified; the
committed `docs/readings.json` snapshot itself was deliberately left as-is
this session on request — it will pick up the fix next time
`run_month.py`'s normal monthly cycle regenerates it.

## Path B — a richer, graded disruption indicator (the real work)

**Status: not started. This is where an actual fix would come from.**

Stop only counting the 8 headline, internationally-recognized crisis
onsets. Build a graded, per-corridor, per-month disruption indicator using
signals that occur far more often than "major war":

- Freight rate spikes on the corridor's own lanes
- War-risk / hull premium moves (extends `warrisk.py`'s existing register
  — 2 real episodes today — rather than replacing it)
- Reported incidents of any severity (not just headline-onset scale)
- Port congestion / transit-time anomalies
- JWC listed-area changes (extends `services.listing_stats()`'s existing
  register)

Done well, this turns the sample from single digits into potentially
hundreds of corridor-months — enough to fit something real. This is
genuine data-sourcing and coding work (systematically finding and grading
historical events per corridor per month), not a config change or a
modeling trick.

## Honest limits that persist even with more data

Not reasons to stop — reasons to report results honestly if this is
pursued:

1. **Onset events are driven by discrete political decisions**, not a
   smooth physical process. A media-coverage-based index can genuinely
   miss what doesn't show up in coverage until after the fact — Hormuz
   1990 (TAR 0.834, "Routine," the month before a real invasion) is a real
   example already on record, not a hypothetical.
2. **Non-stationarity**: pooling 1987 Iran–Iraq tanker-war dynamics with
   2026 Hormuz dynamics assumes those regimes are comparable enough to
   model together. That's a real methodological question a bigger sample
   doesn't answer by itself.

## Required validation methodology (already specified, unbuilt)

`upgrade.txt` (this project's own prior planning doc) already calls for
exactly the right discipline here, under Phase 3 — quoted directly, not
reinvented:

> "Do not simply optimize TAR on 1985–2026 and then evaluate it on the
> same history... Training period... Validation... Test... Or preferably
> use rolling/expanding windows... Never allow future information to leak
> into past thresholds."

Any corridor-conditioning work must use walk-forward / expanding-window
validation, fit and evaluated on strictly non-overlapping periods. Report
out-of-sample performance plainly — including if it still doesn't beat the
global threshold. That is a real possible outcome, the same way 0.466 was.

## Concrete sequence, if this gets picked up

1. ~~Check GPR country-level coverage before 1985 (Path A)~~ — **done,
   2026-08-15**, see above.
2. Define what counts as a graded disruption event per corridor per month
   (Path B) — a real scoping decision, needs domain judgment before any
   data collection starts.
3. Source and code that data historically, corridor by corridor.
4. Implement expanding-window validation per `upgrade.txt`'s own spec —
   never fit and test on the same data.
5. Report out-of-sample AUC / precision / recall honestly, whatever it
   turns out to be, graded the same way every other number in this
   product already is (`CLIENT_QUOTED`/`EPISODE_ANALOGUE`/etc. vocabulary).

## Scope note

This is a multi-week-to-multi-month research and data-engineering effort,
not a coding task. Nothing here is started. If step 2 (defining the graded
disruption indicator) gets picked up, it should be scoped as its own
planned piece of work before any implementation begins.

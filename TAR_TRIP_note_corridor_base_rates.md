# Corridor-specific base rates — implementing Section 5.6's own recommendation

**Status:** unreviewed supplementary note, not part of the manuscript. This
one is not a new empirical result — it documents a product decision that
follows directly from a finding already in the manuscript, and records why
the product does *not* do the thing that finding rules out.

## The question this answers

Two pieces of the product look the same for every chokepoint no matter
which one you ask about: the historical hit-rate used as a fallback
probability (27% of alarms, globally, precede a real disruption), and the
"time to onset" horizon attached to the current alarm band (e.g. "60–90
days" at the Procurement Watch band). A reasonable question is why these
aren't chokepoint-specific, given the product otherwise goes to some
trouble to describe each corridor individually (see
`TAR_TRIP_companion_profiles.md`).

## Why they can't be, and this isn't a gap

Both numbers derive from a single, global TAR value. That isn't an
implementation shortcut — the manuscript tested a corridor-specific version
of exactly this and reports it does not work:

> "The published measure is global: the maximum cross-corridor standard
> deviation of the panel signal within any month is 0.000000, so a
> threshold estimated on it is by construction identical everywhere...
> Estimating corridor-level thresholds against transit episodes across
> twelve corridors returns a pooled area under the curve of 0.466... The
> defensible operational output is therefore a single global threshold
> applied with corridor-specific base rates, not a corridor-specific
> threshold." (Section 5.6)

The band-and-horizon table (`src/tar_ingest.py:48-55`, the manuscript's own
Table S1) is deliberately locked for the same reason — the module's own
comment: "Construction constants. All from the paper; none are tunable at
runtime, because a threshold you can tune is a threshold you can calibrate
with hindsight" (`tar_ingest.py:37-39`). The horizon is emitted once,
globally, on purpose: "The horizon belongs to the band, and the band is
global — so it is emitted once here rather than repeated on every
corridor, where it would imply a route-specific warning time that this
method cannot give" (`tar_ingest.py:440-442`). A selftest hard-asserts this
never drifts: `assert out["horizon"] == assign_band(out["readings"][0]["tar"])[2]`
(`tar_ingest.py:621`).

So: not a gap. A finding, already established, already enforced in code.

## What was built instead

The sentence quoted above names the defensible alternative explicitly: keep
the one global threshold, but read it against each corridor's own base
rate. `src/decision_engine.py`'s `base_rate_context(corridor)` now does
exactly that — nothing more.

It adds one new fact, a raw historical onset **count**, sourced from
`tar_ingest.ONSETS` (`tar_ingest.py:75-80`) — the same dict the product's
own live regime-detection logic already uses, so this can never drift from
it and required no new data entry. It is not a probability, not a
threshold, and not computed from the TAR signal at all — it is simply "how
many of the manuscript's own Table 1 headline onsets happened at this
corridor."

```python
# src/decision_engine.py — base_rate_context()
n = len(ONSETS.get(corridor, []))
"corridor_onsets": n,
"corridor_onset_grade": "EPISODE_ANALOGUE" if n else "STRUCTURAL",
"corridor_note": (
    f"{corridor} has recorded {n} of the sample's 8 headline onsets "
    f"since 1985-01 — a historical frequency, not a recalibrated "
    f"probability." if n else
    f"{corridor} has recorded no headline onset in the historical "
    f"sample (1985-01–2026). Treat this as a coverage-era fact, not "
    f"a claim of immunity."
)
```

The fact is routed through the same ledger every other number in the
product goes through, with a real grade — `EPISODE_ANALOGUE` for corridors
with a recorded onset, `STRUCTURAL` for those without (Suez, Malacca,
Taiwan Strait) — and it is attached to the same place the global
band/horizon is displayed, alongside the standing disclosure that the band
itself is global:

> "TAR 1.6, Procurement Watch (60-90 days). Band is a global reading — it
> does not indicate which theatre is moving, and the same horizon estimate
> applies at every corridor. **Strait of Hormuz has recorded 4 of the
> sample's 8 headline onsets since 1985 — a historical frequency, not a
> recalibrated probability.**"
>
> "TAR 1.6, Procurement Watch (60-90 days). Band is a global reading —
> [same sentence]. **Suez Canal has recorded no headline onset in the
> historical sample. Treat this as a coverage-era fact, not a claim of
> immunity.**"

Same alarm, same horizon, for every corridor — because that's what the
data supports — with a second sentence, unique per corridor, saying
plainly what is and isn't known about that specific one.

## What this deliberately does not claim

- It does not give any corridor a different alarm threshold or a different
  horizon estimate. Both stay global, as the manuscript requires.
- It does not turn 4 onsets into a rate, a percentage, or a probability. A
  count of 4 events in 41 years is not a base rate in the statistical
  sense — it is disclosed as a count, on purpose.
- It is a different fact from `chokepoint_profiles.py`'s response-character
  evidence (whether media coverage detectably responds to a *known*
  incident at that corridor — see `TAR_TRIP_complement_attribution.md`).
  The two can and do disagree: Adriatic has 2 recorded onsets
  (`EPISODE_ANALOGUE` here) but its response-character evidence is
  `STRUCTURAL` (those onsets predate GDELT coverage); Suez has 0 recorded
  onsets (`STRUCTURAL` here) but strong response-character evidence
  (`EPISODE_ANALOGUE`, 14.0× and 4.1× on two tested incidents). Neither
  contradicts the other — they answer different questions and are kept in
  separate fields rather than merged into one.

## Provenance

- `src/tar_ingest.py:37-39, 48-55, 75-80, 440-443, 621` (bands, onsets,
  the global-horizon design and its selftest)
- `src/decision_engine.py` — `base_rate_context()`, and its use in
  `build_decision()`'s ledger and `decision_brief_html()`'s Reading section
- `TAR_TRIP_Main.docx`, Section 5.6 ("Corridor-specific action thresholds
  are not identifiable")

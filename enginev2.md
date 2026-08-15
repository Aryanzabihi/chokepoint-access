# TAR Decision Engine v2 — architecture

Status: design, not built. Supersedes nothing — `economic_engine.py` (v0.2) stays
on disk and untouched, imported as a pricing kernel.

New files only: `decision_engine.py`, `intake.py`, `episodes.py`. Nothing existing
is edited except one bug fix (§9.1) and one line in `run_month.py` (§11).

---

## 1. The design problem in one sentence

`economic_engine.py` asks the client for `residual_loss_estimate` per strategy,
`loss_if_disrupted`, and four `conditional_loss` figures. Those *are* the answer.
The engine formats a decision the client had to make before opening it, and the TAR
signal contributes nothing to the ranking — remove the band check and the
recommended strategy is unchanged.

Everything below follows from inverting that:

> **The client supplies the normal world, which their systems already hold. The
> engine supplies the crisis transformation, which is the thing they are paying
> for.**

## 2. What this engine is, and is not

**Is:** a decision layer that takes a shipment or contract described in ordinary
procurement terms, prices the disruption from observed episodes, and answers three
questions — act or wait, what would have to change, and what is the cost of another
week of waiting.

**Is not**, and deliberately: no option-value model, no value-of-information model,
no cascade elasticities, no portfolio optimiser, no probability of its own. Each of
those requires parameters no company holds, which means supplying them ourselves —
which is fabrication, and breaks the rule the rest of this codebase is built on. A
number that cannot be traced to a client system, a dated register entry, or the
frozen paper does not enter a result.

The v1 critiques asked for fifteen layers on top of an input set nobody can fill.
This spec goes the other way: **fewer inputs, more inference, every inferred number
labelled with where it came from.**

---

## 3. Layer map

```
                    CLIENT SYSTEMS (ERP / contracts / finance)
                                    │
                    ┌───────────────▼───────────────┐
              L1    │  INTAKE — tiered, by dept,    │   intake.py
                    │  Incoterm-branched            │
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────▼───────────────┐
              L2    │  DERIVATION — finance facts   │   intake.py
                    │  → engine parameters          │
                    └───────────────┬───────────────┘
                                    │
     WAR-RISK       ┌───────────────▼───────────────┐
     REGISTER  ────►│  CRISIS MULTIPLIER — observed │   episodes.py
     + ONSETS       │  episode analogues            │   ◄── THE DIFFERENTIATOR
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────▼───────────────┐
              L3    │  PRICING KERNEL — unchanged   │   economic_engine.py
                    │  transport/insurance/delay/   │   (imported, not edited)
                    │  inventory/commodity          │
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────▼───────────────┐
              L4    │  DECISION — correct expected  │   decision_engine.py
                    │  cost, break-even p, flip,    │
                    │  cost of waiting, regret      │
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────▼───────────────┐
              L5    │  LEDGER + EVIDENCE GRADE      │   decision_engine.py
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────▼───────────────┐
              L6    │  ONE-PAGE BRIEF (html + json) │
                    └───────────────────────────────┘
```

---

## 4. L1 — Intake

### 4.1 Incoterm branch, asked first

The Incoterm decides which fields are *answerable*. Asking a CIF buyer for a
war-risk premium they never see is how a tool gets abandoned in the first five
minutes.

| Incoterm | Buyer holds | Engine must not ask for | Buyer's real exposure |
|---|---|---|---|
| EXW / FOB / FCA | freight, insurance, war risk | — | full set |
| CIF / CIP | goods price only | freight, premiums, reroute | delay, stockout, replacement, penalty |
| DAP / DDP | goods price only | all transport and insurance | delay, stockout, penalty |
| CFR | freight (seller), insurance (buyer) | freight, reroute | insurance + the DAP set |

On CIF/CIP/DAP/DDP the brief must state plainly: *the seller carries the transport
and insurance exposure; your exposure is late delivery and replacement, and it is
smaller than a freight-based tool would tell you.* That honesty is worth more than
the fields it costs us.

### 4.2 Tiers

Each tier produces a usable answer. The output states its own width and names what
the next tier would buy.

**Tier 0 — three numbers (already shipped as `client_profile.py`)**
Early-action cost, crisis replacement cost, chokepoint → α, band position, verdict.
Keep it. It is the only tier answerable in a meeting.

**Tier 1 — procurement alone, 8 fields, no help needed**

| Field | System of record |
|---|---|
| Chokepoint | — (picklist) |
| Ship date or window | PO / shipping schedule |
| Incoterm | PO / contract |
| Cargo value | commercial invoice / PO |
| Quantity + unit | PO |
| Contract freight rate for the lane | freight contract / last invoice |
| Contract transit time (days) | freight contract |
| Days of cover at destination | ERP / WMS — **days, never units** |

Output: full decision, wide ranges, every crisis figure labelled
`EPISODE_ANALOGUE`.

**Tier 2 — finance adds 4 fields**

| Field | Usual form | System of record |
|---|---|---|
| WACC / cost of capital | % p.a., board-approved | treasury |
| Inventory carrying cost | % p.a. (typically 15–25) | controlling |
| Gross margin on affected product | % | controlling |
| Late-delivery penalty | currency per day | customer contract / legal |

Narrows delay and inventory from analogue to client-specific.

**Tier 3 — real quotations**

Disrupted or spot freight quote, reroute quote, war-risk premium quote, emergency
replacement quote. Any one of these **overrides** the analogue for that component
and re-grades it `CLIENT_QUOTED`.

**Tier 4 — their own disruption history (highest value, lowest ask)**

Most importers already lived through Suez 2021, Black Sea 2022, Red Sea 2023–24. In
their ERP that history exists as purchase price variance on emergency buys and
expedited freight spend. Asking for *their own last three disruptions* is more
answerable than any forecast question, and it feeds `client_profile.py`'s α
directly. This is the intake path to prioritise commercially.

### 4.3 Rules

- Every field carries an owning department in the schema, so a partially completed
  intake prints *"ask finance for carrying-cost %; it narrows this result by
  €X"* rather than an error.
- No field has a default. Missing → analogue (labelled) or `None` (stated), never a
  silent number.
- Reject unit ambiguity at the door: currency per intake, days not weeks, % p.a.
  not per-day.

---

## 5. L2 — Derivation

Fields the client cannot supply, built from fields they can. Each derivation is
printed in the ledger with its formula, so it can be argued with.

**Delay cost per day** — replaces `delay_cost_rate`, which no company holds:

```
delay_cost_per_day = (WACC / 365) × cargo_value
                   + contractual_penalty_per_day
                   + stockout_probability × daily_gross_margin
```

where `stockout_probability` is 0 while days-of-cover exceeds the delay and 1
after — a step, not a curve, because a curve would be invented.

**Inventory holding cost per unit per day**:

```
holding_per_unit_day = (carrying_cost_pct_pa / 365) × unit_value
```

**Loss if disrupted** — assembled, not asked:

```
L_conditional = replacement_premium          (Tier 3 quote, else analogue)
              + expedited_freight_delta      (analogue)
              + delay_cost_per_day × delay_days
              + penalty_exposure
              − value_recovered_by_insurance (if cover confirmed)
```

**Not derived, and removed as an input:** `disruption_attributable_price_change`.
Separating a market-wide commodity move from the share attributable to one
disruption is an econometric judgment, not a company record. The commodity
component becomes optional and off by default, with the market-wide move reported
beside the result as context only.

---

## 6. L3 — Crisis multiplier (`episodes.py`)

This is the part no competitor can copy, because it runs on the register.

### 6.1 Principle

Do not model a deterioration curve. **Quote observed episodes as analogues.**

From the register and the frozen paper:

| Episode | Onset | Premium move | Over | JWC listing lag |
|---|---|---|---|---|
| Hormuz | 2026-02-28 | ×37 | 18 days | 3 days (day precision) |
| Bab-el-Mandeb | 2023-11-19 | ×20 | 104 days | 29 days (day precision) |
| Black Sea | 2022-02 | — | — | ≤34 days (month precision) |

### 6.2 Interface

```python
analogue(corridor, component, day_offset) -> Analogue | None
```

Returns a multiplier against the client's own baseline, the episode it came from,
its dates, its source reference, and `n`. Returns `None` — never a number — when
the register holds fewer than two comparable episodes for that component.

Components covered at launch: war-risk premium (register), freight (register +
public indices where dated), listing lag (JWC circulars). Delay days and
replacement premium stay `None` until the premium register is filled; the brief
says so.

### 6.3 Two rules that keep this honest

1. **Always plural, never averaged.** Present the fast case and the slow case
   side by side — *"if this behaves like Hormuz, ×37 within three weeks; like
   Bab-el-Mandeb, ×20 over three months"* — with `n = 2` on the face of it.
   Averaging two episodes into one number invents a central tendency that three
   observations cannot support.
2. **Regime check before any lead-time claim.** Reuse
   `tar_ingest.regime()`. Inside `POST_ONSET_MONTHS = 12` the reading is
   concurrent, not anticipatory: suppress the horizon, keep the level. Same rule
   already enforced on the board; a decision brief that violates it is worse than
   one that omits it.

---

## 7. L3 — Pricing kernel (reused as-is)

Imported unchanged from `economic_engine.py`:

`baseline_cost`, `transport_cost`, `delay_cost`, `insurance_cost`,
`inventory_cost`, `commodity_effect`, `total_disrupted_cost`, `welfare_gap`,
`Provenance`, `weakest_source`, `CostBreakdown`.

Imported from `services.py`: `positive_band`, `value_score`, `point_in_time`.
From `tar_ingest.py`: `CORRIDORS`, `regime`, `ONSETS`.

Roughly 60–70% of v1's mechanics survive as the calculation layer, exactly as the
critique suggested. What does not survive is v1's *top level*: `compute()`,
`compare_strategies()`, `template_scenario()`, and the CLI are replaced, not
extended.

---

## 8. L4 — Decision layer

### 8.1 Strategies declare effects, not answers

The single change that fixes §1. A strategy no longer carries
`residual_loss_estimate`; it carries what it *does*:

```json
{
  "name": "Partial reroute",
  "direct_cost": 700000,
  "effects": {
    "delay_days_delta": -6,
    "capacity_restored": 0.40,
    "war_risk_premium_multiplier": 0.25,
    "days_of_cover_delta": 0
  }
}
```

Residual loss is then **recomputed by the kernel** with those effects applied. The
client supplies what an action costs and what it buys — both quotable, both things
procurement negotiates — and the engine derives the consequence. A quoted residual
may still override, but it is no longer required.

### 8.2 Correct expected cost

v1's defect, stated exactly: `compare_strategies()` sets
`expected_total_cost = direct_cost + residual_loss_estimate`. Probability never
enters. The field is named `expected_` and is conditional.

v2 keeps the two quantities separately named and never mixes them:

```
L_cond(s, j)  conditional loss of strategy s in state j   (kernel output)
E[C_s] = C_action(s) + Σ_j P_j · L_cond(s, j)
```

`conditional_cost` and `expected_cost` are both reported. Any result that carries
one where the other belongs fails the selftest.

### 8.3 Break-even probability — the generalised threshold

v1's `P* = C_mitigation / L_disruption` silently assumes mitigation removes the
loss entirely. It does not. The correct form divides by the loss *avoided*:

```
p*(s vs s₀) = [C_action(s) − C_action(s₀)] / [L_cond(s₀) − L_cond(s)]
```

which reduces to `C/L` exactly when `L_cond(s) = 0` and `C_action(s₀) = 0` — so the
published threshold remains a special case, and the paper's figures still reproduce.
Assert that reduction in the selftest.

### 8.4 Inverse mode — the default output

Do not ask for probability. Solve for it, then place it against evidence we already
publish:

> This reroute pays only if a closure is more likely than **16%** in your shipping
> window. The published base rate across 11 alarm episodes is **27%** (95% CI
> 10–57%). The current index reading for this chokepoint is **2.31** — active
> disruption, so no lead time is claimed.

Zero new inputs. It turns the paper's widest confidence interval into the product's
core sentence, and it is the one screen a CFO can act on without learning anything
about the method.

### 8.5 Cost of waiting

```
COW(Δ) = E[C_act at t+Δ] − E[C_act at t]
```

with the cost path taken from §6 analogues at discrete day offsets, not a fitted
curve. Output is two paths, fast and slow, each named for its episode. Suppressed
entirely when §6 returns `None` — a cost-of-waiting figure with no observed
trajectory behind it is the single most tempting fabrication in this whole design.

### 8.6 Decision flip

For each parameter, solve one-dimensionally for the value that equalises the top
two strategies. Exact, no simulation. Report only parameters whose flip point lies
inside a plausible range, sorted by how close the current value sits to its flip
point:

> Recommendation flips to WAIT if the probability you assign falls below 16%, or if
> the reroute quote exceeds €940k. It flips to FULL REROUTE if delay exceeds 18
> days. Two of these three are things you can watch.

### 8.7 Regret at the range endpoints

```
R(s) = E[C_s] − min_s' E[C_s']   evaluated at both ends of the supplied range
```

Plain sentences, per the existing house style: *"if probability is at the low end
of your range, insurance-only would have been cheaper by €210k."* No new inputs —
it reuses the range the client already gave.

### 8.8 Deferred to Phase 2 (named so they are not forgotten)

Combination strategies enumerated from declared effects — buildable once §8.1
exists, but effects do not compose linearly (two actions that both cut delay do not
cut it twice), so it needs an explicit non-additivity rule before it ships. Not a
portfolio optimiser; enumeration with a warning.

---

## 9. L5 — Ledger and grades

### 9.1 The one edit to existing code

Fix or annotate `economic_engine.compare_strategies`. Preferred: rename the field
to `conditional_total_cost` and add a deprecation note pointing at
`decision_engine`. Do not leave a field named `expected_` that is not an
expectation — it is the kind of thing that ends a technical due-diligence call.

### 9.2 Input grades

Every number in a result carries one, stamped on the brief and not suppressible:

| Grade | Meaning |
|---|---|
| `CLIENT_QUOTED` | a dated quotation the client holds |
| `CLIENT_SYSTEM` | read from their ERP, contract or finance record |
| `DERIVED` | computed from client facts by a §5 formula, formula printed |
| `EPISODE_ANALOGUE` | multiplier from a dated register episode, episode named, `n` shown |
| `PUBLISHED` | from the frozen paper or the hash-chained record |
| `ABSENT` | not supplied, consequence stated |

Mirrors the `PUBLISHED` / `RECONSTRUCTED` distinction `services.py` already stamps.
Rolls up as `weakest_source` already does: a brief is as strong as its weakest
input, and says so.

### 9.3 Travelling ledger

The full input list, with grade, source and formula, goes into the JSON and renders
as a checklist in the brief. A downstream system can refuse to act on a result
whose ledger has an `ABSENT` in a load-bearing row — the opposite of every
optimiser that hides its defaults.

---

## 10. L6 — The brief

One screen, generated as HTML on `site.css` and inlined by `build_site.py`. Order
is deliberate: the decision, then the reason, then the doubt.

```
DECISION            PARTIAL REROUTE — or wait, if you assign under 16%
BREAK-EVEN          16%   vs published base rate 27% (CI 10–57%, n=11)
READING             2.31 · active disruption · no lead time claimed
COST OF WAITING     fast path (Hormuz-like): €—/week · slow (BEM-like): €—/week
WHAT FLIPS IT       probability < 16% · reroute quote > €940k · delay > 18 days
EXPOSURE            baseline €— → disrupted €— · avoidable €—
LEDGER              4 CLIENT_SYSTEM · 3 DERIVED · 2 EPISODE_ANALOGUE · 1 ABSENT
WHAT WOULD SHARPEN  a carrier quote narrows this by €— · finance's carrying-cost %
NOT CLAIMED         global origin · no theatre identification · n=2 analogues
```

`WHAT WOULD SHARPEN` is value-of-information done for free — the honest half of
what the critique asked for, with no VOI model and no invented priors.

---

## 11. Files and integration

**New:** `decision_engine.py` (decision math, brief, CLI, selftest), `intake.py`
(tiers, department map, Incoterm branch, derivations), `episodes.py` (analogues from
`warrisk_jwc.csv` + `warrisk.csv` + `ONSETS`).

**Unchanged:** `economic_engine.py` (kernel), `tar_ingest.py`, `publish.py`,
`services.py`, `client_profile.py`, `warrisk.py`, `reconcile.py`, `map.html`.

**One line elsewhere:** `run_month.py` gains a `decision_engine.py --selftest` step
alongside the other five.

CLI, matching house convention:

```
python decision_engine.py intake --tier 1 --out intake.json
python decision_engine.py decide --intake intake.json --brief brief.html
python decision_engine.py flip   --intake intake.json
python decision_engine.py --selftest
```

## 12. Selftest — the assertions that make this real

1. `E[C_s]` reproduces a hand-worked figure with probability applied.
2. `p*` reduces exactly to `C/L` when residual loss is 0 — the published threshold
   is a special case.
3. Endogenous residual from declared effects reproduces the kernel's own component
   arithmetic.
4. `episodes.analogue()` returns `None`, not a number, when the register holds
   fewer than two comparable episodes.
5. Cost of waiting is absent from the result when §6 returned `None`.
6. Regime check: inside 12 months of an onset, no horizon appears anywhere in the
   brief.
7. Incoterm CIF intake never requests a war-risk premium and the brief states why.
8. No field is silently defaulted: mutate the intake to drop each field in turn and
   assert the result either degrades with a stated grade or raises.
9. Ledger completeness: every number in the JSON resolves to a grade.
10. Same input, same output modulo timestamp.

## 13. Build order

1. `intake.py` + derivations + Incoterm branch — the usability unlock, no new data
   needed.
2. §8.1 effects and §8.2 correct expected cost — the credibility fix.
3. §8.3 break-even and §8.4 inverse mode — the product's core sentence, zero new
   inputs.
4. §9 ledger and grades.
5. §10 brief.
6. `episodes.py` and §8.5 cost of waiting — **gated on the premium register.**

Steps 1–5 are buildable now and make the tool usable by one person in one sitting.

## 14. The dependency worth stating plainly

Step 6 is the only differentiating feature in this document — the one a competitor
cannot copy, because it runs on a register nobody else has. That register's premium
half is currently empty.

So the honest sequence is not *build v2, then collect data*. It is: build steps 1–5
because they need nothing, and fill the premium register in parallel, because until
it holds two dated episodes per corridor, cost of waiting cannot ship and the v2
engine is a better-designed calculator rather than a different product.

The other open item is unchanged and outranks all of this: no buyer has yet
confirmed they can answer the cost-ratio question. This spec reduces that risk
(Tier 1 asks only for things an ERP holds) but does not remove it. One conversation
with one procurement manager, holding this intake list, would tell you more than
step 5.

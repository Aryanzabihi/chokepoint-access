"""
decision_engine.py — the TAR Decision Engine v2 (enginev2.md, L4 Decision +
L5 Ledger + L6 Brief).

economic_engine.py (v1) asks the client for `residual_loss_estimate` per
strategy — the answer the engine should be deriving — and its
`expected_total_cost` never actually weights by probability despite the
name. This module inverts that: a strategy declares what it costs and what
it *does* (StrategyEffects), and the conditional loss is recomputed by the
pricing kernel with those effects applied. v1 (economic_engine.py) is not
imported for its comparison logic at all — only its pure, effect-free
kernel functions (transport_cost, insurance_cost, inventory_cost,
commodity_effect, CostBreakdown, welfare_gap) and its published constants
are reused. v1 is untouched and stays a second, standalone tool.

intake.py (L1 + the client-fact half of L2) and episodes.py (L3's crisis-
multiplier half) are imported; this module owns the seam where L2's
derivations meet L3's analogues and the kernel — assembling a conditional
loss per strategy — plus everything from there up: expected cost,
break-even probability, decision flip, cost of waiting, regret, the
travelling ledger, and the one-page brief.

Every number that reaches a result is written through a Ledger, which
grades it CLIENT_QUOTED / CLIENT_SYSTEM / DERIVED / EPISODE_ANALOGUE /
PUBLISHED / ABSENT (enginev2.md section 9.2) — never a default, never a
silent substitution.

Usage
-----
    python decision_engine.py intake --tier 1 [--incoterm CIF] --out intake.json
    python decision_engine.py decide --intake intake.json [--source ../data/gpr_monthly.dta] \\
        [--warrisk ../data/warrisk.csv] [--jwc ../data/warrisk_jwc.csv] [--as-of YYYY-MM] \\
        [--out result.json] [--brief brief.html]
    python decision_engine.py flip --intake intake.json [--source ...] [--warrisk ...] [--jwc ...]
    python decision_engine.py --selftest
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intake  # noqa: E402
import episodes  # noqa: E402
import chokepoint_profiles  # noqa: E402

from economic_engine import (  # noqa: E402
    transport_cost, insurance_cost, inventory_cost, commodity_effect,
    CostBreakdown, welfare_gap, mitigation_threshold,
    TransportInputs, InsuranceInputs,
    ALARM_EPISODES, EPISODES_WITH_DISRUPTION, EPISODE_HIT_RATE_CI,
)
from tar_ingest import CORRIDORS, POST_ONSET_MONTHS, regime, ONSETS, BENCHMARK_START  # noqa: E402
from services import point_in_time  # noqa: E402

MODEL_VERSION = "decision-engine-2.0"


# --------------------------------------------------------------------------
# Strategy effects (section 8.1 — the core fix)
# --------------------------------------------------------------------------

@dataclass
class StrategyEffects:
    """forward_buy_fraction/forward_buy_early_days only add the financing +
    carrying cost of committing early (forward_buy_cost() below) -- they do
    not by themselves reduce conditional_loss(). A forward-buy strategy that
    is meant to remove risk on the secured fraction sets capacity_restored/
    war_risk_premium_multiplier alongside them, same as any other
    risk-reducing strategy; the two pairs of fields are independent knobs on
    purpose, so forward-buy composes with (rather than silently overrides) a
    strategy that also reroutes or insures.

    sourced_from_emergency_replacement_quote marks a strategy whose total
    conditional loss IS the client's emergency_replacement_quote (sourcing
    replacement cargo from an alternate supplier entirely, rather than
    mitigating freight/insurance on the same shipment) -- read live from
    intake_data in conditional_loss() rather than copied onto
    quoted_residual_loss by hand, so a later reassessment with an updated
    quote re-prices this strategy with no extra plumbing. Checked before
    the older quoted_residual_loss override below, which still works
    unchanged for a manually-typed figure when this flag isn't set."""
    delay_days_delta: float = 0.0
    capacity_restored: float = 0.0                      # fraction 0-1
    war_risk_premium_multiplier: float | None = None    # None = leave the quote as-is
    days_of_cover_delta: float = 0.0
    forward_buy_fraction: float = 0.0                    # fraction 0-1, secured before the
                                                          # contract timeline would have required
    forward_buy_early_days: float = 0.0                  # how many days sooner that fraction is committed
    sourced_from_emergency_replacement_quote: bool = False


@dataclass
class Strategy:
    name: str
    direct_cost: float
    effects: StrategyEffects = field(default_factory=StrategyEffects)
    quoted_residual_loss: float | None = None    # Tier-3 override, still optional
    is_baseline: bool = False
    notes: str = ""


def _strategy_from_dict(d: dict) -> Strategy:
    return Strategy(name=d["name"], direct_cost=d["direct_cost"],
                    effects=StrategyEffects(**d.get("effects", {})),
                    quoted_residual_loss=d.get("quoted_residual_loss"),
                    is_baseline=d.get("is_baseline", False),
                    notes=d.get("notes", ""))


# --------------------------------------------------------------------------
# Assembly — the L2/L4 seam (section 5's L_conditional, per strategy)
# --------------------------------------------------------------------------

def baseline_costbreakdown(intake_data: dict) -> CostBreakdown:
    """The counterfactual: normal-state cost, no disruption. v2 does not
    collect a baseline insurance premium (not in intake.py's field set —
    the decision only needs the *change* in war-risk exposure, not a full
    steady-state accounting), so insurance stays 0 here by design."""
    f = intake_data.get("fields", {})
    return CostBreakdown({
        "transport": f.get("contract_freight_rate") or 0.0,
        "insurance": 0.0,
        "delay": 0.0,
        "inventory": 0.0,
        "commodity": 0.0,
    })


def disrupted_costbreakdown(intake_data: dict) -> CostBreakdown:
    """The unmitigated crisis state, for the exposure/avoidable-loss figure
    only (section 10's EXPOSURE line) — not per-strategy. Built from
    whatever Tier-3 quotes exist; components with no quote and no
    derivation available stay 0 here and are flagged ABSENT by
    build_decision()'s ledger, never faked as a real cost."""
    f = intake_data.get("fields", {})
    transport = f.get("disrupted_freight_quote") or f.get("contract_freight_rate") or 0.0
    insurance = f.get("war_risk_premium_quote") or 0.0
    delay_rate = intake.delay_cost_per_day(
        wacc_pct=f.get("wacc_pct"), cargo_value=f.get("cargo_value"),
        penalty_per_day=f.get("penalty_per_day"), days_of_cover=f.get("days_of_cover"),
        delay_days=f.get("delay_days_estimate"),
        daily_gross_margin=_daily_gross_margin_value(f))
    delay = delay_rate.value * (f.get("delay_days_estimate") or 0.0)
    holding_rate = intake.holding_per_unit_day(
        f.get("carrying_cost_pct_pa"), intake.unit_value(f.get("cargo_value"), f.get("quantity")))
    inventory = inventory_cost(f.get("quantity") or 0.0,
                               holding_rate.value if holding_rate else 0.0,
                               f.get("delay_days_estimate") or 0.0)
    return CostBreakdown({
        "transport": transport, "insurance": insurance, "delay": delay,
        "inventory": inventory, "commodity": 0.0,
    })


def _daily_gross_margin_value(fields: dict, days_of_cover: float | None = None) -> float | None:
    dgm = intake.daily_gross_margin(fields.get("cargo_value"), fields.get("gross_margin_pct"),
                                    days_of_cover if days_of_cover is not None else fields.get("days_of_cover"))
    return dgm.value if dgm is not None else None


@dataclass
class ConditionalLoss:
    strategy: str
    components: dict[str, float]
    total: float
    grade: str                              # "CLIENT_QUOTED" (quoted override) or "DERIVED"
    absent_components: list[str] = field(default_factory=list)
    effective_days_of_cover: float | None = None   # days_of_cover + this strategy's
                                                    # days_of_cover_delta -- "coverage after
                                                    # strategy" (section 10-style transparency:
                                                    # already computed to derive delay/stockout
                                                    # above, surfaced here rather than discarded)
    effective_delay_days: float | None = None       # delay_days_estimate + this strategy's
                                                    # delay_days_delta -- same "already computed,
                                                    # previously discarded" transparency, now also
                                                    # surfaced for module_1's service_risk_stockout


def conditional_loss(intake_data: dict, strategy: Strategy,
                     premium_analogue: "episodes.Analogue | None" = None) -> ConditionalLoss:
    """section 5's L_conditional, recomputed per strategy per section 8.1.
    Strategy effects shift days_of_cover/delay_days BEFORE
    delay_cost_per_day is re-derived — this is where the step-function
    interaction section 8.6 is about actually lives. war_risk_premium_
    multiplier scales a Tier-3 war-risk quote (None = leave it unmitigated);
    capacity_restored scales both the Tier-3 disrupted-freight delta and,
    independently, how much of a reroute_quote this strategy picks up as a
    rerouting premium -- the same fraction serves as each strategy's "how
    much of the rerouting problem does this address" signal for both
    quotes, reusing economic_engine.py's already-tested (in v1)
    TransportInputs.rerouting_premium field. commodity
    is excluded by default, per section 5. `premium_analogue` is accepted
    for interface symmetry with cost_of_waiting() but is not used to
    manufacture a currency figure here: without a baseline premium field in
    intake.py's schema, a multiplier alone has no monetary anchor — see the
    module docstring. If strategy.effects.sourced_from_emergency_replacement_quote
    is set and an emergency_replacement_quote is on file, or (failing that)
    if strategy.quoted_residual_loss is set directly, the total is
    overridden (grade CLIENT_QUOTED) — but the assembled components stay in
    the return value alongside it — the override is auditable, never a
    silent substitution."""
    f = intake_data.get("fields", {})
    absent: list[str] = []

    days_of_cover = f.get("days_of_cover")
    effective_days_of_cover = (None if days_of_cover is None
                               else days_of_cover + strategy.effects.days_of_cover_delta)
    delay_days = f.get("delay_days_estimate")
    effective_delay_days = (None if delay_days is None
                            else max(0.0, delay_days + strategy.effects.delay_days_delta))

    dgm = _daily_gross_margin_value(f, effective_days_of_cover)
    delay_rate = intake.delay_cost_per_day(
        wacc_pct=f.get("wacc_pct"), cargo_value=f.get("cargo_value"),
        penalty_per_day=f.get("penalty_per_day"), days_of_cover=effective_days_of_cover,
        delay_days=effective_delay_days, daily_gross_margin=dgm)
    delay_component = delay_rate.value * (effective_delay_days or 0.0)

    holding_rate = intake.holding_per_unit_day(
        f.get("carrying_cost_pct_pa"), intake.unit_value(f.get("cargo_value"), f.get("quantity")))
    inventory_component = inventory_cost(f.get("quantity") or 0.0,
                                         holding_rate.value if holding_rate else 0.0,
                                         effective_delay_days or 0.0)

    disrupted_quote = f.get("disrupted_freight_quote")
    contract_rate = f.get("contract_freight_rate")
    reroute_quote = f.get("reroute_quote")
    freight_pair_present = disrupted_quote is not None and contract_rate is not None
    # One combined branch, not two independent ones: "transport" must be
    # graded ABSENT only when neither source of data exists at all, and a
    # second independent `absent.append("transport")` for the reroute case
    # would double-record the same ledger line (`absent` has no dedup).
    if freight_pair_present or reroute_quote is not None:
        freight_delta = max(0.0, disrupted_quote - contract_rate) if freight_pair_present else 0.0
        rerouting_premium = (reroute_quote * strategy.effects.capacity_restored
                             if reroute_quote is not None else 0.0)
        transport_component = transport_cost(TransportInputs(
            disrupted_freight=freight_delta * (1.0 - strategy.effects.capacity_restored),
            rerouting_premium=rerouting_premium))
    else:
        transport_component = 0.0
        absent.append("transport")

    war_risk_quote = f.get("war_risk_premium_quote")
    if war_risk_quote is not None:
        mult = strategy.effects.war_risk_premium_multiplier
        war_risk_component = war_risk_quote if mult is None else war_risk_quote * mult
        insurance_component = insurance_cost(InsuranceInputs(war_risk_premium=war_risk_component))
    else:
        insurance_component = 0.0
        absent.append("insurance")

    commodity_component = commodity_effect(f.get("quantity") or 0.0, 0.0, 0.0)

    components = {
        "transport": transport_component, "insurance": insurance_component,
        "delay": delay_component, "inventory": inventory_component,
        "commodity": commodity_component,
    }
    computed_total = CostBreakdown(components).total

    emergency_quote = f.get("emergency_replacement_quote")
    residual_override = None
    if strategy.effects.sourced_from_emergency_replacement_quote and emergency_quote is not None:
        residual_override = emergency_quote
    elif strategy.quoted_residual_loss is not None:
        residual_override = strategy.quoted_residual_loss

    if residual_override is not None:
        return ConditionalLoss(strategy=strategy.name, components=components,
                               total=residual_override, grade="CLIENT_QUOTED",
                               absent_components=absent,
                               effective_days_of_cover=effective_days_of_cover,
                               effective_delay_days=effective_delay_days)
    return ConditionalLoss(strategy=strategy.name, components=components, total=computed_total,
                           grade="DERIVED", absent_components=absent,
                           effective_days_of_cover=effective_days_of_cover,
                           effective_delay_days=effective_delay_days)


# --------------------------------------------------------------------------
# Expected cost (section 8.2)
# --------------------------------------------------------------------------

def expected_cost(direct_cost: float, probability: float, l_cond: float) -> float:
    """E[C_s] = C_action(s) + p * L_cond(s). Binary state (disruption / no
    disruption) by deliberate scope — matches section 8.3's own reduction
    to C/L (inherently two-state) and section 2's refusal of cascade
    elasticities."""
    return direct_cost + probability * l_cond


def _expected_costs(strategies: list[Strategy], losses: dict[str, float],
                    probability: float) -> dict[str, float]:
    return {s.name: expected_cost(s.direct_cost, probability, losses[s.name]) for s in strategies}


# --------------------------------------------------------------------------
# Forward-buy cost — securing part of an order ahead of the contract
# timeline. Not a new forecast: the fraction and lead time are the client's
# own choice (informed by whatever they read off the corridor's current
# band, base rate and episode analogues below), and the cost is derived
# entirely from rates the client already supplied (wacc_pct,
# carrying_cost_pct_pa) for the days that fraction is held sooner than the
# contract required. No claim about when a future disruption will happen or
# how severe it will be enters this calculation.
# --------------------------------------------------------------------------

@dataclass
class ForwardBuyCost:
    value: float
    grade: str    # "DERIVED" or "ABSENT" -- ABSENT when a fraction is set but the client's
                  # own wacc_pct/cargo_value aren't, so the cost can't be derived, never guessed


def forward_buy_cost(intake_data: dict, effects: StrategyEffects) -> ForwardBuyCost:
    """Financing cost (WACC on the secured fraction, held forward_buy_early_days
    sooner) plus the extra inventory-carrying cost of holding that fraction
    that much longer, both at the client's own quoted rates. Zero, DERIVED,
    when no forward buy is requested (the default -- every strategy that
    doesn't use this field is unaffected)."""
    if effects.forward_buy_fraction <= 0 or effects.forward_buy_early_days <= 0:
        return ForwardBuyCost(0.0, "DERIVED")

    f = intake_data.get("fields", {})
    cargo_value = f.get("cargo_value")
    wacc_pct = f.get("wacc_pct")
    if cargo_value is None or wacc_pct is None:
        return ForwardBuyCost(0.0, "ABSENT")

    financing = wacc_pct * effects.forward_buy_fraction * cargo_value * (
        effects.forward_buy_early_days / 365.0)

    extra_carrying = 0.0
    carrying_pct = f.get("carrying_cost_pct_pa")
    quantity = f.get("quantity")
    if carrying_pct is not None and quantity:
        holding_rate = intake.holding_per_unit_day(carrying_pct, intake.unit_value(cargo_value, quantity))
        if holding_rate is not None:
            extra_carrying = (holding_rate.value * effects.forward_buy_fraction * quantity
                              * effects.forward_buy_early_days)

    return ForwardBuyCost(financing + extra_carrying, "DERIVED")


# --------------------------------------------------------------------------
# Break-even probability (section 8.3 — the generalised threshold)
# --------------------------------------------------------------------------

@dataclass
class BreakEvenResult:
    strategy: str
    baseline: str
    p_star: float | None
    reason: str | None = None


def break_even_probability(strategy: Strategy, baseline: Strategy,
                           l_cond_s: float, l_cond_s0: float) -> BreakEvenResult:
    """p*(s vs s0) = [C_action(s) - C_action(s0)] / [L_cond(s0) - L_cond(s)].
    When the denominator is <= 0, s does not reduce conditional loss versus
    s0 — no probability makes the trade worthwhile on this axis alone.
    Returns p_star=None with a reason, never raises ZeroDivisionError, never
    silently flips sign. Reduces exactly to economic_engine.
    mitigation_threshold()'s C/L when l_cond(s)=0 and C_action(s0)=0 — see
    selftest()."""
    denom = l_cond_s0 - l_cond_s
    if denom <= 0:
        return BreakEvenResult(
            strategy.name, baseline.name, None,
            reason=(f"{strategy.name} does not reduce conditional loss versus {baseline.name} "
                    f"(L_cond {l_cond_s:,.0f} vs {l_cond_s0:,.0f}) — no probability makes this "
                    f"trade worthwhile on this axis alone"))
    numer = strategy.direct_cost - baseline.direct_cost
    return BreakEvenResult(strategy.name, baseline.name, numer / denom, None)


def base_rate_context(corridor: str | None = None) -> dict:
    """Wraps economic_engine's own published constants — reused, not
    duplicated, since economic_engine.py already exposes them as named
    module-level constants. These three numbers are GLOBAL, pooled across
    every corridor, and stay that way here (main text Section 5.6 tested a
    corridor-specific version of this and got a pooled AUC of 0.466 against
    real transit episodes -- worse than chance; there is no signal to
    threshold per corridor).

    What Section 5.6 recommends instead is a single global threshold read
    against corridor-specific BASE RATES, not a corridor-specific
    threshold. When corridor is supplied, this adds exactly that: a raw
    historical onset COUNT for this corridor from tar_ingest.ONSETS (main
    text Table 1) -- a frequency fact, never a recalibrated probability.
    tar_ingest.ONSETS is reused rather than re-entered so this can never
    drift from the same data regime() already uses live."""
    result = {
        "alarm_episodes": ALARM_EPISODES,
        "episodes_with_disruption": EPISODES_WITH_DISRUPTION,
        "hit_rate": round(EPISODES_WITH_DISRUPTION / ALARM_EPISODES, 3),
        "hit_rate_ci_95": EPISODE_HIT_RATE_CI,
    }
    if corridor is not None:
        onsets = ONSETS.get(corridor, [])
        n = len(onsets)
        result["corridor_onsets"] = n
        result["corridor_onset_dates"] = onsets
        result["corridor_onset_grade"] = "EPISODE_ANALOGUE" if n else "STRUCTURAL"
        result["corridor_note"] = (
            f"{corridor} has recorded {n} of the sample's 8 headline onsets "
            f"since {BENCHMARK_START} — a historical frequency, not a "
            f"recalibrated probability."
            if n else
            f"{corridor} has recorded no headline onset in the historical "
            f"sample ({BENCHMARK_START}–2026). Treat this as a coverage-era "
            f"fact, not a claim of immunity."
        )
    return result


# --------------------------------------------------------------------------
# Procurement window — how much time actually remains, derived from fields
# the client already supplied. Not a forecast: today's date plus ship_date
# and contract_transit_time_days, arithmetic only.
# --------------------------------------------------------------------------

@dataclass
class ProcurementWindow:
    supply_window_days: int | None        # days from today until the material is required
    procurement_window_days: int | None   # days from today until a normal-schedule purchase
                                          # decision would need to be made to still meet it
    grade: str                            # "DERIVED" when both are computable, else "ABSENT"


def procurement_window(intake_data: dict, *, today: date | None = None) -> ProcurementWindow:
    """supply_window = ship_date - today. procurement_window = supply_window
    - contract_transit_time_days (the point at which a NORMAL-schedule
    purchase must be placed to still meet ship_date by normal lead time).
    today defaults to the real current date -- this describes time
    remaining as of now, unlike the rest of the engine's leak-free
    historical reconstruction, because a countdown that didn't move with
    real time would be the wrong kind of honest."""
    f = intake_data.get("fields", {})
    ship_date_str = f.get("ship_date")
    if ship_date_str is None:
        return ProcurementWindow(None, None, "ABSENT")
    today = today or datetime.now(timezone.utc).date()
    supply_window = (date.fromisoformat(ship_date_str) - today).days

    transit_days = f.get("contract_transit_time_days")
    if transit_days is None:
        return ProcurementWindow(supply_window, None, "ABSENT")
    return ProcurementWindow(supply_window, supply_window - int(transit_days), "DERIVED")


def demand_supply_stockout_date(days_of_cover: float | None, *, today: date | None = None) -> str | None:
    """today + days_of_cover, floored at 0 -- the one wall-clock-dependent
    step of intake.demand_supply_netting()'s result, kept out of intake.py
    for the same reason procurement_window() above lives here rather than
    there: a countdown that didn't move with real time would be the wrong
    kind of honest, but intake.py's own derivations stay deterministic (see
    its module docstring and enginev2.md selftest criterion #10 -- same
    input, same output, modulo timestamp). None when days_of_cover itself
    is None -- nothing computable, not a fabricated date."""
    if days_of_cover is None:
        return None
    today = today or datetime.now(timezone.utc).date()
    return (today + timedelta(days=max(0.0, days_of_cover))).isoformat()


def inverse_mode_sentence(recommended: BreakEvenResult, base_rate: dict) -> str:
    """section 8.4's core sentence. Zero new inputs — solves for the
    probability rather than asking for one, then places it against the
    already-published base rate."""
    if recommended.p_star is None:
        return f"{recommended.strategy} — {recommended.reason}"
    lo, hi = base_rate["hit_rate_ci_95"]
    return (f"{recommended.strategy} pays only if a closure is more likely than "
            f"{recommended.p_star:.0%} in your shipping window. The published base rate "
            f"across {base_rate['alarm_episodes']} alarm episodes is {base_rate['hit_rate']:.0%} "
            f"(95% CI {lo:.0%}-{hi:.0%}).")


# --------------------------------------------------------------------------
# Decision flip (section 8.6) — two mechanisms, not one generic solver
# --------------------------------------------------------------------------

LINEAR_FLIP_PARAMS = ("probability", "direct_cost", "war_risk_premium_multiplier")
STEP_FLIP_PARAMS = ("delay_days_estimate", "days_of_cover")


@dataclass
class FlipPoint:
    parameter: str
    strategy: str | None
    current_value: float
    flip_value: float
    direction: str


def solve_linear_flip(param: str, current: float, probe_fn) -> FlipPoint | None:
    """Exact two-point interpolation, no numerical solver, no simulation —
    valid because `probe_fn` is guaranteed linear in `param` for every
    parameter this is called with (see LINEAR_FLIP_PARAMS)."""
    g0 = probe_fn(current)
    g1 = probe_fn(current + 1.0)
    slope = g1 - g0
    if slope == 0:
        return None
    flip = current - g0 / slope
    return FlipPoint(param, None, current, flip, "top strategy changes")


def solve_step_flip(param: str, current: float, threshold: float, probe_fn) -> FlipPoint | None:
    """stockout_probability (intake.py section 5) is a step, not a curve —
    delay_days_estimate and days_of_cover cannot be solved as if the
    underlying cost were smooth through their shared threshold. Reports the
    threshold itself as the flip point, only when probing threshold-eps and
    threshold+eps actually changes which strategy ranks first — checked
    mechanically, not assumed."""
    eps = 1e-6
    below, above = probe_fn(threshold - eps), probe_fn(threshold + eps)
    if (below > 0) == (above > 0):
        return None
    return FlipPoint(param, None, current, threshold, "top strategy changes at the stockout threshold")


def solve_flips(intake_data: dict, strategies: list[Strategy],
                premium_analogue: "episodes.Analogue | None", probability: float) -> list[FlipPoint]:
    """Runs every whitelisted linear param (on the current top strategy)
    plus the two step params, drops any with no in-range flip point, sorts
    by relative proximity of current value to flip value (section 8.6's own
    ordering rule)."""
    if len(strategies) < 2:
        return []

    losses = {s.name: conditional_loss(intake_data, s, premium_analogue).total for s in strategies}
    expected = _expected_costs(strategies, losses, probability)
    ranked = sorted(expected, key=expected.get)
    top_name, runner_name = ranked[0], ranked[1]
    by_name = {s.name: s for s in strategies}
    top = by_name[top_name]

    def gap_with_losses(overridden_losses: dict[str, float], p: float) -> float:
        e = _expected_costs(strategies, overridden_losses, p)
        return e[top_name] - e[runner_name]

    flips: list[FlipPoint] = []

    fp = solve_linear_flip("probability", probability, lambda p: gap_with_losses(losses, p))
    if fp is not None and 0.0 <= fp.flip_value <= 1.0:
        flips.append(fp)

    def gap_top_direct_cost(x: float) -> float:
        modified = dataclasses.replace(top, direct_cost=x)
        e = {**expected, top_name: expected_cost(x, probability, losses[top_name])}
        return e[top_name] - e[runner_name]

    fp = solve_linear_flip("direct_cost", top.direct_cost, gap_top_direct_cost)
    if fp is not None and fp.flip_value >= 0:
        fp.strategy = top_name
        flips.append(fp)

    if top.effects.war_risk_premium_multiplier is not None:
        def gap_top_wrpm(x: float) -> float:
            modified = dataclasses.replace(top, effects=dataclasses.replace(
                top.effects, war_risk_premium_multiplier=x))
            l2 = dict(losses)
            l2[top_name] = conditional_loss(intake_data, modified, premium_analogue).total
            return gap_with_losses(l2, probability)

        fp = solve_linear_flip("war_risk_premium_multiplier",
                               top.effects.war_risk_premium_multiplier, gap_top_wrpm)
        if fp is not None and fp.flip_value >= 0:
            fp.strategy = top_name
            flips.append(fp)

    f = intake_data.get("fields", {})
    dc0, dd0 = f.get("days_of_cover"), f.get("delay_days_estimate")
    if dc0 is not None and dd0 is not None:
        def gap_with_field(name: str, x: float) -> float:
            fields2 = dict(f)
            fields2[name] = x
            data2 = dict(intake_data)
            data2["fields"] = fields2
            l2 = {s.name: conditional_loss(data2, s, premium_analogue).total for s in strategies}
            return gap_with_losses(l2, probability)

        threshold_dd = dc0 + top.effects.days_of_cover_delta - top.effects.delay_days_delta
        fp = solve_step_flip("delay_days_estimate", dd0, threshold_dd,
                             lambda x: gap_with_field("delay_days_estimate", x))
        if fp is not None:
            fp.strategy = top_name
            flips.append(fp)

        threshold_dc = dd0 + top.effects.delay_days_delta - top.effects.days_of_cover_delta
        fp = solve_step_flip("days_of_cover", dc0, threshold_dc,
                             lambda x: gap_with_field("days_of_cover", x))
        if fp is not None:
            fp.strategy = top_name
            flips.append(fp)

    flips.sort(key=lambda fp: abs(fp.current_value - fp.flip_value) / max(abs(fp.current_value), 1e-9))
    return flips


# --------------------------------------------------------------------------
# Regret (section 8.7)
# --------------------------------------------------------------------------

def regret_at_range(strategies: list[Strategy], intake_data: dict,
                    premium_analogue: "episodes.Analogue | None",
                    prob_range: tuple[float, float] | None = None) -> dict:
    """R(s) = E[C_s] - min_s' E[C_s'], evaluated at both ends of a
    probability range. Defaults to EPISODE_HIT_RATE_CI — already-published
    context, not a new client ask — unless intake.json supplies its own
    probability_range."""
    lo, hi = prob_range or EPISODE_HIT_RATE_CI
    losses = {s.name: conditional_loss(intake_data, s, premium_analogue).total for s in strategies}
    out: dict = {}
    for p, label in ((lo, "low"), (hi, "high")):
        expected = _expected_costs(strategies, losses, p)
        best = min(expected, key=expected.get)
        out[label] = {"probability": p, "best": best,
                     "regret": {name: round(v - expected[best], 2) for name, v in expected.items()}}
    return out


def value_of_information(regret: dict, recommended: str) -> dict:
    """EVPI(p) = E[C_recommended](p) - min_strategy E[C_strategy](p) is, by
    definition, exactly regret[p]['regret'][recommended] -- this relabels
    an existing number computed by regret_at_range(), it does not recompute
    one, so it can never drift from the Regret table shown alongside it.
    Two honest endpoints, always -- never blended into one prior-weighted
    figure, which would mean inventing a probability of being in the low-
    vs high-probability state (exactly what section 10's "no invented
    priors" rules out; same discipline as recovery.py's duration analogues,
    which are never averaged into one number either)."""
    return {"low": {"probability": regret["low"]["probability"],
                    "value": regret["low"]["regret"][recommended]},
           "high": {"probability": regret["high"]["probability"],
                   "value": regret["high"]["regret"][recommended]}}


# --------------------------------------------------------------------------
# Cost of waiting (section 8.5)
# --------------------------------------------------------------------------

@dataclass
class CostOfWaitingPath:
    episode_label: str
    points: list[tuple[int, float]]


@dataclass
class CostOfWaiting:
    paths: list[CostOfWaitingPath] = field(default_factory=list)


def _with_field(intake_data: dict, name: str, value) -> dict:
    fields2 = dict(intake_data.get("fields", {}))
    fields2[name] = value
    out = dict(intake_data)
    out["fields"] = fields2
    return out


def cost_of_waiting(intake_data: dict, strategy: Strategy, day_offsets: list[int], corridor: str,
                    probability: float, *, warrisk_path: Path | None = None,
                    jwc_path: Path | None = None, as_of: str | None = None) -> CostOfWaiting | None:
    """COW(delta) = E[C_act at t+delta] - E[C_act at t], cost path taken
    from episodes.analogue() at each discrete day offset, never a fitted
    curve. Requires a war_risk_premium_quote as the currency anchor for the
    trajectory (a multiplier alone has no monetary anchor without a
    baseline premium field — see conditional_loss()'s docstring); returns
    None — the whole object absent, not a null field — when that anchor is
    missing, or when episodes.analogue() itself returns None at any
    requested offset (section 8.5: 'suppressed entirely when section 6
    returns None')."""
    anchor = intake_data.get("fields", {}).get("war_risk_premium_quote")
    if anchor is None:
        return None

    by_episode: dict[str, list[tuple[int, float]]] = {}
    for d in day_offsets:
        a = episodes.analogue(corridor, "war_risk_premium", d, warrisk_path=warrisk_path,
                              jwc_path=jwc_path, as_of=as_of)
        if a is None:
            return None
        for obs in a.observations:
            by_episode.setdefault(obs.episode_label, []).append((d, obs.value))

    if len(by_episode) < 2:
        return None

    t0 = min(day_offsets)
    paths = []
    for label, points in sorted(by_episode.items()):
        points = sorted(points)
        mult_at_t0 = next((v for d, v in points if d == t0), points[0][1])
        loss_t0 = conditional_loss(_with_field(intake_data, "war_risk_premium_quote",
                                               anchor * mult_at_t0), strategy).total
        e_t0 = expected_cost(strategy.direct_cost, probability, loss_t0)
        cow_points = []
        for d, mult in points:
            loss_then = conditional_loss(_with_field(intake_data, "war_risk_premium_quote",
                                                     anchor * mult), strategy).total
            e_then = expected_cost(strategy.direct_cost, probability, loss_then)
            cow_points.append((d, round(e_then - e_t0, 2)))
        paths.append(CostOfWaitingPath(episode_label=label, points=cow_points))
    return CostOfWaiting(paths=paths)


def cost_of_waiting_by_strategy(intake_data: dict, strategies: list[Strategy], day_offsets: list[int],
                                corridor: str, probability: float, *, warrisk_path: Path | None = None,
                                jwc_path: Path | None = None, as_of: str | None = None
                                ) -> dict[str, "CostOfWaiting | None"]:
    """cost_of_waiting() for every compared strategy, not just the winner.
    Episode-observation timing doesn't depend on which strategy is being
    priced (day_offsets/corridor/intake_data are shared across the call),
    so this is all-or-nothing per DECISION, not per strategy or per
    corridor: either the register has enough comparable observations at
    these offsets, in which case every strategy gets a real trajectory
    (genuinely different strategy to strategy, by each one's own war-risk-
    premium-multiplier/capacity-restored sensitivity -- see
    conditional_loss()), or none does. Comparison enrichment only -- never
    feeds back into expected_cost/recommended, which are already chosen by
    the time this runs."""
    return {s.name: cost_of_waiting(intake_data, s, day_offsets, corridor, probability,
                                    warrisk_path=warrisk_path, jwc_path=jwc_path, as_of=as_of)
           for s in strategies}


def cost_of_waiting_unavailable_reason(intake_data: dict, corridor: str, day_offset_anchor: int,
                                       premium_analogue: "episodes.Analogue | None") -> str | None:
    """None when cost-of-waiting IS available. Otherwise names the real
    reason -- never a corridor-coverage claim, since episodes.py pools its
    register across every corridor by design (a corridor with zero rows of
    its own still receives the full pooled analogue set -- see episodes.py's
    own docstring/selftest). The two real reasons are a missing currency
    anchor, or a delay estimate shorter than the register's own earliest
    comparable post-onset observation -- both true regardless of corridor."""
    if premium_analogue is not None:
        return None
    anchor = intake_data.get("fields", {}).get("war_risk_premium_quote")
    if anchor is None:
        return ("No war-risk premium quote on file — nothing to anchor a cost-of-waiting "
               "trajectory to in currency terms.")
    return (f"The premium register's comparable post-onset observations don't yet reach back "
           f"to day {day_offset_anchor} (your estimated delay). Not specific to {corridor} — "
           f"episodes.py pools every corridor's observations together, and none currently "
           f"covers a horizon this short.")


# --------------------------------------------------------------------------
# Procurement Counterfactual Engine (module 1) — a strategy x scenario
# outcome matrix, assembled entirely from numbers build_decision() already
# computes elsewhere. No new probability, severity, or disruption-magnitude
# figure is invented anywhere below: "scenario" means a real, named, dated
# episode analogue already gated by episodes.analogue()'s own n>=2 rule
# (section 6.3: always plural, never averaged), never an invented severity
# bucket.
# --------------------------------------------------------------------------

def counterfactual_matrix(strategy_rows: list[dict],
                          cow_by_strategy: "dict[str, CostOfWaiting | None] | None") -> list[dict]:
    """For each strategy, "baseline" relabels this decision's own
    strategy_rows entry for that strategy (today, this strategy's real
    quoted/derived expected cost, no analogue applied). Each further
    scenario column relabels one (episode_label, day_offset) point
    cost_of_waiting_by_strategy already produced for that strategy -- a
    real, dated, EPISODE_ANALOGUE-graded case, applied as a delta on top of
    THIS strategy's own baseline expected cost. A strategy with no
    register-eligible episode at these offsets gets baseline only, never a
    padded/filled placeholder scenario -- matches cost_of_waiting_by_
    strategy's own all-or-nothing-per-decision honesty. Strategies with no
    name (an unused hand-typed slot -- see _strategies_from_form()'s own
    "blank name = unused slot, silently skipped" rule) are skipped here
    too, for the same reason: an empty slot is not a real alternative to
    compare, and would otherwise surface as an unlabeled row/column."""
    out = []
    for row in strategy_rows:
        if not row["name"]:
            continue
        scenarios = [{
            "scenario": "baseline", "scenario_kind": "baseline", "day_offset": None,
            "expected_total_cost": row["expected_cost"],
            "expected_total_cost_delta": 0.0,
            "grade": row["grade"],
        }]
        cow = (cow_by_strategy or {}).get(row["name"])
        if cow is not None:
            for path in cow.paths:
                for day, delta in path.points:
                    scenarios.append({
                        "scenario": path.episode_label, "scenario_kind": "episode_analogue",
                        "day_offset": day,
                        "expected_total_cost": round(row["expected_cost"] + delta, 2),
                        "expected_total_cost_delta": delta,
                        "grade": "EPISODE_ANALOGUE",
                    })
        out.append({
            "strategy": row["name"], "is_baseline": row["is_baseline"],
            "expected_disruption_loss": row["conditional_loss"],
            "expected_delay_cost": row["components"]["delay"],
            "expected_logistics_cost": row["components"]["transport"],
            "inventory_impact": row["components"]["inventory"],
            "coverage_after_strategy": row["coverage_after_strategy"],
            "cash_impact": row.get("cash_impact"), "cash_impact_grade": row.get("cash_impact_grade"),
            "service_risk_stockout": row.get("service_risk_stockout"),
            "scenarios": scenarios,
        })
    return out


def scenario_robustness(matrix: list[dict], recommended: str) -> dict:
    """Does the strategy recommended at baseline also win in every real
    episode-analogue scenario column? A pure argmin comparison over numbers
    counterfactual_matrix() already assembled -- the same shape of thing
    regret_at_range() already does, just over the episode axis instead of
    the probability axis. `recommended` is passed in rather than re-derived,
    so this can never disagree with result["recommended"] by construction.

    Deliberately NOT an alias of decision_confidence: that measures
    robustness across the PROBABILITY range: does the same strategy win at
    both ends of the published hit-rate CI. This measures robustness across
    the EPISODE/SEVERITY axis instead -- a different uncertainty dimension.
    Conflating the two would mislabel one axis as the other.

    status="not_computable" (never a forced robust/weak call) when no
    strategy in this decision has a scenario column beyond baseline -- the
    common case against the real, committed register at realistic delay
    assumptions today (see cost_of_waiting_unavailable_reason)."""
    by_column: dict[tuple, dict[str, float]] = {}
    for m in matrix:
        for sc in m["scenarios"]:
            if sc["scenario_kind"] != "episode_analogue":
                continue
            key = (sc["scenario"], sc["day_offset"])
            by_column.setdefault(key, {})[m["strategy"]] = sc["expected_total_cost"]
    if not by_column:
        return {"status": "not_computable", "recommended": recommended,
               "reason": "no episode-analogue scenario column available for this decision "
                         "(see cost_of_waiting_unavailable_reason)"}
    disagreements = []
    for (label, day), costs in sorted(by_column.items()):
        winner = min(costs, key=costs.get)
        if winner != recommended:
            disagreements.append({"scenario": label, "day_offset": day, "winner": winner})
    return {"status": "robust" if not disagreements else "not_robust",
           "recommended": recommended, "disagreements": disagreements}


# --------------------------------------------------------------------------
# Decision Deadline Engine (module 2) — "how long can I wait before acting
# becomes economically preferable" (newmodules.txt module_2_decision_
# deadline_engine). Tries a real economic crossover first, reusing
# scenario_robustness()'s own disagreements rather than re-scanning a day
# grid; falls back to the real operational procurement_window fact when the
# economic axis has nothing to say (newmodules.txt itself allows "linked to
# procurement economics AND operational constraints") -- never a third,
# invented basis. Against the real committed register today the economic
# basis is reachable in code but empirically dead (episodes.analogue()
# returns None below day 139 and is flat from day 139 onward -- see
# cost_of_waiting_unavailable_reason); it activates automatically the
# moment the register gets a second post-peak observation, with no engine
# change needed.
# --------------------------------------------------------------------------

def decision_deadline(matrix: list[dict], robustness: dict, recommended: str,
                      proc_window: ProcurementWindow) -> dict:
    """estimated_deadline_day/basis/preferred_action_before_deadline/
    preferred_action_after_deadline/uncertainty_range/explanation.

    basis="economic_crossover": the earliest day_offset among robustness's
    own disagreements. uncertainty_range brackets between the last checked
    day that still agreed with `recommended` and that flip day -- both real,
    checked, dated grid points, never interpolated.

    basis="operational_schedule": proc_window.procurement_window_days when
    the economic axis found nothing. No uncertainty_range -- ship_date/
    contract_transit_time_days are treated as fixed client-supplied facts
    everywhere else in this engine (procurement_window() itself reports no
    uncertainty), so this module does not invent one around them.
    preferred_action_after_deadline is None here on purpose: an operational
    deadline means "this stops being a normal-schedule decision," not "a
    specific different strategy becomes preferred."

    basis="not_computable": neither axis has anything -- stated in the
    explanation, never a silent gap."""
    if robustness["status"] == "not_robust":
        first = min(robustness["disagreements"], key=lambda d: d["day_offset"])
        checked = sorted({sc["day_offset"] for m in matrix for sc in m["scenarios"]
                          if sc["scenario_kind"] == "episode_analogue"})
        lower = max((d for d in checked if d < first["day_offset"]), default=None)
        deadline_day = lower if lower is not None else first["day_offset"]
        basis_note = (
            f"no earlier checked day disagreed — day {first['day_offset']} is the earliest "
            f"offset checked against the register at this decision's delay estimate, so the "
            f"true crossover could fall earlier but is not checked below it."
            if lower is None else
            f"{recommended} still wins at day {lower}, the last checked day before the flip.")
        return {
            "estimated_deadline_day": deadline_day,
            "basis": "economic_crossover",
            "preferred_action_before_deadline": recommended,
            "preferred_action_after_deadline": first["winner"],
            "uncertainty_range": {"lower_bound_day": lower, "upper_bound_day": first["day_offset"]},
            "explanation": (
                f"At the {first['scenario']} episode analogue (day {first['day_offset']}), "
                f"{first['winner']} has a lower expected total cost than {recommended} — driven "
                f"by the war-risk insurance component of conditional loss, the only cost element "
                f"that moves with elapsed time in this model; direct/action costs do not. "
                f"{basis_note}"),
        }

    economic_note = ("no economic strategy crossover was found in the available real "
                     "episode-analogue scenarios for this decision"
                     if robustness["status"] == "robust" else
                     "no real episode-analogue scenario is available yet to check for an "
                     "economic strategy crossover")

    if proc_window.procurement_window_days is not None:
        day = proc_window.procurement_window_days
        schedule_note = (
            f"a normal-schedule purchase must be placed within {day} day(s) "
            f"({proc_window.supply_window_days} day(s) until the material is required, minus "
            f"the contract transit time) to still meet the required ship date by normal lead time."
            if day >= 0 else
            f"already {-day} day(s) past the point a normal-schedule purchase would need to "
            f"have been made — any action now is expedited, not routine.")
        return {
            "estimated_deadline_day": day,
            "basis": "operational_schedule",
            "preferred_action_before_deadline": recommended,
            "preferred_action_after_deadline": None,
            "uncertainty_range": None,
            "explanation": (
                f"{economic_note.capitalize()}, so this deadline is the operational constraint "
                f"instead: {schedule_note} No uncertainty range is given — ship_date and "
                f"contract_transit_time_days are treated as fixed facts elsewhere in this engine, "
                f"not estimates this module has a basis to bound. This does not claim a specific "
                f"action becomes preferable afterward, only that the decision stops being a "
                f"normal-schedule one."),
        }

    return {
        "estimated_deadline_day": None,
        "basis": "not_computable",
        "preferred_action_before_deadline": recommended,
        "preferred_action_after_deadline": None,
        "uncertainty_range": None,
        "explanation": (
            f"{economic_note.capitalize()}, and no operational procurement window is computable "
            f"(ship_date not supplied) — nothing to anchor an estimated decision deadline to."),
    }


# --------------------------------------------------------------------------
# Point-of-No-Return Detector (module 3) — "when may the ability to act
# effectively disappear" (newmodules.txt module_3_point_of_no_return_
# detector), distinct from decision_deadline() by the spec's own words:
# "decision_deadline: when should I act? point_of_no_return: when may the
# ability to act effectively disappear?" -- so this compares the latest day
# each mitigation option can still satisfy operational requirements, never
# an economic preference crossover.
#
# Three of newmodules.txt's eleven tracked_constraints have real data
# behind them anywhere in this codebase, each already computed elsewhere,
# reused here, never recomputed: inventory remaining
# (demand_supply_stockout_date()), required delivery date
# (procurement_window()'s procurement_window_days), and alternative
# supplier lead time (procurement_window()'s supply_window_days -- ship_date
# alone, deliberately not procurement_window_days, since contract transit
# time is a freight concept unrelated to an alternative supplier's own lead
# time -- minus supplier_lead_time_days). The other eight have no field
# anywhere in this codebase and are always reported not_determinable, by
# name, never silently omitted -- the spec's own "if no reliable data
# exists ... return 'not determinable from available data' rather than
# inventing a date."
# --------------------------------------------------------------------------

ALWAYS_NOT_DETERMINABLE_CONSTRAINTS = (
    ("supplier capacity",
     "no supplier-capacity field exists anywhere in this codebase."),
    ("alternative supplier availability",
     "no field captures confirmed alternative-supplier capacity/availability -- "
     "supplier_lead_time_days, when supplied, is a lead-time estimate, not an "
     "availability confirmation."),
    ("freight capacity",
     "no freight-capacity field exists anywhere in this codebase."),
    ("alternative route availability",
     "no alternative-route field exists anywhere in this codebase."),
    ("production schedule",
     "no production-schedule field exists anywhere in this codebase."),
    ("contractual constraints",
     "no contractual-flexibility field exists anywhere in this codebase."),
    ("minimum feasible mitigation lead time",
     "no field aggregates a minimum lead time across mitigation options -- only "
     "the alternative supplier's own lead time is ever tracked, and only when "
     "supplied."),
    ("expected price escalation",
     "disruption_attributable_price_change was deliberately removed as an input "
     "(enginev2.md section 5) -- separating a market-wide commodity move from the "
     "share attributable to one disruption is an econometric judgment, not a "
     "company record."),
)


def point_of_no_return(intake_data: dict, proc_window: ProcurementWindow,
                       stockout_date: str | None, *, today: date | None = None) -> dict:
    """estimated_point_of_no_return_day/binding_constraint/basis/
    candidate_constraints/not_determinable_constraints/explanation.

    Compares up to three real, already-computed candidate dates (never a
    new forecast) and reports whichever is EARLIEST as the binding
    constraint, by name -- see the module comment above for where each
    comes from. Every other newmodules.txt tracked_constraint this
    codebase has no data for is listed in not_determinable_constraints,
    always, whether or not any candidate was computable for this decision.

    estimated_point_of_no_return_day is a day offset from today (negative =
    already past), matching decision_deadline()'s own estimated_deadline_
    day convention. binding_constraint/basis are None/"not_determinable"
    only when NONE of the three candidates are computable for this
    decision.

    today defaults to the real current date, like procurement_window() and
    demand_supply_stockout_date() -- a countdown that didn't move with real
    time would be the wrong kind of honest -- but accepts an explicit
    today= for reproducible testing, same convention. Not threaded through
    by build_decision() itself, same as those two."""
    today = today or datetime.now(timezone.utc).date()
    f = intake_data.get("fields", {})

    candidates: list[dict] = []
    not_determinable: list[dict] = []

    if stockout_date is not None:
        candidates.append({
            "constraint": "inventory remaining",
            "day_offset": (date.fromisoformat(stockout_date) - today).days,
            "detail": f"on-hand and inbound cover would be exhausted by {stockout_date}",
        })
    else:
        not_determinable.append({
            "constraint": "inventory remaining",
            "reason": "no days-of-cover figure (direct or netted from forecast/inventory/"
                     "inbound/safety-stock fields) on file for this decision.",
        })

    if proc_window.procurement_window_days is not None:
        candidates.append({
            "constraint": "required delivery date",
            "day_offset": proc_window.procurement_window_days,
            "detail": "a normal-schedule purchase could no longer be placed in time to meet "
                     "the required ship date by normal lead time",
        })
    else:
        reason = ("ship_date not supplied." if proc_window.supply_window_days is None else
                  "contract_transit_time_days not supplied, so the normal-schedule purchase "
                  "window can't be computed.")
        not_determinable.append({"constraint": "required delivery date", "reason": reason})

    lead_time = f.get("supplier_lead_time_days")
    if proc_window.supply_window_days is not None and lead_time is not None:
        candidates.append({
            "constraint": "alternative supplier lead time",
            "day_offset": proc_window.supply_window_days - int(lead_time),
            "detail": f"the alternative supplier's own quoted lead time ({lead_time:g} days) "
                     f"would no longer fit before the required ship date",
        })
    else:
        reason = ("no alternative-supplier lead time on file." if lead_time is None else
                  "ship_date not supplied.")
        not_determinable.append({"constraint": "alternative supplier lead time", "reason": reason})

    not_determinable.extend({"constraint": c, "reason": r}
                            for c, r in ALWAYS_NOT_DETERMINABLE_CONSTRAINTS)

    if not candidates:
        conditional_reasons = "; ".join(
            f"{c['constraint']} ({c['reason']})" for c in not_determinable[:3])
        return {
            "estimated_point_of_no_return_day": None,
            "binding_constraint": None,
            "basis": "not_determinable",
            "candidate_constraints": [],
            "not_determinable_constraints": not_determinable,
            "explanation": f"Not determinable from available data: {conditional_reasons}",
        }

    earliest = min(candidates, key=lambda c: c["day_offset"])
    others = [c for c in candidates if c is not earliest]
    others_note = " ".join(f"{c['constraint']} would bind on day {c['day_offset']}." for c in others)
    when = (f"day {earliest['day_offset']}" if earliest["day_offset"] >= 0 else
           f"{-earliest['day_offset']} day(s) ago (day {earliest['day_offset']})")
    return {
        "estimated_point_of_no_return_day": earliest["day_offset"],
        "binding_constraint": earliest["constraint"],
        "basis": earliest["constraint"].replace(" ", "_"),
        "candidate_constraints": candidates,
        "not_determinable_constraints": not_determinable,
        "explanation": (
            f"Estimated point of no return: {when}, bound by {earliest['constraint']} — "
            f"{earliest['detail']}. {others_note} "
            f"{len(not_determinable)} other tracked constraint(s) could not be determined "
            f"from available data (see not_determinable_constraints)."),
    }


# --------------------------------------------------------------------------
# Shadow Price of Geopolitical Risk Engine (module 4) -- "how much expected
# economic cost is attributable to the geopolitical disruption relative to a
# baseline" (newmodules.txt module_4_shadow_price_engine). Pure repackaging:
# baseline_costbreakdown()/disrupted_costbreakdown()/welfare_gap() already
# compute every number this needs (build_decision() calls all three just
# above); this only relabels them into the spec's own vocabulary
# (baseline_cost/risk_adjusted_cost/shadow_price) and adds the per-component
# breakdown and honest gap list newmodules.txt itself asks for ("if some
# cost components are missing, identify them explicitly"). No new intake
# read, no recomputation -- shadow_price == gap["private"] ==
# result["exposure"]["avoidable"] always, same subtraction under a third
# name.
#
# Two of the spec's 11 cost_components are deliberately NOT in
# COST_COMPONENTS_NOT_COMPUTED below, because they are not separate gaps:
#   "freight" -- this codebase has exactly one freight/transport cost path
#     (contract_freight_rate / disrupted_freight_quote -> CostBreakdown's
#     "transport" key); "freight" and "transport" are the same computed
#     quantity here, not two components.
#   "disruption loss" -- this is what shadow_price itself already equals
#     (disrupted_cb.total - baseline_cb.total); listing it as missing would
#     misname the module's own headline result as an uncounted input.
#
# Not ledgered: baseline_costbreakdown()/disrupted_costbreakdown()'s own
# components were never individually ledgered either -- result["exposure"]
# has shipped without ledger backing since module 1. This keeps the same
# dollar figure consistently graded (or ungraded) under both names rather
# than making shadow_price stricter than the exposure key it repackages.
# --------------------------------------------------------------------------

COST_COMPONENTS_NOT_COMPUTED = (
    ("commodity",
     "always 0 in both baseline and disrupted breakdowns by deliberate design "
     "(enginev2.md section 5) -- disruption_attributable_price_change was removed as an "
     "input because separating a market-wide commodity move from the share attributable "
     "to one disruption is an econometric judgment, not a company record; a real "
     "commodity exposure on this shipment, if any, is not reflected here."),
    ("expediting",
     "no expedited-freight cost field or computation exists anywhere in this codebase."),
    ("rerouting",
     "reroute_quote now prices into each strategy's own conditional_loss() transport "
     "component, scaled by that strategy's capacity_restored -- but rerouting is "
     "inherently a per-strategy mitigation choice, not a state of the world, so it "
     "still never reaches baseline_costbreakdown/disrupted_costbreakdown (the "
     "strategy-independent EXPOSURE/shadow-price figures). Same carve-out logic as "
     "the working-capital entry below: a real, per-strategy number that cannot be "
     "pulled into a strategy-independent one."),
    ("service-related economic loss",
     "no standalone figure is computed; the overlapping pieces this codebase does track "
     "(contractual late-delivery penalty, stockout-triggered gross-margin loss) are "
     "already folded into the delay component above, not broken out separately."),
    ("working-capital impact",
     "CostBreakdown has no cash/working-capital component. The only working-capital-"
     "shaped figure in this codebase is cash_impact (forward_buy_cost()), which is "
     "per-strategy, answers a different question -- the financing cost of committing to "
     "a specific mitigation early, not how the disruption itself changes working-capital "
     "needs -- and never feeds CostBreakdown, so it cannot be pulled in here."),
)


def shadow_price_of_geopolitical_risk(baseline_cb: CostBreakdown, disrupted_cb: CostBreakdown,
                                       gap: dict) -> dict:
    """baseline_cost/risk_adjusted_cost/shadow_price/components/
    cost_components_not_computed/interpretation -- newmodules.txt's own
    vocabulary over exactly the three objects build_decision() already has
    in hand. No recomputation, no intake_data argument needed.

    components: per-component {baseline, disrupted, delta} for each of
    CostBreakdown's 5 named keys -- the breakdown Section 4 and the (4) card
    never show (they only ever display the two totals and the gap)."""
    components = {
        name: {"baseline": round(baseline_cb.components[name], 2),
              "disrupted": round(disrupted_cb.components[name], 2),
              "delta": round(disrupted_cb.components[name] - baseline_cb.components[name], 2)}
        for name in baseline_cb.components
    }
    baseline_cost = round(baseline_cb.total, 2)
    risk_adjusted_cost = round(disrupted_cb.total, 2)
    shadow_price = round(gap["private"], 2)
    return {
        "baseline_cost": baseline_cost,
        "risk_adjusted_cost": risk_adjusted_cost,
        "shadow_price": shadow_price,
        "components": components,
        "cost_components_not_computed": [{"component": c, "reason": r}
                                         for c, r in COST_COMPONENTS_NOT_COMPUTED],
        "interpretation": (
            f"Under the stated baseline and disruption assumptions, geopolitical exposure "
            f"adds an estimated {shadow_price:,.0f} to expected procurement cost "
            f"({baseline_cost:,.0f} baseline vs {risk_adjusted_cost:,.0f} under the modeled "
            f"disruption) — a modeled comparison, not an observed market price, and not a "
            f"claim that the disruption alone caused this figure beyond what the priced "
            f"components support. {len(COST_COMPONENTS_NOT_COMPUTED)} cost component(s) this "
            f"module has no data for are listed separately."),
    }


# --------------------------------------------------------------------------
# Maximum Rational Premium Engine (module 5) -- "how much should I be
# willing to pay to reduce or avoid the modeled geopolitical exposure"
# (newmodules.txt module_5_maximum_rational_premium_engine). This is
# break_even_probability()'s own defining equation --
# strategy.direct_cost = baseline.direct_cost + p* x (l_cond_s0 - l_cond_s)
# -- solved for direct_cost instead of p*, at the probability actually
# being used (`probability`) instead of solved backward for it. Unlike
# break_even_probability(), this is pure multiplication, never division, so
# it has no degenerate/undefined case: when a strategy doesn't reduce
# conditional loss versus baseline (denom <= 0), the result is simply <=
# baseline.direct_cost (often 0) -- no premium, not even zero, is
# rationally justified -- reported plainly via the same
# actual-cost-vs-ceiling comparison, not a special-cased branch.
#
# Reuses regret_at_range()'s own already-resolved low/high probabilities
# (passed in via `regret`) for the uncertainty range, rather than a second,
# independent resolution of prob_range/EPISODE_HIT_RATE_CI -- the same
# relabel-don't-recompute discipline value_of_information() already
# established. Not ledgered: break_even's own entries, the closest existing
# precedent, aren't ledgered either.
# --------------------------------------------------------------------------

def maximum_rational_premium(strategies: list[Strategy], baseline: Strategy,
                             losses: dict[str, float], probability: float,
                             regret: dict, strategy_ranking: list[str]) -> dict:
    """{"strategies": [...], "headline": {...} | None}. One entry per
    non-baseline, non-blank strategy (matching break_even's own list shape
    and module 1's blank-name filter) -- never a single figure, since this
    app already compares multiple hand-typed strategies per decision.

    Each entry: maximum_rational_premium (the ceiling), actual_direct_cost
    (the strategy's own real quoted/typed cost, for direct comparison),
    verdict ("economically_rational" if actual_direct_cost <= the ceiling,
    else "not_economically_justified" -- newmodules.txt's own decision_rule),
    uncertainty_range (shaped like value_of_information()'s own
    {"low": {"probability", "value"}, "high": {...}}), and an explanation
    using the spec's own required hedge -- never a guaranteed negotiation
    price.

    "headline": the first non-baseline strategy in strategy_ranking's own
    already-expected-cost-sorted order (the cheapest real mitigation being
    compared) -- gives the summary strip one clear number without a second
    sort."""
    entries = []
    for s in strategies:
        if s.name == baseline.name or not s.name:
            continue
        denom = losses[baseline.name] - losses[s.name]
        premium_at = baseline.direct_cost + probability * denom
        premium_low = baseline.direct_cost + regret["low"]["probability"] * denom
        premium_high = baseline.direct_cost + regret["high"]["probability"] * denom
        rational = s.direct_cost <= premium_at
        if denom <= 0:
            explanation = (
                f"{s.name} does not reduce conditional loss versus {baseline.name} -- no "
                f"premium, not even zero, is rationally justified on this axis alone.")
        else:
            explanation = (
                f"At the {probability:.0%} probability used, paying more than "
                f"{premium_at:,.0f} for {s.name} over {baseline.name} is not economically "
                f"justified under the modeled assumptions -- a maximum economically rational "
                f"premium, not a guaranteed negotiation price. {s.name}'s own actual direct "
                f"cost is {s.direct_cost:,.0f}, which is "
                f"{'within' if rational else 'above'} that ceiling.")
        entries.append({
            "strategy": s.name,
            "maximum_rational_premium": round(premium_at, 2),
            "actual_direct_cost": s.direct_cost,
            "verdict": "economically_rational" if rational else "not_economically_justified",
            "uncertainty_range": {
                "low": {"probability": regret["low"]["probability"], "value": round(premium_low, 2)},
                "high": {"probability": regret["high"]["probability"], "value": round(premium_high, 2)},
            },
            "explanation": explanation,
        })

    headline_name = next((n for n in strategy_ranking if n != baseline.name), None)
    headline = next((e for e in entries if e["strategy"] == headline_name), None)
    return {"strategies": entries, "headline": headline}


# --------------------------------------------------------------------------
# Ledger and grades (section 9)
# --------------------------------------------------------------------------

GRADES = ("CLIENT_QUOTED", "CLIENT_SYSTEM", "DERIVED", "EPISODE_ANALOGUE", "STRUCTURAL", "PUBLISHED", "ABSENT")
GRADE_STRENGTH = {g: i for i, g in enumerate(
    ("PUBLISHED", "CLIENT_QUOTED", "CLIENT_SYSTEM", "DERIVED", "EPISODE_ANALOGUE", "STRUCTURAL", "ABSENT"))}
# STRUCTURAL sits between EPISODE_ANALOGUE and ABSENT: a chokepoint_profiles.py
# corridor with no tested incidents still has real descriptive/geographic
# evidence behind it (chokepoint_profiles.ChokepointProfile.evidence_grade ==
# "STRUCTURAL") — weaker than a corridor with tested episodes, but not the
# same as a field nobody supplied at all.


@dataclass
class LedgerEntry:
    field: str
    value: object
    unit: str
    grade: str
    formula: str | None = None
    source: str | None = None
    n: int | None = None
    department: str | None = None


class Ledger:
    """The travelling ledger (section 9.3). Every number that enters the
    result JSON is written through record(), which both stores the graded
    entry AND returns the value for use in the result dict — 'every number
    has a grade' (selftest #9) is true by construction, not a property
    checked after the fact by walking arbitrary JSON keys."""

    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []

    def record(self, field_name: str, value, *, unit: str, grade: str,
              formula: str | None = None, source: str | None = None,
              n: int | None = None, department: str | None = None):
        if grade not in GRADES:
            raise ValueError(f"unknown grade {grade!r} — must be one of {GRADES}")
        self.entries.append(LedgerEntry(field_name, value, unit, grade, formula, source, n, department))
        return value

    def weakest(self) -> str:
        if not self.entries:
            return "unknown"
        return max(self.entries, key=lambda e: GRADE_STRENGTH[e.grade]).grade

    def as_list(self) -> list[dict]:
        return [asdict(e) for e in self.entries]


def weakest_grade(ledger: Ledger) -> str:
    return ledger.weakest()


# --------------------------------------------------------------------------
# What would sharpen this (section 10's "value of information, for free")
# --------------------------------------------------------------------------

def what_would_sharpen(missing: list[intake.FieldSpec]) -> list[str]:
    """Qualitative, not quantified: stating a euro figure a missing field
    would narrow the result by requires assuming a value for that field,
    which is exactly the fabrication section 2 forbids. Names the
    department and what the field would let the engine derive instead of
    leave ABSENT."""
    return [f"ask {f.department} for {f.name.replace('_', ' ')} ({f.system_of_record}) — "
            f"it would let the {f.unit}-denominated figure be derived instead of left absent"
            for f in missing]


# --------------------------------------------------------------------------
# Reassess triggers — named conditions under which this result is stale,
# stated from what's already computed. Never a forecast of when a trigger
# will fire, only what it is: the engine doesn't watch for these between
# runs, it names them so a human (or the monthly alert loop) knows what to
# watch for.
# --------------------------------------------------------------------------

def reassess_triggers(intake_data: dict, reading: dict | None,
                      proc_window: ProcurementWindow) -> list[str]:
    triggers = []
    if reading is not None and reading.get("band"):
        triggers.append(f"the corridor's band moves away from {reading['band']!r}")

    f = intake_data.get("fields", {})
    days_of_cover = f.get("days_of_cover")
    delay_days = f.get("delay_days_estimate")
    if days_of_cover is not None and delay_days is not None and days_of_cover > delay_days:
        triggers.append(f"inventory cover falls below {delay_days:g} days "
                        f"(currently {days_of_cover:g} days against an estimated "
                        f"{delay_days:g}-day delay)")

    if proc_window.procurement_window_days is not None and proc_window.procurement_window_days > 0:
        triggers.append(f"the procurement window closes in {proc_window.procurement_window_days} "
                        f"days, after which this becomes an expedited rather than normal-schedule decision")

    return triggers


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def build_decision(intake_data: dict, *, as_of: str | None = None,
                   gpr_source: Path | None = None, reading: dict | None = None,
                   warrisk_path: Path | None = None, jwc_path: Path | None = None) -> dict:
    problems = intake.validate_intake(intake_data)
    if problems:
        raise ValueError(f"intake has {len(problems)} problem(s): {problems}")

    corridor = intake_data["corridor"]
    incoterm = intake_data["incoterm"]
    strategies_data = intake_data.get("strategies", [])
    if len(strategies_data) < 1:
        raise ValueError("intake needs at least one strategy")
    strategies = [_strategy_from_dict(s) for s in strategies_data]
    baseline_candidates = [s for s in strategies if s.is_baseline]
    baseline = baseline_candidates[0] if baseline_candidates else strategies[0]

    ledger = Ledger()

    # Forward-buy strategies: add the financing/carrying cost of securing
    # part of the order early into that strategy's direct cost, once, here
    # -- so conditional_loss(), expected_cost(), break_even_probability()
    # and everything downstream see one consistent, already-graded number
    # rather than each having to know about forward-buy separately.
    forward_buy_by_strategy: dict[str, ForwardBuyCost] = {}
    for s in strategies:
        if s.effects.forward_buy_fraction > 0:
            fb = forward_buy_cost(intake_data, s.effects)
            forward_buy_by_strategy[s.name] = fb
            s.direct_cost += fb.value
            ledger.record(f"forward_buy_cost[{s.name}]", round(fb.value, 2), unit="currency",
                         grade=fb.grade,
                         formula=(f"wacc x {s.effects.forward_buy_fraction:.0%} x cargo_value x "
                                  f"({s.effects.forward_buy_early_days:g}/365) + carrying x "
                                  f"{s.effects.forward_buy_fraction:.0%} x quantity x "
                                  f"{s.effects.forward_buy_early_days:g} days"))

    chokepoint = chokepoint_profiles.profile_for(corridor)
    ledger.record("chokepoint_evidence_grade", chokepoint.response_character,
                 unit="text", grade=chokepoint.evidence_grade,
                 source=chokepoint.evidence_source,
                 n=len(chokepoint.tested_incidents) or None)

    if reading is None and gpr_source is not None and as_of:
        try:
            reading = point_in_time(gpr_source, as_of, corridor)
        except SystemExit:
            reading = None
    if reading is not None:
        mapped_grade = "PUBLISHED" if reading.get("grade") == "PUBLISHED" else "DERIVED"
        ledger.record("reading_tar", reading.get("tar"), unit="index", grade=mapped_grade,
                     source="services.point_in_time() (reconstructed from the public vintage)"
                     if mapped_grade == "DERIVED" else "record.jsonl")

    day_offset_anchor = int(intake_data.get("fields", {}).get("delay_days_estimate") or 0)
    premium_analogue = episodes.analogue(corridor, "war_risk_premium", day_offset_anchor,
                                         warrisk_path=warrisk_path, jwc_path=jwc_path, as_of=as_of)
    if premium_analogue is not None:
        ledger.record("premium_analogue", [round(o.value, 3) for o in premium_analogue.observations],
                     unit="multiplier vs. pre-onset baseline", grade="EPISODE_ANALOGUE",
                     source="; ".join(o.episode_label for o in premium_analogue.observations),
                     n=premium_analogue.n)

    probability = intake_data.get("client_probability_estimate")
    base_rate = base_rate_context(corridor)
    ledger.record("corridor_onset_history", base_rate["corridor_onsets"], unit="count",
                 grade=base_rate["corridor_onset_grade"],
                 source="tar_ingest.ONSETS (main text Table 1)",
                 n=base_rate["corridor_onsets"] or None)
    ranking_probability = probability if probability is not None else base_rate["hit_rate"]

    proc_window = procurement_window(intake_data)
    ledger.record("procurement_window_days", proc_window.procurement_window_days,
                 unit="days", grade=proc_window.grade,
                 formula="ship_date - today - contract_transit_time_days")

    # Demand & supply netting (forecast + inventory + inbound + safety
    # stock -> days of cover, stockout date, demand/quantity at risk) --
    # computed from the fields as the client actually supplied them, before
    # any fallback substitution below.
    f0 = intake_data.get("fields", {})
    netting = intake.demand_supply_netting(f0, delay_days=f0.get("delay_days_estimate"))
    if netting.daily_demand_rate is not None:
        ledger.record("daily_demand_rate", round(netting.daily_demand_rate.value, 3),
                     unit="units/day", grade="DERIVED", formula=netting.daily_demand_rate.formula)
    if netting.net_available_cover is not None:
        ledger.record("net_available_cover", round(netting.net_available_cover, 2),
                     unit="units", grade="DERIVED",
                     formula="current_inventory + inbound_confirmed_quantity - safety_stock")
    if netting.days_of_cover is not None:
        ledger.record("days_of_cover_derived", round(netting.days_of_cover, 2),
                     unit="days", grade="DERIVED", formula="net_available_cover / daily_demand_rate")
    if netting.quantity_at_risk is not None:
        ledger.record("quantity_at_risk", round(netting.quantity_at_risk, 2),
                     unit="units", grade="DERIVED",
                     formula="max(0, daily_demand_rate * delay_days_estimate - net_available_cover)")
    if netting.demand_at_risk_value is not None:
        ledger.record("demand_at_risk_value", round(netting.demand_at_risk_value, 2),
                     unit="currency", grade="DERIVED",
                     formula="quantity_at_risk * unit_value(cargo_value, quantity)")

    # A client who supplies the finer-grained netting inputs but leaves the
    # direct days_of_cover field blank gets the derived figure USED, not
    # just displayed -- the same "client value wins, derived is the graded
    # fallback" pattern as ranking_probability above (client_probability_
    # estimate vs. base_rate['hit_rate']). Rewriting intake_data here, before
    # any conditional_loss() call, means every downstream function that
    # reads days_of_cover (that one, reassess_triggers, solve_flips,
    # missing_fields...) sees one consistent value with no signature
    # changes -- the same override mechanism cost_of_waiting() already uses
    # via _with_field().
    days_of_cover_is_derived_fallback = (f0.get("days_of_cover") is None
                                         and netting.days_of_cover is not None)
    if days_of_cover_is_derived_fallback:
        intake_data = _with_field(intake_data, "days_of_cover", netting.days_of_cover)

    effective_days_of_cover_used = intake_data.get("fields", {}).get("days_of_cover")
    stockout_date = demand_supply_stockout_date(effective_days_of_cover_used)
    if stockout_date is not None:
        ledger.record("stockout_date", stockout_date, unit="date", grade="DERIVED",
                     formula="today + days_of_cover")

    strategy_rows = []
    losses: dict[str, float] = {}
    for s in strategies:
        cl = conditional_loss(intake_data, s, premium_analogue)
        losses[s.name] = cl.total
        ledger.record(f"conditional_loss[{s.name}]", round(cl.total, 2), unit="currency",
                     grade=cl.grade,
                     formula="quoted override" if cl.grade == "CLIENT_QUOTED" else
                             "sum(transport, insurance, delay, inventory, commodity)")
        for absent_component in cl.absent_components:
            ledger.record(f"conditional_loss[{s.name}].{absent_component}", None,
                         unit="currency", grade="ABSENT")
        fb = forward_buy_by_strategy.get(s.name)
        strategy_rows.append({
            "name": s.name, "direct_cost": s.direct_cost, "conditional_loss": round(cl.total, 2),
            "expected_cost": round(expected_cost(s.direct_cost, ranking_probability, cl.total), 2),
            "components": {k: round(v, 2) for k, v in cl.components.items()},
            "grade": cl.grade, "is_baseline": s.is_baseline,
            "coverage_after_strategy": (round(cl.effective_days_of_cover, 2)
                                        if cl.effective_days_of_cover is not None else None),
            "service_risk_stockout": intake.stockout_probability(cl.effective_days_of_cover,
                                                                  cl.effective_delay_days),
            "cash_impact": round(fb.value, 2) if fb else None,
            "cash_impact_grade": fb.grade if fb else "ABSENT",
        })

    recommended = min(strategy_rows, key=lambda r: r["expected_cost"])["name"]

    break_even = [break_even_probability(s, baseline, losses[s.name], losses[baseline.name])
                 for s in strategies if s.name != baseline.name]
    recommended_be = next((b for b in break_even if b.strategy == recommended), None)
    if recommended_be is None and recommended == baseline.name:
        # The baseline itself is winning — there is no "break-even to switch
        # into it" by definition. Show the cheapest alternative's own
        # break-even instead (the probability that would flip the
        # recommendation away from the baseline), so BREAK-EVEN never
        # collapses to "not solvable" just because nothing beats doing
        # nothing yet.
        solvable = [b for b in break_even if b.p_star is not None]
        recommended_be = min(solvable, key=lambda b: b.p_star) if solvable else (
            break_even[0] if break_even else None)

    cow_by_strategy = None
    if premium_analogue is not None:
        # Start from the LATEST day_offset_used among the episodes already
        # pooled at the anchor offset (every episode in premium_analogue has
        # data at-or-before that day) and step forward only — "at or before"
        # only gains coverage as the offset grows, so this never drops an
        # episode below the n>=2 floor the way starting from a smaller,
        # per-episode day_offset_used could.
        start = max(o.day_offset_used for o in premium_analogue.observations)
        offsets = sorted({start, start + 7, start + 14})
        cow_by_strategy = cost_of_waiting_by_strategy(intake_data, strategies, offsets, corridor,
                                                       ranking_probability, warrisk_path=warrisk_path,
                                                       jwc_path=jwc_path, as_of=as_of)
    # cow (the recommended strategy's own trajectory, today's existing single-
    # strategy key) is now DERIVED from cow_by_strategy rather than computed
    # separately -- one source of truth, never two independent calls that
    # could drift apart.
    cow = cow_by_strategy.get(recommended) if cow_by_strategy else None
    cow_unavailable_reason = cost_of_waiting_unavailable_reason(intake_data, corridor,
                                                                 day_offset_anchor, premium_analogue)

    counterfactual = counterfactual_matrix(strategy_rows, cow_by_strategy)
    robustness = scenario_robustness(counterfactual, recommended)
    strategy_ranking = [r["name"] for r in sorted(strategy_rows, key=lambda r: r["expected_cost"])
                        if r["name"]]
    deadline = decision_deadline(counterfactual, robustness, recommended, proc_window)
    ponr = point_of_no_return(intake_data, proc_window, stockout_date)

    flips = solve_flips(intake_data, strategies, premium_analogue, ranking_probability)
    regret = regret_at_range(strategies, intake_data, premium_analogue,
                             tuple(intake_data["probability_range"])
                             if intake_data.get("probability_range") else None)
    voi = value_of_information(regret, recommended)
    mrp = maximum_rational_premium(strategies, baseline, losses, ranking_probability, regret,
                                   strategy_ranking)
    _voi_contributors = {recommended, regret["low"]["best"], regret["high"]["best"]}
    _voi_grade = max((row["grade"] for row in strategy_rows if row["name"] in _voi_contributors),
                     key=lambda g: GRADE_STRENGTH[g])
    ledger.record("value_of_information", {"low": voi["low"]["value"], "high": voi["high"]["value"]},
                 unit="currency", grade=_voi_grade,
                 formula="regret_at_range()'s own regret[recommended] at each end of the "
                         "published range -- relabelled, not a new computation")

    # Recommendation confidence: don't present a razor-thin or fragile pick
    # as a strong call. Two independent checks, neither inventing a new
    # threshold beyond what's already computed --
    #   1. robustness: does the SAME strategy win at both ends of the
    #      published probability range regret was just evaluated at
    #      (EPISODE_HIT_RATE_CI by default, or the client's own range)?
    #   2. margin: at the single ranking probability actually used, is the
    #      gap to the next-best strategy trivial relative to its own cost?
    _ranked = sorted(strategy_rows, key=lambda r: r["expected_cost"])
    _top, _second = _ranked[0], (_ranked[1] if len(_ranked) > 1 else None)
    _RAZOR_THIN = 0.03   # disclosed here, not hidden -- 3% of the leading strategy's own cost
    _margin_ratio = (None if _second is None else
                     (_second["expected_cost"] - _top["expected_cost"])
                     / max(abs(_top["expected_cost"]), 1.0))
    _robust_across_range = regret["low"]["best"] == regret["high"]["best"]
    razor_thin = _margin_ratio is not None and _margin_ratio < _RAZOR_THIN
    decision_confidence = "robust" if (_robust_across_range and not razor_thin) else "weak"
    if decision_confidence == "robust":
        decision_confidence_note = None
    elif not _robust_across_range:
        decision_confidence_note = (
            f"No economically significant advantage: the better choice flips between "
            f"{regret['low']['best']!r} and {regret['high']['best']!r} depending on where in the "
            f"published probability range ({regret['low']['probability']:.0%}-"
            f"{regret['high']['probability']:.0%}) the true probability sits. {recommended} is "
            f"shown because it wins at the {ranking_probability:.0%} used for ranking, not because "
            f"it clearly dominates — maintaining normal procurement is equally defensible.")
    else:
        decision_confidence_note = (
            f"No economically significant advantage: {_top['name']} beats {_second['name']} by "
            f"only {_second['expected_cost'] - _top['expected_cost']:,.0f} in expected cost "
            f"({_margin_ratio:.1%} of {_top['name']}'s own expected cost) — within rounding and "
            f"estimation noise, not a clear win.")

    baseline_cb = baseline_costbreakdown(intake_data)
    disrupted_cb = disrupted_costbreakdown(intake_data)
    gap = welfare_gap(disrupted_cb, baseline_cb)
    shadow_price = shadow_price_of_geopolitical_risk(baseline_cb, disrupted_cb, gap)

    missing = intake.missing_fields(intake_data)
    for m in missing:
        ledger.record(m.name, None, unit=m.unit, grade="ABSENT", department=m.department)

    not_claimed = [
        "no claim about the disruption's global origin",
        "no theatre identification beyond the named chokepoint",
        (f"episode analogue n={premium_analogue.n} — plural, never averaged"
         if premium_analogue is not None else
         "episode analogue — no register supplied, or fewer than two comparable episodes on file"),
    ]

    result = {
        "scenario_id": intake_data.get("scenario_id", "SCENARIO-UNSPECIFIED"),
        "corridor": corridor, "incoterm": incoterm,
        "client_exposure_note": intake.client_exposure_note(incoterm),
        "chokepoint_profile": {
            "geography": chokepoint.geography,
            "width": chokepoint.width,
            "traffic_character": chokepoint.traffic_character,
            "primary_cargo": chokepoint.primary_cargo,
            "alternate_route": chokepoint.alternate_route,
            "littoral_states": chokepoint.littoral_states,
            "fact_grade": chokepoint.fact_grade,
            "evidence_grade": chokepoint.evidence_grade,
            "response_character": chokepoint.response_character,
            "tested_incidents": [asdict(i) for i in chokepoint.tested_incidents],
        },
        "strategies": strategy_rows,
        "recommended": recommended,
        "decision_confidence": decision_confidence,
        "decision_confidence_note": decision_confidence_note,
        "recommended_break_even": asdict(recommended_be) if recommended_be else None,
        "break_even": [asdict(b) for b in break_even],
        "probability_used": ranking_probability,
        "probability_is_published_fallback": probability is None,
        "reading": reading,
        "base_rate": base_rate,
        "procurement_window": {"supply_window_days": proc_window.supply_window_days,
                               "procurement_window_days": proc_window.procurement_window_days,
                               "grade": proc_window.grade},
        "demand_supply": {
            "daily_demand_rate": (round(netting.daily_demand_rate.value, 3)
                                  if netting.daily_demand_rate is not None else None),
            "net_available_cover": (round(netting.net_available_cover, 2)
                                    if netting.net_available_cover is not None else None),
            "days_of_cover_derived": (round(netting.days_of_cover, 2)
                                      if netting.days_of_cover is not None else None),
            "days_of_cover_used": effective_days_of_cover_used,
            "days_of_cover_is_derived_fallback": days_of_cover_is_derived_fallback,
            "stockout_date": stockout_date,
            "quantity_at_risk": (round(netting.quantity_at_risk, 2)
                                 if netting.quantity_at_risk is not None else None),
            "demand_at_risk_value": (round(netting.demand_at_risk_value, 2)
                                     if netting.demand_at_risk_value is not None else None),
        },
        "cost_of_waiting": ({"paths": [{"episode_label": p.episode_label, "points": p.points}
                                       for p in cow.paths]} if cow else None),
        "cost_of_waiting_by_strategy": ({name: ({"paths": [{"episode_label": p.episode_label,
                                                            "points": p.points} for p in c.paths]}
                                                if c else None)
                                        for name, c in cow_by_strategy.items()}
                                       if cow_by_strategy else None),
        "cost_of_waiting_unavailable_reason": cow_unavailable_reason,
        "counterfactual_matrix": counterfactual,
        "scenario_robustness": robustness,
        "strategy_ranking": strategy_ranking,
        "decision_deadline": deadline,
        "point_of_no_return": ponr,
        "flip_points": [asdict(fp) for fp in flips],
        "regret": regret,
        "value_of_information": voi,
        "maximum_rational_premium": mrp,
        "exposure": {"baseline": round(baseline_cb.total, 2), "disrupted": round(disrupted_cb.total, 2),
                    "avoidable": round(gap["private"], 2)},
        "shadow_price": shadow_price,
        "ledger": ledger.as_list(),
        "weakest_grade": ledger.weakest(),
        "what_would_sharpen": what_would_sharpen(missing),
        "reassess_triggers": reassess_triggers(intake_data, reading, proc_window),
        "not_claimed": not_claimed,
        "model_version": MODEL_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return result


# --------------------------------------------------------------------------
# Brief (section 10, L6)
# --------------------------------------------------------------------------

CSS = """
:root{--water:#E9EFF1;--ink:#10222E;--soft:#4A6070;--rule:#B4C3C8;
--caution:#C0157A;--sound:#1F5F6B;--paper:#F6F9FA}
body{margin:0;background:var(--water);color:var(--ink);
font:15px/1.55 "IBM Plex Sans",system-ui,sans-serif}
.w{max-width:880px;margin:0 auto;padding:34px 20px 64px}
h1{font:600 clamp(24px,3.6vw,33px)/1.14 Spectral,Georgia,serif;margin:8px 0 10px}
h2{font:600 11px/1 "IBM Plex Mono",monospace;letter-spacing:.14em;
text-transform:uppercase;color:var(--soft);margin:0 0 12px;
padding-bottom:8px;border-bottom:1px solid var(--rule)}
.eyebrow{font:11px/1 "IBM Plex Mono",monospace;letter-spacing:.16em;
text-transform:uppercase;color:var(--soft)}
section{background:var(--paper);border:1px solid var(--rule);padding:20px;margin-top:20px}
.stats{display:flex;gap:28px;flex-wrap:wrap;margin-top:14px}
.stats b{display:block;font:500 22px/1 "IBM Plex Mono",monospace}
.stats span{font-size:11.5px;color:var(--soft);max-width:24ch;display:block;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--rule)}
th{font:600 10px/1 "IBM Plex Mono",monospace;letter-spacing:.1em;
text-transform:uppercase;color:var(--soft)}
.m{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}
ol,ul{margin:0;padding-left:20px} li{margin-bottom:6px}
.note{font-size:12.5px;color:var(--soft);border-left:3px solid var(--rule);
padding-left:12px;margin-top:20px;max-width:74ch}
"""


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def decision_brief_html(decision: dict, path: Path) -> None:
    """One screen, self-contained inline <style>, section order fixed to
    section 10's mockup — same precedent as economic_engine.
    economic_report_html(): not registered in build_site.py's PAGES list,
    since this is generated per-scenario output, not a static site page."""
    recommended = decision["recommended"]
    be = decision.get("recommended_break_even")
    is_baseline_recommended = be is not None and be.get("baseline") == recommended
    if be and be.get("p_star") is not None and is_baseline_recommended:
        decision_line = (f"{recommended} — switches to {be['strategy']} if you assign "
                         f"over {be['p_star']:.0%}")
    elif be and be.get("p_star") is not None:
        decision_line = f"{recommended} — or wait, if you assign under {be['p_star']:.0%}"
    elif be:
        decision_line = f"{recommended} — {be['reason']}"
    else:
        decision_line = recommended
    if decision.get("decision_confidence") == "weak":
        decision_line = f"{decision['decision_confidence_note']} (nominal pick: {recommended})"

    br = decision["base_rate"]
    lo, hi = br["hit_rate_ci_95"]
    break_even_line = (
        f"{be['p_star']:.0%} vs published base rate {br['hit_rate']:.0%} "
        f"(CI {lo:.0%}-{hi:.0%}, n={br['alarm_episodes']})"
        if be and be.get("p_star") is not None else "not solvable — see WHAT FLIPS IT")

    reading = decision.get("reading")
    if reading:
        reading_line = f"TAR {reading['tar']}, {reading.get('band', '')}"
        if reading.get("horizon"):
            # The band -- and the horizon estimate that comes with it -- is a
            # global reading, identical at every corridor by construction
            # (tar_ingest.py's own selftest hard-asserts this). Pairing it
            # with the corridor's own onset history says plainly why the
            # timing estimate can't be corridor-specific, right where a
            # reader would otherwise wonder why every corridor shows the
            # same window.
            reading_line += (f" ({reading['horizon']}). Band is a global reading — "
                             f"it does not indicate which theatre is moving, and the "
                             f"same horizon estimate applies at every corridor.")
        if br.get("corridor_note"):
            reading_line += " " + br["corridor_note"]
    else:
        reading_line = "no current reading supplied"

    cp = decision["chokepoint_profile"]
    incidents = cp["tested_incidents"]
    if incidents:
        def _incident_row(i: dict) -> str:
            ratio = "—" if i["response_ratio"] is None else f"{i['response_ratio']:.2f}x"
            return (f"<tr><td>{esc(i['date'])}</td><td>{esc(i['label'])}</td>"
                   f"<td class='m'>{ratio}</td><td>{esc(i['source'])}</td></tr>")
        incident_rows = "".join(_incident_row(i) for i in incidents)
        incidents_table = (f"<table><thead><tr><th>Date</th><th>Incident</th>"
                           f"<th>Response</th><th>Source</th></tr></thead>"
                           f"<tbody>{incident_rows}</tbody></table>")
    else:
        incidents_table = ""
    chokepoint_html = (
        f"<p>{esc(cp['geography'])}</p>"
        f"<p><b>Width:</b> {esc(cp['width'])} &middot; <b>Traffic:</b> {esc(cp['traffic_character'])}</p>"
        f"<p><b>Alternate route:</b> {esc(cp['alternate_route'] or 'none')}</p>"
        f"<p class='note'>Geography/traffic figures: {esc(cp['fact_grade'])} — general/public "
        f"knowledge, not independently cited. Evidence grade: {esc(cp['evidence_grade'])}.</p>"
        f"<p>{esc(cp['response_character'])}</p>"
        f"{incidents_table}")

    cow = decision.get("cost_of_waiting")
    if cow:
        cow_line = " · ".join(
            f"{p['episode_label'].split(',')[0]}-like: "
            f"{p['points'][-1][1]:+,.0f} by day {p['points'][-1][0]}"
            for p in cow["paths"])
    else:
        cow_line = "not shown — insufficient episode register coverage"

    flips = decision["flip_points"]
    flips_line = " · ".join(
        f"{fp['parameter'].replace('_', ' ')} {fp['direction']} at {fp['flip_value']:g}"
        for fp in flips) if flips else "no in-range flip points"

    exp = decision["exposure"]
    ledger_by_grade: dict[str, int] = {}
    for e in decision["ledger"]:
        ledger_by_grade[e["grade"]] = ledger_by_grade.get(e["grade"], 0) + 1
    ledger_line = " · ".join(f"{n} {g}" for g, n in sorted(ledger_by_grade.items()))

    strategy_rows = "".join(
        f"<tr><td>{esc(r['name'])}</td><td class='m'>{r['direct_cost']:,.0f}</td>"
        f"<td class='m'>{r['conditional_loss']:,.0f}</td>"
        f"<td class='m'>{r['expected_cost']:,.0f}</td><td>{esc(r['grade'])}</td></tr>"
        for r in decision["strategies"])

    sharpen_items = "".join(f"<li>{esc(x)}</li>" for x in decision["what_would_sharpen"])
    not_claimed_items = "".join(f"<li>{esc(x)}</li>" for x in decision["not_claimed"])
    reassess_items = "".join(f"<li>{esc(x)}</li>" for x in decision["reassess_triggers"])

    pw = decision.get("procurement_window") or {}
    if pw.get("procurement_window_days") is not None:
        procurement_window_line = (
            f"{pw['procurement_window_days']} day(s) until a normal-schedule purchase decision "
            f"would need to be made ({pw['supply_window_days']} day(s) until the material is "
            f"required, minus the contract transit time)."
            if pw["procurement_window_days"] >= 0 else
            f"Already {-pw['procurement_window_days']} day(s) past the point a normal-schedule "
            f"purchase would need to have been made — any action now is expedited, not routine.")
    elif pw.get("supply_window_days") is not None:
        procurement_window_line = (f"{pw['supply_window_days']} day(s) until the material is "
                                   f"required. Supply contract_transit_time_days to see how much "
                                   f"of that is still a normal-schedule decision window.")
    else:
        procurement_window_line = "not computable — ship_date not supplied."

    exposure_note = (f"<p class='note'>{esc(decision['client_exposure_note'])}</p>"
                     if decision.get("client_exposure_note") else "")

    path.write_text(f"""<!DOCTYPE html>
<meta charset="utf-8"><title>Decision — {esc(decision['scenario_id'])}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style>
<div class="w">
<div class="eyebrow">Decision engine v2 &middot; {esc(decision['corridor'])} &middot; {esc(decision['incoterm'])}</div>
<h1>{esc(decision['scenario_id'])}</h1>
{exposure_note}

<section><h2>Decision</h2><p>{esc(decision_line)}</p></section>
<section><h2>Break-even</h2><p>{esc(break_even_line)}</p></section>
<section><h2>Reading</h2><p>{esc(reading_line)}</p></section>
<section><h2>Procurement window</h2><p>{esc(procurement_window_line)}</p></section>
<section><h2>Chokepoint</h2>{chokepoint_html}</section>
<section><h2>Cost of waiting</h2><p>{esc(cow_line)}</p></section>
<section><h2>What flips it</h2><p>{esc(flips_line)}</p></section>
<section><h2>Reassess if</h2>{f"<ul>{reassess_items}</ul>" if reassess_items else
    "<p>Nothing currently computable to watch for — see WHAT WOULD SHARPEN THIS.</p>"}</section>

<section><h2>Exposure</h2>
<div class="stats">
<div><b>{exp['baseline']:,.0f}</b><span>baseline</span></div>
<div><b>{exp['disrupted']:,.0f}</b><span>disrupted (unmitigated)</span></div>
<div><b>{exp['avoidable']:,.0f}</b><span>avoidable</span></div>
</div></section>

<section><h2>Strategy comparison</h2>
<table><thead><tr><th>Strategy</th><th>Direct cost</th><th>Conditional loss</th>
<th>Expected cost</th><th>Grade</th></tr></thead>
<tbody>{strategy_rows}</tbody></table></section>

<section><h2>Ledger</h2><p>{esc(ledger_line)}</p></section>
<section><h2>What would sharpen this</h2><ul>{sharpen_items}</ul></section>
<section><h2>Not claimed</h2><ul>{not_claimed_items}</ul></section>

<p class="note">Model {esc(decision['model_version'])}. Computed {esc(decision['timestamp'])}.
Weakest grade feeding this result: {esc(decision['weakest_grade'])}.
{"Probability shown is the published base rate, not a client estimate — the decision above is "
 "phrased conditionally." if decision.get('probability_is_published_fallback') else
 "Probability shown was supplied by the client."}
</p>
</div>
""", encoding="utf-8")
    print(f"wrote {path}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    it = sub.add_parser("intake", help="write an editable intake template")
    it.add_argument("--tier", type=int, default=1, choices=(1, 2, 3))
    it.add_argument("--incoterm", choices=sorted(intake.INCOTERM_GROUPS))
    it.add_argument("--out", type=Path, default=Path("intake.json"))

    d = sub.add_parser("decide", help="compute a decision from an intake document")
    d.add_argument("--intake", type=Path, required=True)
    d.add_argument("--source", type=Path, help="GPR vintage, for the current reading")
    d.add_argument("--warrisk", type=Path)
    d.add_argument("--jwc", type=Path)
    d.add_argument("--as-of", help="YYYY-MM")
    d.add_argument("--out", type=Path)
    d.add_argument("--brief", type=Path)

    f = sub.add_parser("flip", help="print decision-flip sentences")
    f.add_argument("--intake", type=Path, required=True)
    f.add_argument("--source", type=Path)
    f.add_argument("--warrisk", type=Path)
    f.add_argument("--jwc", type=Path)
    f.add_argument("--as-of")

    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if a.cmd == "intake":
        intake.write_template(a.tier, a.incoterm, a.out)
        return 0
    if a.cmd in ("decide", "flip"):
        data = json.loads(a.intake.read_text(encoding="utf-8"))
        result = build_decision(data, as_of=a.as_of, gpr_source=a.source,
                                warrisk_path=a.warrisk, jwc_path=a.jwc)
        if a.cmd == "flip":
            if not result["flip_points"]:
                print("no in-range flip points")
            for fp in result["flip_points"]:
                print(f"  {fp['parameter']}: current {fp['current_value']:g} -> "
                     f"flips at {fp['flip_value']:g} ({fp['direction']})")
            return 0
        text = json.dumps(result, indent=2, default=str)
        print(text)
        if a.out:
            a.out.write_text(text, encoding="utf-8")
            print(f"wrote {a.out}")
        if a.brief:
            decision_brief_html(result, a.brief)
        return 0
    p.error("pass a subcommand (intake, decide, flip) or --selftest")
    return 1


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def _sample_intake() -> dict:
    return {
        "scenario_id": "HORMUZ-2026-001", "corridor": "Strait of Hormuz", "incoterm": "FOB",
        "fields": {
            "ship_date": "2026-09-01", "cargo_value": 5_000_000, "quantity": 50_000,
            "quantity_unit": "MT", "contract_freight_rate": 400_000,
            "contract_transit_time_days": 25, "days_of_cover": 10, "delay_days_estimate": 11,
            "supplier_lead_time_days": 15,
            # Demand & supply netting: 45,000 MT forecast over 90 days (500/day),
            # 3,000 on hand + 1,500 confirmed inbound - 1,000 safety stock =
            # 3,500 net available -> derives to 7 days of cover, DIFFERENT from
            # the direct days_of_cover=10 above on purpose (see selftest: the
            # direct field must win over the derived one when both are present).
            "forecast_quantity": 45_000, "forecast_window_days": 90,
            "current_inventory": 3_000, "inbound_confirmed_quantity": 1_500,
            "safety_stock": 1_000,
            "wacc_pct": 0.08, "carrying_cost_pct_pa": 0.18, "gross_margin_pct": 0.12,
            "penalty_per_day": 5_000, "disrupted_freight_quote": 650_000,
            "reroute_quote": None, "war_risk_premium_quote": 340_000,
            "emergency_replacement_quote": None,
        },
        "strategies": [
            {"name": "Continue", "direct_cost": 0, "effects": {}, "is_baseline": True},
            {"name": "Partial reroute", "direct_cost": 700_000,
             "effects": {"delay_days_delta": -6, "capacity_restored": 0.4,
                        "war_risk_premium_multiplier": 0.25, "days_of_cover_delta": 0}},
        ],
        "client_probability_estimate": None, "probability_range": None,
    }


def selftest() -> int:
    data = _sample_intake()
    strategies = [_strategy_from_dict(s) for s in data["strategies"]]
    continue_s, reroute_s = strategies

    # --- #1 / #3: hand-worked conditional loss, reproduced component by
    # component (the oracle is derived and shown here, not lifted from a
    # spec table — enginev2.md has no worked numeric example to reuse).
    cl_continue = conditional_loss(data, continue_s)
    dgm_c = 5_000_000 * 0.12 / 10
    delay_rate_c = (0.08 / 365) * 5_000_000 + 5_000 + 1.0 * dgm_c   # stockout=1: 10 <= 11
    expected_delay_c = delay_rate_c * 11
    holding_rate = (0.18 / 365) * (5_000_000 / 50_000)
    expected_inventory_c = holding_rate * 50_000 * 11
    expected_transport_c = (650_000 - 400_000) * (1 - 0)
    expected_insurance_c = 340_000
    expected_total_c = expected_delay_c + expected_inventory_c + expected_transport_c + expected_insurance_c
    assert abs(cl_continue.components["delay"] - expected_delay_c) < 1e-6
    assert abs(cl_continue.components["inventory"] - expected_inventory_c) < 1e-6
    assert abs(cl_continue.components["transport"] - expected_transport_c) < 1e-6
    assert abs(cl_continue.components["insurance"] - expected_insurance_c) < 1e-6
    assert abs(cl_continue.total - expected_total_c) < 1e-6, (cl_continue.total, expected_total_c)
    assert cl_continue.grade == "DERIVED"

    cl_reroute = conditional_loss(data, reroute_s)
    dgm_r = 5_000_000 * 0.12 / 10   # days_of_cover unaffected by this strategy's effects
    delay_rate_r = (0.08 / 365) * 5_000_000 + 5_000 + 0.0 * dgm_r   # stockout=0: 10 > 5
    expected_delay_r = delay_rate_r * 5
    expected_inventory_r = holding_rate * 50_000 * 5
    expected_transport_r = (650_000 - 400_000) * (1 - 0.4)
    expected_insurance_r = 340_000 * 0.25
    expected_total_r = expected_delay_r + expected_inventory_r + expected_transport_r + expected_insurance_r
    assert abs(cl_reroute.total - expected_total_r) < 1e-6, (cl_reroute.total, expected_total_r)
    assert cl_reroute.total < cl_continue.total   # the mitigation genuinely mitigates

    # --- module 4 (Shadow Price of Geopolitical Risk Engine): same
    # subtraction as exposure/gap above, relabelled -- not re-derived.
    baseline_cb = baseline_costbreakdown(data)
    disrupted_cb = disrupted_costbreakdown(data)
    gap_check = welfare_gap(disrupted_cb, baseline_cb)
    assert abs(baseline_cb.total - 400_000.0) < 1e-6
    assert abs(disrupted_cb.total - 1_744_178.082191781) < 1e-3
    assert abs(gap_check["private"] - cl_continue.total) < 1e-6   # same number, cross-checked
    sp = shadow_price_of_geopolitical_risk(baseline_cb, disrupted_cb, gap_check)
    assert sp["baseline_cost"] == round(baseline_cb.total, 2)
    assert sp["risk_adjusted_cost"] == round(disrupted_cb.total, 2)
    assert sp["shadow_price"] == round(gap_check["private"], 2)
    assert sp["components"]["commodity"] == {"baseline": 0.0, "disrupted": 0.0, "delta": 0.0}
    missing_names = {c["component"] for c in sp["cost_components_not_computed"]}
    assert missing_names == {"commodity", "expediting", "rerouting",
                             "service-related economic loss", "working-capital impact"}
    assert "freight" not in missing_names and "disruption loss" not in missing_names

    # --- module 5 (Maximum Rational Premium Engine): break_even_
    # probability()'s own equation, solved for cost instead of probability,
    # at the real probability in use -- hand-verified against real executed
    # numbers, not estimated (run directly against this same sample before
    # writing these assertions).
    regret_direct = regret_at_range(strategies, data, None)
    losses_direct = {"Continue": cl_continue.total, "Partial reroute": cl_reroute.total}
    mrp_check = maximum_rational_premium(strategies, continue_s, losses_direct, 0.273,
                                         regret_direct, ["Continue", "Partial reroute"])
    reroute_entry = next(e for e in mrp_check["strategies"] if e["strategy"] == "Partial reroute")
    assert abs(reroute_entry["maximum_rational_premium"] - 291_118.97) < 1.0, reroute_entry
    assert reroute_entry["actual_direct_cost"] == 700_000
    assert reroute_entry["verdict"] == "not_economically_justified"   # 700,000 > 291,118.97
    assert abs(reroute_entry["uncertainty_range"]["low"]["value"] - 106_636.99) < 1.0, reroute_entry
    assert abs(reroute_entry["uncertainty_range"]["high"]["value"] - 607_830.82) < 1.0, reroute_entry
    assert reroute_entry["uncertainty_range"]["low"]["probability"] == 0.10
    assert reroute_entry["uncertainty_range"]["high"]["probability"] == 0.57
    assert mrp_check["headline"]["strategy"] == "Partial reroute"

    # E[C_s] with an explicit probability — assertion #1.
    p = 0.22
    e_continue = expected_cost(continue_s.direct_cost, p, cl_continue.total)
    e_reroute = expected_cost(reroute_s.direct_cost, p, cl_reroute.total)
    assert abs(e_continue - (0 + p * cl_continue.total)) < 1e-6
    assert abs(e_reroute - (700_000 + p * cl_reroute.total)) < 1e-6

    # Quoted override (CLIENT_QUOTED) is auditable: components survive
    # alongside the override, not discarded.
    quoted = dataclasses.replace(reroute_s, quoted_residual_loss=250_000)
    cl_quoted = conditional_loss(data, quoted)
    assert cl_quoted.total == 250_000 and cl_quoted.grade == "CLIENT_QUOTED"
    assert abs(cl_quoted.components["transport"] - expected_transport_r) < 1e-6

    # --- reroute_quote wiring: prices into "transport" via the same
    # capacity_restored gate as disrupted_freight_quote, even with no
    # contract_freight_rate/disrupted_freight_quote pair on file at all
    # (the exact gap this was fixed for -- "Live verify SKU #5" had no
    # contract_freight_rate on file).
    data_reroute_only = _with_field(_with_field(data, "contract_freight_rate", None),
                                    "reroute_quote", 500_000)
    cl_reroute_only = conditional_loss(data_reroute_only, reroute_s)   # capacity_restored=0.4
    assert abs(cl_reroute_only.components["transport"] - 500_000 * 0.4) < 1e-6
    assert "transport" not in cl_reroute_only.absent_components
    cl_reroute_only_baseline = conditional_loss(data_reroute_only, continue_s)   # capacity_restored=0
    assert cl_reroute_only_baseline.components["transport"] == 0.0
    assert "transport" not in cl_reroute_only_baseline.absent_components   # data present, effect
                                                                           # zero -- not the same
                                                                           # as no data at all
    # No freight data of either kind at all -> genuinely ABSENT, unchanged.
    data_no_freight = _with_field(_with_field(data, "contract_freight_rate", None),
                                  "disrupted_freight_quote", None)
    cl_no_freight = conditional_loss(data_no_freight, reroute_s)
    assert "transport" in cl_no_freight.absent_components

    # --- emergency_replacement_quote wiring: a strategy whose effects flag
    # sourced_from_emergency_replacement_quote is priced directly from the
    # live quote (CLIENT_QUOTED), reading it from intake_data each call
    # rather than needing it copied onto quoted_residual_loss by hand -- so
    # a later reassessment with an updated quote re-prices it automatically.
    data_emergency = _with_field(data, "emergency_replacement_quote", 900_000)
    emergency_effects = dataclasses.replace(reroute_s.effects,
                                            sourced_from_emergency_replacement_quote=True)
    emergency_strategy = dataclasses.replace(reroute_s, effects=emergency_effects)
    cl_emergency = conditional_loss(data_emergency, emergency_strategy)
    assert cl_emergency.total == 900_000 and cl_emergency.grade == "CLIENT_QUOTED"
    assert abs(cl_emergency.components["transport"] - expected_transport_r) < 1e-6   # components
                                                                                     # still assembled,
                                                                                     # not discarded
    # The live quote takes priority over a stale manual quoted_residual_loss
    # when both are set on the same strategy...
    emergency_and_manual = dataclasses.replace(emergency_strategy, quoted_residual_loss=123.0)
    assert conditional_loss(data_emergency, emergency_and_manual).total == 900_000
    # ...but falls back to the manual override when the flag is set and no
    # live quote is on file (never silently drops to DERIVED instead).
    cl_fallback = conditional_loss(data, emergency_and_manual)   # data has no emergency quote
    assert cl_fallback.total == 123.0 and cl_fallback.grade == "CLIENT_QUOTED"
    # A strategy with the flag unset is completely unaffected by the quote
    # existing -- still plain DERIVED.
    assert conditional_loss(data_emergency, reroute_s).grade == "DERIVED"

    # --- #2: p* reduces exactly to v1's published C/L special case.
    zero_loss_s = Strategy("s", 700_000, quoted_residual_loss=0.0)
    zero_cost_s0 = Strategy("s0", 0.0, quoted_residual_loss=0.0)
    be = break_even_probability(zero_loss_s, zero_cost_s0, 0.0, 4_375_000.0)
    assert be.p_star is not None
    assert abs(be.p_star - mitigation_threshold(700_000, 4_375_000)) < 1e-9, be.p_star
    assert abs(be.p_star - 0.16) < 1e-9

    # Degenerate case: denominator <= 0 -> None with a reason, never a crash.
    worse = Strategy("worse", 100_000, quoted_residual_loss=5_000_000.0)
    base0 = Strategy("base", 0.0, quoted_residual_loss=1_000_000.0)
    be_bad = break_even_probability(worse, base0, 5_000_000.0, 1_000_000.0)
    assert be_bad.p_star is None and be_bad.reason is not None

    # Real scenario's own break-even, sanity-checked against the hand-worked totals.
    be_real = break_even_probability(reroute_s, continue_s, cl_reroute.total, cl_continue.total)
    assert be_real.p_star is not None
    expected_p_star = 700_000 / (expected_total_c - expected_total_r)
    assert abs(be_real.p_star - expected_p_star) < 1e-6

    # --- #7: CIF intake never asks for war-risk premium; brief states why.
    cif_fields = {f.name for f in intake.fields_for(3, "CIF")}
    assert "war_risk_premium_quote" not in cif_fields
    assert intake.client_exposure_note("CIF") is not None

    # --- #6: regime check — no horizon language inside 12 months of onset.
    # (episodes.py's own selftest proves the mechanism directly; here we
    # confirm decision_engine wires it through to the analogue it fetches.)
    onset_dt, _ = episodes._onset_date("Strait of Hormuz", "2026-02")
    assert onset_dt.isoformat() == "2026-02-28"

    # --- #4 / #5: analogue absent -> cost_of_waiting absent (whole key
    # missing from the result, not a null field) when no register is
    # supplied at all.
    result_no_register = build_decision(data)
    assert result_no_register["cost_of_waiting"] is None
    assert "cost_of_waiting" in result_no_register   # present as a key, value None — see below

    # --- module 1 (Procurement Counterfactual Engine): matrix / scenario
    # robustness / ranking / service risk / cash impact, all assembled from
    # numbers already computed above -- no new probability or severity
    # figure. Against this scenario with no register supplied, only the
    # baseline column exists for either strategy — honest absence, not a
    # gap to fill.
    strategies_by_name = {r["name"]: r for r in result_no_register["strategies"]}
    assert strategies_by_name["Continue"]["service_risk_stockout"] == 1.0   # cover 10 <= delay 11
    assert strategies_by_name["Partial reroute"]["service_risk_stockout"] == 0.0   # cover 10 > delay 5
    assert strategies_by_name["Continue"]["cash_impact"] is None
    assert strategies_by_name["Continue"]["cash_impact_grade"] == "ABSENT"

    cf_matrix = result_no_register["counterfactual_matrix"]
    assert {m["strategy"] for m in cf_matrix} == {"Continue", "Partial reroute"}
    for m in cf_matrix:
        assert len(m["scenarios"]) == 1, m["scenarios"]   # honest absence, never padded
        assert m["scenarios"][0]["scenario_kind"] == "baseline"
        own_row = strategies_by_name[m["strategy"]]
        assert m["scenarios"][0]["expected_total_cost"] == own_row["expected_cost"]
        assert m["expected_disruption_loss"] == own_row["conditional_loss"]

    assert result_no_register["scenario_robustness"]["status"] == "not_computable"
    assert result_no_register["scenario_robustness"]["recommended"] == result_no_register["recommended"]

    assert result_no_register["strategy_ranking"][0] == result_no_register["recommended"]
    assert result_no_register["strategy_ranking"] == [
        r["name"] for r in sorted(result_no_register["strategies"], key=lambda r: r["expected_cost"])]

    # --- reading= bypasses point_in_time()/gpr_source entirely (the path a
    # deployed backend uses, since it has no access to the gitignored GPR
    # vintage — see economic_engine.compute()'s identical historical_reading=
    # precedent). A caller-supplied reading populates the ledger exactly
    # like a gpr_source-derived one would, and gpr_source is never touched.
    fake_reading = {"tar": 0.19, "grade": "PUBLISHED"}
    result_with_reading = build_decision(data, reading=fake_reading)
    assert result_with_reading["reading"] == fake_reading
    reading_rows = [e for e in result_with_reading["ledger"] if e["field"] == "reading_tar"]
    assert reading_rows and reading_rows[0]["grade"] == "PUBLISHED"

    # --- #9: ledger completeness — every strategy's conditional loss and
    # every genuinely missing field appears, each with a valid grade.
    fields_by_name = {e["field"]: e for e in result_no_register["ledger"]}
    assert "conditional_loss[Continue]" in fields_by_name
    assert fields_by_name["conditional_loss[Continue]"]["grade"] in GRADES
    for e in result_no_register["ledger"]:
        assert e["grade"] in GRADES

    # --- #8: dropping a field degrades with a stated grade, never silently.
    stripped = json.loads(json.dumps(data))
    stripped["fields"]["war_risk_premium_quote"] = None
    result_stripped = build_decision(stripped)
    absent_rows = [e for e in result_stripped["ledger"]
                  if e["grade"] == "ABSENT" and "insurance" in e["field"]]
    assert absent_rows, result_stripped["ledger"]

    # --- flip points: at least one linear (probability) flip on the real scenario.
    flips = solve_flips(data, strategies, None, 0.22)
    assert any(fp.parameter == "probability" for fp in flips), flips

    # A scenario engineered so the step in stockout_probability (intake.py
    # section 5) is exactly what swaps the ranking: "Continue" has every
    # other cost term zeroed out (wacc/penalty/carrying/quotes all 0 or
    # None), so its conditional loss is 0 while days_of_cover (10) exceeds
    # delay_days_estimate and jumps to margin*delay_days the instant it
    # doesn't. "Mitigate" is a flat, quoted 200,000 regardless. At p=0.5
    # that flat cost sits between the two sides of the step (0 vs ~250,000
    # expected), so crossing delay_days_estimate=10 must flip the ranking —
    # this is the concrete case constraint 3 (a smooth solver cannot assume
    # continuity here) is about.
    step_data = {
        "scenario_id": "STEP-TEST", "corridor": "Strait of Hormuz", "incoterm": "FOB",
        "fields": {
            "ship_date": "2026-09-01", "cargo_value": 1_000_000, "quantity": 1_000,
            "quantity_unit": "MT", "contract_freight_rate": 0,
            "contract_transit_time_days": 0, "days_of_cover": 10, "delay_days_estimate": 9.5,
            "wacc_pct": 0.0, "carrying_cost_pct_pa": 0.0, "gross_margin_pct": 0.5,
            "penalty_per_day": 0.0, "disrupted_freight_quote": None,
            "reroute_quote": None, "war_risk_premium_quote": None,
            "emergency_replacement_quote": None,
        },
        "strategies": [
            {"name": "Continue", "direct_cost": 0, "effects": {}, "is_baseline": True},
            {"name": "Mitigate", "direct_cost": 200_000, "effects": {},
             "quoted_residual_loss": 0.0},
        ],
    }
    step_strats = [_strategy_from_dict(s) for s in step_data["strategies"]]
    step_losses = {s.name: conditional_loss(step_data, s).total for s in step_strats}
    assert step_losses["Continue"] == 0.0, step_losses          # below the step: no stockout yet
    assert step_losses["Mitigate"] == 0.0                        # quoted override, flat
    step_expected = _expected_costs(step_strats, step_losses, 0.5)
    assert step_expected["Continue"] < step_expected["Mitigate"]  # Continue currently ranks first
    flips3 = solve_flips(step_data, step_strats, None, 0.5)
    step_flip = next((fp for fp in flips3 if fp.parameter == "delay_days_estimate"), None)
    assert step_flip is not None, flips3
    assert abs(step_flip.flip_value - 10.0) < 1e-6, step_flip

    # --- #10: reproducibility — same input, same output modulo timestamp.
    r1 = build_decision(data)
    r2 = build_decision(data)
    d1, d2 = dict(r1), dict(r2)
    del d1["timestamp"], d2["timestamp"]
    assert d1 == d2, "same input should reproduce the same result"

    # --- end-to-end sanity, and the actual point of section 8.2's fix:
    # unlike v1's compare_strategies() (direct_cost + residual_loss_estimate,
    # probability never entering), the SAME scenario's recommendation must
    # depend on probability. At the published base-rate fallback (~27%),
    # paying 700,000 up front for reroute does not clear its own ~66%
    # break-even, so "Continue" (cheap, high conditional loss) wins on
    # expectation; give the client a high probability estimate instead and
    # the recommendation flips to the mitigation. A tool where probability
    # never changes the answer could not produce this pair of results.
    result_low_p = build_decision(data)
    assert result_low_p["probability_is_published_fallback"] is True
    assert result_low_p["recommended"] == "Continue", result_low_p["recommended"]

    high_p_data = json.loads(json.dumps(data))
    high_p_data["client_probability_estimate"] = 0.80
    result_high_p = build_decision(high_p_data)
    assert result_high_p["probability_is_published_fallback"] is False
    assert result_high_p["recommended"] == "Partial reroute", result_high_p["recommended"]

    # --- value_of_information: relabels regret_at_range()'s own
    # regret[recommended] at each end, never a new computation. At the base
    # rate, Continue wins at both ends of the published range too, so EVPI
    # is genuinely zero -- nothing to gain from more information. At p=0.80
    # (outside the published range), Continue still wins at BOTH ends of
    # that range, yet "recommended" (Partial reroute, chosen at 0.80) does
    # not -- real, hand-verified avoidable regret exists at both ends
    # despite decision_confidence calling this case "robust" below (a real,
    # pre-existing gap between what "robust" measures and real EVPI -- see
    # act_or_wait()'s own GATHER docstring in strategy_decision.py).
    assert result_low_p["value_of_information"] == {"low": {"probability": 0.1, "value": 0.0},
                                                     "high": {"probability": 0.57, "value": 0.0}}
    voi_high_p = result_high_p["value_of_information"]
    assert voi_high_p["low"]["value"] == 593363.01, voi_high_p
    assert voi_high_p["high"]["value"] == 92169.18, voi_high_p
    assert voi_high_p["low"]["value"] == result_high_p["regret"]["low"]["regret"]["Partial reroute"]
    assert voi_high_p["high"]["value"] == result_high_p["regret"]["high"]["regret"]["Partial reroute"]

    # --- cost_of_waiting_by_strategy(): a synthetic register (episodes.py's
    # own selftest fixture, early enough observations to clear the n>=2
    # floor -- the real, committed src/warrisk.csv doesn't, at any
    # realistic delay, see below) proves two strategies with different
    # war_risk_premium_multiplier sensitivity get genuinely different
    # trajectories, not one path copied onto both.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cow_warrisk_path = Path(d) / "warrisk.csv"
        cow_jwc_path = Path(d) / "warrisk_jwc.csv"
        episodes._write_warrisk_csv(cow_warrisk_path)
        episodes._write_jwc_csv(cow_jwc_path)
        cow_data = json.loads(json.dumps(data))
        # 10, not 18 (Hormuz's own fixture peak) -- at anchor=18 every
        # offset this produces (18/25/32) still resolves to the SAME
        # latest-available reading in both fixture episodes (no further
        # data past day 18/day 10 until day 104), so every strategy's
        # trajectory is flat and indistinguishable regardless of its own
        # multiplier -- not a bug, just the wrong anchor to prove the point
        # with. At anchor=10, stepping to offset 24 crosses Hormuz's real
        # day-18 peak, producing genuine day-to-day change.
        cow_data["fields"]["delay_days_estimate"] = 10
        result_cow = build_decision(cow_data, warrisk_path=cow_warrisk_path, jwc_path=cow_jwc_path)
        assert result_cow["cost_of_waiting"] is not None
        assert result_cow["cost_of_waiting_unavailable_reason"] is None
        cbs = result_cow["cost_of_waiting_by_strategy"]
        assert cbs is not None and set(cbs) == {"Continue", "Partial reroute"}
        assert cbs["Continue"] is not None and cbs["Partial reroute"] is not None
        # compare the whole paths list (both episodes), not just paths[0] --
        # Bab-el-Mandeb's own trajectory happens to stay flat for both
        # strategies at this anchor (no fixture data between its day-10 and
        # day-104 readings); the real divergence is in Hormuz's path, which
        # crosses its fixture peak at offset 24.
        assert cbs["Continue"]["paths"] != cbs["Partial reroute"]["paths"], \
            "different war_risk_premium_multiplier must produce different trajectories, not a copy"

        # counterfactual_matrix() reshapes these same paths, never
        # recomputing them independently -- cross-check one real cell
        # against cost_of_waiting_by_strategy's own already-verified number.
        cf_by_name = {m["strategy"]: m for m in result_cow["counterfactual_matrix"]}
        assert len(cf_by_name["Continue"]["scenarios"]) > 1
        assert len(cf_by_name["Partial reroute"]["scenarios"]) > 1
        continue_expected = next(r["expected_cost"] for r in result_cow["strategies"]
                                 if r["name"] == "Continue")
        day0, delta0 = cbs["Continue"]["paths"][0]["points"][0]
        label0 = cbs["Continue"]["paths"][0]["episode_label"]
        matching_scenario = next(sc for sc in cf_by_name["Continue"]["scenarios"]
                                 if sc["scenario_kind"] == "episode_analogue"
                                 and sc["scenario"] == label0 and sc["day_offset"] == day0)
        assert matching_scenario["expected_total_cost"] == round(continue_expected + delta0, 2)
        assert matching_scenario["expected_total_cost_delta"] == delta0
        assert matching_scenario["grade"] == "EPISODE_ANALOGUE"

        # decision_deadline()'s economic_crossover basis -- the ONLY place
        # this is reachable, since the real committed register is provably
        # flat past day 139 (see below). Continue has no war-risk
        # mitigation multiplier, so it takes the fixture's full day-24
        # escalation; Partial reroute's 0.25 multiplier dampens it and
        # becomes cheaper.
        dd_crossover = result_cow["decision_deadline"]
        assert dd_crossover["basis"] == "economic_crossover"
        assert dd_crossover["estimated_deadline_day"] == 17, dd_crossover
        assert dd_crossover["preferred_action_before_deadline"] == "Continue"
        assert dd_crossover["preferred_action_after_deadline"] == "Partial reroute"
        assert dd_crossover["uncertainty_range"] == {"lower_bound_day": 17, "upper_bound_day": 24}, dd_crossover

    # --- against the REAL, committed src/warrisk.csv: cost-of-waiting is
    # genuinely unavailable at this scenario's realistic delay (Hormuz's
    # only real post-onset observation is ~day 139), and the reason names
    # this as a register-depth fact, never a per-corridor coverage claim.
    assert result_low_p["cost_of_waiting_by_strategy"] is None
    cow_reason = result_low_p["cost_of_waiting_unavailable_reason"]
    assert cow_reason is not None
    assert "Not specific to" in cow_reason, cow_reason

    result = result_low_p
    assert result["weakest_grade"] in GRADES
    assert len(result["ledger"]) > 0
    assert isinstance(result["what_would_sharpen"], list)
    assert result["exposure"]["avoidable"] >= 0

    # --- chokepoint_profiles.py integration: an EPISODE_ANALOGUE corridor
    # (Hormuz, already this scenario's corridor) carries real tested
    # incidents; a STRUCTURAL corridor (Adriatic) carries none, honestly,
    # without lowering weakest_grade below what missing fields already do.
    cp_hormuz = result["chokepoint_profile"]
    assert cp_hormuz["evidence_grade"] == "EPISODE_ANALOGUE"
    assert len(cp_hormuz["tested_incidents"]) == 7, cp_hormuz["tested_incidents"]
    assert any(e["field"] == "chokepoint_evidence_grade" and e["grade"] == "EPISODE_ANALOGUE"
              for e in result["ledger"])

    adriatic_data = json.loads(json.dumps(data))
    adriatic_data["corridor"] = "Adriatic"
    result_adriatic = build_decision(adriatic_data)
    cp_adriatic = result_adriatic["chokepoint_profile"]
    assert cp_adriatic["evidence_grade"] == "STRUCTURAL"
    assert cp_adriatic["tested_incidents"] == []
    assert cp_adriatic["response_character"]   # still says something, honestly
    assert any(e["field"] == "chokepoint_evidence_grade" and e["grade"] == "STRUCTURAL"
              for e in result_adriatic["ledger"])
    # This sample scenario's Tier-1 fields are all filled (missing_fields()
    # defaults to tier=1 when the intake carries no "tier" key, and every
    # Tier-1 field here is set), so the chokepoint's own STRUCTURAL entry is
    # genuinely the weakest thing in the ledger — correctly surfaced, not
    # masked by an unrelated ABSENT field.
    assert result_adriatic["weakest_grade"] == "STRUCTURAL", result_adriatic["weakest_grade"]

    # --- corridor-specific base rates (Section 5.6: "a single global
    # threshold applied with corridor-specific base rates, not a
    # corridor-specific threshold"). A raw historical onset COUNT from
    # tar_ingest.ONSETS, never a recalibrated probability -- the global
    # alarm_episodes/hit_rate stay untouched by corridor.
    assert base_rate_context()["alarm_episodes"] == ALARM_EPISODES  # no corridor: unchanged
    assert "corridor_onsets" not in base_rate_context()

    br_hormuz = base_rate_context("Strait of Hormuz")
    assert br_hormuz["alarm_episodes"] == ALARM_EPISODES  # global figure untouched
    assert br_hormuz["corridor_onsets"] == 4, br_hormuz["corridor_onsets"]
    assert br_hormuz["corridor_onset_grade"] == "EPISODE_ANALOGUE"
    onset_rows = [e for e in result_no_register["ledger"] if e["field"] == "corridor_onset_history"]
    assert onset_rows and onset_rows[0]["grade"] == "EPISODE_ANALOGUE" and onset_rows[0]["value"] == 4

    # Suez has zero recorded onsets in tar_ingest.ONSETS -- STRUCTURAL here
    # -- even though chokepoint_profiles.py grades Suez's media-response
    # evidence EPISODE_ANALOGUE. The two facts answer different questions
    # (has a real onset happened here vs. does coverage respond to one)
    # and are allowed to disagree; Adriatic disagrees the other way (2
    # onsets, EPISODE_ANALOGUE here, but STRUCTURAL for chokepoint response
    # since its onsets predate GDELT coverage).
    suez_data = json.loads(json.dumps(data))
    suez_data["corridor"] = "Suez Canal"
    result_suez = build_decision(suez_data)
    assert result_suez["chokepoint_profile"]["evidence_grade"] == "EPISODE_ANALOGUE"
    suez_onset_rows = [e for e in result_suez["ledger"] if e["field"] == "corridor_onset_history"]
    assert suez_onset_rows and suez_onset_rows[0]["grade"] == "STRUCTURAL" and suez_onset_rows[0]["value"] == 0

    # --- decision confidence: the real scenario's two strategies are far
    # apart (1.34M vs 278k conditional loss) -- a robust call, no note.
    assert result["decision_confidence"] == "robust"
    assert result["decision_confidence_note"] is None

    # A third strategy quoted with the SAME conditional loss as Continue and
    # a trivial direct cost (1,000, ~0.3% of Continue's own expected cost)
    # is razor-thin, not a real win -- must say so plainly rather than
    # presenting it as a strong recommendation.
    thin_data = json.loads(json.dumps(data))
    thin_data["strategies"].append({
        "name": "Near tie", "direct_cost": 1_000,
        "quoted_residual_loss": cl_continue.total,
    })
    result_thin = build_decision(thin_data)
    assert result_thin["recommended"] == "Continue"   # still the argmin -- unchanged
    assert result_thin["decision_confidence"] == "weak"
    assert "No economically significant advantage" in result_thin["decision_confidence_note"]

    # --- procurement window: pure arithmetic on ship_date/contract_transit_
    # time_days, no wall-clock dependence in the test (today= pinned).
    # sample ship_date is 2026-09-01, contract_transit_time_days is 25.
    pw = procurement_window(data, today=date(2026, 8, 13))
    assert pw.supply_window_days == 19, pw.supply_window_days   # 2026-09-01 minus 2026-08-13
    assert pw.procurement_window_days == 19 - 25, pw.procurement_window_days   # already past normal lead time
    assert pw.grade == "DERIVED"

    no_ship_date = json.loads(json.dumps(data))
    no_ship_date["fields"]["ship_date"] = None
    assert procurement_window(no_ship_date).grade == "ABSENT"

    result_pw = build_decision(data)   # end to end: wired into the result and the ledger
    assert result_pw["procurement_window"]["supply_window_days"] is not None
    pw_ledger_rows = [e for e in result_pw["ledger"] if e["field"] == "procurement_window_days"]
    assert pw_ledger_rows and pw_ledger_rows[0]["grade"] == "DERIVED"

    # --- decision_deadline(): operational_schedule when the economic axis
    # has nothing to say and a real procurement window exists -- reuses the
    # pw object already hand-verified above (today pinned 2026-08-13) so
    # the -6 figure is deterministic, not wall-clock-dependent.
    _no_crossover = {"status": "not_computable", "recommended": "Continue"}
    dd_op = decision_deadline([], _no_crossover, "Continue", pw)
    assert dd_op["basis"] == "operational_schedule"
    assert dd_op["estimated_deadline_day"] == -6, dd_op
    assert dd_op["preferred_action_before_deadline"] == "Continue"
    assert dd_op["preferred_action_after_deadline"] is None
    assert dd_op["uncertainty_range"] is None

    dd_absent = decision_deadline([], _no_crossover, "Continue",
                                  ProcurementWindow(None, None, "ABSENT"))
    assert dd_absent["basis"] == "not_computable"
    assert dd_absent["estimated_deadline_day"] is None

    # end to end: build_decision() wires it through using its own real
    # wall-clock proc_window -- cross-checked against that same run's own
    # procurement_window, never a hardcoded day count (procurement_window_
    # days is wall-clock-dependent by design, same reason result_pw's own
    # assertions above only check "is not None").
    result_no_ship_date = build_decision(no_ship_date)
    assert result_no_ship_date["decision_deadline"]["basis"] == "not_computable"
    assert result_no_ship_date["decision_deadline"]["estimated_deadline_day"] is None
    assert result_pw["decision_deadline"]["basis"] == "operational_schedule"
    assert result_pw["decision_deadline"]["estimated_deadline_day"] == \
        result_pw["procurement_window"]["procurement_window_days"]

    # --- point_of_no_return() (module 3): compares up to three real
    # candidate dates and reports the earliest as binding, by name.
    today_ponr = date(2026, 8, 13)

    # "inventory remaining" wins: cover runs out well before the
    # procurement window closes.
    pw_far = ProcurementWindow(supply_window_days=100, procurement_window_days=80, grade="DERIVED")
    stockout_soon = demand_supply_stockout_date(15, today=today_ponr)
    ponr_stockout = point_of_no_return({"fields": {}}, pw_far, stockout_soon, today=today_ponr)
    assert ponr_stockout["binding_constraint"] == "inventory remaining"
    assert ponr_stockout["estimated_point_of_no_return_day"] == 15, ponr_stockout
    assert ponr_stockout["basis"] == "inventory_remaining"

    # "required delivery date" wins: the procurement window closes sooner
    # than inventory exhaustion (and no alternative-supplier data at all).
    pw_soon = ProcurementWindow(supply_window_days=30, procurement_window_days=5, grade="DERIVED")
    stockout_later = demand_supply_stockout_date(40, today=today_ponr)
    ponr_pw = point_of_no_return({"fields": {}}, pw_soon, stockout_later, today=today_ponr)
    assert ponr_pw["binding_constraint"] == "required delivery date"
    assert ponr_pw["estimated_point_of_no_return_day"] == 5, ponr_pw
    assert ponr_pw["basis"] == "required_delivery_date"

    # "alternative supplier lead time" wins -- the real differentiator vs
    # decision_deadline(), which has no equivalent axis at all.
    pw_alt = ProcurementWindow(supply_window_days=30, procurement_window_days=20, grade="DERIVED")
    stockout_alt = demand_supply_stockout_date(25, today=today_ponr)
    alt_data = {"fields": {"supplier_lead_time_days": 28}}   # 30 - 28 = day 2
    ponr_alt = point_of_no_return(alt_data, pw_alt, stockout_alt, today=today_ponr)
    assert ponr_alt["binding_constraint"] == "alternative supplier lead time"
    assert ponr_alt["estimated_point_of_no_return_day"] == 2, ponr_alt
    assert ponr_alt["basis"] == "alternative_supplier_lead_time"
    assert any(c["constraint"] == "alternative supplier lead time"
              for c in ponr_alt["candidate_constraints"])

    # not determinable: nothing on file at all -- the module's own explicit
    # rule, never an invented date. All 11 tracked_constraints named.
    ponr_none = point_of_no_return({"fields": {}}, ProcurementWindow(None, None, "ABSENT"), None,
                                   today=today_ponr)
    assert ponr_none["estimated_point_of_no_return_day"] is None
    assert ponr_none["binding_constraint"] is None
    assert ponr_none["basis"] == "not_determinable"
    assert "not determinable from available data" in ponr_none["explanation"].lower()
    assert len(ponr_none["not_determinable_constraints"]) == 11, ponr_none["not_determinable_constraints"]

    # every other newmodules.txt tracked_constraint is explicitly named,
    # not silently dropped, regardless of whether this decision has a
    # binding constraint.
    nd_names = {c["constraint"] for c in ponr_alt["not_determinable_constraints"]}
    assert {"supplier capacity", "alternative supplier availability", "freight capacity",
           "alternative route availability", "production schedule", "contractual constraints",
           "minimum feasible mitigation lead time", "expected price escalation"} <= nd_names

    # end to end: build_decision() wires it through under its own key,
    # using its own real wall-clock proc_window/stockout_date -- structural
    # check only (wall-clock-dependent by design), same convention as
    # result_pw's own procurement_window assertions above. The sample
    # intake now DOES set supplier_lead_time_days (15 days, added this
    # module so the "every Tier-1 field filled" invariant a few sections up
    # stays true), so it must appear as a real candidate, not not_determinable.
    result_ponr = build_decision(data)
    assert "point_of_no_return" in result_ponr
    assert result_ponr["point_of_no_return"]["basis"] in (
        "inventory_remaining", "required_delivery_date", "alternative_supplier_lead_time")
    assert any(c["constraint"] == "alternative supplier lead time"
              for c in result_ponr["point_of_no_return"]["candidate_constraints"])

    no_alt_supplier_data = json.loads(json.dumps(data))
    no_alt_supplier_data["fields"]["supplier_lead_time_days"] = None
    result_ponr_no_alt = build_decision(no_alt_supplier_data)
    assert any(c["constraint"] == "alternative supplier lead time"
              for c in result_ponr_no_alt["point_of_no_return"]["not_determinable_constraints"])

    # --- reassess triggers: named plainly from state already computed,
    # never a new prediction of when they'll fire. The sample scenario has
    # a reading=None (no gpr_source/reading passed) so the band trigger is
    # absent; days_of_cover (10) < delay_days_estimate (11), so the
    # inventory trigger is also correctly absent (already past that point,
    # not a future one to watch for) -- only the procurement-window trigger
    # should fire, since it's negative (already past) in this scenario per
    # the pw assertion above... use reading= to also exercise the band case.
    result_triggers = build_decision(data, reading={"tar": 1.5, "band": "Procurement Watch"})
    assert any("Procurement Watch" in t for t in result_triggers["reassess_triggers"])
    assert not any("inventory cover" in t for t in result_triggers["reassess_triggers"])

    cover_data = json.loads(json.dumps(data))
    cover_data["fields"]["days_of_cover"] = 20   # now exceeds delay_days_estimate (11)
    result_cover = build_decision(cover_data)
    assert any("inventory cover falls below 11" in t for t in result_cover["reassess_triggers"])

    # --- demand & supply netting, wired end to end. The sample's direct
    # days_of_cover=10 coexists with netting inputs that derive to 7 (see
    # _sample_intake()'s comment) -- the direct field must win, exactly like
    # ranking_probability favours client_probability_estimate over the
    # published base rate.
    ds = result["demand_supply"]
    assert ds["daily_demand_rate"] == 500.0
    assert ds["net_available_cover"] == 3_500.0
    assert ds["days_of_cover_derived"] == 7.0
    assert ds["days_of_cover_used"] == 10           # direct field wins
    assert ds["days_of_cover_is_derived_fallback"] is False
    assert ds["quantity_at_risk"] == 2_000.0
    assert ds["demand_at_risk_value"] == 200_000.0
    assert ds["stockout_date"] is not None
    ds_ledger_field_names = {e["field"] for e in result["ledger"]}
    assert {"daily_demand_rate", "net_available_cover", "days_of_cover_derived",
           "quantity_at_risk", "demand_at_risk_value", "stockout_date"} <= ds_ledger_field_names

    # "coverage after strategy": conditional_loss() already computes
    # days_of_cover + this strategy's days_of_cover_delta internally to
    # derive delay cost -- confirm it survives into ConditionalLoss and into
    # build_decision()'s strategy_rows, not just used and discarded. The
    # sample's two strategies both leave days_of_cover_delta at 0, so use a
    # freshly-built strategy with a nonzero delta to prove the column
    # actually reflects the effect, not just echoing the input.
    plus_five_cover = dataclasses.replace(continue_s, effects=StrategyEffects(days_of_cover_delta=5.0))
    cl_plus_five = conditional_loss(data, plus_five_cover)
    assert cl_plus_five.effective_days_of_cover == 15.0, cl_plus_five.effective_days_of_cover
    assert result["strategies"][0]["coverage_after_strategy"] == 10.0   # Continue: delta 0

    # A client who leaves days_of_cover blank but supplies the netting
    # inputs gets the derived 7 USED, not just displayed -- and it actually
    # feeds the kernel (higher conditional loss for Continue: less cover
    # means more margin at risk during the same 11-day delay).
    no_direct_cover = json.loads(json.dumps(data))
    no_direct_cover["fields"]["days_of_cover"] = None
    result_fallback = build_decision(no_direct_cover)
    ds_fb = result_fallback["demand_supply"]
    assert ds_fb["days_of_cover_is_derived_fallback"] is True
    assert ds_fb["days_of_cover_used"] == 7.0
    dgm_fb = 5_000_000 * 0.12 / 7
    delay_rate_fb = (0.08 / 365) * 5_000_000 + 5_000 + 1.0 * dgm_fb   # stockout=1: 7 <= 11 too
    expected_total_fb = (delay_rate_fb * 11) + expected_inventory_c + expected_transport_c + expected_insurance_c
    continue_row_fb = next(r for r in result_fallback["strategies"] if r["name"] == "Continue")
    assert abs(continue_row_fb["conditional_loss"] - expected_total_fb) < 1e-2, (
        continue_row_fb["conditional_loss"], expected_total_fb)
    assert continue_row_fb["conditional_loss"] > cl_continue.total   # less cover, more at risk
    assert continue_row_fb["coverage_after_strategy"] == 7.0

    # Missing every netting input -> the whole sub-object degrades to Nones,
    # never a fabricated figure, and no netting rows enter the ledger.
    no_netting = json.loads(json.dumps(data))
    for name in ("forecast_quantity", "forecast_window_days", "current_inventory",
                "inbound_confirmed_quantity", "safety_stock"):
        no_netting["fields"][name] = None
    result_no_netting = build_decision(no_netting)
    ds_none = result_no_netting["demand_supply"]
    assert ds_none["daily_demand_rate"] is None and ds_none["days_of_cover_derived"] is None
    assert ds_none["quantity_at_risk"] is None and ds_none["demand_at_risk_value"] is None
    assert ds_none["days_of_cover_used"] == 10   # direct field still present in this variant
    no_netting_ledger_fields = {e["field"] for e in result_no_netting["ledger"]}
    assert "daily_demand_rate" not in no_netting_ledger_fields

    # --- forward-buy cost: hand-worked oracle against the documented
    # formula (financing on the secured fraction + extra carrying cost of
    # holding it forward_buy_early_days sooner), both at the client's own
    # rates -- no forecast of when or how severe a future disruption is.
    fb_effects = StrategyEffects(forward_buy_fraction=0.4, forward_buy_early_days=30)
    expected_financing = 0.08 * 0.4 * 5_000_000 * (30 / 365.0)
    expected_carrying = (0.18 / 365.0) * (5_000_000 / 50_000) * 0.4 * 50_000 * 30
    fb = forward_buy_cost(data, fb_effects)
    assert fb.grade == "DERIVED"
    assert abs(fb.value - (expected_financing + expected_carrying)) < 0.01, fb.value

    # forward_buy_fraction=0 (the default) costs nothing -- every existing
    # strategy that doesn't use this field is unaffected.
    assert forward_buy_cost(data, StrategyEffects()).value == 0.0

    # ABSENT, not a silent zero, when a fraction is requested but the
    # client's own wacc_pct isn't supplied.
    no_wacc = json.loads(json.dumps(data))
    no_wacc["fields"]["wacc_pct"] = None
    assert forward_buy_cost(no_wacc, fb_effects).grade == "ABSENT"

    # end to end: a forward-buy strategy's direct_cost in build_decision()'s
    # own output includes the forward-buy cost (not just the strategy's
    # quoted direct_cost), the ledger records it under a DERIVED grade, and
    # break-even/expected-cost downstream see the combined number -- one
    # consistent figure, not two the reader has to add up themselves.
    fb_data = json.loads(json.dumps(data))
    fb_data["strategies"].append({
        "name": "Forward-buy 40%", "direct_cost": 5_000,
        "effects": {"forward_buy_fraction": 0.4, "forward_buy_early_days": 30,
                   "capacity_restored": 0.4, "war_risk_premium_multiplier": 0.6},
    })
    result_fb = build_decision(fb_data)
    fb_row = next(r for r in result_fb["strategies"] if r["name"] == "Forward-buy 40%")
    assert fb_row["direct_cost"] > 5_000 + expected_financing + expected_carrying - 1, fb_row
    fb_ledger_rows = [e for e in result_fb["ledger"] if e["field"] == "forward_buy_cost[Forward-buy 40%]"]
    assert fb_ledger_rows and fb_ledger_rows[0]["grade"] == "DERIVED"
    # its conditional loss is lower than Continue's -- the risk-reducing
    # effects (capacity_restored/war_risk_premium_multiplier) actually bit,
    # confirming forward-buy composes with them rather than being ignored
    continue_row = next(r for r in result_fb["strategies"] if r["name"] == "Continue")
    assert fb_row["conditional_loss"] < continue_row["conditional_loss"], fb_row

    # cash_impact: real for a forward-buy strategy (the same financing +
    # carrying figure just hand-verified above), explicit ABSENT for every
    # other strategy -- no invented general-case formula.
    assert fb_row["cash_impact_grade"] == "DERIVED"
    assert abs(fb_row["cash_impact"] - (expected_financing + expected_carrying)) < 0.01, fb_row
    assert continue_row["cash_impact"] is None and continue_row["cash_impact_grade"] == "ABSENT"

    print("all checks passed")
    print(f"  conditional loss (Continue)        {cl_continue.total:,.0f}")
    print(f"  conditional loss (Partial reroute) {cl_reroute.total:,.0f}")
    print(f"  break-even p* (Partial reroute)    {be_real.p_star:.1%}")
    print(f"  section 8.3 C/L special case reproduces v1's mitigation_threshold(): "
         f"{be.p_star:.0%} (doc: 16%)")
    print("  degenerate break-even (denominator <= 0) returns None with a reason, never raises")
    print("  quoted-override conditional loss keeps its assembled components auditable")
    print("  reroute_quote prices into transport via capacity_restored, even with no freight-rate pair")
    print("  emergency_replacement_quote prices a flagged strategy's total via a live intake read")
    print("  ledger grades every recorded number; missing fields degrade to ABSENT, never silently")
    print("  same input reproduces the same result, modulo timestamp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

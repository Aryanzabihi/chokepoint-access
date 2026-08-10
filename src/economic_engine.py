"""
economic_engine.py — the TAR Economic & Decision Engine (engine.md, Phase 1 MVP).

Everything else in this src/ folder reduces a client's exposure to one
scalar: alpha, the ratio of what acting early costs against what a closure
costs (client_profile.py, services.py). That is deliberately coarse — it
lets someone answer act-or-wait with three numbers. This module is the
opposite instrument: given a scenario the user actually builds — transport,
insurance, delay, inventory, commodity effect, named mitigation strategies —
it prices each component separately, builds the counterfactual baseline,
and compares strategies by expected total cost. Nothing here replaces
alpha; it is a second, more detailed lens for someone who has real
quotations to put into it.

Scope is exactly engine.md's Phase 1 MVP (see its section 23): baseline
engine, the five disruption-cost components, a basic welfare gap, a basic
decision engine, a basic threshold engine. No market agent, no Monte Carlo
uncertainty engine, no licensed data feeds — the spec defers those.

Probability is never invented here. engine.md section 3.1 is explicit that
the Economic Engine "should not independently invent geopolitical
probabilities unless explicitly configured to do so," and Implementation
Rule 18 says never silently substitute missing data. historical_context()
surfaces the *existing* published base rate (3 of 11 alarm episodes have
preceded a real disruption) as labelled context next to the input — it
never fills the field.

Usage
-----
    python economic_engine.py template --out scenario.json
    python economic_engine.py compute --scenario scenario.json --out result.json --report report.html
    python economic_engine.py compute --scenario scenario.json --source ../data/gpr_monthly.dta
    python economic_engine.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tar_ingest import CORRIDORS  # noqa: E402
from services import point_in_time  # noqa: E402

MODEL_VERSION = "economic-engine-0.1"

# Mirrors the published figures already used in services.py's attestation
# document and threshold-engine.html's track record panel. Duplicated
# rather than imported: services.py embeds them as literal strings inside
# an HTML template, not as named constants. If the underlying evaluation
# changes, both places need updating — same convention already used for
# H/M/F/N_MONTHS, which is duplicated (not shared) across client_profile.py
# and services.py.
ALARM_EPISODES = 11
EPISODES_WITH_DISRUPTION = 3
EPISODE_HIT_RATE_CI = (0.10, 0.57)
H, M, F, N_MONTHS = 15, 41, 70, 484

# engine.md section 5's recommended source hierarchy, strongest first.
SOURCE_HIERARCHY = ["customer_quotation", "licensed", "public", "model_estimate", "proxy_estimate"]


# --------------------------------------------------------------------------
# Data provenance (section 5)
# --------------------------------------------------------------------------

@dataclass
class Provenance:
    value: float
    unit: str
    currency: str
    source: str
    source_type: str
    retrieved_at: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: str = "medium"

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_HIERARCHY:
            raise ValueError(f"source_type {self.source_type!r} not in {SOURCE_HIERARCHY}")


def weakest_source(items: list[Provenance]) -> str:
    """The overall confidence label for a result: as strong as its weakest input.

    Implementation Rule 19: flag low-confidence calculations. A result built
    on five licensed inputs and one proxy estimate is only as trustworthy as
    that one proxy estimate — this makes that fact visible instead of
    averaging it away.
    """
    if not items:
        return "unknown"
    rank = {t: i for i, t in enumerate(SOURCE_HIERARCHY)}
    return max(items, key=lambda p: rank[p.source_type]).source_type


# --------------------------------------------------------------------------
# Input layer (section 4)
# --------------------------------------------------------------------------

@dataclass
class DisruptionScenario:
    corridor: str
    probability: float
    severity: str
    duration_days: float
    capacity_reduction: float
    delay_days: float
    closure_probability: float = 0.0
    recovery_days: float = 0.0

    def __post_init__(self) -> None:
        if self.corridor not in CORRIDORS:
            raise ValueError(f"unknown corridor {self.corridor!r}. Known: {sorted(CORRIDORS)}")
        if not 0 <= self.probability <= 1:
            raise ValueError("probability must be between 0 and 1")


@dataclass
class CargoInputs:
    commodity: str
    quantity: float
    cargo_value: float
    origin: str = ""
    destination: str = ""
    shipment_frequency: str = ""
    inventory_level: float = 0.0
    criticality: str = "normal"
    baseline_commodity_price: float = 0.0


@dataclass
class TransportInputs:
    baseline_freight: float = 0.0
    disrupted_freight: float = 0.0
    alt_route_freight: float = 0.0
    fuel_cost: float = 0.0
    port_charges: float = 0.0
    handling_costs: float = 0.0
    transit_time_days: float = 0.0
    congestion_cost: float = 0.0
    rerouting_premium: float = 0.0


@dataclass
class InsuranceInputs:
    baseline_premium: float = 0.0
    war_risk_premium: float = 0.0
    additional_surcharge: float = 0.0
    deductible: float = 0.0
    coverage_limit: float = 0.0


@dataclass
class EconomicInputs:
    commodity_price: float = 0.0
    price_volatility: float = 0.0
    shortage_cost: float = 0.0
    inventory_holding_cost_rate: float = 0.0
    delay_cost_rate: float = 0.0
    lost_margin: float = 0.0
    substitution_cost: float = 0.0
    discount_rate: float = 0.0


@dataclass
class MitigationStrategy:
    name: str
    kind: str
    direct_cost: float
    residual_loss_estimate: float
    notes: str = ""


@dataclass
class ScenarioOutcome:
    label: str
    probability: float
    conditional_loss: float


# --------------------------------------------------------------------------
# Baseline + disruption cost engines (sections 6-8)
# --------------------------------------------------------------------------

@dataclass
class CostBreakdown:
    """Never one opaque number (section 8) — components stay named and
    separately observable through to the API output and the HTML report."""
    components: dict[str, float]
    total: float = field(init=False)

    def __post_init__(self) -> None:
        self.total = sum(self.components.values())


def baseline_cost(cargo: CargoInputs, transport: TransportInputs,
                   insurance: InsuranceInputs, economic: EconomicInputs) -> CostBreakdown:
    """The counterfactual (section 6): what this shipment would have cost
    with no disruption at all. Kept structurally separate from
    total_disrupted_cost() rather than merged — section 6's rule."""
    return CostBreakdown({
        "transport": (transport.baseline_freight + transport.fuel_cost
                      + transport.port_charges + transport.handling_costs),
        "insurance": insurance.baseline_premium,
        "delay": 0.0,
        "inventory": cargo.inventory_level * economic.inventory_holding_cost_rate,
        "commodity": 0.0,
    })


def transport_cost(t: TransportInputs) -> float:
    """C_transport = freight + fuel + port costs + handling + rerouting premium."""
    return t.disrupted_freight + t.fuel_cost + t.port_charges + t.handling_costs + t.rerouting_premium


def delay_cost(cargo_value: float, daily_exposure_rate: float | None,
               additional_delay_days: float) -> float:
    """C_delay = cargo_value x daily_exposure_rate x additional_delay_days.

    daily_exposure_rate must be supplied by the caller (section 7.2: "must
    be configurable and never silently assumed") — a customer-provided
    rate, a historical company margin, a commodity-specific proxy, or a
    scenario estimate. There is no default here on purpose.
    """
    if daily_exposure_rate is None:
        raise ValueError("daily_exposure_rate must be supplied — see engine.md section 7.2: "
                          "customer-provided rate, historical margin, commodity proxy, or "
                          "scenario estimate. This function will not assume one.")
    return cargo_value * daily_exposure_rate * additional_delay_days


def insurance_cost(i: InsuranceInputs) -> float:
    """C_insurance = baseline premium + war-risk premium + surcharge.

    This is the total disrupted-state premium, not an incremental figure —
    baseline_premium also appears in baseline_cost(), and welfare_gap()'s
    subtraction (disrupted.total - baseline.total) is what actually cancels
    it out of the incremental figure. That is the anti-double-counting
    mechanism section 7.3 asks for: structural, not a instruction to
    remember.
    """
    return i.baseline_premium + i.war_risk_premium + i.additional_surcharge


def inventory_cost(additional_inventory_qty: float, holding_cost_rate: float,
                    additional_days: float) -> float:
    """C_inventory = additional_inventory_quantity x holding_cost_rate x additional_days."""
    return additional_inventory_qty * holding_cost_rate * additional_days


def commodity_effect(affected_quantity: float, market_wide_price_change: float,
                      disruption_attributable_price_change: float) -> float:
    """Only the disruption-attributable share of a price move is priced.

    market_wide_price_change is accepted as an argument — not silently
    dropped — so a caller and a report can see both figures side by side,
    but only disruption_attributable_price_change feeds the total. Passing
    market_wide_price_change into the total would double-count a move that
    was happening anyway (section 7.5).
    """
    return affected_quantity * disruption_attributable_price_change


def total_disrupted_cost(*, transport: TransportInputs, insurance: InsuranceInputs,
                          cargo: CargoInputs, economic: EconomicInputs,
                          scenario: DisruptionScenario,
                          additional_inventory_qty: float = 0.0,
                          market_wide_price_change: float = 0.0,
                          disruption_attributable_price_change: float = 0.0) -> CostBreakdown:
    return CostBreakdown({
        "transport": transport_cost(transport),
        "insurance": insurance_cost(insurance),
        "delay": delay_cost(cargo.cargo_value, economic.delay_cost_rate, scenario.delay_days),
        "inventory": inventory_cost(additional_inventory_qty,
                                     economic.inventory_holding_cost_rate, scenario.delay_days),
        "commodity": commodity_effect(cargo.quantity, market_wide_price_change,
                                       disruption_attributable_price_change),
    })


# --------------------------------------------------------------------------
# Expected loss (section 9)
# --------------------------------------------------------------------------

def expected_loss(scenarios: list[ScenarioOutcome], tol: float = 0.01) -> float:
    total_p = sum(s.probability for s in scenarios)
    if abs(total_p - 1.0) > tol:
        raise ValueError(
            f"scenario probabilities sum to {total_p:.3f}, not 1.0 — the scenario set must "
            f"be exhaustive and mutually exclusive (engine.md section 9). Add a residual/"
            f"'no disruption' scenario or fix the numbers; this will not silently normalize "
            f"them for you.")
    return sum(s.probability * s.conditional_loss for s in scenarios)


# --------------------------------------------------------------------------
# Welfare gap engine (section 10)
# --------------------------------------------------------------------------

def welfare_gap(disrupted: CostBreakdown, baseline: CostBreakdown, *,
                 supply_chain_pass_through: float | None = None,
                 social_multiplier: float | None = None) -> dict:
    private = disrupted.total - baseline.total
    out: dict = {"private": private, "private_label": "Avoidable Economic Loss"}
    if supply_chain_pass_through is not None:
        out["supply_chain"] = private * supply_chain_pass_through
    else:
        out["supply_chain"] = None
        out["supply_chain_note"] = "not computed — no pass-through fraction supplied"
    if social_multiplier is not None:
        out["social"] = private * social_multiplier
    else:
        out["social"] = None
        out["social_note"] = ("not computed — no social multiplier supplied; broader-economy "
                               "estimates need data this engine does not fabricate a default for")
    return out


# --------------------------------------------------------------------------
# Decision engine (section 11)
# --------------------------------------------------------------------------

@dataclass
class Constraints:
    max_delay_days: float | None = None
    max_budget: float | None = None
    min_service_level: float | None = None
    max_disruption_probability: float | None = None


@dataclass
class StrategyResult:
    name: str
    kind: str
    direct_cost: float
    residual_loss: float
    expected_total_cost: float
    eligible: bool
    ineligibility_reason: str | None = None


def compare_strategies(strategies: list[MitigationStrategy], *, scenario: DisruptionScenario,
                        constraints: Constraints | None = None,
                        strategy_delay_days: dict[str, float] | None = None
                        ) -> list[StrategyResult]:
    """Expected Total Cost = Direct Cost + Expected Residual Disruption Loss
    (section 11; implementation-cost and benefit terms fold into direct_cost
    and residual_loss_estimate on the input strategy).

    A strategy that violates a constraint is marked ineligible and stays in
    the output with a reason — it is never silently dropped, so a customer
    can see what was excluded and why rather than wondering why an option
    is missing.
    """
    constraints = constraints or Constraints()
    strategy_delay_days = strategy_delay_days or {}
    out = []
    for s in strategies:
        eligible, reason = True, None
        if constraints.max_budget is not None and s.direct_cost > constraints.max_budget:
            eligible, reason = False, (f"direct cost {s.direct_cost:,.0f} exceeds max budget "
                                        f"{constraints.max_budget:,.0f}")
        delay = strategy_delay_days.get(s.name, scenario.delay_days)
        if eligible and constraints.max_delay_days is not None and delay > constraints.max_delay_days:
            eligible, reason = False, (f"{delay:.0f} days exceeds max acceptable delay "
                                        f"{constraints.max_delay_days:.0f}")
        out.append(StrategyResult(
            name=s.name, kind=s.kind, direct_cost=s.direct_cost,
            residual_loss=s.residual_loss_estimate,
            expected_total_cost=s.direct_cost + s.residual_loss_estimate,
            eligible=eligible, ineligibility_reason=reason))
    return sorted(out, key=lambda r: (not r.eligible, r.expected_total_cost))


# --------------------------------------------------------------------------
# Decision threshold engine (section 12)
# --------------------------------------------------------------------------

def mitigation_threshold(cost_of_mitigation: float, loss_if_disrupted: float) -> float:
    """P* = cost_of_mitigation / loss_if_disrupted."""
    if loss_if_disrupted <= 0:
        raise ValueError("loss_if_disrupted must be positive")
    return cost_of_mitigation / loss_if_disrupted


def threshold_status(current_probability: float, p_star: float) -> str:
    return "MITIGATION_JUSTIFIED" if current_probability > p_star else "NOT_JUSTIFIED"


def threshold_status_range(probability_range: tuple[float, float], p_star: float) -> dict:
    """Section 12: 'thresholds must be reported with uncertainty rather
    than presented as perfectly precise values.' Reporting status at both
    ends of a probability range is the Phase 1 way to satisfy that without
    building the full uncertainty engine section 16 describes (out of scope
    for this MVP pass)."""
    lo, hi = probability_range
    lo_status, hi_status = threshold_status(lo, p_star), threshold_status(hi, p_star)
    return {"low": lo_status, "high": hi_status, "p_star": p_star,
            "probability_range": probability_range, "robust": lo_status == hi_status}


# --------------------------------------------------------------------------
# Historical context — never a substitute for the manual probability input
# --------------------------------------------------------------------------

def historical_context(corridor: str, source: Path | None = None, as_of: str | None = None, *,
                        reading: dict | None = None) -> dict:
    """What the existing TAR engine already knows about this corridor,
    presented as context for the user's own probability estimate — never
    used to fill it in. See the module docstring and engine.md section 3.1.

    Two ways to get the reading: pass `source` (the raw GPR vintage — the
    normal CLI path, calls services.point_in_time() directly), or pass an
    already-built `reading` dict of the same shape and skip the recompute.
    The second path exists for callers with no access to the raw vintage —
    a deployed backend, for instance, where the vintage is deliberately
    never redistributed (see .gitignore). Whoever built that dict is
    responsible for it being current; this function just presents it.
    """
    if corridor not in CORRIDORS:
        raise ValueError(f"unknown corridor {corridor!r}. Known: {sorted(CORRIDORS)}")

    reading_error = None
    if reading is None:
        if source is None:
            raise ValueError("historical_context() needs either source or a pre-built reading")
        try:
            from tar_ingest import load
            df = load(source, None, None)
            resolved_as_of = as_of or df.index[-1].strftime("%Y-%m")
            reading = point_in_time(source, resolved_as_of, corridor)
        except SystemExit as exc:
            reading_error = str(exc)

    return {
        "corridor": corridor,
        "reading": reading,
        "reading_error": reading_error,
        "global_base_rate": {
            "alarm_episodes": ALARM_EPISODES,
            "episodes_with_disruption": EPISODES_WITH_DISRUPTION,
            "hit_rate": round(EPISODES_WITH_DISRUPTION / ALARM_EPISODES, 3),
            "hit_rate_ci_95": EPISODE_HIT_RATE_CI,
            "note": (f"This is a GLOBAL base rate across all corridors, not specific to "
                     f"{corridor}. It is context for your own probability estimate, not a "
                     f"substitute for it — the Economic Engine does not invent geopolitical "
                     f"probabilities (engine.md section 3.1)."),
        },
    }


# --------------------------------------------------------------------------
# Explainability (section 20) + API-shaped output (section 21)
# --------------------------------------------------------------------------

def explain(result: dict) -> list[str]:
    lines = [f"Disruption probability: {result['probability']:.0%}"]
    if result.get("action_threshold") is not None:
        lines.append(f"Mitigation threshold (P*): {result['action_threshold']:.0%}")
    lines.append(f"Expected exposure: {result['currency']} {result['expected_exposure']:,.0f}")
    lines.append(f"Avoidable economic loss: {result['currency']} {result['avoidable_loss']:,.0f}")
    if result.get("mitigation_cost") is not None:
        lines.append(f"Mitigation cost: {result['currency']} {result['mitigation_cost']:,.0f}")
    if result.get("recommended_strategy"):
        lines.append(f"Recommended strategy: {result['recommended_strategy']}")
    if result.get("expected_saving") is not None:
        lines.append(f"Expected saving vs. the costliest eligible alternative: "
                      f"{result['currency']} {result['expected_saving']:,.0f}")
    if result.get("current_status"):
        lines.append(f"Result: {result['current_status'].replace('_', ' ').title()}")
    return lines


def to_result_dict(*, scenario_id: str, currency: str, scenario: DisruptionScenario,
                    baseline: CostBreakdown, disrupted: CostBreakdown,
                    expected_loss_value: float | None, welfare: dict,
                    strategy_results: list[StrategyResult], recommended: StrategyResult | None,
                    mitigation_cost: float | None, action_threshold: float | None,
                    current_status: str | None, confidence: str) -> dict:
    expected_exposure = disrupted.total - baseline.total
    eligible_results = [r for r in strategy_results if r.eligible]
    worst = max(eligible_results, key=lambda r: r.expected_total_cost) if eligible_results else None
    expected_saving = (worst.expected_total_cost - recommended.expected_total_cost
                        if worst and recommended and worst is not recommended else None)

    return {
        "scenario_id": scenario_id,
        "currency": currency,
        "corridor": scenario.corridor,
        "probability": scenario.probability,
        "baseline_cost": round(baseline.total, 2),
        "baseline_breakdown": baseline.components,
        "disrupted_cost": round(disrupted.total, 2),
        "disrupted_breakdown": disrupted.components,
        "expected_exposure": round(expected_exposure, 2),
        "avoidable_loss": round(welfare["private"], 2),
        "welfare_gap": welfare,
        "expected_loss_across_scenarios": expected_loss_value,
        "recommended_strategy": recommended.name if recommended else None,
        "strategy_comparison": [asdict(r) for r in strategy_results],
        "mitigation_cost": mitigation_cost,
        "expected_saving": expected_saving,
        "action_threshold": action_threshold,
        "current_status": current_status,
        "confidence": confidence,
        "calculation_version": MODEL_VERSION,
        "model_version": MODEL_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def compute(data: dict, *, source: Path | None = None,
            historical_reading: dict | None = None) -> dict:
    scenario = DisruptionScenario(**data["disruption"])
    cargo = CargoInputs(**data["cargo"])
    transport = TransportInputs(**data.get("transport", {}))
    insurance = InsuranceInputs(**data.get("insurance", {}))
    economic = EconomicInputs(**data.get("economic", {}))
    ce = data.get("commodity_effect", {})

    baseline = baseline_cost(cargo, transport, insurance, economic)
    disrupted = total_disrupted_cost(
        transport=transport, insurance=insurance, cargo=cargo, economic=economic,
        scenario=scenario, additional_inventory_qty=data.get("additional_inventory_qty", 0.0),
        market_wide_price_change=ce.get("market_wide_price_change", 0.0),
        disruption_attributable_price_change=ce.get("disruption_attributable_price_change", 0.0))

    outcomes = [ScenarioOutcome(**s) for s in data.get("scenarios", [])]
    exp_loss = expected_loss(outcomes) if outcomes else None

    gap = welfare_gap(disrupted, baseline,
                       supply_chain_pass_through=data.get("supply_chain_pass_through"),
                       social_multiplier=data.get("social_multiplier"))

    strategies = [MitigationStrategy(**s) for s in data.get("strategies", [])]
    constraints = Constraints(**data["constraints"]) if data.get("constraints") else None
    strategy_results = (compare_strategies(strategies, scenario=scenario, constraints=constraints)
                         if strategies else [])
    recommended = next((r for r in strategy_results if r.eligible), None)

    mitigation_cost = data.get("mitigation_cost")
    loss_if_disrupted = data.get("loss_if_disrupted")
    p_star = status = None
    if mitigation_cost is not None and loss_if_disrupted:
        p_star = mitigation_threshold(mitigation_cost, loss_if_disrupted)
        status = threshold_status(scenario.probability, p_star)

    provenance = [Provenance(**p) for p in data.get("provenance", [])]
    confidence = weakest_source(provenance) if provenance else "unknown"

    result = to_result_dict(
        scenario_id=data.get("scenario_id", "SCENARIO-UNSPECIFIED"),
        currency=data.get("currency", "EUR"), scenario=scenario, baseline=baseline,
        disrupted=disrupted, expected_loss_value=exp_loss, welfare=gap,
        strategy_results=strategy_results, recommended=recommended,
        mitigation_cost=mitigation_cost, action_threshold=p_star, current_status=status,
        confidence=confidence)

    result["assumptions"] = {
        "delay_cost_rate": economic.delay_cost_rate,
        "additional_inventory_qty": data.get("additional_inventory_qty", 0.0),
        "commodity_effect": ce,
        "supply_chain_pass_through": data.get("supply_chain_pass_through"),
        "social_multiplier": data.get("social_multiplier"),
    }
    result["historical_context"] = (
        historical_context(scenario.corridor, source, reading=historical_reading)
        if (source or historical_reading) else None)
    result["explain"] = explain(result)
    return result


# --------------------------------------------------------------------------
# HTML report (MVP acceptance criterion 13 — export a decision report)
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
ol{margin:0;padding-left:20px} li{margin-bottom:6px}
.note{font-size:12.5px;color:var(--soft);border-left:3px solid var(--rule);
padding-left:12px;margin-top:20px;max-width:74ch}
.ineligible{color:var(--caution)}
"""


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def economic_report_html(result: dict, path: Path) -> None:
    def rows(components: dict[str, float]) -> str:
        return "".join(f"<tr><td>{esc(k.replace('_', ' ').title())}</td>"
                        f"<td class='m'>{v:,.0f}</td></tr>" for k, v in components.items())

    strategy_rows = "".join(
        f"<tr><td>{esc(r['name'])}</td><td class='m'>{r['direct_cost']:,.0f}</td>"
        f"<td class='m'>{r['residual_loss']:,.0f}</td>"
        f"<td class='m'>{r['expected_total_cost']:,.0f}</td>"
        f"<td class=\"{'' if r['eligible'] else 'ineligible'}\">"
        f"{'eligible' if r['eligible'] else 'ineligible: ' + esc(r['ineligibility_reason'] or '')}"
        f"</td></tr>" for r in result["strategy_comparison"])

    explain_items = "".join(f"<li>{esc(x)}</li>" for x in result["explain"])

    ctx = result.get("historical_context")
    ctx_block = ""
    if ctx:
        br = ctx["global_base_rate"]
        ctx_block = (f"<section><h2>Historical context for {esc(ctx['corridor'])}</h2>"
                     f"<p>{esc(br['note'])}</p>"
                     f"<p class='m'>{br['episodes_with_disruption']} of {br['alarm_episodes']} "
                     f"alarm episodes since 1985 preceded a real disruption "
                     f"({br['hit_rate']:.0%}, 95% CI {br['hit_rate_ci_95'][0]:.0%}"
                     f"&ndash;{br['hit_rate_ci_95'][1]:.0%}).</p>"
                     + (f"<p>Current reading: TAR {ctx['reading']['tar']}, "
                        f"{esc(ctx['reading']['band'])}.</p>" if ctx.get("reading") else
                        f"<p class='note'>{esc(ctx.get('reading_error') or 'no current reading')}"
                        f"</p>") + "</section>")

    path.write_text(f"""<!DOCTYPE html>
<meta charset="utf-8"><title>Economic exposure — {esc(result['scenario_id'])}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style>
<div class="w">
<div class="eyebrow">Economic &amp; decision engine &middot; {esc(result['corridor'])}</div>
<h1>{esc(result['scenario_id'])}</h1>
<div class="stats">
<div><b>{esc(result['currency'])} {result['expected_exposure']:,.0f}</b>
<span>expected economic exposure</span></div>
<div><b>{esc(result['currency'])} {result['avoidable_loss']:,.0f}</b>
<span>avoidable economic loss</span></div>
<div><b>{esc(result['recommended_strategy'] or "&mdash;")}</b>
<span>recommended strategy</span></div>
{"<div><b>" + esc(result['current_status'].replace('_',' ')) + "</b><span>threshold status</span></div>" if result.get('current_status') else ""}
</div>

<section><h2>Baseline cost (counterfactual)</h2>
<table><tbody>{rows(result['baseline_breakdown'])}</tbody></table></section>

<section><h2>Disrupted cost, by component</h2>
<table><tbody>{rows(result['disrupted_breakdown'])}</tbody></table></section>

<section><h2>Strategy comparison</h2>
<table><thead><tr><th>Strategy</th><th>Direct cost</th><th>Residual loss</th>
<th>Expected total cost</th><th>Status</th></tr></thead>
<tbody>{strategy_rows}</tbody></table></section>

{ctx_block}

<section><h2>Why</h2><ol>{explain_items}</ol></section>

<p class="note">Model {esc(result['model_version'])}. Computed {esc(result['timestamp'])}.
Confidence: {esc(result['confidence'])} (the weakest data source behind this result).
Phase 1 MVP: no market agent, no Monte Carlo uncertainty ranges, no licensed data feeds
&mdash; see engine.md for what is deferred to later phases.</p>
</div>
""", encoding="utf-8")
    print(f"wrote {path}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def template_scenario() -> dict:
    return {
        "scenario_id": "HORMUZ-2026-001",
        "currency": "EUR",
        "disruption": {
            "corridor": "Strait of Hormuz", "probability": 0.22, "severity": "severe",
            "duration_days": 11, "capacity_reduction": 0.55, "delay_days": 11,
            "closure_probability": 0.03, "recovery_days": 18,
        },
        "cargo": {
            "commodity": "Steel", "quantity": 50000, "cargo_value": 5000000,
            "inventory_level": 0, "baseline_commodity_price": 100,
        },
        "transport": {
            "baseline_freight": 400000, "disrupted_freight": 650000, "fuel_cost": 80000,
            "port_charges": 30000, "handling_costs": 20000, "rerouting_premium": 150000,
            "transit_time_days": 25,
        },
        "insurance": {"baseline_premium": 60000, "war_risk_premium": 340000,
                      "additional_surcharge": 0},
        "economic": {"delay_cost_rate": 0.0012, "inventory_holding_cost_rate": 5,
                     "commodity_price": 100, "price_volatility": 0.1},
        "commodity_effect": {"market_wide_price_change": 2.0,
                             "disruption_attributable_price_change": 0.8},
        "additional_inventory_qty": 0,
        "scenarios": [
            {"label": "No disruption", "probability": 0.60, "conditional_loss": 0},
            {"label": "Partial disruption", "probability": 0.25, "conditional_loss": 1200000},
            {"label": "Severe disruption", "probability": 0.12, "conditional_loss": 4500000},
            {"label": "Closure", "probability": 0.03, "conditional_loss": 15000000},
        ],
        "strategies": [
            {"name": "Continue", "kind": "continue", "direct_cost": 0,
             "residual_loss_estimate": 2800000},
            {"name": "Reroute", "kind": "full_reroute", "direct_cost": 1200000,
             "residual_loss_estimate": 350000},
            {"name": "Extra inventory", "kind": "inventory", "direct_cost": 800000,
             "residual_loss_estimate": 1100000},
            {"name": "Insurance", "kind": "insurance", "direct_cost": 400000,
             "residual_loss_estimate": 2100000},
            {"name": "Partial reroute", "kind": "partial_reroute", "direct_cost": 700000,
             "residual_loss_estimate": 750000},
        ],
        "constraints": None,
        "mitigation_cost": 700000,
        "loss_if_disrupted": 4375000,
        "provenance": [
            {"value": 650000, "unit": "EUR", "currency": "EUR",
             "source": "carrier quote, 2026-08-01", "source_type": "customer_quotation",
             "confidence": "high"},
        ],
    }


def write_template(path: Path) -> None:
    if path.exists():
        sys.exit(f"{path} exists — refusing to overwrite")
    path.write_text(json.dumps(template_scenario(), indent=2), encoding="utf-8")
    print(f"wrote {path}")
    print("  edit it, then: python economic_engine.py compute --scenario "
          f"{path.name} --report report.html")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    t = sub.add_parser("template", help="write an editable example scenario")
    t.add_argument("--out", type=Path, default=Path("scenario.json"))

    c = sub.add_parser("compute", help="compute a result from a scenario file")
    c.add_argument("--scenario", type=Path, required=True)
    c.add_argument("--out", type=Path)
    c.add_argument("--report", type=Path)
    c.add_argument("--source", type=Path, help="GPR vintage, for historical corridor context")

    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if a.cmd == "template":
        write_template(a.out)
        return 0
    if a.cmd == "compute":
        data = json.loads(a.scenario.read_text(encoding="utf-8"))
        result = compute(data, source=a.source)
        text = json.dumps(result, indent=2, default=str)
        print(text)
        if a.out:
            a.out.write_text(text, encoding="utf-8")
            print(f"wrote {a.out}")
        if a.report:
            economic_report_html(result, a.report)
        return 0
    p.error("pass a subcommand (template, compute) or --selftest")
    return 1


# --------------------------------------------------------------------------
# Self-test — asserts against engine.md's own worked examples, so the
# spec's numbers are the test oracle, not numbers this module invented.
# --------------------------------------------------------------------------

def selftest() -> int:
    # section 9: expected-loss table (60/25/12/3% at EUR 0/1.2M/4.5M/15M)
    scenarios = [
        ScenarioOutcome("No disruption", 0.60, 0),
        ScenarioOutcome("Partial disruption", 0.25, 1_200_000),
        ScenarioOutcome("Severe disruption", 0.12, 4_500_000),
        ScenarioOutcome("Closure", 0.03, 15_000_000),
    ]
    el = expected_loss(scenarios)
    assert abs(el - 1_290_000) < 1, el

    bad = [ScenarioOutcome("a", 0.5, 100), ScenarioOutcome("b", 0.3, 200)]
    try:
        expected_loss(bad)
        assert False, "probabilities summing to 0.8 should have raised"
    except ValueError:
        pass

    # section 11: five-strategy comparison — partial reroute wins at EUR 1.45M
    strategies = [
        MitigationStrategy("Continue", "continue", 0, 2_800_000),
        MitigationStrategy("Reroute", "full_reroute", 1_200_000, 350_000),
        MitigationStrategy("Extra inventory", "inventory", 800_000, 1_100_000),
        MitigationStrategy("Insurance", "insurance", 400_000, 2_100_000),
        MitigationStrategy("Partial reroute", "partial_reroute", 700_000, 750_000),
    ]
    scenario = DisruptionScenario(corridor="Strait of Hormuz", probability=0.22,
                                  severity="severe", duration_days=11,
                                  capacity_reduction=0.55, delay_days=11)
    results = compare_strategies(strategies, scenario=scenario)
    best = results[0]
    assert best.name == "Partial reroute", [r.name for r in results]
    assert abs(best.expected_total_cost - 1_450_000) < 1, best.expected_total_cost

    # section 12: 22% current vs a 16% threshold -> justified
    p_star = mitigation_threshold(700_000, 4_375_000)
    assert abs(p_star - 0.16) < 1e-9, p_star
    assert threshold_status(0.22, p_star) == "MITIGATION_JUSTIFIED"
    assert threshold_status(0.10, p_star) == "NOT_JUSTIFIED"
    rng = threshold_status_range((0.10, 0.30), p_star)
    assert rng["robust"] is False, rng   # NOT_JUSTIFIED at 10%, JUSTIFIED at 30%

    # commodity double-counting guard: only the attributable share prices
    assert commodity_effect(50_000, 2.0, 0.0) == 0
    assert abs(commodity_effect(50_000, 2.0, 0.8) - 40_000) < 1e-9

    # delay_cost refuses to assume a rate
    try:
        delay_cost(5_000_000, None, 11)
        assert False, "missing daily_exposure_rate should have raised"
    except ValueError:
        pass

    # welfare gap: supply-chain/social are not fabricated without an explicit multiplier
    baseline = CostBreakdown({"transport": 850_000})
    disrupted = CostBreakdown({"transport": 4_550_000})
    gap = welfare_gap(disrupted, baseline)
    assert abs(gap["private"] - 3_700_000) < 1e-9, gap
    assert gap["supply_chain"] is None and "supply_chain_note" in gap
    assert gap["social"] is None and "social_note" in gap
    gap2 = welfare_gap(disrupted, baseline, supply_chain_pass_through=0.3)
    assert abs(gap2["supply_chain"] - 1_110_000) < 1e-9, gap2

    # an ineligible strategy stays in the output, marked why, never dropped
    constrained = compare_strategies(strategies, scenario=scenario,
                                     constraints=Constraints(max_budget=500_000))
    assert len(constrained) == len(strategies)
    over_budget = {r.name for r in constrained if not r.eligible}
    assert "Reroute" in over_budget and "Continue" not in over_budget
    assert all(r.ineligibility_reason for r in constrained if not r.eligible)

    # provenance rolls up to the weakest source present
    prov = [Provenance(1, "u", "EUR", "carrier quote", "customer_quotation"),
            Provenance(2, "u", "EUR", "market estimate", "proxy_estimate")]
    assert weakest_source(prov) == "proxy_estimate"
    assert weakest_source([]) == "unknown"

    # corridor validation reuses tar_ingest.CORRIDORS rather than a private list
    try:
        DisruptionScenario(corridor="Not A Real Strait", probability=0.1, severity="x",
                           duration_days=1, capacity_reduction=0.1, delay_days=1)
        assert False, "unknown corridor should have been rejected"
    except ValueError:
        pass

    # end-to-end: the template scenario computes and reproduces the same
    # section 9 / 11 / 12 figures through the full compute() orchestrator
    result = compute(template_scenario())
    assert abs(result["expected_loss_across_scenarios"] - 1_290_000) < 1, result
    assert result["recommended_strategy"] == "Partial reroute", result["recommended_strategy"]
    assert result["current_status"] == "MITIGATION_JUSTIFIED", result["current_status"]
    assert result["model_version"] == MODEL_VERSION
    assert result["historical_context"] is None   # no --source given
    assert len(result["explain"]) >= 5

    # a pre-built reading (the backend's path -- no raw vintage available)
    # works the same as the source= path, without touching tar_ingest.load()
    fake_reading = {"tar": 1.68, "band": "Procurement Watch", "regime": "at risk"}
    ctx = historical_context("Strait of Hormuz", reading=fake_reading)
    assert ctx["reading"] == fake_reading
    assert ctx["reading_error"] is None
    result3 = compute(template_scenario(), historical_reading=fake_reading)
    assert result3["historical_context"]["reading"] == fake_reading
    try:
        historical_context("Strait of Hormuz")
        assert False, "no source and no reading should have raised"
    except ValueError:
        pass

    # reproducibility: same input, same version -> identical result (modulo timestamp)
    result2 = compute(template_scenario())
    r1, r2 = dict(result), dict(result2)
    del r1["timestamp"], r2["timestamp"]
    assert r1 == r2, "same input should reproduce the same result"

    print("all checks passed")
    print(f"  section 9  expected loss    {el:,.0f}  (doc: 1,290,000)")
    print(f"  section 11 recommendation   {best.name} at {best.expected_total_cost:,.0f}  "
          f"(doc: Partial reroute, 1,450,000)")
    print(f"  section 12 threshold        P*={p_star:.0%}, status at 22% = "
          f"{threshold_status(0.22, p_star)}")
    print("  double-counting guards hold; ineligible strategies stay visible;")
    print("  welfare gap never fabricates a supply-chain/social multiplier;")
    print("  compute() is reproducible from the same input and model version")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""strategy_decision.py — the backend's point of contact with
src/decision_engine.py (the TAR Decision Engine v2, enginev2.md).

Plays the same role for the v2 decision engine that economic.py plays for
economic_engine.py — exactly one place imports from src/ for this module.

build_decision() needs a "reading" for the corridor — normally built from
the raw GPR vintage via services.point_in_time(), which this deployment
doesn't have (see engine.py's own docstring: the vintage is deliberately
gitignored and never redistributed). So compute_decision() below sources
that reading from engine.current_reading() instead — the same
docs/readings.json-backed function economic.py already uses — and passes it
into decision_engine.build_decision()'s reading= parameter rather than its
gpr_source= parameter.

warrisk.csv / warrisk_jwc.csv, unlike the GPR vintage, ARE committed
(src/warrisk.csv, src/warrisk_jwc.csv — see .gitignore's "keep: these ARE
the asset" section), so they resolve in a deployed checkout with no extra
plumbing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape as _esc

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import chokepoint_profiles  # noqa: E402
import decision_engine  # noqa: E402
import intake  # noqa: E402
from decision_engine import build_decision, decision_brief_html  # noqa: E402
from intake import FIELDS, INCOTERM_GROUPS, fields_by_group, fields_for, template  # noqa: E402

from . import engine  # noqa: E402

WARRISK_PATH = SRC / "warrisk.csv"
JWC_PATH = SRC / "warrisk_jwc.csv"

CORRIDORS = chokepoint_profiles.all_corridors()


# Field labels a template needs to show a human. Every intake field gets
# SOME label (display_label() falls back to auto title-casing the raw
# name), but a handful read badly that way (acronyms, "_pct" suffixes) or
# are genuinely ambiguous -- "war risk" alone doesn't say this is an
# insurance premium, not a general risk score. Centralized here rather
# than left to each template's own title-case filter: war_risk_premium_
# quote used to render three different ways across three templates
# (auto-generated twice, independently, plus one hand-written "War-risk
# insurance" that had already drifted from both) -- one wrong label copied
# is a typo, three independently-drifting ones is a standing bug.
DISPLAY_LABELS: dict[str, str] = {
    "war_risk_premium_quote": "War-Risk Insurance Premium",
    "wacc_pct": "WACC",
    "carrying_cost_pct_pa": "Carrying Cost (% p.a.)",
    "gross_margin_pct": "Gross Margin",
    "contract_transit_time_days": "Contract Transit Time",
    "delay_days_estimate": "Estimated Delay",
    "forecast_window_days": "Forecast Window",
}


def display_label(field_name: str) -> str:
    return DISPLAY_LABELS.get(field_name, field_name.replace("_", " ").title())


def compute_decision(data: dict, *, as_of: str | None = None) -> dict:
    corridor = data.get("corridor")
    reading = None
    if corridor:
        try:
            reading = engine.current_reading(corridor)
        except (FileNotFoundError, ValueError):
            reading = None
    return build_decision(
        data, as_of=as_of, reading=reading,
        warrisk_path=WARRISK_PATH if WARRISK_PATH.exists() else None,
        jwc_path=JWC_PATH if JWC_PATH.exists() else None)


# --------------------------------------------------------------------------
# Stage-aware strategy defaults (ProcurementOrder.stage) — which 3 slots the
# "new decision" form pre-populates, and what each suggests, depends on
# whether a linked order is a pre-order intent, a placed PO, or a shipment
# already moving. `stage=None` (no order linked) reproduces the form's only
# defaults before this existed, byte-for-byte.
# --------------------------------------------------------------------------

_STAGE_STRATEGY_DEFAULTS: dict[str | None, list[dict]] = {
    None: [
        {"name": "Continue", "direct_cost": 0.0, "is_baseline": True, "effects": {}},
        {"name": "Partial reroute", "direct_cost": 700_000.0, "is_baseline": False,
         "effects": {"capacity_restored": 0.4, "war_risk_premium_multiplier": 0.25}},
        {"name": "", "direct_cost": 0.0, "is_baseline": False, "effects": {}},
    ],
    # No capacity_restored/war_risk_premium_multiplier here, on purpose:
    # decision_engine.conditional_loss() only lets capacity_restored scale
    # anything when a disrupted_freight_quote is already on file, which a
    # pre-order realistically never has yet — baking in an effect that
    # would silently compute to zero is exactly the kind of not-really-
    # wired-up mechanism this project holds the line against elsewhere.
    # "Buy now" is a plain cost suggestion, same as how "Partial reroute"
    # itself works as a suggestion above.
    "pre_order": [
        {"name": "Wait", "direct_cost": 0.0, "is_baseline": True, "effects": {}},
        {"name": "Buy now", "direct_cost": 0.0, "is_baseline": False, "effects": {}},
        {"name": "", "direct_cost": 0.0, "is_baseline": False, "effects": {}},
    ],
    # days_of_cover_delta feeds effective_days_of_cover -> stockout_probability()'s
    # step function directly (intake.py), needs only Tier-1 fields — a
    # default that actually does something at this stage.
    "po_placed": [
        {"name": "Continue", "direct_cost": 0.0, "is_baseline": True, "effects": {}},
        {"name": "Increase inventory", "direct_cost": 0.0, "is_baseline": False,
         "effects": {"days_of_cover_delta": 5.0}},
        {"name": "", "direct_cost": 0.0, "is_baseline": False, "effects": {}},
    ],
    # Same proven mechanism as the no-order "Partial reroute" default above
    # (asserted correct in decision_engine.py's own selftest), renamed --
    # a live freight/premium quote is realistic to have by this stage.
    "in_transit": [
        {"name": "Continue", "direct_cost": 0.0, "is_baseline": True, "effects": {}},
        {"name": "Reroute", "direct_cost": 700_000.0, "is_baseline": False,
         "effects": {"capacity_restored": 0.4, "war_risk_premium_multiplier": 0.25}},
        {"name": "", "direct_cost": 0.0, "is_baseline": False, "effects": {}},
    ],
}


_ZERO_EFFECTS = {"delay_days_delta": 0.0, "capacity_restored": 0.0,
                 "war_risk_premium_multiplier": None, "days_of_cover_delta": 0.0}


def default_strategies_for_stage(stage: str | None) -> list[dict]:
    """Returns fresh dicts each call (no shared mutable state), each with a
    complete 4-key effects dict — every entry in _STAGE_STRATEGY_DEFAULTS
    above only specifies the effects it overrides, merged onto
    _ZERO_EFFECTS here, so the strategy-table template (which reads all 4
    keys unconditionally) never hits a missing one. `"delivered"` is not a
    key in the table — by that stage a StrategyDecision isn't offered at
    all (see order_detail.html) — falls back to the no-order defaults like
    any other unrecognised stage would."""
    raw = _STAGE_STRATEGY_DEFAULTS.get(stage, _STAGE_STRATEGY_DEFAULTS[None])
    out = []
    for s in raw:
        effects = dict(_ZERO_EFFECTS)
        effects.update(s["effects"])
        out.append({"name": s["name"], "direct_cost": s["direct_cost"],
                    "is_baseline": s["is_baseline"], "notes": "", "effects": effects})
    return out


_STAGE_FRAMING = {
    "pre_order": "Procurement question: should you secure this requirement now?",
    "po_placed": "Logistics question: should this purchase order continue on plan?",
    "in_transit": "In-transit question: should this shipment be redirected?",
}


def decision_framing_for_stage(stage: str | None) -> str | None:
    """One-line context sentence shown above the Decision section's verdict
    when a StrategyDecision is linked to a ProcurementOrder. None for no
    order linked, and for "delivered" — by then it's not a live logistics
    decision."""
    return _STAGE_FRAMING.get(stage)


# --------------------------------------------------------------------------
# Act/Wait — a derived read on build_decision()'s own output, not a second
# engine. "Recommended" is already the lowest-expected-cost strategy;
# Act/Wait just names whether that pick differs from doing nothing
# differently (the baseline). No new math beyond a per-strategy net-benefit
# subtraction that nothing else in this codebase already computes.
# --------------------------------------------------------------------------

def act_or_wait(result: dict) -> dict:
    """Resolves the baseline the same way decision_engine.build_decision()
    does internally (decision_engine.py's own baseline_candidates[0] else
    strategies[0] fallback) so this never disagrees with the break-even
    numbers already shown on the same page. is_baseline is NOT guaranteed
    to be set on any row -- reachable from the real draft-mode edit UI, not
    just API misuse (check the radio on a blank slot, or blank the row you
    checked, then Recompute) -- so this recovers the name from
    recommended_break_even.baseline when no row is flagged, and only gives
    up (timing=None) when neither source has an answer, rather than ever
    silently mislabeling a real recommendation as WAIT."""
    strategies = result.get("strategies", [])
    by_name = {s["name"]: s for s in strategies}
    recommended_name = result.get("recommended")

    baseline_name = next((s["name"] for s in strategies if s.get("is_baseline")), None)
    if baseline_name is None:
        be = result.get("recommended_break_even")
        if be:
            baseline_name = be.get("baseline")
    baseline = by_name.get(baseline_name)

    if baseline is None:
        return {"timing": None, "recommended_strategy": recommended_name,
                "baseline_strategy": None, "net_benefit": None,
                "cheapest_mitigation": None, "strategies_with_net_benefit": strategies,
                "reason": None}

    timing = "WAIT" if recommended_name == baseline_name else "ACT"
    recommended_row = by_name.get(recommended_name)
    net_benefit = (baseline["expected_cost"] - recommended_row["expected_cost"]
                   if recommended_row else None)

    augmented = []
    cheapest_mitigation = None
    for s in strategies:
        row = dict(s)
        if s["name"] == baseline_name:
            row["net_benefit"] = None
        else:
            row["net_benefit"] = baseline["expected_cost"] - s["expected_cost"]
            # Guards against the raw JSON API path, which -- unlike the
            # form's own _strategies_from_form() -- never rejects a
            # blank-named strategy before it reaches build_decision().
            if s.get("name") and (cheapest_mitigation is None
                                  or s["expected_cost"] < cheapest_mitigation["expected_cost"]):
                cheapest_mitigation = s
        augmented.append(row)

    p = result.get("probability_used") or 0.0
    if timing == "WAIT":
        reason = (f"{baseline_name} costs less in expectation than any available strategy "
                 f"at the {p:.0%} probability used.")
    else:
        reason = (f"{recommended_name} costs {net_benefit:,.0f} less in expectation than "
                 f"{baseline_name} at the {p:.0%} probability used.")

    return {"timing": timing,
            "recommended_strategy": None if timing == "WAIT" else recommended_name,
            "baseline_strategy": baseline_name, "net_benefit": net_benefit,
            "cheapest_mitigation": cheapest_mitigation,
            "strategies_with_net_benefit": augmented, "reason": reason}


# --------------------------------------------------------------------------
# Act/Wait position dial — server-rendered SVG (no client JS anywhere in
# this app). Draws only numbers Section 5/6 already show as text: nothing
# here computes anything build_decision() didn't already compute. A bar
# chart and a break-even curve were tried here too and cut after review --
# both turned out to just re-render numbers already visible elsewhere on
# the page (the strategy table's own "Expected cost" column; the
# probability-used/break-even stat tiles the dial itself already visualizes).
# --------------------------------------------------------------------------

def act_wait_dial_svg(result: dict, act: dict) -> dict | None:
    """A direct geometric port of threshold-engine.html's drawDial() --
    same semicircular annulus, same polar()/annulus()/angle-mapping math,
    same tick/needle/center-verdict layout -- adapted from its 3 zones
    (v1's [lo, hi] band has two edges) to 2 (v2's break-even is a single
    crossing point; there is no third, honest zone to draw). Skips
    entirely in the same case the source dial goes inert for (there "this
    corridor is already disrupted, the timing question doesn't apply";
    here, no resolved baseline to compare against)."""
    be = result.get("recommended_break_even")
    baseline_name = act.get("baseline_strategy")
    if not be or not baseline_name or be.get("p_star") is None:
        return None
    p_star = be["p_star"]
    if not (0.0 <= p_star <= 1.0):
        return None
    probability_used = result.get("probability_used") or 0.0
    other_name = be.get("strategy") or "the alternative"

    import math
    CX, CY, RO, RI = 390, 300, 214, 150

    def ang(v: float) -> float:
        return 180 - min(max(v, 0.0), 1.0) * 180

    def polar(r: float, deg: float) -> tuple[float, float]:
        a = math.radians(deg)
        return (CX + r * math.cos(a), CY - r * math.sin(a))

    def annulus(a0: float, a1: float) -> str:
        x0, y0 = polar(RO, a0)
        x1, y1 = polar(RO, a1)
        x2, y2 = polar(RI, a1)
        x3, y3 = polar(RI, a0)
        big = 1 if abs(a1 - a0) > 180 else 0
        return (f"M{x0:.1f} {y0:.1f} A{RO} {RO} 0 {big} 1 {x1:.1f} {y1:.1f} "
               f"L{x2:.1f} {y2:.1f} A{RI} {RI} 0 {big} 0 {x3:.1f} {y3:.1f} Z")

    parts = []
    zones = [
        (0.0, p_star, "var(--ink-faint)", .14, "WAIT", "cheaper to hold"),
        (p_star, 1.0, "var(--sound)", .30, "ACT", f"{_esc(other_name)} pays"),
    ]
    for lo, hi, color, opacity, head, sub in zones:
        if hi - lo < 0.004:
            continue
        parts.append(f'<path d="{annulus(ang(lo), ang(hi))}" fill="{color}" '
                    f'fill-opacity="{opacity}" stroke="{color}" stroke-opacity=".5" stroke-width="1"/>')
        mid = (ang(lo) + ang(hi)) / 2
        lx, ly = polar(RO + 26, mid)
        anchor = "end" if mid > 105 else "start" if mid < 75 else "middle"
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
                    f'style="font:600 10.5px var(--mono);letter-spacing:.06em" fill="{color}">{head}</text>')
        parts.append(f'<text x="{lx:.1f}" y="{ly + 13:.1f}" text-anchor="{anchor}" '
                    f'style="font:9.5px var(--sans)" fill="var(--ink-faint)">{sub}</text>')

    # the break-even edge, called out with its own number (the one edge
    # v2 actually has, vs the source dial's two)
    x0, y0 = polar(RI - 4, ang(p_star))
    x1, y1 = polar(RO + 6, ang(p_star))
    parts.append(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
                'stroke="var(--sound)" stroke-width="1.4" stroke-opacity=".8"/>')
    tx, ty = polar(RI - 20, ang(p_star))
    parts.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" '
                f'style="font:600 10px var(--mono)" fill="var(--sound)">{p_star:.0%}</text>')

    # ticks every 10%, major numbers every 20%
    for i in range(11):
        v = i * 0.10
        a = ang(v)
        major = i % 2 == 0
        x0, y0 = polar(RO + 2, a)
        x1, y1 = polar(RO + (11 if major else 6), a)
        parts.append(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" '
                    f'stroke="var(--sea-edge)" stroke-width="{1.4 if major else .9}"/>')
        if major:
            tx, ty = polar(RO + 24, a)
            parts.append(f'<text x="{tx:.1f}" y="{ty + 3:.1f}" text-anchor="middle" '
                        f'style="font:9.5px var(--sans)" fill="var(--ink-faint)">{round(v * 100)}%</text>')

    # needle -- static (server-rendered, nothing to animate)
    needle_color = "var(--sound)" if probability_used >= p_star else "var(--ink-faint)"
    na = ang(probability_used)
    nx, ny = polar(RO - 6, na)
    parts.append(f'<line x1="{CX - 26}" y1="{CY}" x2="{nx:.1f}" y2="{ny:.1f}" '
                f'stroke="{needle_color}" stroke-width="2.6" stroke-linecap="round"/>')
    parts.append(f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="5" fill="{needle_color}"/>')
    parts.append(f'<circle cx="{CX}" cy="{CY}" r="11" fill="var(--sea)" stroke="var(--ink)" stroke-width="2.4"/>')

    # the answer, centred under the arc
    verdict = "WAIT" if act["timing"] == "WAIT" else f"ACT — {_esc(act['recommended_strategy'])}"
    verdict_color = "var(--ink-faint)" if act["timing"] == "WAIT" else "var(--sound)"
    sub = "cheaper to hold" if act["timing"] == "WAIT" else f"{_esc(other_name)} pays"
    parts.append(f'<text x="{CX}" y="{CY - 96}" text-anchor="middle" '
                f'style="font:600 34px var(--serif);letter-spacing:-.02em" fill="{verdict_color}">{verdict}</text>')
    parts.append(f'<text x="{CX}" y="{CY - 66}" text-anchor="middle" '
                f'style="font:12px var(--mono)" fill="var(--ink-faint)">{sub}</text>')
    parts.append(f'<text x="{CX}" y="{CY + 44}" text-anchor="middle" '
                f'style="font:13px var(--mono)" fill="var(--ink)">your position: {probability_used:.0%}</text>')

    svg = (f'<svg viewBox="0 0 780 380" role="img" '
          f'aria-label="Act/Wait position: {probability_used:.0%} against a {p_star:.0%} break-even" '
          f'style="width:100%;height:auto">' + "".join(parts) + '</svg>')
    return {"svg": svg, "p_star": p_star, "probability_used": probability_used,
            "other_name": other_name}

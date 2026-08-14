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

"""
intake.py — the TAR Decision Engine v2's intake layer (enginev2.md, L1 + L2).

Everything here is answerable from client-held systems alone: a procurement
team's own PO/contract fields (Tier 1), finance's board-approved rates
(Tier 2), a live quotation (Tier 3), or the client's own disruption spend
history (Tier 4). No field has a default. A field that is missing stays
`None` and is reported as missing, with the department that owns it and
(once decision_engine.py has run) what supplying it would narrow the result
by — never silently defaulted, never silently dropped.

This module never imports episodes.py, economic_engine.py, or
decision_engine.py — it is the leaf of the dependency graph on purpose
(enginev2.md section 3's layer map: intake.py is L1 Intake + the half of L2
Derivation computable from client facts alone). The other half of L2 — the
assembly of a conditional loss per strategy, which needs episode analogues
— lives in decision_engine.py, at the seam where L2 meets L4.

The Incoterm decides which fields are even answerable (section 4.1): a CIF
buyer never sees a war-risk premium, so asking for one is how a tool gets
abandoned in the first five minutes. `fields_for()` filters the field list
by Incoterm before anything is ever asked.

Usage
-----
    python intake.py template --tier 1 --incoterm CIF --out intake.json
    python intake.py validate --intake intake.json
    python intake.py tier4-template --out tier4_ledger.csv
    python intake.py --selftest

`decision_engine.py intake` wraps `template()`/`write_template()` below and
is the CLI entry point a client actually uses end to end; this module's own
CLI exists for standalone testing of the intake layer.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tar_ingest import CORRIDORS, regime  # noqa: E402

MODEL_VERSION = "intake-2.0"

# --------------------------------------------------------------------------
# Incoterm branch (section 4.1)
# --------------------------------------------------------------------------

INCOTERM_GROUPS: dict[str, str] = {
    "EXW": "full_exposure", "FOB": "full_exposure", "FCA": "full_exposure",
    "CIF": "goods_only", "CIP": "goods_only",
    "DAP": "goods_only_seller_transport", "DDP": "goods_only_seller_transport",
    "CFR": "freight_seller_insurance_buyer",
}

# section 4.1's "Engine must not ask for" column, expressed as field names
# (see FIELDS below). Not a hard reject — a client who happens to know a
# forbidden figure anyway may still supply it — this only controls what
# fields_for() asks for and what missing_fields() counts as missing.
FORBIDDEN_FIELDS: dict[str, frozenset[str]] = {
    "full_exposure": frozenset(),
    "goods_only": frozenset({
        "contract_freight_rate", "disrupted_freight_quote",
        "reroute_quote", "war_risk_premium_quote",
    }),
    "goods_only_seller_transport": frozenset({
        "contract_freight_rate", "disrupted_freight_quote",
        "reroute_quote", "war_risk_premium_quote",
    }),
    "freight_seller_insurance_buyer": frozenset({
        "contract_freight_rate", "disrupted_freight_quote", "reroute_quote",
    }),
}

# section 4.1's honesty statement, shown verbatim on the brief for the two
# groups where the buyer's real exposure is materially smaller than a
# freight-based tool would suggest. CFR is a genuine middle case (buyer
# holds the insurance exposure) and is deliberately not in this set — the
# doc's instruction is scoped to "On CIF/CIP/DAP/DDP", not CFR.
_EXPOSURE_NOTE_GROUPS = {"goods_only", "goods_only_seller_transport"}
_EXPOSURE_NOTE = (
    "Under this Incoterm the seller carries the transport and insurance "
    "exposure. Your exposure is late delivery and replacement, and it is "
    "smaller than a freight-based tool would tell you.")


def incoterm_group(incoterm: str) -> str:
    if incoterm not in INCOTERM_GROUPS:
        raise ValueError(f"unknown Incoterm {incoterm!r}. Known: {sorted(INCOTERM_GROUPS)}")
    return INCOTERM_GROUPS[incoterm]


def client_exposure_note(incoterm: str) -> str | None:
    """section 4.1's plain-language honesty statement for CIF/CIP/DAP/DDP.
    None for EXW/FOB/FCA (full exposure, nothing to caveat) and CFR
    (a genuine middle case, not one of the four the doc calls out)."""
    return _EXPOSURE_NOTE if incoterm_group(incoterm) in _EXPOSURE_NOTE_GROUPS else None


# --------------------------------------------------------------------------
# Field schema (section 4.2, 4.3)
# --------------------------------------------------------------------------

#  Display grouping (independent of tier): tier answers "who needs to be
#  asked and how confidently", group answers "what is this fact ABOUT" --
#  the two cut across each other on purpose (days_of_cover is Tier 1 but
#  groups with the Tier-2/3-adjacent "demand_supply" story, penalty_per_day
#  is Tier 2 but groups with "economic_exposure" alongside Tier-3 quotes).
#  GROUPS is the single source of truth for section order and label; a
#  template iterates this, not a hand-written list, so a field can never be
#  silently dropped from the form.
GROUPS: list[tuple[str, str]] = [
    ("exposure_basics", "Exposure basics"),
    ("demand_supply", "Demand & supply"),
    ("disruption_assumptions", "Disruption assumptions"),
    ("economic_exposure", "Economic exposure"),
]
_GROUP_KEYS = frozenset(k for k, _ in GROUPS)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    tier: int                  # 1-3; cumulative (tier 2 = tier-1 fields + tier-2 fields)
    department: str
    system_of_record: str
    unit: str                  # enforces section 4.3: currency / days / fraction_pa / fraction / text / date_iso / count
    group: str = "exposure_basics"   # a key from GROUPS -- what this fact is about, for display


# The 8 Tier-1 fields from section 4.2's table, plus one addition:
# `delay_days_estimate`. The doc's own L_conditional formula (section 5)
# needs a delay-days figure to multiply delay_cost_per_day by, and no
# tier's table lists one — Tier 1 is the only tier where a procurement/
# logistics team plausibly holds a working estimate of it (a broker's or
# shipping schedule's estimate of how long this specific disruption would
# add), so it is added here rather than left with nowhere to come from.
# It can be corroborated or overridden by an EPISODE_ANALOGUE for the
# "delay_days" component once episodes.py's register supports it — see
# episodes.py's `_UNSOURCED_COMPONENTS`, which currently gates that
# component to None.
#
# `quantity` (section 4.2's "Quantity + unit" row) is split into
# `quantity` and `quantity_unit` so each can be validated independently.
FIELDS: list[FieldSpec] = [
    FieldSpec("ship_date", 1, "procurement", "PO / shipping schedule", "date_iso",
             group="exposure_basics"),
    FieldSpec("cargo_value", 1, "procurement", "commercial invoice / PO", "currency",
             group="exposure_basics"),
    FieldSpec("quantity", 1, "procurement", "PO", "count", group="exposure_basics"),
    FieldSpec("quantity_unit", 1, "procurement", "PO", "text", group="exposure_basics"),
    FieldSpec("contract_freight_rate", 1, "procurement", "freight contract / last invoice",
             "currency", group="exposure_basics"),
    FieldSpec("contract_transit_time_days", 1, "procurement", "freight contract", "days",
             group="exposure_basics"),
    FieldSpec("days_of_cover", 1, "logistics", "ERP / WMS", "days", group="demand_supply"),
    FieldSpec("delay_days_estimate", 1, "procurement", "shipping schedule / broker estimate",
             "days", group="disruption_assumptions"),

    # Demand & supply netting (forecast + inventory + inbound + safety
    # stock -> days of cover, stockout date, demand/quantity at risk --
    # see demand_supply_netting() below). All Tier 1: a logistics/demand-
    # planning function holds these without needing another department,
    # same answerability bar as days_of_cover above. inbound_confirmed_
    # quantity is read as INCLUDING this shipment's own `quantity`, not
    # additional to it -- stated here once rather than left ambiguous
    # (section 4.3: reject unit ambiguity at the door).
    FieldSpec("forecast_quantity", 1, "logistics", "demand forecast / S&OP", "count",
             group="demand_supply"),
    FieldSpec("forecast_window_days", 1, "logistics", "demand forecast / S&OP", "days",
             group="demand_supply"),
    FieldSpec("current_inventory", 1, "logistics", "WMS -- on-hand at destination", "count",
             group="demand_supply"),
    FieldSpec("inbound_confirmed_quantity", 1, "logistics",
             "ERP -- confirmed inbound, including this shipment", "count", group="demand_supply"),
    FieldSpec("safety_stock", 1, "logistics", "inventory policy -- minimum buffer", "count",
             group="demand_supply"),

    FieldSpec("wacc_pct", 2, "treasury", "treasury, board-approved", "fraction_pa",
             group="economic_exposure"),
    FieldSpec("carrying_cost_pct_pa", 2, "controlling", "controlling", "fraction_pa",
             group="economic_exposure"),
    FieldSpec("gross_margin_pct", 2, "controlling", "controlling", "fraction",
             group="economic_exposure"),
    FieldSpec("penalty_per_day", 2, "legal", "customer contract / legal", "currency",
             group="economic_exposure"),

    FieldSpec("disrupted_freight_quote", 3, "procurement", "carrier / broker quote", "currency",
             group="economic_exposure"),
    FieldSpec("reroute_quote", 3, "procurement", "carrier / broker quote", "currency",
             group="economic_exposure"),
    FieldSpec("war_risk_premium_quote", 3, "procurement", "underwriter / broker quote", "currency",
             group="economic_exposure"),
    FieldSpec("emergency_replacement_quote", 3, "procurement", "supplier quote", "currency",
             group="economic_exposure"),
]
_FIELDS_BY_NAME = {f.name: f for f in FIELDS}
assert all(f.group in _GROUP_KEYS for f in FIELDS), "every field must use a GROUPS key"


def fields_by_group(fields: list[FieldSpec]) -> list[tuple[str, str, list[FieldSpec]]]:
    """Buckets an already-selected field list (typically fields_for()'s
    output) into GROUPS's 4 display sections, in GROUPS's order. A group
    with none of the supplied fields is omitted rather than shown empty.
    Pure regrouping, decoupled from tier/Incoterm filtering, so it composes
    with whatever selection the caller already made."""
    out = []
    for key, label in GROUPS:
        fs = [f for f in fields if f.group == key]
        if fs:
            out.append((key, label, fs))
    return out


def fields_for(tier: int, incoterm: str | None = None) -> list[FieldSpec]:
    """Fields answerable at this tier (cumulative — tier 2 includes tier 1),
    minus any this Incoterm's group forbids asking for."""
    if tier not in (1, 2, 3):
        raise ValueError(f"tier must be 1, 2 or 3, got {tier!r}")
    forbidden = FORBIDDEN_FIELDS[incoterm_group(incoterm)] if incoterm else frozenset()
    return [f for f in FIELDS if f.tier <= tier and f.name not in forbidden]


def missing_fields(data: dict) -> list[FieldSpec]:
    """Which fields this intake could have answered at its own tier/Incoterm
    but didn't. Forbidden-for-this-Incoterm fields never count as missing —
    they were never asked. Pure: no calculation of impact here, that needs
    the kernel and lives in decision_engine.what_would_sharpen()."""
    tier = data.get("tier", 1)
    incoterm = data.get("incoterm")
    values = data.get("fields", {})
    return [f for f in fields_for(tier, incoterm) if values.get(f.name) is None]


# --------------------------------------------------------------------------
# Derivations (section 5) — the half computable from client facts alone
# --------------------------------------------------------------------------

@dataclass
class DerivedRate:
    """Every derivation prints its own formula (so it can be argued with,
    per section 5) and which of its additive terms were actually supplied.
    An absent term contributes 0 to `value` and is named in
    `components_absent` — never silently folded in as a true zero."""
    value: float
    formula: str
    components_used: list[str] = field(default_factory=list)
    components_absent: list[str] = field(default_factory=list)


def stockout_probability(days_of_cover: float | None, delay_days: float | None) -> float | None:
    """0 while days-of-cover exceeds the delay, 1 after — a step, not a
    curve (section 5): a curve would be invented from nothing. None only
    when an input needed to evaluate the step is itself missing."""
    if days_of_cover is None or delay_days is None:
        return None
    return 0.0 if days_of_cover > delay_days else 1.0


def daily_gross_margin(cargo_value: float | None, gross_margin_pct: float | None,
                        days_of_cover: float | None) -> DerivedRate | None:
    """The margin at risk per day once inventory cover runs out: total
    cargo margin, spread over the client's own cover window. Not spelled
    out as a formula in enginev2.md section 5 (it names `daily_gross_margin`
    as a term without deriving it) — this is this module's own, documented
    interpretation: (cargo_value * gross_margin_pct) / days_of_cover. Unlike
    delay_cost_per_day's additive terms, this is a single multiplicative
    expression with no meaningful partial answer, so it returns None (not
    a DerivedRate with value 0) when any input is missing.
    """
    if cargo_value is None or gross_margin_pct is None or days_of_cover is None:
        return None
    if days_of_cover <= 0:
        return None
    value = (cargo_value * gross_margin_pct) / days_of_cover
    return DerivedRate(
        value=value,
        formula="(cargo_value * gross_margin_pct) / days_of_cover",
        components_used=["cargo_value", "gross_margin_pct", "days_of_cover"],
        components_absent=[])


def delay_cost_per_day(*, wacc_pct: float | None, cargo_value: float | None,
                        penalty_per_day: float | None,
                        days_of_cover: float | None, delay_days: float | None,
                        daily_gross_margin: float | None) -> DerivedRate:
    """delay_cost_per_day = (WACC/365)*cargo_value + penalty_per_day
    + stockout_probability*daily_gross_margin  (section 5). Each additive
    term is independently omittable; an omitted term contributes 0 and is
    named in components_absent, so a result built on zero real inputs is
    visibly different from one built on all three, even though both may
    numerically equal 0.0."""
    used, absent, total = [], [], 0.0

    if wacc_pct is not None and cargo_value is not None:
        total += (wacc_pct / 365.0) * cargo_value
        used.append("wacc_component")
    else:
        absent.append("wacc_component")

    if penalty_per_day is not None:
        total += penalty_per_day
        used.append("penalty_component")
    else:
        absent.append("penalty_component")

    stockout_p = stockout_probability(days_of_cover, delay_days)
    if stockout_p is not None and daily_gross_margin is not None:
        total += stockout_p * daily_gross_margin
        used.append("stockout_component")
    else:
        absent.append("stockout_component")

    return DerivedRate(
        value=total,
        formula="(wacc_pct/365)*cargo_value + penalty_per_day + stockout_probability*daily_gross_margin",
        components_used=used, components_absent=absent)


def holding_per_unit_day(carrying_cost_pct_pa: float | None,
                          unit_value: float | None) -> DerivedRate | None:
    """holding_per_unit_day = (carrying_cost_pct_pa/365) * unit_value.
    None (not a 0-valued DerivedRate) when either input is missing — like
    daily_gross_margin, this is a single multiplicative expression with no
    partial answer."""
    if carrying_cost_pct_pa is None or unit_value is None:
        return None
    value = (carrying_cost_pct_pa / 365.0) * unit_value
    return DerivedRate(
        value=value, formula="(carrying_cost_pct_pa/365) * unit_value",
        components_used=["carrying_cost_pct_pa", "unit_value"], components_absent=[])


def unit_value(cargo_value: float | None, quantity: float | None) -> float | None:
    """cargo_value / quantity. None if either is missing or quantity is 0."""
    if cargo_value is None or quantity is None or quantity == 0:
        return None
    return cargo_value / quantity


# --------------------------------------------------------------------------
# Demand & supply netting — forecast quantity + inventory + inbound +
# safety stock -> days of cover and demand/quantity at risk. Pure
# arithmetic on client-held facts, same family as the derivations above
# (no episode or probability input, no wall-clock dependency — "today" for
# a stockout date is computed one layer up, in decision_engine.py, next to
# procurement_window(), the one other derivation in this codebase that
# needs it; see that function's docstring for why the split).
# --------------------------------------------------------------------------

@dataclass
class DemandSupplyNetting:
    """Every field is None (never 0 or a default) when an input it needs
    is missing, matching daily_gross_margin/holding_per_unit_day above.
    `days_of_cover` here is the DERIVED figure from these five inputs — a
    cross-check against the directly-supplied `days_of_cover` field, not a
    replacement for it; decision_engine.build_decision() decides whether it
    is used as a fallback when the direct field is absent."""
    daily_demand_rate: DerivedRate | None
    net_available_cover: float | None     # current_inventory + inbound_confirmed_quantity
                                          # - safety_stock; can be negative (already below buffer)
    days_of_cover: float | None
    quantity_at_risk: float | None        # units of forecast demand during the delay window
                                          # that available cover would not meet
    demand_at_risk_value: float | None    # quantity_at_risk priced at this shipment's own
                                          # unit_value (cargo_value / quantity); None if that
                                          # unit price isn't derivable


def demand_supply_netting(fields: dict, *, delay_days: float | None) -> DemandSupplyNetting:
    """forecast_quantity/forecast_window_days -> a daily demand rate (a
    period forecast is what a demand-planning function actually holds; a
    raw per-day rate is not asked for directly, same reasoning as every
    other derived rate in this module). current_inventory +
    inbound_confirmed_quantity - safety_stock -> net_available_cover.
    days_of_cover = net_available_cover / daily_demand_rate.
    quantity_at_risk = max(0, daily_demand_rate * delay_days -
    net_available_cover) — the portion of demand during the delay window
    that available cover would not meet, using the same step logic as
    stockout_probability() (a shortfall either exists or it doesn't; a
    probability curve over it would be invented)."""
    forecast_quantity = fields.get("forecast_quantity")
    forecast_window_days = fields.get("forecast_window_days")
    current_inventory = fields.get("current_inventory")
    inbound = fields.get("inbound_confirmed_quantity")
    safety_stock = fields.get("safety_stock")

    daily_demand_rate = None
    if forecast_quantity is not None and forecast_window_days:
        daily_demand_rate = DerivedRate(
            value=forecast_quantity / forecast_window_days,
            formula="forecast_quantity / forecast_window_days",
            components_used=["forecast_quantity", "forecast_window_days"])

    net_available_cover = None
    if current_inventory is not None and inbound is not None and safety_stock is not None:
        net_available_cover = current_inventory + inbound - safety_stock

    days_of_cover = None
    if (daily_demand_rate is not None and daily_demand_rate.value > 0
            and net_available_cover is not None):
        days_of_cover = net_available_cover / daily_demand_rate.value

    quantity_at_risk = None
    if (daily_demand_rate is not None and delay_days is not None
            and net_available_cover is not None):
        quantity_at_risk = max(0.0, daily_demand_rate.value * delay_days - net_available_cover)

    demand_at_risk_value = None
    if quantity_at_risk is not None:
        uv = unit_value(fields.get("cargo_value"), fields.get("quantity"))
        if uv is not None:
            demand_at_risk_value = quantity_at_risk * uv

    return DemandSupplyNetting(daily_demand_rate, net_available_cover, days_of_cover,
                               quantity_at_risk, demand_at_risk_value)


# --------------------------------------------------------------------------
# Tier 4 — the client's own disruption history (section 4.2, 4.3)
# --------------------------------------------------------------------------

# Byte-identical to client_profile.COLUMNS. Duplicated as a literal rather
# than imported — intake.py does not import client_profile.py, matching the
# codebase's existing convention of duplicating small shared constants
# (e.g. H/M/F/N_MONTHS across client_profile.py and services.py) rather
# than cross-importing between sibling analytical modules.
TIER4_COLUMNS = ["month", "corridor", "category", "amount", "transits", "notes"]
MIN_MONTHS_PER_SIDE = 3   # mirrors client_profile.MIN_MONTHS_PER_SIDE


@dataclass
class Tier4Row:
    month: str          # YYYY-MM
    corridor: str
    category: str        # war_risk_premium | reroute | hedge | inventory | other
    amount: float
    transits: float | None = None
    notes: str = ""


def tier4_write_ledger_csv(rows: list[Tier4Row], path: Path) -> None:
    """Writes rows in client_profile.py's exact column order. Downstream,
    `python client_profile.py --spend <path> --source ...` runs on this
    file unmodified — intake.py produces data in the shape client_profile.py
    already reads, it does not import or call client_profile.py itself."""
    if path.exists():
        sys.exit(f"{path} exists — refusing to overwrite a client ledger")
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(TIER4_COLUMNS)
        for r in rows:
            w.writerow([r.month, r.corridor, r.category, r.amount,
                       "" if r.transits is None else r.transits, r.notes])
    print(f"wrote {path}")


def tier4_coverage(rows: list[Tier4Row]) -> dict[str, dict]:
    """Per corridor: how many calm / post-onset months are present so far,
    and how many more are needed before client_profile.analyse() can
    report an alpha for that corridor (MIN_MONTHS_PER_SIDE=3 each side).
    Uses tar_ingest.regime() the same way client_profile.monthly_unit_cost
    does, via each row's own month."""
    import pandas as pd
    by_corridor: dict[str, dict[str, int]] = {}
    for r in rows:
        reg, _, _ = regime(r.corridor, pd.Timestamp(r.month))
        side = "crisis_months" if reg == "in episode" else "calm_months"
        slot = by_corridor.setdefault(r.corridor, {"calm_months": 0, "crisis_months": 0})
        slot[side] += 1
    out: dict[str, dict] = {}
    for corridor, counts in by_corridor.items():
        calm, crisis = counts["calm_months"], counts["crisis_months"]
        needed_calm = max(0, MIN_MONTHS_PER_SIDE - calm)
        needed_crisis = max(0, MIN_MONTHS_PER_SIDE - crisis)
        out[corridor] = {
            "calm_months": calm, "crisis_months": crisis,
            "estimable": needed_calm == 0 and needed_crisis == 0,
            "more_calm_months_needed": needed_calm,
            "more_crisis_months_needed": needed_crisis,
        }
    return out


# --------------------------------------------------------------------------
# Template / validation / CLI
# --------------------------------------------------------------------------

def template(tier: int, incoterm: str | None = None) -> dict:
    values = {f.name: None for f in fields_for(tier, incoterm)}
    return {
        "scenario_id": "SCENARIO-UNSPECIFIED",
        "currency": "EUR",
        "corridor": None,
        "incoterm": incoterm,
        "tier": tier,
        "fields": values,
        "strategies": [],
        "client_probability_estimate": None,
        "probability_range": None,
        "tier4_ledger_path": None,
    }


def write_template(tier: int, incoterm: str | None, path: Path) -> None:
    if path.exists():
        sys.exit(f"{path} exists — refusing to overwrite")
    path.write_text(json.dumps(template(tier, incoterm), indent=2), encoding="utf-8")
    print(f"wrote {path}")
    note = client_exposure_note(incoterm) if incoterm else None
    if note:
        print(f"  note: {note}")


_PCT_UNITS = {"fraction", "fraction_pa"}
_NONNEGATIVE_UNITS = {"currency", "days", "count"}


def validate_intake(data: dict) -> list[str]:
    """Returns problem strings; an empty list means clean. Never fills a
    default and never raises — mirrors client_profile.read_spend()'s
    row-level problem reporting, at the intake-document level."""
    problems: list[str] = []

    corridor = data.get("corridor")
    if not corridor:
        problems.append("corridor is required")
    elif corridor not in CORRIDORS:
        problems.append(f"corridor {corridor!r} not in the registry: {sorted(CORRIDORS)}")

    incoterm = data.get("incoterm")
    if not incoterm:
        problems.append("incoterm is required — it decides which fields are answerable")
    elif incoterm not in INCOTERM_GROUPS:
        problems.append(f"incoterm {incoterm!r} not recognised: {sorted(INCOTERM_GROUPS)}")

    values = data.get("fields", {})
    for name, v in values.items():
        if v is None:
            continue
        spec = _FIELDS_BY_NAME.get(name)
        if spec is None:
            problems.append(f"field {name!r} is not a known intake field")
            continue
        if spec.unit in _PCT_UNITS:
            if not isinstance(v, (int, float)) or not (0 <= v <= 1):
                problems.append(
                    f"{name} must be a fraction between 0 and 1 (e.g. 0.08 for 8% p.a.), "
                    f"got {v!r} — reject unit ambiguity at the door (section 4.3)")
        elif spec.unit in _NONNEGATIVE_UNITS:
            if not isinstance(v, (int, float)) or v < 0:
                problems.append(f"{name} must be a non-negative number, got {v!r}")

    return problems


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    t = sub.add_parser("template", help="write an editable intake template")
    t.add_argument("--tier", type=int, default=1, choices=(1, 2, 3))
    t.add_argument("--incoterm", choices=sorted(INCOTERM_GROUPS))
    t.add_argument("--out", type=Path, default=Path("intake.json"))

    v = sub.add_parser("validate", help="check an intake document")
    v.add_argument("--intake", type=Path, required=True)

    t4 = sub.add_parser("tier4-template", help="write a blank Tier 4 ledger CSV")
    t4.add_argument("--out", type=Path, default=Path("tier4_ledger.csv"))

    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if a.cmd == "template":
        write_template(a.tier, a.incoterm, a.out)
        return 0
    if a.cmd == "validate":
        data = json.loads(a.intake.read_text(encoding="utf-8"))
        problems = validate_intake(data)
        if problems:
            print(f"{len(problems)} problem(s):")
            for pr in problems:
                print(f"  {pr}")
            return 1
        missing = missing_fields(data)
        print("intake is valid")
        if missing:
            print(f"{len(missing)} field(s) not yet answered (not required, will widen ranges):")
            for f in missing:
                print(f"  {f.name} ({f.department}, tier {f.tier})")
        return 0
    if a.cmd == "tier4-template":
        if a.out.exists():
            sys.exit(f"{a.out} exists — refusing to overwrite a client ledger")
        with a.out.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(TIER4_COLUMNS)
            w.writerow(["2023-06", "Bab-el-Mandeb", "war_risk_premium", "18000", "6",
                       "example row — delete before use"])
        print(f"wrote {a.out}")
        return 0
    p.error("pass a subcommand (template, validate, tier4-template) or --selftest")
    return 1


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def selftest() -> int:
    # Incoterm branch: CIF must never be asked for freight/premium/reroute
    # fields, but keeps the delay/stockout/replacement/penalty set.
    cif_fields = {f.name for f in fields_for(3, "CIF")}
    assert "contract_freight_rate" not in cif_fields
    assert "disrupted_freight_quote" not in cif_fields
    assert "reroute_quote" not in cif_fields
    assert "war_risk_premium_quote" not in cif_fields
    assert "emergency_replacement_quote" in cif_fields
    assert "penalty_per_day" in cif_fields
    assert "days_of_cover" in cif_fields
    assert client_exposure_note("CIF") is not None
    assert client_exposure_note("DDP") is not None
    assert client_exposure_note("CFR") is None
    assert client_exposure_note("FOB") is None

    # CFR: freight/reroute forbidden, but war-risk premium (buyer's own
    # insurance exposure) stays askable.
    cfr_fields = {f.name for f in fields_for(3, "CFR")}
    assert "contract_freight_rate" not in cfr_fields
    assert "war_risk_premium_quote" in cfr_fields

    # Full exposure Incoterms: nothing forbidden.
    fob_fields = {f.name for f in fields_for(3, "FOB")}
    assert "war_risk_premium_quote" in fob_fields and "reroute_quote" in fob_fields

    # Tiers are cumulative.
    t1 = {f.name for f in fields_for(1)}
    t2 = {f.name for f in fields_for(2)}
    t3 = {f.name for f in fields_for(3)}
    assert t1 < t2 < t3, (len(t1), len(t2), len(t3))
    # section 4.2's 8 Tier-1 fields (quantity split in two) + 5 demand/supply
    # netting fields (forecast_quantity, forecast_window_days,
    # current_inventory, inbound_confirmed_quantity, safety_stock)
    assert len(t1) == 13, sorted(t1)
    assert len(t2) == 17, sorted(t2)         # + 4 Tier-2 fields
    assert len(t3) == 21, sorted(t3)         # + 4 Tier-3 fields

    # fields_by_group: every field appears in exactly one group, in
    # GROUPS's order, and no group is shown empty.
    grouped = fields_by_group(fields_for(3))
    assert [key for key, _, _ in grouped] == [k for k, _ in GROUPS]
    regrouped_names = [f.name for _, _, fs in grouped for f in fs]
    assert sorted(regrouped_names) == sorted(t3)
    assert len(regrouped_names) == len(set(regrouped_names))   # no field listed twice
    demand_supply_group = next(fs for key, _, fs in grouped if key == "demand_supply")
    assert {f.name for f in demand_supply_group} == {
        "days_of_cover", "forecast_quantity", "forecast_window_days",
        "current_inventory", "inbound_confirmed_quantity", "safety_stock"}

    # Unknown Incoterm / tier are rejected, not silently accepted.
    try:
        fields_for(1, "XYZ")
        assert False, "unknown Incoterm should have raised"
    except ValueError:
        pass
    try:
        fields_for(9)
        assert False, "invalid tier should have raised"
    except ValueError:
        pass

    # missing_fields never counts a forbidden field as missing.
    doc = template(3, "CIF")
    doc["fields"]["cargo_value"] = 5_000_000
    miss = {f.name for f in missing_fields(doc)}
    assert "contract_freight_rate" not in miss   # forbidden for CIF, not "missing"
    assert "cargo_value" not in miss              # supplied
    assert "days_of_cover" in miss                # askable, not yet supplied

    # stockout_probability: a step, not a curve. None when data is missing.
    assert stockout_probability(20, 11) == 0.0     # cover exceeds delay
    assert stockout_probability(10, 11) == 1.0     # cover no longer exceeds delay
    assert stockout_probability(11, 11) == 1.0     # equality falls on the "after" side
    assert stockout_probability(None, 11) is None
    assert stockout_probability(20, None) is None

    # daily_gross_margin: single multiplicative expression, None (not 0)
    # when any input is missing.
    dgm = daily_gross_margin(5_000_000, 0.12, 18)
    assert dgm is not None and abs(dgm.value - (5_000_000 * 0.12 / 18)) < 1e-6
    assert daily_gross_margin(None, 0.12, 18) is None
    assert daily_gross_margin(5_000_000, 0.12, 0) is None

    # delay_cost_per_day: each additive term independently omittable,
    # absent terms contribute 0 and are named, not silently folded in.
    full = delay_cost_per_day(wacc_pct=0.08, cargo_value=5_000_000, penalty_per_day=5_000,
                              days_of_cover=10, delay_days=11, daily_gross_margin=dgm.value)
    expected = (0.08 / 365) * 5_000_000 + 5_000 + 1.0 * dgm.value
    assert abs(full.value - expected) < 1e-6, (full.value, expected)
    assert full.components_absent == []
    assert set(full.components_used) == {"wacc_component", "penalty_component", "stockout_component"}

    partial = delay_cost_per_day(wacc_pct=None, cargo_value=None, penalty_per_day=5_000,
                                 days_of_cover=None, delay_days=None, daily_gross_margin=None)
    assert abs(partial.value - 5_000) < 1e-9
    assert partial.components_absent == ["wacc_component", "stockout_component"]
    assert partial.components_used == ["penalty_component"]

    nothing = delay_cost_per_day(wacc_pct=None, cargo_value=None, penalty_per_day=None,
                                 days_of_cover=None, delay_days=None, daily_gross_margin=None)
    assert nothing.value == 0.0
    assert nothing.components_used == []
    assert len(nothing.components_absent) == 3

    # holding_per_unit_day: None (not 0) when an input is missing.
    hpud = holding_per_unit_day(0.18, 100.0)
    assert abs(hpud.value - (0.18 / 365) * 100.0) < 1e-9
    assert holding_per_unit_day(None, 100.0) is None
    assert holding_per_unit_day(0.18, None) is None

    assert unit_value(5_000_000, 50_000) == 100.0
    assert unit_value(5_000_000, 0) is None
    assert unit_value(None, 50_000) is None

    # demand_supply_netting: forecast quantity + inventory + inbound +
    # safety stock -> days of cover and demand/quantity at risk. Hand-worked
    # oracle: 45,000 units forecast over 90 days = 500/day; 3,000 on hand +
    # 1,500 confirmed inbound - 1,000 safety stock = 3,500 net available;
    # 3,500 / 500 = 7 days of cover; at an 11-day delay, 500*11=5,500 units
    # of demand fall inside the delay window against 3,500 available, so
    # 2,000 units are at risk, priced at this shipment's own unit_value
    # (5,000,000 / 50,000 = 100) = 200,000.
    net = demand_supply_netting(
        {"forecast_quantity": 45_000, "forecast_window_days": 90,
         "current_inventory": 3_000, "inbound_confirmed_quantity": 1_500,
         "safety_stock": 1_000, "cargo_value": 5_000_000, "quantity": 50_000},
        delay_days=11)
    assert net.daily_demand_rate is not None
    assert abs(net.daily_demand_rate.value - 500.0) < 1e-9
    assert net.net_available_cover == 3_500.0
    assert net.days_of_cover == 7.0
    assert net.quantity_at_risk == 2_000.0
    assert net.demand_at_risk_value == 200_000.0

    # Missing forecast_window_days -> daily_demand_rate is None (not 0),
    # which cascades to days_of_cover and quantity_at_risk -- the same "no
    # partial answer" convention as daily_gross_margin/holding_per_unit_day.
    no_window = demand_supply_netting(
        {"forecast_quantity": 45_000, "forecast_window_days": None,
         "current_inventory": 3_000, "inbound_confirmed_quantity": 1_500,
         "safety_stock": 1_000}, delay_days=11)
    assert no_window.daily_demand_rate is None
    assert no_window.days_of_cover is None
    assert no_window.quantity_at_risk is None

    # net_available_cover can be negative (already below safety stock) --
    # a real, reportable fact, not clamped away. days_of_cover follows it
    # negative too (already past cover); quantity_at_risk still clamps at 0
    # on its own axis (a shortfall either exists or it doesn't).
    below_safety = demand_supply_netting(
        {"forecast_quantity": 45_000, "forecast_window_days": 90,
         "current_inventory": 500, "inbound_confirmed_quantity": 0,
         "safety_stock": 1_000}, delay_days=11)
    assert below_safety.net_available_cover == -500.0
    assert below_safety.days_of_cover == -1.0
    assert below_safety.quantity_at_risk == 6_000.0   # 500*11 - (-500)

    # quantity_at_risk clamps at 0 when cover comfortably exceeds the delay
    # window's demand -- the step's other side.
    comfortable = demand_supply_netting(
        {"forecast_quantity": 900, "forecast_window_days": 90,
         "current_inventory": 3_000, "inbound_confirmed_quantity": 0,
         "safety_stock": 0}, delay_days=5)
    assert comfortable.daily_demand_rate.value == 10.0
    assert comfortable.quantity_at_risk == 0.0        # 10*5=50, well under 3,000 available

    # demand_at_risk_value is None (not 0) when this shipment's own
    # unit_value isn't derivable (no cargo_value/quantity supplied) --
    # quantity_at_risk is still reported in units regardless.
    no_unit_value = demand_supply_netting(
        {"forecast_quantity": 45_000, "forecast_window_days": 90,
         "current_inventory": 3_000, "inbound_confirmed_quantity": 1_500,
         "safety_stock": 1_000}, delay_days=11)
    assert no_unit_value.quantity_at_risk == 2_000.0
    assert no_unit_value.demand_at_risk_value is None

    # validate_intake: unit-ambiguity rejection (an integer percentage typed
    # where a fraction is expected must be caught, not silently accepted).
    bad = template(2, "FOB")
    bad["corridor"] = "Strait of Hormuz"
    bad["fields"]["wacc_pct"] = 8   # should be 0.08
    probs = validate_intake(bad)
    assert any("wacc_pct" in p for p in probs), probs

    good = template(2, "FOB")
    good["corridor"] = "Strait of Hormuz"
    good["fields"]["wacc_pct"] = 0.08
    assert validate_intake(good) == []

    # Unknown corridor / Incoterm are rejected, not silently accepted.
    unknown_corridor = template(1, "FOB")
    unknown_corridor["corridor"] = "Not A Real Strait"
    assert any("corridor" in p for p in validate_intake(unknown_corridor))

    missing_incoterm = template(1)
    missing_incoterm["corridor"] = "Strait of Hormuz"
    assert any("incoterm" in p for p in validate_intake(missing_incoterm))

    # Tier 4: coverage counting reuses tar_ingest.regime() exactly the way
    # client_profile.monthly_unit_cost does, and the CSV is byte-identical
    # to client_profile.COLUMNS.
    from tar_ingest import ONSETS
    assert TIER4_COLUMNS == ["month", "corridor", "category", "amount", "transits", "notes"]
    onset = ONSETS["Bab-el-Mandeb"][0]   # "2023-11"
    import pandas as pd
    calm_month = (pd.Timestamp(onset) - pd.DateOffset(months=6)).strftime("%Y-%m")
    crisis_month = (pd.Timestamp(onset) + pd.DateOffset(months=1)).strftime("%Y-%m")
    rows = ([Tier4Row(calm_month, "Bab-el-Mandeb", "war_risk_premium", 200.0)] * 3
            + [Tier4Row(crisis_month, "Bab-el-Mandeb", "war_risk_premium", 9000.0)] * 3)
    cov = tier4_coverage(rows)
    assert cov["Bab-el-Mandeb"]["calm_months"] == 3
    assert cov["Bab-el-Mandeb"]["crisis_months"] == 3
    assert cov["Bab-el-Mandeb"]["estimable"] is True

    short = tier4_coverage(rows[:4])   # 3 calm, 1 crisis
    assert short["Bab-el-Mandeb"]["estimable"] is False
    assert short["Bab-el-Mandeb"]["more_crisis_months_needed"] == 2

    print("all checks passed")
    print(f"  Tier 1/2/3 field counts: {len(t1)}/{len(t2)}/{len(t3)}")
    print("  CIF/DDP intake never asks for war-risk premium; CFR keeps it")
    print("  stockout_probability is a step, not a curve; missing inputs -> None")
    print("  delay_cost_per_day's terms are independently omittable, never silently defaulted")
    print("  fields_by_group covers every field exactly once, in GROUPS's order")
    print("  demand_supply_netting: forecast/inventory/inbound/safety stock -> "
         "7 days cover, 2,000 units at risk (200,000) on the worked example")
    print("  Tier 4 coverage counting matches client_profile.py's MIN_MONTHS_PER_SIDE=3 rule")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

@dataclass(frozen=True)
class FieldSpec:
    name: str
    tier: int                  # 1-3; cumulative (tier 2 = tier-1 fields + tier-2 fields)
    department: str
    system_of_record: str
    unit: str                  # enforces section 4.3: currency / days / fraction_pa / fraction / text / date_iso / count


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
    FieldSpec("ship_date", 1, "procurement", "PO / shipping schedule", "date_iso"),
    FieldSpec("cargo_value", 1, "procurement", "commercial invoice / PO", "currency"),
    FieldSpec("quantity", 1, "procurement", "PO", "count"),
    FieldSpec("quantity_unit", 1, "procurement", "PO", "text"),
    FieldSpec("contract_freight_rate", 1, "procurement", "freight contract / last invoice", "currency"),
    FieldSpec("contract_transit_time_days", 1, "procurement", "freight contract", "days"),
    FieldSpec("days_of_cover", 1, "logistics", "ERP / WMS", "days"),
    FieldSpec("delay_days_estimate", 1, "procurement", "shipping schedule / broker estimate", "days"),

    FieldSpec("wacc_pct", 2, "treasury", "treasury, board-approved", "fraction_pa"),
    FieldSpec("carrying_cost_pct_pa", 2, "controlling", "controlling", "fraction_pa"),
    FieldSpec("gross_margin_pct", 2, "controlling", "controlling", "fraction"),
    FieldSpec("penalty_per_day", 2, "legal", "customer contract / legal", "currency"),

    FieldSpec("disrupted_freight_quote", 3, "procurement", "carrier / broker quote", "currency"),
    FieldSpec("reroute_quote", 3, "procurement", "carrier / broker quote", "currency"),
    FieldSpec("war_risk_premium_quote", 3, "procurement", "underwriter / broker quote", "currency"),
    FieldSpec("emergency_replacement_quote", 3, "procurement", "supplier quote", "currency"),
]
_FIELDS_BY_NAME = {f.name: f for f in FIELDS}


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
    assert len(t1) == 8, sorted(t1)          # section 4.2's 8 Tier-1 fields (quantity split in two)
    assert len(t2) == 12, sorted(t2)         # + 4 Tier-2 fields
    assert len(t3) == 16, sorted(t3)         # + 4 Tier-3 fields

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
    print("  Tier 4 coverage counting matches client_profile.py's MIN_MONTHS_PER_SIDE=3 rule")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

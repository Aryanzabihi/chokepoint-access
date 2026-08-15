"""routers/strategy_decisions.py — the TAR Decision Engine v2 (enginev2.md),
wired into the app. Mirrors routers/economic.py's structure exactly; see
that file's docstring for why the dashboard form asks for a labeled subset
of fields rather than a raw JSON blob.

intake.py's field schema is tiered and unit-typed rather than a fixed
dataclass shape, so the form is built by iterating intake.FIELDS directly
(grouped by tier) instead of hand-listing each field like economic's form
does — same principle (ask only for what the engine actually reads), driven
generically off the schema since it's already declarative. A field's unit
determines its input type: fraction/fraction_pa render as a %, currency/
days/count as a plain number, text/date_iso as text/date. Per intake.py's
own design, a field outside the client's chosen Incoterm is not forbidden to
supply, only not required — so every field is always shown; forbidding is
enforced in what the engine chooses to ask for again, not in this form.

Strategies get 3 fixed slots (mirrors economic's fixed _STRATEGY_DEFS
rather than dynamic add/remove-row JS). A blank name means an unused slot.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.datastructures import FormData
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from .. import crud
from ..db import get_session
from ..deps import current_principal, current_user
from ..models import User
from ..strategy_decision import (
    CORRIDORS, FIELDS, INCOTERM_GROUPS, act_or_wait, act_wait_dial_svg, compute_decision,
    decision_brief_html, decision_framing_for_stage, default_strategies_for_stage, display_label,
    fields_by_group, intake, template,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.globals["display_label"] = display_label

_STRATEGY_SLOT_COUNT = 3   # fixed slots (mirrors economic's fixed _STRATEGY_DEFS rather than
                          # dynamic add/remove-row JS) -- every stage's default set in
                          # strategy_decision.default_strategies_for_stage() has exactly this many

# The Tier-3 "live market data" fields (workflow.md step 5) -- the only
# fields the recalculate loop ("go to market -> reassess with TAR") ever
# overrides. Everything else about the decision (strategies, probability,
# every other field) stays exactly as originally entered.
_QUOTE_FIELDS = ("disrupted_freight_quote", "reroute_quote", "war_risk_premium_quote",
                 "emergency_replacement_quote")


def _num(form: FormData, key: str, default: float | None = None) -> float | None:
    v = form.get(key)
    if v is None or v == "":
        return default
    return float(v)  # type: ignore[arg-type]


def _pct(form: FormData, key: str, default: float | None = None) -> float | None:
    v = _num(form, key)
    return default if v is None else v / 100


def _text(form: FormData, key: str, default: str | None = None) -> str | None:
    v = form.get(key)
    return v if v not in (None, "") else default  # type: ignore[return-value]


_PCT_UNITS = {"fraction_pa", "fraction"}
_TEXT_UNITS = {"text", "date_iso"}


def _field_value(form: FormData, spec) -> object:
    key = f"field_{spec.name}"
    if spec.unit in _PCT_UNITS:
        return _pct(form, key)
    if spec.unit in _TEXT_UNITS:
        return _text(form, key)
    return _num(form, key)


def _strategies_from_form(form: FormData) -> list[dict]:
    """Parses the strategy_{i}_* fields a strategy-editing form posts
    (whether that's the fixed 3-slot table this used to be inline on the
    Input page, or the draft-mode cards on the detail page now) into
    intake.py's strategy shape. A blank name means an unused slot, same
    convention either way."""
    baseline_idx = int(form.get("baseline_strategy") or 0)  # type: ignore[arg-type]
    strategies = []
    for i in range(_STRATEGY_SLOT_COUNT):
        name = form.get(f"strategy_{i}_name")
        if not name:
            continue
        strategies.append({
            "name": name,
            "direct_cost": _num(form, f"strategy_{i}_direct_cost", 0.0),
            "effects": {
                "delay_days_delta": _num(form, f"strategy_{i}_delay_days_delta", 0.0),
                "capacity_restored": _pct(form, f"strategy_{i}_capacity_restored", 0.0),
                "war_risk_premium_multiplier": _pct(form, f"strategy_{i}_war_risk_premium_multiplier"),
                "days_of_cover_delta": _num(form, f"strategy_{i}_days_of_cover_delta", 0.0),
            },
            "is_baseline": i == baseline_idx,
            "notes": form.get(f"strategy_{i}_notes") or "",
        })
    return strategies


# A single, effect-free baseline -- just enough to satisfy build_decision()'s
# ">= 1 strategy" requirement for the Input -> TAR Exposure step, where no
# real strategy has been chosen yet. Confirmed by reading decision_engine.py:
# result["exposure"]/["demand_supply"]/["reading"]/["base_rate"]/["procurement_window"]
# and the ABSENT ledger/missing-fields entries are all computed from
# intake_data["fields"] alone, before the per-strategy loop ever runs --
# this placeholder never reaches the user, only exposure-stage figures do.
_PLACEHOLDER_STRATEGY = [{"name": "Continue", "direct_cost": 0.0, "is_baseline": True,
                         "effects": {}, "notes": ""}]


def _form_to_intake(form: FormData, *, strategies: list[dict] | None = None) -> dict:
    fields = {spec.name: _field_value(form, spec) for spec in FIELDS}
    data = {
        "scenario_id": form.get("scenario_id") or "SCENARIO-UNSPECIFIED",
        "corridor": form.get("corridor") or "",
        "incoterm": form.get("incoterm") or None,
        "tier": int(form.get("tier") or 1),  # type: ignore[arg-type]
        "fields": fields,
        "strategies": strategies if strategies is not None else _strategies_from_form(form),
        "client_probability_estimate": _pct(form, "client_probability_estimate"),
        "probability_range": None,
        "tier4_ledger_path": None,
    }
    lo, hi = _pct(form, "probability_range_low"), _pct(form, "probability_range_high")
    if lo is not None and hi is not None:
        data["probability_range"] = [lo, hi]
    return data


def _carry_forward_fields(data: dict) -> dict[str, object]:
    """Flattens an intake dict back into the exact POST field names
    _form_to_intake() reads, for hidden-field round-tripping from Input to
    TAR Exposure to the final Analyze step (mirrors decision_wizard.py's
    own proven hidden-field pattern, just for the ~21 intake fields instead
    of 5). Fraction-unit fields convert back to the percentage form the
    visible inputs use -- the one place this conversion happens, so a
    later step can never drift out of sync with what field_input() shows."""
    carry: dict[str, object] = {
        "scenario_id": data.get("scenario_id") or "",
        "corridor": data.get("corridor") or "",
        "incoterm": data.get("incoterm") or "",
        "tier": data.get("tier") or 1,
    }
    for spec in FIELDS:
        v = data.get("fields", {}).get(spec.name)
        if v is None:
            continue
        carry[f"field_{spec.name}"] = v * 100 if spec.unit in _PCT_UNITS else v
    if data.get("client_probability_estimate") is not None:
        carry["client_probability_estimate"] = data["client_probability_estimate"] * 100
    if data.get("probability_range"):
        lo, hi = data["probability_range"]
        carry["probability_range_low"] = lo * 100
        carry["probability_range_high"] = hi * 100
    return carry


_DEMAND_SUPPLY_FIELDS = ("forecast_quantity", "forecast_window_days", "current_inventory",
                        "inbound_confirmed_quantity", "safety_stock")

# Every field in intake.py's "exposure_basics" group has a same-named
# attribute directly on ProcurementOrder -- the whole reason that group can
# collapse into a read-only order recap instead of a data-entry section
# when a decision is linked to an order (never ask the user twice for
# something the order already has on file).
_ORDER_SOURCED_FIELDS = ("ship_date", "cargo_value", "quantity", "quantity_unit",
                        "contract_freight_rate", "contract_transit_time_days")


def _order_field_values(order) -> dict[str, object]:
    """Only the ones actually present on the order -- a field the order
    itself lacks falls through to the template's own "Not provided" /
    fallback-input path, same missing-is-reported-not-guessed principle as
    everywhere else in this form."""
    if order is None:
        return {}
    return {name: v for name in _ORDER_SOURCED_FIELDS
            if (v := getattr(order, name)) is not None}


_FINANCIAL_DEFAULT_FIELDS = (("wacc_pct", "default_wacc_pct"),
                            ("carrying_cost_pct_pa", "default_carrying_cost_pct_pa"),
                            ("gross_margin_pct", "default_gross_margin_pct"),
                            ("penalty_per_day", "default_penalty_per_day"))


def _template_context(order=None, demand_supply: dict | None = None, user=None) -> dict:
    # template(3): every field gets a None placeholder regardless of tier,
    # so the form always has a key to look up (fields left above the
    # client's chosen tier just stay blank -- see intake.py, supplying one
    # anyway is never forbidden, only not required).
    # No "strategies" key here anymore -- Input no longer shows a strategy
    # table at all (see the plan this was rebuilt from: strategies are
    # chosen by the engine at the Analyze step, not constructed by hand on
    # this page).
    t = template(3)
    if order is not None:
        # Pre-fill from the linked order -- a deliberate improvement over
        # exposure_id linking below, which pre-fills nothing at all today.
        # scenario_id too: an order already has its own identity (SKU +
        # order number) -- asking for a second, separate name on top of it
        # is redundant, and worse, easy to leave at the literal fallback
        # string "SCENARIO-UNSPECIFIED". No order linked still means no
        # identity to borrow, so that path keeps asking.
        t["scenario_id"] = f"{order.sku} #{order.id}"
        t["corridor"] = order.corridor
        t["incoterm"] = order.incoterm
        t["fields"]["quantity"] = order.quantity
        t["fields"]["quantity_unit"] = order.quantity_unit
        t["fields"]["cargo_value"] = order.cargo_value
        t["fields"]["contract_freight_rate"] = order.contract_freight_rate
        t["fields"]["contract_transit_time_days"] = order.contract_transit_time_days
        t["fields"]["ship_date"] = order.ship_date
    if demand_supply:
        # Carried forward from the New Decision wizard's demand & supply
        # step (decision_wizard.py) as query params, not persisted on the
        # order itself -- these five fields conceptually belong to a
        # reusable DemandPlan this project has deliberately deferred (see
        # memory); carrying them as transient wizard state avoids
        # duplicating that future table ahead of time. They end up
        # persisted exactly once, inside this StrategyDecision's own
        # intake blob, same as if the client had typed them in directly.
        for name in _DEMAND_SUPPLY_FIELDS:
            if demand_supply.get(name) is not None:
                t["fields"][name] = demand_supply[name]
    if user is not None:
        # Settings' financial-assumption defaults (Batch D) -- a last-resort
        # fill only, since neither order nor demand_supply above ever touch
        # these 4 fields: still None here unless the user actually blanked
        # a default back out, or the field was never asked for at all.
        for field_name, attr_name in _FINANCIAL_DEFAULT_FIELDS:
            default_value = getattr(user, attr_name)
            if default_value is not None and t["fields"].get(field_name) is None:
                t["fields"][field_name] = default_value
    return t


# --------------------------------------------------------------- dashboard ---

# Sitemap's "Decisions -> Active/Monitoring/History" (workflow.md), mapped onto
# the status values that already exist rather than a new field: Active is
# still-open decision-making (draft/approved), Monitoring is a decision
# that's live and whose assumptions are worth watching (executed), History
# is closed out (rejected). "all" is the default -- additive, not a
# replacement for the unfiltered view, so nothing already linking here
# silently starts hiding rows.
_VIEW_STATUSES = {"active": ("draft", "approved"), "monitoring": ("executed",),
                  "history": ("rejected",)}


@router.get("/strategy-decisions", response_class=HTMLResponse)
def list_page(request: Request, user: User = Depends(current_user),
              session: Session = Depends(get_session), view: str = "all"):
    if view != "all" and view not in _VIEW_STATUSES:
        raise HTTPException(422, "view must be one of: all, active, monitoring, history")
    decisions = crud.list_strategy_decisions(session, user)
    if view != "all":
        decisions = [d for d in decisions if d.status in _VIEW_STATUSES[view]]
    rows = [{"id": d.id, "scenario_id": d.scenario_id, "corridor": d.corridor,
             "created_at": d.created_at, "status": d.status,
             "recommended": json.loads(d.result_json).get("recommended")}
            for d in decisions]
    return templates.TemplateResponse(request, "strategy_decision_list.html",
        {"user": user, "decisions": rows, "view": view})


@router.get("/strategy-decisions/new", response_class=HTMLResponse)
def new_page(request: Request, user: User = Depends(current_user),
             session: Session = Depends(get_session), exposure_id: int | None = None,
             order_id: int | None = None, decision_id: int | None = None,
             forecast_quantity: float | None = None,
             forecast_window_days: float | None = None, current_inventory: float | None = None,
             inbound_confirmed_quantity: float | None = None, safety_stock: float | None = None):
    linked_exposure = crud.get_exposure_owned(session, user, exposure_id) if exposure_id else None
    linked_order = crud.get_procurement_order_owned(session, user, order_id) if order_id else None
    if decision_id is not None:
        # "Modify": re-open Input pre-filled from the decision's OWN saved
        # input, not re-derived from the linked order -- fixes a real gap
        # the old Modify link had (it silently dropped whatever the analyst
        # had actually typed, since it only ever knew the order).
        row = crud.get_strategy_decision_owned(session, user, decision_id)
        if row is None:
            raise HTTPException(404, "decision not found")
        t = json.loads(row.input_json)
        t.setdefault("currency", "EUR")
        # The Modify link itself only ever carries decision_id (not
        # order_id) -- without this, a decision that WAS built from an
        # order would silently lose its order recap on Modify, even though
        # row.order_id still points right at it.
        if linked_order is None and row.order_id is not None:
            linked_order = crud.get_procurement_order_owned(session, user, row.order_id)
    else:
        demand_supply = {"forecast_quantity": forecast_quantity,
                         "forecast_window_days": forecast_window_days,
                         "current_inventory": current_inventory,
                         "inbound_confirmed_quantity": inbound_confirmed_quantity,
                         "safety_stock": safety_stock}
        t = _template_context(linked_order, demand_supply, user)
    return templates.TemplateResponse(request, "strategy_decision_new.html",
        {"user": user, "t": t, "corridors": sorted(CORRIDORS),
         "incoterms": sorted(INCOTERM_GROUPS), "field_groups": fields_by_group(FIELDS),
         "error": None, "exposure_id": linked_exposure.id if linked_exposure else None,
         "linked_exposure": linked_exposure,
         "order_id": linked_order.id if linked_order else None, "linked_order": linked_order,
         "order_field_values": _order_field_values(linked_order), "decision_id": decision_id})


@router.post("/strategy-decisions", response_class=HTMLResponse)
async def compute_exposure_page(request: Request, exposure_id: int | None = Form(None),
                                 order_id: int | None = Form(None),
                                 decision_id: int | None = Form(None),
                                 user: User = Depends(current_user),
                                 session: Session = Depends(get_session)):
    """Input -> TAR Exposure. Computes with a single placeholder strategy
    (see _PLACEHOLDER_STRATEGY) and renders the Exposure template directly
    -- no redirect, nothing persisted yet -- exactly the "POST renders the
    next template" pattern decision_wizard.py already established. Nothing
    is inserted into the database until /strategy-decisions/analyze."""
    form = await request.form()
    data = _form_to_intake(form, strategies=_PLACEHOLDER_STRATEGY)
    order = crud.get_procurement_order_owned(session, user, order_id) if order_id else None
    try:
        result = compute_decision(data)
    except (KeyError, ValueError, TypeError) as exc:
        t = dict(data)
        t.setdefault("currency", "EUR")
        return templates.TemplateResponse(request, "strategy_decision_new.html",
            {"user": user, "t": t, "corridors": sorted(CORRIDORS),
             "incoterms": sorted(INCOTERM_GROUPS), "field_groups": fields_by_group(FIELDS),
             "error": f"{type(exc).__name__}: {exc}", "exposure_id": exposure_id,
             "linked_exposure": None, "order_id": order.id if order else None,
             "linked_order": order, "order_field_values": _order_field_values(order),
             "decision_id": decision_id}, status_code=422)
    missing = intake.missing_fields(data)
    carry = _carry_forward_fields(data)
    if exposure_id is not None:
        carry["exposure_id"] = exposure_id
    if order_id is not None:
        carry["order_id"] = order_id
    if decision_id is not None:
        carry["decision_id"] = decision_id
    return templates.TemplateResponse(request, "strategy_decision_exposure.html",
        {"user": user, "result": result, "input": data, "missing_fields": missing,
         "carry": carry, "linked_order": order, "field_groups": fields_by_group(FIELDS)})


@router.post("/strategy-decisions/analyze")
async def analyze_page(request: Request, exposure_id: int | None = Form(None),
                        order_id: int | None = Form(None), decision_id: int | None = Form(None),
                        user: User = Depends(current_user), session: Session = Depends(get_session)):
    """TAR Exposure -> TAR Analysis. Recomputes with the engine's real,
    stage-appropriate strategy set and persists for the first and only
    time in this whole flow -- everything from here on (the detail page
    while status == "draft", Approve/Reject/Execute/Recalculate) already
    exists and needs no changes. A carried decision_id means this run
    started as a "Modify" of that decision -- link the new row back to it
    the same way the recalculate loop already links its own new rows, so
    the decision record has real provenance either way."""
    form = await request.form()
    order = crud.get_procurement_order_owned(session, user, order_id) if order_id else None
    data = _form_to_intake(form, strategies=default_strategies_for_stage(
        order.stage if order else None))
    try:
        result = compute_decision(data)
    except (KeyError, ValueError, TypeError) as exc:
        # Exposure-stage fields were already validated once with the same
        # values; this branch is realistically unreachable (only the
        # strategy set changed, and default_strategies_for_stage() always
        # returns a well-formed list), but fail loudly rather than silently
        # if it ever is.
        raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc
    exposure = crud.get_exposure_owned(session, user, exposure_id) if exposure_id else None
    previous_decision = crud.get_strategy_decision_owned(session, user, decision_id) \
        if decision_id else None
    client_id = order.client_id if order else (exposure.client_id if exposure else None)
    linked_exposure_id = order.exposure_id if order else (exposure.id if exposure else None)
    row = crud.create_strategy_decision(
        session, user, scenario_id=data.get("scenario_id", "SCENARIO-UNSPECIFIED"),
        corridor=result["corridor"], input_data=data, result=result,
        client_id=client_id, exposure_id=linked_exposure_id,
        order_id=order.id if order else None,
        previous_decision_id=previous_decision.id if previous_decision else None)
    return RedirectResponse(f"/strategy-decisions/{row.id}", status_code=303)


@router.post("/strategy-decisions/{decision_id}/recompute")
async def recompute_page(decision_id: int, request: Request, user: User = Depends(current_user),
                          session: Session = Depends(get_session)):
    """Editing strategy cards on the detail page while status == "draft"
    ("TAR Analysis"). Only the strategies change -- every other field stays
    exactly what was already validated at Input -- and the row is updated
    in place, not re-inserted (see crud.update_strategy_decision_draft)."""
    row = crud.get_strategy_decision_owned(session, user, decision_id)
    if row is None:
        raise HTTPException(404, "decision not found")
    if row.status != "draft":
        raise HTTPException(409, "only a draft decision can be recomputed")
    form = await request.form()
    input_data = json.loads(row.input_json)
    input_data["strategies"] = _strategies_from_form(form)
    try:
        result = compute_decision(input_data)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc
    crud.update_strategy_decision_draft(session, user, row, input_data=input_data, result=result)
    return RedirectResponse(f"/strategy-decisions/{row.id}", status_code=303)


@router.get("/strategy-decisions/{decision_id}", response_class=HTMLResponse)
def detail_page(decision_id: int, request: Request, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    row = crud.get_strategy_decision_owned(session, user, decision_id)
    if row is None:
        raise HTTPException(404, "decision not found")
    result = json.loads(row.result_json)
    input_data = json.loads(row.input_json)
    # Same grade-tally decision_brief_html() uses for its own LEDGER line --
    # computed here rather than duplicated as Jinja aggregation logic.
    ledger_by_grade: dict[str, int] = {}
    for e in result["ledger"]:
        ledger_by_grade[e["grade"]] = ledger_by_grade.get(e["grade"], 0) + 1
    subscribed = crud.is_subscribed_strategy_decision(session, user, row.id)
    linked_order = crud.get_procurement_order_owned(session, user, row.order_id) \
        if row.order_id else None
    previous_decision = None
    previous_recommended = None
    recommendation_changed = None
    if row.previous_decision_id:
        previous_decision = crud.get_strategy_decision_owned(session, user, row.previous_decision_id)
        if previous_decision is not None:
            previous_recommended = json.loads(previous_decision.result_json).get("recommended")
            recommendation_changed = previous_recommended != result.get("recommended")
    # "TAR Analysis": while a decision is still a draft, section 5 renders
    # the strategies as editable cards (posting to /recompute) instead of
    # a read-only comparison -- padded to the fixed 3 slots so the cards
    # match what Input's old strategy table used to offer, blank slots
    # included. Once status leaves "draft" this is simply unused.
    edit_strategies = None
    if row.status == "draft":
        edit_strategies = list(input_data.get("strategies", []))
        blank_effects = {"delay_days_delta": 0.0, "capacity_restored": 0.0,
                         "war_risk_premium_multiplier": None, "days_of_cover_delta": 0.0}
        while len(edit_strategies) < _STRATEGY_SLOT_COUNT:
            edit_strategies.append({"name": "", "direct_cost": 0.0, "is_baseline": False,
                                    "notes": "", "effects": dict(blank_effects)})
    act = act_or_wait(result)
    return templates.TemplateResponse(request, "strategy_decision_detail.html",
        {"user": user, "row": row, "result": result, "input": input_data,
         "field_groups": fields_by_group(FIELDS),
         "ledger_by_grade": sorted(ledger_by_grade.items()), "subscribed": subscribed,
         "linked_order": linked_order,
         "decision_framing": decision_framing_for_stage(linked_order.stage if linked_order else None),
         "previous_decision": previous_decision, "previous_recommended": previous_recommended,
         "recommendation_changed": recommendation_changed, "edit_strategies": edit_strategies,
         "act": act, "dial": act_wait_dial_svg(result, act)})


@router.post("/strategy-decisions/{decision_id}/subscribe")
def subscribe_page(decision_id: int, user: User = Depends(current_user),
                    session: Session = Depends(get_session)):
    row = crud.get_strategy_decision_owned(session, user, decision_id)
    if row is None:
        raise HTTPException(404, "decision not found")
    crud.toggle_strategy_decision_subscription(session, user, row.id)
    return RedirectResponse(f"/strategy-decisions/{row.id}", status_code=303)


@router.post("/strategy-decisions/{decision_id}/approve")
def approve_page(decision_id: int, user: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    row = crud.get_strategy_decision_owned(session, user, decision_id)
    if row is None:
        raise HTTPException(404, "decision not found")
    crud.approve_strategy_decision(session, user, row)
    return RedirectResponse(f"/strategy-decisions/{row.id}", status_code=303)


@router.post("/strategy-decisions/{decision_id}/reject")
def reject_page(decision_id: int, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    row = crud.get_strategy_decision_owned(session, user, decision_id)
    if row is None:
        raise HTTPException(404, "decision not found")
    crud.reject_strategy_decision(session, user, row)
    return RedirectResponse(f"/strategy-decisions/{row.id}", status_code=303)


@router.post("/strategy-decisions/{decision_id}/execute")
def execute_page(decision_id: int, user: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    row = crud.get_strategy_decision_owned(session, user, decision_id)
    if row is None:
        raise HTTPException(404, "decision not found")
    crud.execute_strategy_decision(session, user, row)
    return RedirectResponse(f"/strategy-decisions/{row.id}", status_code=303)


@router.get("/strategy-decisions/{decision_id}/recalculate", response_class=HTMLResponse)
def recalculate_page(decision_id: int, request: Request, user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
    row = crud.get_strategy_decision_owned(session, user, decision_id)
    if row is None:
        raise HTTPException(404, "decision not found")
    current_fields = json.loads(row.input_json).get("fields", {})
    current_quotes = {name: current_fields.get(name) for name in _QUOTE_FIELDS}
    return templates.TemplateResponse(request, "strategy_decision_recalculate.html",
        {"user": user, "row": row, "current_quotes": current_quotes, "error": None})


@router.post("/strategy-decisions/{decision_id}/recalculate")
async def recalculate_submit(decision_id: int, request: Request, user: User = Depends(current_user),
                             session: Session = Depends(get_session)):
    """workflow.md's "go to market -> reassess with TAR" loop: plug in real
    quotes, recompute the SAME decision (same corridor, same strategies,
    same everything else), see whether the recommendation survives contact
    with the market. Never touches anything but the 4 Tier-3 quote fields —
    the new decision is a genuine recalculation, not a fresh one."""
    row = crud.get_strategy_decision_owned(session, user, decision_id)
    if row is None:
        raise HTTPException(404, "decision not found")
    form = await request.form()
    new_input = json.loads(row.input_json)
    for name in _QUOTE_FIELDS:
        v = _num(form, f"field_{name}")
        if v is not None:
            new_input["fields"][name] = v
    try:
        result = compute_decision(new_input)
    except (KeyError, ValueError, TypeError) as exc:
        current_quotes = {name: new_input.get("fields", {}).get(name) for name in _QUOTE_FIELDS}
        return templates.TemplateResponse(request, "strategy_decision_recalculate.html",
            {"user": user, "row": row, "current_quotes": current_quotes,
             "error": f"{type(exc).__name__}: {exc}"}, status_code=422)
    new_row = crud.create_strategy_decision(
        session, user, scenario_id=f"{row.scenario_id}-RECALC", corridor=result["corridor"],
        input_data=new_input, result=result, client_id=row.client_id,
        exposure_id=row.exposure_id, order_id=row.order_id, previous_decision_id=row.id)
    return RedirectResponse(f"/strategy-decisions/{new_row.id}", status_code=303)


@router.get("/strategy-decisions/{decision_id}/report", response_class=HTMLResponse)
def report_page(decision_id: int, request: Request, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    row = crud.get_strategy_decision_owned(session, user, decision_id)
    if row is None:
        raise HTTPException(404, "decision not found")
    result = json.loads(row.result_json)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.html"
        decision_brief_html(result, out)
        return HTMLResponse(out.read_text(encoding="utf-8"))


# ------------------------------------------------------------------- API ---

@router.get("/api/v1/strategy-decisions")
def api_list(user: User = Depends(current_principal), session: Session = Depends(get_session)):
    return [{"id": d.id, "scenario_id": d.scenario_id, "corridor": d.corridor,
             "created_at": d.created_at.isoformat()}
            for d in crud.list_strategy_decisions(session, user)]


@router.get("/api/v1/strategy-decisions/template")
def api_template(user: User = Depends(current_principal)):
    return template(3)  # every field across all 3 tiers, same as the dashboard form


@router.post("/api/v1/strategy-decisions", status_code=201)
def api_create(data: dict, user: User = Depends(current_principal),
               session: Session = Depends(get_session)):
    if data.get("corridor") not in CORRIDORS:
        raise HTTPException(422, f"unknown or missing corridor. Known: {sorted(CORRIDORS)}")
    try:
        result = compute_decision(data)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc
    row = crud.create_strategy_decision(
        session, user, scenario_id=data.get("scenario_id", "SCENARIO-UNSPECIFIED"),
        corridor=result["corridor"], input_data=data, result=result)
    return {"id": row.id, "result": result}


@router.get("/api/v1/strategy-decisions/{decision_id}")
def api_detail(decision_id: int, user: User = Depends(current_principal),
               session: Session = Depends(get_session)):
    row = crud.get_strategy_decision_owned(session, user, decision_id)
    if row is None:
        raise HTTPException(404, "decision not found")
    return {"id": row.id, "scenario_id": row.scenario_id, "corridor": row.corridor,
            "input": json.loads(row.input_json), "result": json.loads(row.result_json)}

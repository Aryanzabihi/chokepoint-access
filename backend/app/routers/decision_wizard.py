"""routers/decision_wizard.py — the "+ New Decision" guided entry point
(workflow.md steps 1-4: What are you deciding? / Demand & Supply /
Procurement & Order / hand off into TAR Exposure & Analysis).

This does NOT reimplement TAR Exposure or TAR Analysis (workflow.md steps
5-9) -- those are exactly strategy_decisions.py's existing "new decision"
form and detail page, already pre-fill-aware and stage-aware for a linked
ProcurementOrder (see strategy_decision.default_strategies_for_stage()).
This module's whole job is the two steps *before* that page: turn "I need
to purchase" / "shipment in transit" into a real ProcurementOrder (via
crud.create_procurement_order), then redirect into
/strategy-decisions/new?order_id=... . "Existing order" needs no new code
at all -- it's just a link to /orders, whose own detail page already has a
"build a strategy decision from this order" CTA.

Same conventions as every other router here: plain HTML forms (no fetch/
JS), state carried between the two steps as hidden fields in the posted
form (this app has no server-side wizard-state store, and doesn't need
one) rather than a session object.

The five demand & supply fields (forecast_quantity, forecast_window_days,
current_inventory, inbound_confirmed_quantity, safety_stock) are
deliberately NOT persisted on ProcurementOrder -- they conceptually belong
to a reusable DemandPlan this project has deferred (see memory). They're
carried forward as query params into strategy_decisions.new_page() instead,
landing exactly once, inside the eventual StrategyDecision's own intake
blob -- see that router's _template_context() docstring.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from .. import crud
from ..db import get_session
from ..deps import current_user
from ..models import User
from ..strategy_decision import CORRIDORS, INCOTERM_GROUPS, intake

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

_DS_FIELDS = ("forecast_quantity", "forecast_window_days", "current_inventory",
             "inbound_confirmed_quantity", "safety_stock")


def _num(form, key: str) -> float | None:
    v = form.get(key)
    return None if v in (None, "") else float(v)


def _text(form, key: str) -> str | None:
    v = form.get(key)
    return v if v not in (None, "") else None


def _additional_requirement(fields: dict) -> float | None:
    """max(0, forecast_quantity - net_available_cover), reusing intake.py's
    own tested arithmetic (net_available_cover = current_inventory +
    inbound_confirmed_quantity - safety_stock) rather than recomputing it —
    delay_days is unknown this early in the wizard (no disruption
    assumptions collected yet), so quantity_at_risk/demand_at_risk_value
    on the returned object are always None here; only net_available_cover
    is used."""
    forecast_quantity = fields.get("forecast_quantity")
    netting = intake.demand_supply_netting(fields, delay_days=None)
    if forecast_quantity is None or netting.net_available_cover is None:
        return None
    return max(0.0, forecast_quantity - netting.net_available_cover)


@router.get("/decisions/new", response_class=HTMLResponse)
def start_page(request: Request, user: User = Depends(current_user)):
    return templates.TemplateResponse(request, "decision_wizard_start.html", {"user": user})


@router.get("/decisions/new/demand-supply", response_class=HTMLResponse)
def demand_supply_page(request: Request, user: User = Depends(current_user), stage: str = "pre_order"):
    if stage not in ("pre_order", "in_transit"):
        raise HTTPException(422, "stage must be pre_order or in_transit")
    return templates.TemplateResponse(request, "decision_wizard_demand_supply.html",
        {"user": user, "stage": stage, "fields": {}})


@router.post("/decisions/new/demand-supply", response_class=HTMLResponse)
async def demand_supply_submit(request: Request, user: User = Depends(current_user)):
    form = await request.form()
    stage = form.get("stage") or "pre_order"
    ds_fields = {name: _num(form, name) for name in _DS_FIELDS}
    sku = _text(form, "sku") or ""
    quantity_unit = _text(form, "quantity_unit") or ""
    additional_requirement = _additional_requirement(ds_fields)
    return templates.TemplateResponse(request, "decision_wizard_order.html",
        {"user": user, "stage": stage, "ds_fields": ds_fields, "sku": sku,
         "quantity_unit": quantity_unit, "additional_requirement": additional_requirement,
         "corridors": sorted(CORRIDORS), "incoterms": sorted(INCOTERM_GROUPS), "error": None})


@router.post("/decisions/new/order")
async def order_submit(request: Request, user: User = Depends(current_user),
                        session: Session = Depends(get_session)):
    form = await request.form()
    stage = form.get("stage") or "pre_order"
    ds_fields = {name: _num(form, f"ds_{name}") for name in _DS_FIELDS}
    corridor = form.get("corridor") or ""
    quantity = _num(form, "quantity")
    if corridor not in CORRIDORS or quantity is None:
        return templates.TemplateResponse(request, "decision_wizard_order.html",
            {"user": user, "stage": stage, "ds_fields": ds_fields,
             "sku": form.get("sku") or "", "quantity_unit": form.get("quantity_unit") or "",
             "additional_requirement": quantity,
             "corridors": sorted(CORRIDORS), "incoterms": sorted(INCOTERM_GROUPS),
             "error": "A known corridor and a quantity are both required."}, status_code=422)

    order = crud.create_procurement_order(
        session, user, corridor=corridor, stage=stage, sku=form.get("sku") or "",
        quantity=quantity, quantity_unit=form.get("quantity_unit") or "",
        cargo_value=_num(form, "cargo_value"), incoterm=_text(form, "incoterm"),
        supplier=_text(form, "supplier"), currency=form.get("currency") or "EUR",
        origin=_text(form, "origin"), destination=_text(form, "destination"),
        supplier_lead_time_days=_num(form, "supplier_lead_time_days"),
        alternative_supplier=_text(form, "alternative_supplier"))

    query_params = {"order_id": order.id}
    query_params.update({k: v for k, v in ds_fields.items() if v is not None})
    return RedirectResponse(f"/strategy-decisions/new?{urlencode(query_params)}", status_code=303)

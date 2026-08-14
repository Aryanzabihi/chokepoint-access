"""routers/orders.py — ProcurementOrder: the pre-order / PO-placed /
in-transit / delivered lifecycle a client's own procurement requirement
moves through, independent of any one computed StrategyDecision. Own
router file, matching the strategy_decisions.py/economic.py precedent
(each carries both its dashboard HTML pages and its own /api/v1/X JSON
routes in one file) rather than folding into dashboard.py, which owns
Client/Exposure/home/api-keys specifically.

Plain HTML forms, not fetch()/JSON, same as every other dashboard page in
this app. `stage` is the one field in this whole app that's genuinely
mutated in place on an existing row (see ProcurementOrder's own docstring
in models.py) rather than becoming a new row -- POST /orders/{id}/advance-
stage is that one mutating action.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from .. import crud
from ..db import get_session
from ..deps import current_principal, current_user
from ..models import User
from ..strategy_decision import CORRIDORS, INCOTERM_GROUPS

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

STAGES = ("pre_order", "po_placed", "in_transit", "delivered")
STAGE_LABELS = {"pre_order": "Pre-order", "po_placed": "PO placed",
               "in_transit": "In transit", "delivered": "Delivered"}


def _num(form, key: str) -> float | None:
    v = form.get(key)
    return None if v in (None, "") else float(v)


def _text(form, key: str) -> str | None:
    v = form.get(key)
    return v if v not in (None, "") else None


# --------------------------------------------------------------- dashboard ---

@router.get("/orders", response_class=HTMLResponse)
def list_page(request: Request, user: User = Depends(current_user),
              session: Session = Depends(get_session)):
    orders = crud.list_procurement_orders_for_user(session, user)
    return templates.TemplateResponse(request, "orders_list.html",
        {"user": user, "orders": orders})


@router.get("/orders/new", response_class=HTMLResponse)
def new_page(request: Request, user: User = Depends(current_user),
             session: Session = Depends(get_session), exposure_id: int | None = None):
    linked_exposure = crud.get_exposure_owned(session, user, exposure_id) if exposure_id else None
    return templates.TemplateResponse(request, "orders_new.html",
        {"user": user, "corridors": sorted(CORRIDORS), "incoterms": sorted(INCOTERM_GROUPS),
         "stages": STAGES, "stage_labels": STAGE_LABELS, "error": None,
         "exposure_id": linked_exposure.id if linked_exposure else None,
         "linked_exposure": linked_exposure})


@router.post("/orders")
async def create_page(request: Request, exposure_id: int | None = Form(None),
                       user: User = Depends(current_user), session: Session = Depends(get_session)):
    form = await request.form()
    exposure = crud.get_exposure_owned(session, user, exposure_id) if exposure_id else None
    corridor = form.get("corridor") or ""
    if corridor not in CORRIDORS:
        return templates.TemplateResponse(request, "orders_new.html",
            {"user": user, "corridors": sorted(CORRIDORS), "incoterms": sorted(INCOTERM_GROUPS),
             "stages": STAGES, "stage_labels": STAGE_LABELS,
             "error": f"unknown or missing corridor. Known: {sorted(CORRIDORS)}",
             "exposure_id": exposure_id, "linked_exposure": exposure}, status_code=422)
    order = crud.create_procurement_order(
        session, user, corridor=corridor, stage=form.get("stage") or "pre_order",
        sku=form.get("sku") or "", quantity=_num(form, "quantity") or 0.0,
        quantity_unit=form.get("quantity_unit") or "", cargo_value=_num(form, "cargo_value"),
        incoterm=_text(form, "incoterm"), supplier=_text(form, "supplier"),
        currency=form.get("currency") or "EUR",
        client_id=exposure.client_id if exposure else None,
        exposure_id=exposure.id if exposure else None)
    return RedirectResponse(f"/orders/{order.id}", status_code=303)


@router.get("/orders/{order_id}", response_class=HTMLResponse)
def detail_page(order_id: int, request: Request, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    order = crud.get_procurement_order_owned(session, user, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    strategy_decisions = [
        {"row": d, "recommended": json.loads(d.result_json).get("recommended")}
        for d in crud.list_strategy_decisions_for_order(session, order.id)]
    return templates.TemplateResponse(request, "order_detail.html",
        {"user": user, "order": order, "stages": STAGES, "stage_labels": STAGE_LABELS,
         "strategy_decisions": strategy_decisions})


@router.post("/orders/{order_id}/advance-stage")
async def advance_stage_page(order_id: int, request: Request, user: User = Depends(current_user),
                              session: Session = Depends(get_session)):
    order = crud.get_procurement_order_owned(session, user, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    form = await request.form()
    new_stage = form.get("new_stage") or order.stage
    if new_stage not in STAGES:
        raise HTTPException(422, f"unknown stage {new_stage!r}. Known: {STAGES}")
    crud.advance_procurement_order_stage(
        session, user, order, new_stage,
        po_number=_text(form, "po_number"), unit_price=_num(form, "unit_price"),
        ship_date=_text(form, "ship_date"),
        contract_transit_time_days=_num(form, "contract_transit_time_days"),
        contract_freight_rate=_num(form, "contract_freight_rate"))
    return RedirectResponse(f"/orders/{order.id}", status_code=303)


# ------------------------------------------------------------------- API ---

@router.get("/api/v1/orders")
def api_list(user: User = Depends(current_principal), session: Session = Depends(get_session)):
    return [{"id": o.id, "corridor": o.corridor, "stage": o.stage, "sku": o.sku,
             "quantity": o.quantity, "created_at": o.created_at.isoformat()}
            for o in crud.list_procurement_orders_for_user(session, user)]


@router.get("/api/v1/orders/template")
def api_template():
    return {"corridor": None, "stage": "pre_order", "sku": None, "quantity": None,
            "quantity_unit": None, "cargo_value": None, "incoterm": None, "supplier": None,
            "currency": "EUR", "client_id": None, "exposure_id": None}


@router.post("/api/v1/orders", status_code=201)
def api_create(data: dict, user: User = Depends(current_principal),
               session: Session = Depends(get_session)):
    if data.get("corridor") not in CORRIDORS:
        raise HTTPException(422, f"unknown or missing corridor. Known: {sorted(CORRIDORS)}")
    order = crud.create_procurement_order(
        session, user, corridor=data["corridor"], stage=data.get("stage") or "pre_order",
        sku=data.get("sku") or "", quantity=data.get("quantity") or 0.0,
        quantity_unit=data.get("quantity_unit") or "", cargo_value=data.get("cargo_value"),
        incoterm=data.get("incoterm"), supplier=data.get("supplier"),
        currency=data.get("currency") or "EUR", client_id=data.get("client_id"),
        exposure_id=data.get("exposure_id"))
    return {"id": order.id, "corridor": order.corridor, "stage": order.stage}


@router.get("/api/v1/orders/{order_id}")
def api_detail(order_id: int, user: User = Depends(current_principal),
               session: Session = Depends(get_session)):
    order = crud.get_procurement_order_owned(session, user, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    return order.model_dump()

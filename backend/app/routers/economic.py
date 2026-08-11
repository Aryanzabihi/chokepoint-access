"""routers/economic.py — the economic & decision engine (engine.md), wired
into the app. The dashboard's "new scenario" page is a labeled form, one
field per input economic_engine.py's compute() actually reads — a handful
of dataclass fields (severity, duration_days, capacity_reduction,
closure_probability, recovery_days, transit_time_days, commodity_price,
price_volatility, plus insurance's deductible/coverage_limit) are never
read by any cost function or surfaced in the output, so they're left out
of the form entirely rather than asking for numbers that would silently do
nothing. The JSON API underneath (/api/v1/economic-scenarios) still takes
the full scenario.json shape, unabridged, for scripted/external callers.
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
from ..economic import CORRIDORS, compute_scenario, economic_report_html, template_scenario
from ..models import User

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

# Fixed labels/kinds for the two form tables — the engine's own template()
# uses this same order, so a fresh form and a re-rendered error both line up.
_SCENARIO_LABELS = ["No disruption", "Partial disruption", "Severe disruption", "Closure"]
_STRATEGY_DEFS = [
    ("Continue", "continue"), ("Reroute", "full_reroute"),
    ("Extra inventory", "inventory"), ("Insurance", "insurance"),
    ("Partial reroute", "partial_reroute"),
]


def _ff(form: FormData, key: str, default: float | None = 0.0) -> float | None:
    v = form.get(key)
    if v is None or v == "":
        return default
    return float(v)  # type: ignore[arg-type]


def _form_to_data(form: FormData) -> dict:
    """Reassembles the flat form fields into the same nested shape
    compute_scenario() / the CLI's own scenario.json expects. The handful
    of fields DisruptionScenario's constructor requires but the engine
    never actually reads (severity, duration_days, capacity_reduction,
    closure_probability, recovery_days) get harmless placeholders — they
    don't appear anywhere in the computed result."""
    data: dict = {
        "scenario_id": form.get("scenario_id") or "SCENARIO-UNSPECIFIED",
        "currency": form.get("currency") or "EUR",
        "disruption": {
            "corridor": form.get("corridor") or "",
            "probability": (_ff(form, "probability") or 0.0) / 100,
            "severity": "unspecified", "duration_days": 0, "capacity_reduction": 0,
            "delay_days": _ff(form, "delay_days"),
            "closure_probability": 0, "recovery_days": 0,
        },
        "cargo": {
            "commodity": form.get("commodity") or "",
            "quantity": _ff(form, "quantity"),
            "cargo_value": _ff(form, "cargo_value"),
            "inventory_level": _ff(form, "inventory_level"),
        },
        "transport": {
            "baseline_freight": _ff(form, "baseline_freight"),
            "disrupted_freight": _ff(form, "disrupted_freight"),
            "fuel_cost": _ff(form, "fuel_cost"),
            "port_charges": _ff(form, "port_charges"),
            "handling_costs": _ff(form, "handling_costs"),
            "rerouting_premium": _ff(form, "rerouting_premium"),
        },
        "insurance": {
            "baseline_premium": _ff(form, "baseline_premium"),
            "war_risk_premium": _ff(form, "war_risk_premium"),
            "additional_surcharge": _ff(form, "additional_surcharge"),
        },
        "economic": {
            "delay_cost_rate": _ff(form, "delay_cost_rate"),
            "inventory_holding_cost_rate": _ff(form, "inventory_holding_cost_rate"),
        },
        "commodity_effect": {
            "market_wide_price_change": _ff(form, "market_wide_price_change"),
            "disruption_attributable_price_change":
                _ff(form, "disruption_attributable_price_change"),
        },
        "additional_inventory_qty": _ff(form, "additional_inventory_qty"),
        "scenarios": [
            {"label": label, "probability": (_ff(form, f"scenario_{i}_probability") or 0.0) / 100,
             "conditional_loss": _ff(form, f"scenario_{i}_conditional_loss")}
            for i, label in enumerate(_SCENARIO_LABELS)
        ],
        "strategies": [
            {"name": name, "kind": kind, "direct_cost": _ff(form, f"strategy_{i}_direct_cost"),
             "residual_loss_estimate": _ff(form, f"strategy_{i}_residual_loss_estimate")}
            for i, (name, kind) in enumerate(_STRATEGY_DEFS)
        ],
        "mitigation_cost": _ff(form, "mitigation_cost", None),
        "loss_if_disrupted": _ff(form, "loss_if_disrupted", None),
    }
    if form.get("enable_uncertainty") == "on":
        data["uncertainty"] = {
            "probability": {"low": (_ff(form, "unc_probability_low") or 0.0) / 100,
                             "high": (_ff(form, "unc_probability_high") or 0.0) / 100},
            "cost_multiplier": {"low": (_ff(form, "unc_cost_multiplier_low") or 0.0) / 100,
                                 "high": (_ff(form, "unc_cost_multiplier_high") or 0.0) / 100},
            "n_simulations": int(_ff(form, "unc_n_simulations", 2000) or 2000),
        }
    return data


# --------------------------------------------------------------- dashboard ---

@router.get("/economic-scenarios", response_class=HTMLResponse)
def list_page(request: Request, user: User = Depends(current_user),
              session: Session = Depends(get_session)):
    scenarios = crud.list_economic_scenarios(session, user)
    rows = [{"id": s.id, "scenario_id": s.scenario_id, "corridor": s.corridor,
             "created_at": s.created_at,
             "recommended": json.loads(s.result_json).get("recommended_strategy")}
            for s in scenarios]
    return templates.TemplateResponse(request, "economic_list.html",
        {"user": user, "scenarios": rows})


@router.get("/economic-scenarios/new", response_class=HTMLResponse)
def new_page(request: Request, user: User = Depends(current_user),
             session: Session = Depends(get_session), exposure_id: int | None = None):
    linked_exposure = crud.get_exposure_owned(session, user, exposure_id) if exposure_id else None
    return templates.TemplateResponse(request, "economic_new.html",
        {"user": user, "t": template_scenario(),
         "corridors": sorted(CORRIDORS), "error": None,
         "exposure_id": linked_exposure.id if linked_exposure else None,
         "linked_exposure": linked_exposure})


@router.post("/economic-scenarios")
async def create_page(request: Request, exposure_id: int | None = Form(None),
                       user: User = Depends(current_user), session: Session = Depends(get_session)):
    form = await request.form()
    data = _form_to_data(form)
    try:
        result = compute_scenario(data)
    except (KeyError, ValueError, TypeError) as exc:
        return templates.TemplateResponse(request, "economic_new.html",
            {"user": user, "t": data, "corridors": sorted(CORRIDORS),
             "error": f"{type(exc).__name__}: {exc}", "exposure_id": exposure_id,
             "linked_exposure": None}, status_code=422)
    # re-verify ownership server-side rather than trust the hidden field;
    # client_id is taken from the verified exposure itself, not the form,
    # since the exposure already knows its own client
    exposure = crud.get_exposure_owned(session, user, exposure_id) if exposure_id else None
    row = crud.create_economic_scenario(
        session, user, scenario_id=data.get("scenario_id", "SCENARIO-UNSPECIFIED"),
        corridor=result["corridor"], input_data=data, result=result,
        client_id=exposure.client_id if exposure else None,
        exposure_id=exposure.id if exposure else None)
    return RedirectResponse(f"/economic-scenarios/{row.id}", status_code=303)


@router.get("/economic-scenarios/{scenario_id}", response_class=HTMLResponse)
def detail_page(scenario_id: int, request: Request, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    row = crud.get_economic_scenario_owned(session, user, scenario_id)
    if row is None:
        raise HTTPException(404, "scenario not found")
    result = json.loads(row.result_json)
    subscribed = crud.is_subscribed_economic_scenario(session, user, row.id)
    return templates.TemplateResponse(request, "economic_detail.html",
        {"user": user, "row": row, "result": result, "subscribed": subscribed})


@router.post("/economic-scenarios/{scenario_id}/subscribe")
def subscribe_page(scenario_id: int, user: User = Depends(current_user),
                    session: Session = Depends(get_session)):
    row = crud.get_economic_scenario_owned(session, user, scenario_id)
    if row is None:
        raise HTTPException(404, "scenario not found")
    crud.toggle_economic_scenario_subscription(session, user, row.id)
    return RedirectResponse(f"/economic-scenarios/{row.id}", status_code=303)


@router.get("/economic-scenarios/{scenario_id}/report", response_class=HTMLResponse)
def report_page(scenario_id: int, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    row = crud.get_economic_scenario_owned(session, user, scenario_id)
    if row is None:
        raise HTTPException(404, "scenario not found")
    result = json.loads(row.result_json)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.html"
        economic_report_html(result, out)
        return HTMLResponse(out.read_text(encoding="utf-8"))


# ------------------------------------------------------------------- API ---

@router.get("/api/v1/economic-scenarios")
def api_list(user: User = Depends(current_principal), session: Session = Depends(get_session)):
    return [{"id": s.id, "scenario_id": s.scenario_id, "corridor": s.corridor,
             "created_at": s.created_at.isoformat()}
            for s in crud.list_economic_scenarios(session, user)]


@router.get("/api/v1/economic-scenarios/template")
def api_template(user: User = Depends(current_principal)):
    return template_scenario()


@router.post("/api/v1/economic-scenarios", status_code=201)
def api_create(data: dict, user: User = Depends(current_principal),
               session: Session = Depends(get_session)):
    if data.get("disruption", {}).get("corridor") not in CORRIDORS:
        raise HTTPException(422, f"unknown or missing corridor. Known: {sorted(CORRIDORS)}")
    try:
        result = compute_scenario(data)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc
    row = crud.create_economic_scenario(
        session, user, scenario_id=data.get("scenario_id", "SCENARIO-UNSPECIFIED"),
        corridor=result["corridor"], input_data=data, result=result)
    return {"id": row.id, "result": result}


@router.get("/api/v1/economic-scenarios/{scenario_id}")
def api_detail(scenario_id: int, user: User = Depends(current_principal),
               session: Session = Depends(get_session)):
    row = crud.get_economic_scenario_owned(session, user, scenario_id)
    if row is None:
        raise HTTPException(404, "scenario not found")
    return {"id": row.id, "scenario_id": row.scenario_id, "corridor": row.corridor,
            "input": json.loads(row.input_json), "result": json.loads(row.result_json)}

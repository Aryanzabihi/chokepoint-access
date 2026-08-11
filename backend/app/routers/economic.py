"""routers/economic.py — the economic & decision engine (engine.md), wired
into the app. Dashboard pages take a pasted/edited scenario.json (the same
shape economic_engine.py's own `template` command writes) rather than a
huge multi-section form — this mirrors the CLI's actual design intent (a
scenario file you build once and re-run) instead of building weeks of form
UI for ~40 fields most of which are optional. The JSON API underneath
(/api/v1/economic-scenarios) is what a real form, or an external caller,
would eventually post to.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
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


def _parse_and_compute(raw_json: str) -> tuple[dict, dict]:
    """Returns (input_data, result); raises on bad JSON or invalid input —
    the caller decides how to surface that."""
    data = json.loads(raw_json)
    result = compute_scenario(data)
    return data, result


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
        {"user": user, "template_json": json.dumps(template_scenario(), indent=2),
         "corridors": sorted(CORRIDORS), "error": None,
         "exposure_id": linked_exposure.id if linked_exposure else None,
         "linked_exposure": linked_exposure})


@router.post("/economic-scenarios")
def create_page(request: Request, scenario_json: str = Form(...),
                 exposure_id: int | None = Form(None),
                 user: User = Depends(current_user), session: Session = Depends(get_session)):
    try:
        data, result = _parse_and_compute(scenario_json)
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        return templates.TemplateResponse(request, "economic_new.html",
            {"user": user, "template_json": scenario_json, "corridors": sorted(CORRIDORS),
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

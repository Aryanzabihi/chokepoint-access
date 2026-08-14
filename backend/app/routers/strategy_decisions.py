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
    CORRIDORS, FIELDS, INCOTERM_GROUPS, compute_decision, decision_brief_html, fields_by_group,
    template,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

_STRATEGY_DEFAULTS = [
    ("Continue", 0.0, True),
    ("Partial reroute", 700_000.0, False),
    ("", 0.0, False),
]


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


def _form_to_intake(form: FormData) -> dict:
    fields = {spec.name: _field_value(form, spec) for spec in FIELDS}
    baseline_idx = int(form.get("baseline_strategy") or 0)  # type: ignore[arg-type]

    strategies = []
    for i in range(len(_STRATEGY_DEFAULTS)):
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

    data = {
        "scenario_id": form.get("scenario_id") or "SCENARIO-UNSPECIFIED",
        "corridor": form.get("corridor") or "",
        "incoterm": form.get("incoterm") or None,
        "tier": int(form.get("tier") or 1),  # type: ignore[arg-type]
        "fields": fields,
        "strategies": strategies,
        "client_probability_estimate": _pct(form, "client_probability_estimate"),
        "probability_range": None,
        "tier4_ledger_path": None,
    }
    lo, hi = _pct(form, "probability_range_low"), _pct(form, "probability_range_high")
    if lo is not None and hi is not None:
        data["probability_range"] = [lo, hi]
    return data


def _template_context() -> dict:
    # template(3): every field gets a None placeholder regardless of tier,
    # so the form always has a key to look up (fields left above the
    # client's chosen tier just stay blank -- see intake.py, supplying one
    # anyway is never forbidden, only not required).
    t = template(3)
    t["strategies"] = [
        {"name": n, "direct_cost": c, "is_baseline": b, "notes": "",
         "effects": {"delay_days_delta": 0, "capacity_restored": 0,
                     "war_risk_premium_multiplier": None, "days_of_cover_delta": 0}}
        for n, c, b in _STRATEGY_DEFAULTS]
    return t


# --------------------------------------------------------------- dashboard ---

@router.get("/strategy-decisions", response_class=HTMLResponse)
def list_page(request: Request, user: User = Depends(current_user),
              session: Session = Depends(get_session)):
    decisions = crud.list_strategy_decisions(session, user)
    rows = [{"id": d.id, "scenario_id": d.scenario_id, "corridor": d.corridor,
             "created_at": d.created_at,
             "recommended": json.loads(d.result_json).get("recommended")}
            for d in decisions]
    return templates.TemplateResponse(request, "strategy_decision_list.html",
        {"user": user, "decisions": rows})


@router.get("/strategy-decisions/new", response_class=HTMLResponse)
def new_page(request: Request, user: User = Depends(current_user),
             session: Session = Depends(get_session), exposure_id: int | None = None):
    linked_exposure = crud.get_exposure_owned(session, user, exposure_id) if exposure_id else None
    return templates.TemplateResponse(request, "strategy_decision_new.html",
        {"user": user, "t": _template_context(), "corridors": sorted(CORRIDORS),
         "incoterms": sorted(INCOTERM_GROUPS), "field_groups": fields_by_group(FIELDS),
         "error": None, "exposure_id": linked_exposure.id if linked_exposure else None,
         "linked_exposure": linked_exposure})


@router.post("/strategy-decisions")
async def create_page(request: Request, exposure_id: int | None = Form(None),
                       user: User = Depends(current_user), session: Session = Depends(get_session)):
    form = await request.form()
    data = _form_to_intake(form)
    try:
        result = compute_decision(data)
    except (KeyError, ValueError, TypeError) as exc:
        t = dict(data)
        t.setdefault("currency", "EUR")
        return templates.TemplateResponse(request, "strategy_decision_new.html",
            {"user": user, "t": t, "corridors": sorted(CORRIDORS),
             "incoterms": sorted(INCOTERM_GROUPS), "field_groups": fields_by_group(FIELDS),
             "error": f"{type(exc).__name__}: {exc}", "exposure_id": exposure_id,
             "linked_exposure": None}, status_code=422)
    exposure = crud.get_exposure_owned(session, user, exposure_id) if exposure_id else None
    row = crud.create_strategy_decision(
        session, user, scenario_id=data.get("scenario_id", "SCENARIO-UNSPECIFIED"),
        corridor=result["corridor"], input_data=data, result=result,
        client_id=exposure.client_id if exposure else None,
        exposure_id=exposure.id if exposure else None)
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
    return templates.TemplateResponse(request, "strategy_decision_detail.html",
        {"user": user, "row": row, "result": result, "input": input_data,
         "field_groups": fields_by_group(FIELDS),
         "ledger_by_grade": sorted(ledger_by_grade.items()), "subscribed": subscribed})


@router.post("/strategy-decisions/{decision_id}/subscribe")
def subscribe_page(decision_id: int, user: User = Depends(current_user),
                    session: Session = Depends(get_session)):
    row = crud.get_strategy_decision_owned(session, user, decision_id)
    if row is None:
        raise HTTPException(404, "decision not found")
    crud.toggle_strategy_decision_subscription(session, user, row.id)
    return RedirectResponse(f"/strategy-decisions/{row.id}", status_code=303)


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

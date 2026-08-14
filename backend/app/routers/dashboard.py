"""routers/dashboard.py — the authenticated, server-rendered HTML app.

Plain HTML forms, not fetch()/JSON — consistent with the rest of the site
having no build step. current_user (not current_principal) throughout:
these are browser pages, not API endpoints, so only a real login session
should render them.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from .. import crud, engine
from ..auth import (
    SESSION_COOKIE, generate_api_key, hash_password, make_session_cookie,
    verify_password,
)
from ..db import get_session
from ..deps import current_user, current_user_optional
from ..models import User

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/")
def index(user: User | None = Depends(current_user_optional)):
    return RedirectResponse("/home" if user else "/login", status_code=303)


# ------------------------------------------------------------------ auth ---

@router.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return templates.TemplateResponse(request, "signup.html", {"error": None})


@router.post("/signup")
def signup(request: Request, email: str = Form(...), password: str = Form(...),
           session: Session = Depends(get_session)):
    if len(password) < 8:
        return templates.TemplateResponse(request, "signup.html",
            {"error": "Password must be at least 8 characters."}, status_code=422)
    if crud.get_user_by_email(session, email):
        return templates.TemplateResponse(request, "signup.html",
            {"error": "An account with that email already exists."}, status_code=422)
    user = crud.create_user(session, email, hash_password(password))
    resp = RedirectResponse("/home", status_code=303)
    resp.set_cookie(SESSION_COOKIE, make_session_cookie(user.id),
                     httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return resp


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          session: Session = Depends(get_session)):
    user = crud.get_user_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html",
            {"error": "Wrong email or password."}, status_code=401)
    resp = RedirectResponse("/home", status_code=303)
    resp.set_cookie(SESSION_COOKIE, make_session_cookie(user.id),
                     httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ------------------------------------------------------------------ home ---

@router.get("/home", response_class=HTMLResponse)
def home_page(request: Request, user: User = Depends(current_user),
              session: Session = Depends(get_session)):
    """Every exposure across every client this user owns, one row each,
    worst corridor first. Answers "what needs my attention right now"
    instead of requiring a click into each client to find out. No new
    scoring formula -- urgency is read directly off the same band/regime
    the corridor's live reading already carries, "in episode" ranked above
    every band since an ongoing disruption outranks any alarm level."""
    pairs = crud.list_exposures_for_user(session, user)
    band_severity = {label: i for i, (_, label, _, _) in enumerate(engine.BANDS)}

    rows = []
    for exposure, client in pairs:
        try:
            reading = engine.current_reading(exposure.corridor)
        except (FileNotFoundError, ValueError):
            reading = None
        latest_sd = crud.latest_strategy_decision_for_exposure(session, exposure.id)
        sd_result = json.loads(latest_sd.result_json) if latest_sd else None
        rows.append({"exposure": exposure, "client": client, "reading": reading,
                     "strategy_decision": latest_sd, "sd_result": sd_result})

    def _urgency(row):
        reading = row["reading"]
        if reading is None:
            return (-1, "")
        if reading.get("regime") == "in episode":
            return (len(engine.BANDS), reading.get("as_of", ""))
        return (band_severity.get(reading.get("band"), 0), reading.get("as_of", ""))

    rows.sort(key=_urgency, reverse=True)
    return templates.TemplateResponse(request, "home.html", {"user": user, "rows": rows})


# --------------------------------------------------------------- clients ---

@router.get("/clients", response_class=HTMLResponse)
def clients_page(request: Request, user: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    clients = crud.list_clients(session, user)
    return templates.TemplateResponse(request, "clients.html",
        {"user": user, "clients": clients})


@router.post("/clients")
def create_client(name: str = Form(...), user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
    crud.create_client(session, user, name)
    return RedirectResponse("/clients", status_code=303)


@router.get("/clients/{client_id}", response_class=HTMLResponse)
def client_page(client_id: int, request: Request, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    client = crud.get_client_owned(session, user, client_id)
    if client is None:
        raise HTTPException(404, "client not found")
    exposures = crud.list_exposures(session, client)
    return templates.TemplateResponse(request, "client_detail.html",
        {"user": user, "client": client, "exposures": exposures,
         "corridors": sorted(engine.CORRIDORS)})


@router.get("/clients/{client_id}/portfolio", response_class=HTMLResponse)
def portfolio_page(client_id: int, request: Request, user: User = Depends(current_user),
                    session: Session = Depends(get_session)):
    """Sums each exposure's most recent strategy decision, and flags when
    two or more exposures share a corridor -- a plain structural note
    ("these move together"), not a fabricated correlation coefficient this
    app has no data to back up. Totals are kept per-currency rather than
    summed across currencies, which would silently produce a wrong number.
    """
    client = crud.get_client_owned(session, user, client_id)
    if client is None:
        raise HTTPException(404, "client not found")
    exposures = crud.list_exposures(session, client)

    rows = []
    totals: dict[str, dict[str, float]] = {}
    by_corridor: dict[str, int] = {}
    for exp in exposures:
        decision = crud.latest_strategy_decision_for_exposure(session, exp.id)
        result = json.loads(decision.result_json) if decision else None
        currency = None
        if result:
            currency = json.loads(decision.input_json).get("currency", "EUR")
            t = totals.setdefault(currency, {"disrupted": 0.0, "avoidable": 0.0})
            t["disrupted"] += result.get("exposure", {}).get("disrupted") or 0.0
            t["avoidable"] += result.get("exposure", {}).get("avoidable") or 0.0
        rows.append({"exposure": exp, "decision": decision, "result": result, "currency": currency})
        by_corridor[exp.corridor] = by_corridor.get(exp.corridor, 0) + 1

    overlaps = [corridor for corridor, n in by_corridor.items() if n > 1]

    return templates.TemplateResponse(request, "client_portfolio.html",
        {"user": user, "client": client, "rows": rows, "totals": totals, "overlaps": overlaps})


@router.post("/clients/{client_id}/exposures")
def create_exposure(client_id: int, corridor: str = Form(...),
                     commodity: str = Form(""),
                     annual_exposure: str = Form(""),
                     crisis_replacement_cost: float = Form(...),
                     currency: str = Form("EUR"),
                     user: User = Depends(current_user),
                     session: Session = Depends(get_session)):
    client = crud.get_client_owned(session, user, client_id)
    if client is None:
        raise HTTPException(404, "client not found")
    if corridor not in engine.CORRIDORS:
        raise HTTPException(422, "unknown corridor")
    crud.create_exposure(session, user, client, corridor=corridor,
                          commodity=commodity or None,
                          annual_exposure=float(annual_exposure) if annual_exposure else None,
                          crisis_replacement_cost=crisis_replacement_cost, currency=currency)
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


# ------------------------------------------------------------- exposures ---

@router.get("/clients/{client_id}/exposures/{exposure_id}", response_class=HTMLResponse)
def exposure_page(client_id: int, exposure_id: int, request: Request,
                   user: User = Depends(current_user), session: Session = Depends(get_session)):
    client = crud.get_client_owned(session, user, client_id)
    exposure = crud.get_exposure_owned(session, user, exposure_id)
    if client is None or exposure is None or exposure.client_id != client.id:
        raise HTTPException(404, "not found")
    strategy_decisions = [
        {"row": d, "recommended": json.loads(d.result_json).get("recommended")}
        for d in crud.list_strategy_decisions_for_exposure(session, exposure.id)
    ]
    orders = crud.list_procurement_orders_for_exposure(session, exposure.id)
    error = None
    try:
        reading = engine.current_reading(exposure.corridor)
    except (FileNotFoundError, ValueError) as exc:
        reading, error = None, str(exc)
    return templates.TemplateResponse(request, "exposure_detail.html",
        {"user": user, "client": client, "exposure": exposure,
         "strategy_decisions": strategy_decisions, "orders": orders,
         "reading": reading, "reading_error": error})


@router.post("/clients/{client_id}/exposures/{exposure_id}/decide")
def decide(client_id: int, exposure_id: int, alpha_pct: float = Form(...),
           window_months: int = Form(6), user: User = Depends(current_user),
           session: Session = Depends(get_session)):
    exposure = crud.get_exposure_owned(session, user, exposure_id)
    if exposure is None or exposure.client_id != client_id:
        raise HTTPException(404, "exposure not found")
    reading = engine.current_reading(exposure.corridor)
    result = engine.decide(alpha_pct / 100, window_months, reading)
    crud.create_decision(
        session, user, exposure, as_of=reading["as_of"], window_months=window_months,
        alpha=alpha_pct / 100, tar=reading["tar"], band=reading["band"],
        regime=reading["regime"], decision_level=result["level"],
        decision_margin_pp=result["margin_pp"],
        computed={"reading": reading, "decision": result})
    return RedirectResponse(f"/clients/{client_id}/exposures/{exposure_id}", status_code=303)


@router.post("/clients/{client_id}/exposures/{exposure_id}/subscribe")
def subscribe(client_id: int, exposure_id: int, user: User = Depends(current_user),
              session: Session = Depends(get_session)):
    exposure = crud.get_exposure_owned(session, user, exposure_id)
    if exposure is None or exposure.client_id != client_id:
        raise HTTPException(404, "exposure not found")
    crud.toggle_subscription(session, user, exposure.id)
    return RedirectResponse(f"/clients/{client_id}/exposures/{exposure_id}", status_code=303)


# -------------------------------------------------------------- api keys ---

@router.get("/account/api-keys", response_class=HTMLResponse)
def api_keys_page(request: Request, user: User = Depends(current_user),
                   session: Session = Depends(get_session), created: str | None = None):
    keys = crud.list_api_keys(session, user)
    return templates.TemplateResponse(request, "api_keys.html",
        {"user": user, "keys": keys, "just_created": created})


@router.post("/account/api-keys")
def create_api_key(name: str = Form(...), user: User = Depends(current_user),
                    session: Session = Depends(get_session)):
    raw, hashed = generate_api_key()
    crud.create_api_key_row(session, user, name, hashed)
    return RedirectResponse(f"/account/api-keys?created={raw}", status_code=303)


@router.post("/account/api-keys/{key_id}/revoke")
def revoke_api_key(key_id: int, user: User = Depends(current_user),
                    session: Session = Depends(get_session)):
    crud.revoke_api_key(session, user, key_id)
    return RedirectResponse("/account/api-keys", status_code=303)

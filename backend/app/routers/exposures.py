"""routers/exposures.py — /api/v1/exposures, plus the decision-compute
endpoint that is the actual point of the whole product: given a saved
exposure and a cost ratio, what does the current reading say to do."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from .. import crud, engine
from ..db import get_session
from ..deps import current_principal
from ..models import User
from ..schemas import DecideBody, DecisionOut, ExposureCreate, ExposureOut

router = APIRouter()


def _out(e) -> ExposureOut:
    return ExposureOut(id=e.id, client_id=e.client_id, corridor=e.corridor,
                        commodity=e.commodity, annual_exposure=e.annual_exposure,
                        crisis_replacement_cost=e.crisis_replacement_cost,
                        currency=e.currency, created_at=e.created_at.isoformat())


@router.get("", response_model=list[ExposureOut])
def list_exposures(client_id: int = Query(...), user: User = Depends(current_principal),
                    session: Session = Depends(get_session)):
    client = crud.get_client_owned(session, user, client_id)
    if client is None:
        raise HTTPException(404, "client not found")
    return [_out(e) for e in crud.list_exposures(session, client)]


@router.post("", response_model=ExposureOut, status_code=201)
def create_exposure(client_id: int, body: ExposureCreate,
                     user: User = Depends(current_principal),
                     session: Session = Depends(get_session)):
    client = crud.get_client_owned(session, user, client_id)
    if client is None:
        raise HTTPException(404, "client not found")
    if body.corridor not in engine.CORRIDORS:
        raise HTTPException(422, f"unknown corridor {body.corridor!r}. "
                                  f"Known: {sorted(engine.CORRIDORS)}")
    e = crud.create_exposure(session, user, client, corridor=body.corridor,
                              commodity=body.commodity,
                              annual_exposure=body.annual_exposure,
                              crisis_replacement_cost=body.crisis_replacement_cost,
                              currency=body.currency)
    return _out(e)


@router.get("/{exposure_id}", response_model=ExposureOut)
def get_exposure(exposure_id: int, user: User = Depends(current_principal),
                  session: Session = Depends(get_session)):
    e = crud.get_exposure_owned(session, user, exposure_id)
    if e is None:
        raise HTTPException(404, "exposure not found")
    return _out(e)


@router.get("/{exposure_id}/decisions", response_model=list[DecisionOut])
def list_decisions(exposure_id: int, user: User = Depends(current_principal),
                    session: Session = Depends(get_session)):
    e = crud.get_exposure_owned(session, user, exposure_id)
    if e is None:
        raise HTTPException(404, "exposure not found")
    return [DecisionOut(id=d.id, as_of=d.as_of, window_months=d.window_months,
                         alpha=d.alpha, tar=d.tar, band=d.band, regime=d.regime,
                         decision_level=d.decision_level,
                         decision_margin_pp=d.decision_margin_pp,
                         created_at=d.created_at.isoformat())
            for d in crud.list_decisions(session, e)]


@router.post("/{exposure_id}/decide", response_model=DecisionOut, status_code=201)
def decide(exposure_id: int, body: DecideBody, user: User = Depends(current_principal),
           session: Session = Depends(get_session)):
    """Compute a decision against the current reading and save it.

    Uses engine.current_reading() + engine.decide() — the same cost-loss
    math and band logic as threshold-engine.html, run server-side so it can
    be persisted rather than recomputed in a browser each time.
    """
    e = crud.get_exposure_owned(session, user, exposure_id)
    if e is None:
        raise HTTPException(404, "exposure not found")
    if body.window_months not in engine.WINDOWS:
        raise HTTPException(422, f"window_months must be one of {sorted(engine.WINDOWS)}")

    try:
        reading = engine.current_reading(e.corridor)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc

    result = engine.decide(body.alpha, body.window_months, reading)
    d = crud.create_decision(
        session, user, e, as_of=reading["as_of"], window_months=body.window_months,
        alpha=body.alpha, tar=reading["tar"], band=reading["band"],
        regime=reading["regime"], decision_level=result["level"],
        decision_margin_pp=result["margin_pp"],
        computed={"reading": reading, "decision": result},
    )
    return DecisionOut(id=d.id, as_of=d.as_of, window_months=d.window_months,
                        alpha=d.alpha, tar=d.tar, band=d.band, regime=d.regime,
                        decision_level=d.decision_level,
                        decision_margin_pp=d.decision_margin_pp,
                        created_at=d.created_at.isoformat())

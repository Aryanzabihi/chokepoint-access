"""routers/api_v1.py — general-purpose endpoints for external API callers,
beyond their own saved clients/exposures (which live in clients.py /
exposures.py). This is the part of "API" that's useful to an integrator
who doesn't necessarily want an account-scoped portfolio at all — just the
current reading for a corridor.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import engine
from ..deps import current_principal
from ..models import User

router = APIRouter()


@router.get("/corridors")
def list_corridors(user: User = Depends(current_principal)):
    return sorted(engine.CORRIDORS)


@router.get("/readings/{corridor}")
def reading(corridor: str, user: User = Depends(current_principal)):
    try:
        return engine.current_reading(corridor)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc

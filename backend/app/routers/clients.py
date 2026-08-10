"""routers/clients.py — /api/v1/clients. Used by both the dashboard's own
pages and external API callers (current_principal accepts either auth)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from .. import crud
from ..db import get_session
from ..deps import current_principal
from ..models import User
from ..schemas import ClientCreate, ClientOut

router = APIRouter()


@router.get("", response_model=list[ClientOut])
def list_clients(user: User = Depends(current_principal),
                  session: Session = Depends(get_session)):
    return [ClientOut(id=c.id, name=c.name, created_at=c.created_at.isoformat())
            for c in crud.list_clients(session, user)]


@router.post("", response_model=ClientOut, status_code=201)
def create_client(body: ClientCreate, user: User = Depends(current_principal),
                   session: Session = Depends(get_session)):
    c = crud.create_client(session, user, body.name)
    return ClientOut(id=c.id, name=c.name, created_at=c.created_at.isoformat())


@router.get("/{client_id}", response_model=ClientOut)
def get_client(client_id: int, user: User = Depends(current_principal),
                session: Session = Depends(get_session)):
    c = crud.get_client_owned(session, user, client_id)
    if c is None:
        raise HTTPException(404, "client not found")
    return ClientOut(id=c.id, name=c.name, created_at=c.created_at.isoformat())

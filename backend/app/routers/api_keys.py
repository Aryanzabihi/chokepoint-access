"""routers/api_keys.py — issuing and revoking API keys.

Dashboard-only (current_user, not current_principal): an API key should not
be able to mint another API key, or a compromised key becomes a way to
mint infinite replacements for itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from .. import crud
from ..auth import generate_api_key
from ..db import get_session
from ..deps import current_user
from ..models import User
from ..schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut

router = APIRouter()


def _out(k) -> ApiKeyOut:
    return ApiKeyOut(id=k.id, name=k.name, created_at=k.created_at.isoformat(),
                      last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
                      revoked_at=k.revoked_at.isoformat() if k.revoked_at else None)


@router.get("", response_model=list[ApiKeyOut])
def list_keys(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return [_out(k) for k in crud.list_api_keys(session, user)]


@router.post("", response_model=ApiKeyCreated, status_code=201)
def create_key(body: ApiKeyCreate, user: User = Depends(current_user),
               session: Session = Depends(get_session)):
    raw, hashed = generate_api_key()
    k = crud.create_api_key_row(session, user, body.name, hashed)
    out = _out(k)
    return ApiKeyCreated(**out.model_dump(), raw_key=raw)


@router.post("/{key_id}/revoke", status_code=204)
def revoke_key(key_id: int, user: User = Depends(current_user),
               session: Session = Depends(get_session)):
    if not crud.revoke_api_key(session, user, key_id):
        raise HTTPException(404, "api key not found")

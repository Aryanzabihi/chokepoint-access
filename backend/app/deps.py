"""deps.py — FastAPI dependencies for identifying who's calling.

Two paths in, one identity out:
  - the dashboard's own pages/AJAX calls carry the signed session cookie
  - third-party API callers carry `Authorization: Bearer <api_key>`

Everything downstream (routers) just asks for a User and doesn't care which
path produced it, except where an endpoint is explicitly dashboard-only
(current_user) or explicitly requires a real API key (current_api_user).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlmodel import Session, select

from .auth import SESSION_COOKIE, hash_api_key, read_session_cookie
from .db import get_session
from .models import ApiKey, User


def get_user_from_cookie(
    session: Session = Depends(get_session),
    cx_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User | None:
    if not cx_session:
        return None
    user_id = read_session_cookie(cx_session)
    if user_id is None:
        return None
    return session.get(User, user_id)


def get_user_from_api_key(
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> User | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    raw = authorization.split(" ", 1)[1].strip()
    hashed = hash_api_key(raw)
    key = session.exec(
        select(ApiKey).where(ApiKey.hashed_key == hashed, ApiKey.revoked_at.is_(None))
    ).first()
    if key is None:
        return None
    key.last_used_at = datetime.now(timezone.utc)
    session.add(key)
    session.commit()
    return session.get(User, key.user_id)


def current_user(
    from_cookie: User | None = Depends(get_user_from_cookie),
) -> User:
    """Dashboard pages: cookie only. A stray API key header should not log
    someone into the HTML dashboard of a session that isn't theirs."""
    if from_cookie is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not logged in")
    return from_cookie


def current_user_optional(
    from_cookie: User | None = Depends(get_user_from_cookie),
) -> User | None:
    return from_cookie


def current_principal(
    from_cookie: User | None = Depends(get_user_from_cookie),
    from_api_key: User | None = Depends(get_user_from_api_key),
) -> User:
    """/api/v1/*: either credential works."""
    user = from_cookie or from_api_key
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                             "not logged in and no valid API key")
    return user

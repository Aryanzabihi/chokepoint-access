"""auth.py — password hashing, signed session cookies, API keys.

Self-hosted on purpose (see the Phase 5 plan): no external identity vendor,
so no vendor account was needed to get this far. Swapping in a hosted
provider later means replacing what's in this file — the data model
(User.password_hash) doesn't have to change, since a hosted-provider
migration would just stop writing to that column.
"""

from __future__ import annotations

import hashlib
import os
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext

SESSION_SECRET = os.environ.get("SESSION_SECRET")
if not SESSION_SECRET:
    raise RuntimeError(
        "SESSION_SECRET is not set. Generate one (e.g. `python -c "
        "\"import secrets; print(secrets.token_hex(32))\"`) and set it in "
        "backend/.env locally or as a Render secret in production. Session "
        "cookies are signed with this — losing or rotating it logs everyone "
        "out, and it must never be committed."
    )

SESSION_COOKIE = "cx_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
_serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="cx-session")


def hash_password(password: str) -> str:
    return _pwd_ctx.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _pwd_ctx.verify(password, hashed)


def make_session_cookie(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id})


def read_session_cookie(value: str) -> int | None:
    try:
        data = _serializer.loads(value, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("uid")


def generate_api_key() -> tuple[str, str]:
    """Returns (raw_key_to_show_once, hashed_key_to_store).

    The raw key is shown to the user exactly once, at creation. Only the
    hash is ever persisted — a stolen database dump doesn't hand over usable
    keys, same reasoning as password hashing.
    """
    raw = "cx_" + secrets.token_urlsafe(32)
    return raw, hash_api_key(raw)


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

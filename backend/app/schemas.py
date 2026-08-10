"""schemas.py — request/response bodies for the JSON API (routers/clients.py,
exposures.py, api_v1.py, api_keys.py). Kept separate from models.py: the DB
tables and the wire format are allowed to diverge (e.g. computed_json is a
string in the DB, a parsed object over the wire)."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class SignupBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ClientOut(BaseModel):
    id: int
    name: str
    created_at: str


class ExposureCreate(BaseModel):
    corridor: str
    commodity: str | None = None
    annual_exposure: float | None = None
    crisis_replacement_cost: float = Field(gt=0)
    currency: str = "EUR"


class ExposureOut(BaseModel):
    id: int
    client_id: int
    corridor: str
    commodity: str | None
    annual_exposure: float | None
    crisis_replacement_cost: float
    currency: str
    created_at: str


class DecideBody(BaseModel):
    alpha: float = Field(gt=0, lt=1, description="action cost as a fraction, e.g. 0.12 for 12%")
    window_months: int = Field(default=6, description="3, 6 or 12")


class DecisionOut(BaseModel):
    id: int
    as_of: str
    window_months: int
    alpha: float
    tar: float
    band: str
    regime: str
    decision_level: str
    decision_margin_pp: float | None
    created_at: str


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKeyOut(BaseModel):
    id: int
    name: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None


class ApiKeyCreated(ApiKeyOut):
    raw_key: str  # shown once

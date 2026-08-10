"""models.py — the Phase 5 schema.

One user owns clients; each client has exposures; each exposure accumulates
decisions over time. AuditEvent is append-only (nothing in routers/ ever
issues an UPDATE or DELETE against it) — the same tamper-evidence spirit as
record.jsonl, without yet hash-chaining it (see the plan this was built
from for why that's deferred rather than skipped).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)
    email_verified_at: datetime | None = None


class Client(SQLModel, table=True):
    """A user's book of business — one row per end client they advise."""
    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int = Field(foreign_key="user.id", index=True)
    name: str
    created_at: datetime = Field(default_factory=utcnow)


class Exposure(SQLModel, table=True):
    """One corridor exposure belonging to a client — mirrors the inputs the
    cost calculator added to threshold-engine.html (crisis replacement cost,
    currency), plus which corridor and what's being shipped."""
    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", index=True)
    corridor: str
    commodity: str | None = None
    annual_exposure: float | None = None
    crisis_replacement_cost: float
    currency: str = Field(default="EUR")
    created_at: datetime = Field(default_factory=utcnow)


class Decision(SQLModel, table=True):
    """A computed, saved decision for an exposure at a point in time.

    computed_json holds the full snapshot (every input and intermediate
    value the verdict depended on) so a report generated later is
    reproducible from stored inputs alone — engine.md rule #6.
    """
    id: int | None = Field(default=None, primary_key=True)
    exposure_id: int = Field(foreign_key="exposure.id", index=True)
    as_of: str                       # YYYY-MM-DD, the reading this was computed against
    window_months: int
    alpha: float
    tar: float
    band: str
    regime: str
    decision_level: str              # act | borderline | wait | borderline-wait | always | episode
    decision_margin_pp: float | None = None
    computed_json: str               # json.dumps() of the full engine.decide() + reading output
    created_at: datetime = Field(default_factory=utcnow)


class AuditEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    entity_type: str                 # "client" | "exposure" | "decision" | "api_key"
    entity_id: int
    action: str                      # "created" | "computed" | "exported" | "revoked" ...
    detail: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ApiKey(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str
    hashed_key: str = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class EconomicScenario(SQLModel, table=True):
    """A saved run of the economic_engine.py decision engine (engine.md) —
    a different, more detailed lens than Decision above. Scoped directly to
    the owning user rather than through a client, since a scenario is
    useful standalone (a broker exploring a "what if" isn't necessarily
    building it for one specific client yet); client_id/exposure_id are
    optional links for organising it once it is.

    input_json is exactly the scenario.json shape economic_engine.py's CLI
    consumes (economic_engine.py template writes an editable example of
    it) — same input format whether it came from the CLI or this app.
    result_json is compute()'s full output, so a report generated later
    reproduces from stored inputs alone (MVP criterion #14).
    """
    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int = Field(foreign_key="user.id", index=True)
    client_id: int | None = Field(default=None, foreign_key="client.id", index=True)
    exposure_id: int | None = Field(default=None, foreign_key="exposure.id", index=True)
    scenario_id: str
    corridor: str
    input_json: str
    result_json: str
    created_at: datetime = Field(default_factory=utcnow)


class AlertSubscription(SQLModel, table=True):
    """Opting an exposure into monthly email alerts. Email address is
    implicit — always the owning user's, so there's no separate address to
    keep in sync or leak."""
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    exposure_id: int = Field(foreign_key="exposure.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)

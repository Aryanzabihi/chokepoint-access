"""crud.py — data access shared by the dashboard (HTML forms) and the JSON
API. Keeping this in one place means the two front doors can't drift into
different validation or ownership-checking behaviour.

Every "owned" lookup takes the requesting user's id and returns None (never
another user's row) if the id doesn't belong to them — this is the entire
multi-tenant isolation boundary for v1, so it lives in one obvious place
rather than being re-derived per router.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from .models import (
    AlertSubscription, ApiKey, AuditEvent, Client, Decision, EconomicScenario,
    EconomicScenarioSubscription, Exposure, StrategyDecision, User,
)


def audit(session: Session, user_id: int, entity_type: str, entity_id: int,
          action: str, detail: str | None = None) -> None:
    session.add(AuditEvent(user_id=user_id, entity_type=entity_type,
                            entity_id=entity_id, action=action, detail=detail))


# ---------------------------------------------------------------- users ---

def get_user_by_email(session: Session, email: str) -> User | None:
    return session.exec(select(User).where(User.email == email.lower().strip())).first()


def create_user(session: Session, email: str, password_hash: str) -> User:
    user = User(email=email.lower().strip(), password_hash=password_hash)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# -------------------------------------------------------------- clients ---

def list_clients(session: Session, user: User) -> list[Client]:
    return list(session.exec(
        select(Client).where(Client.owner_user_id == user.id).order_by(Client.name)))


def create_client(session: Session, user: User, name: str) -> Client:
    client = Client(owner_user_id=user.id, name=name.strip())
    session.add(client)
    session.commit()
    session.refresh(client)
    audit(session, user.id, "client", client.id, "created", name)
    session.commit()
    return client


def get_client_owned(session: Session, user: User, client_id: int) -> Client | None:
    client = session.get(Client, client_id)
    if client is None or client.owner_user_id != user.id:
        return None
    return client


# ------------------------------------------------------------ exposures ---

def list_exposures(session: Session, client: Client) -> list[Exposure]:
    return list(session.exec(
        select(Exposure).where(Exposure.client_id == client.id)
        .order_by(Exposure.created_at.desc())))


def create_exposure(session: Session, user: User, client: Client, *, corridor: str,
                     commodity: str | None, annual_exposure: float | None,
                     crisis_replacement_cost: float, currency: str) -> Exposure:
    exp = Exposure(client_id=client.id, corridor=corridor, commodity=commodity,
                    annual_exposure=annual_exposure,
                    crisis_replacement_cost=crisis_replacement_cost, currency=currency)
    session.add(exp)
    session.commit()
    session.refresh(exp)
    audit(session, user.id, "exposure", exp.id, "created", corridor)
    session.commit()
    return exp


def get_exposure_owned(session: Session, user: User, exposure_id: int) -> Exposure | None:
    exp = session.get(Exposure, exposure_id)
    if exp is None:
        return None
    client = get_client_owned(session, user, exp.client_id)
    if client is None:
        return None
    return exp


# ------------------------------------------------------------ decisions ---

def list_decisions(session: Session, exposure: Exposure) -> list[Decision]:
    return list(session.exec(
        select(Decision).where(Decision.exposure_id == exposure.id)
        .order_by(Decision.created_at.desc())))


def latest_decision(session: Session, exposure: Exposure) -> Decision | None:
    return session.exec(
        select(Decision).where(Decision.exposure_id == exposure.id)
        .order_by(Decision.created_at.desc())).first()


def create_decision(session: Session, user: User, exposure: Exposure, *, as_of: str,
                     window_months: int, alpha: float, tar: float, band: str,
                     regime: str, decision_level: str, decision_margin_pp: float | None,
                     computed: dict) -> Decision:
    d = Decision(exposure_id=exposure.id, as_of=as_of, window_months=window_months,
                 alpha=alpha, tar=tar, band=band, regime=regime,
                 decision_level=decision_level, decision_margin_pp=decision_margin_pp,
                 computed_json=json.dumps(computed))
    session.add(d)
    session.commit()
    session.refresh(d)
    audit(session, user.id, "decision", d.id, "computed",
          f"{exposure.corridor} {as_of} -> {decision_level}")
    session.commit()
    return d


# ------------------------------------------------------- economic scenarios ---

def list_economic_scenarios(session: Session, user: User) -> list[EconomicScenario]:
    return list(session.exec(
        select(EconomicScenario).where(EconomicScenario.owner_user_id == user.id)
        .order_by(EconomicScenario.created_at.desc())))


def get_economic_scenario_owned(session: Session, user: User,
                                 scenario_id: int) -> EconomicScenario | None:
    s = session.get(EconomicScenario, scenario_id)
    if s is None or s.owner_user_id != user.id:
        return None
    return s


def create_economic_scenario(session: Session, user: User, *, scenario_id: str, corridor: str,
                              input_data: dict, result: dict,
                              client_id: int | None = None,
                              exposure_id: int | None = None) -> EconomicScenario:
    row = EconomicScenario(owner_user_id=user.id, client_id=client_id, exposure_id=exposure_id,
                            scenario_id=scenario_id, corridor=corridor,
                            input_json=json.dumps(input_data), result_json=json.dumps(result))
    session.add(row)
    session.commit()
    session.refresh(row)
    audit(session, user.id, "economic_scenario", row.id, "computed",
          f"{corridor} {scenario_id} -> {result.get('recommended_strategy')}")
    session.commit()
    return row


def latest_economic_scenario_for_exposure(session: Session,
                                           exposure_id: int) -> EconomicScenario | None:
    return session.exec(
        select(EconomicScenario).where(EconomicScenario.exposure_id == exposure_id)
        .order_by(EconomicScenario.created_at.desc())).first()


# -------------------------------------------------------- strategy decisions ---

def list_strategy_decisions(session: Session, user: User) -> list[StrategyDecision]:
    return list(session.exec(
        select(StrategyDecision).where(StrategyDecision.owner_user_id == user.id)
        .order_by(StrategyDecision.created_at.desc())))


def get_strategy_decision_owned(session: Session, user: User,
                                 decision_id: int) -> StrategyDecision | None:
    d = session.get(StrategyDecision, decision_id)
    if d is None or d.owner_user_id != user.id:
        return None
    return d


def create_strategy_decision(session: Session, user: User, *, scenario_id: str, corridor: str,
                              input_data: dict, result: dict,
                              client_id: int | None = None,
                              exposure_id: int | None = None) -> StrategyDecision:
    row = StrategyDecision(owner_user_id=user.id, client_id=client_id, exposure_id=exposure_id,
                            scenario_id=scenario_id, corridor=corridor,
                            input_json=json.dumps(input_data), result_json=json.dumps(result))
    session.add(row)
    session.commit()
    session.refresh(row)
    audit(session, user.id, "strategy_decision", row.id, "computed",
          f"{corridor} {scenario_id} -> {result.get('recommended')}")
    session.commit()
    return row


def latest_strategy_decision_for_exposure(session: Session,
                                           exposure_id: int) -> StrategyDecision | None:
    return session.exec(
        select(StrategyDecision).where(StrategyDecision.exposure_id == exposure_id)
        .order_by(StrategyDecision.created_at.desc())).first()


# -------------------------------------------------------------- api keys ---

def list_api_keys(session: Session, user: User) -> list[ApiKey]:
    return list(session.exec(select(ApiKey).where(ApiKey.user_id == user.id)
                              .order_by(ApiKey.created_at.desc())))


def create_api_key_row(session: Session, user: User, name: str, hashed_key: str) -> ApiKey:
    key = ApiKey(user_id=user.id, name=name.strip(), hashed_key=hashed_key)
    session.add(key)
    session.commit()
    session.refresh(key)
    audit(session, user.id, "api_key", key.id, "created", name)
    session.commit()
    return key


def revoke_api_key(session: Session, user: User, key_id: int) -> bool:
    key = session.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        return False
    key.revoked_at = datetime.now(timezone.utc)
    session.add(key)
    audit(session, user.id, "api_key", key.id, "revoked")
    session.commit()
    return True


# ----------------------------------------------------------------- alerts ---

def list_alert_subscriptions(session: Session, user: User) -> list[AlertSubscription]:
    return list(session.exec(
        select(AlertSubscription).where(AlertSubscription.user_id == user.id)))


def is_subscribed(session: Session, user: User, exposure_id: int) -> bool:
    return session.exec(
        select(AlertSubscription).where(AlertSubscription.user_id == user.id,
                                         AlertSubscription.exposure_id == exposure_id)
    ).first() is not None


def toggle_subscription(session: Session, user: User, exposure_id: int) -> bool:
    """Returns the new state (True = now subscribed)."""
    existing = session.exec(
        select(AlertSubscription).where(AlertSubscription.user_id == user.id,
                                         AlertSubscription.exposure_id == exposure_id)
    ).first()
    if existing:
        session.delete(existing)
        session.commit()
        return False
    session.add(AlertSubscription(user_id=user.id, exposure_id=exposure_id))
    session.commit()
    return True


def is_subscribed_economic_scenario(session: Session, user: User, economic_scenario_id: int) -> bool:
    return session.exec(
        select(EconomicScenarioSubscription).where(
            EconomicScenarioSubscription.user_id == user.id,
            EconomicScenarioSubscription.economic_scenario_id == economic_scenario_id)
    ).first() is not None


def toggle_economic_scenario_subscription(session: Session, user: User,
                                           economic_scenario_id: int) -> bool:
    """Returns the new state (True = now subscribed). Mirrors
    toggle_subscription() above, for EconomicScenario instead of Exposure."""
    existing = session.exec(
        select(EconomicScenarioSubscription).where(
            EconomicScenarioSubscription.user_id == user.id,
            EconomicScenarioSubscription.economic_scenario_id == economic_scenario_id)
    ).first()
    if existing:
        session.delete(existing)
        session.commit()
        return False
    session.add(EconomicScenarioSubscription(user_id=user.id,
                                               economic_scenario_id=economic_scenario_id))
    session.commit()
    return True

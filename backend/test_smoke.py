"""test_smoke.py — end-to-end walkthrough against SQLite, no Postgres/Docker
required. Not a substitute for testing against real Postgres before trusting
this with real data (see README.md), but catches wiring bugs — wrong
imports, broken routes, auth that doesn't actually gate anything — cheaply
and in CI.

    pytest test_smoke.py -v
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("SESSION_SECRET", "smoke-test-secret-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "sqlite:///./smoke_test.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app

TEST_ENGINE = create_engine("sqlite:///:memory:",
                             connect_args={"check_same_thread": False},
                             poolclass=StaticPool)


def _get_test_session():
    with Session(TEST_ENGINE) as session:
        yield session


@pytest.fixture(autouse=True)
def _fresh_db():
    SQLModel.metadata.create_all(TEST_ENGINE)
    app.dependency_overrides[get_session] = _get_test_session
    yield
    SQLModel.metadata.drop_all(TEST_ENGINE)
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_signup_requires_password_length(client):
    r = client.post("/signup", data={"email": "a@b.com", "password": "short"})
    assert r.status_code == 422


def test_full_walkthrough(client):
    # signup logs in immediately (session cookie set)
    r = client.post("/signup", data={"email": "broker@example.com",
                                      "password": "correct horse battery staple"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/clients"
    assert client.cookies.get("cx_session")

    # duplicate signup is rejected
    r = client.post("/signup", data={"email": "broker@example.com", "password": "x" * 12})
    assert r.status_code == 422

    # logged-out state can't see the dashboard
    anon = TestClient(app)
    r = anon.get("/clients", follow_redirects=False)
    assert r.status_code == 401

    # create a client
    r = client.post("/clients", data={"name": "Adriatic Bulk Ltd"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/api/v1/clients")
    assert r.status_code == 200
    clients = r.json()
    assert len(clients) == 1 and clients[0]["name"] == "Adriatic Bulk Ltd"
    client_id = clients[0]["id"]

    # create an exposure on a real corridor
    r = client.post(f"/clients/{client_id}/exposures", data={
        "corridor": "Bab-el-Mandeb", "commodity": "Steel", "annual_exposure": "38000000",
        "crisis_replacement_cost": "5000000", "currency": "EUR",
    }, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/api/v1/exposures", params={"client_id": client_id})
    exposures = r.json()
    assert len(exposures) == 1
    exposure_id = exposures[0]["id"]

    # an unknown corridor is rejected, not silently accepted
    r = client.post(f"/api/v1/exposures?client_id={client_id}", json={
        "corridor": "Not A Real Strait", "crisis_replacement_cost": 1000,
    })
    assert r.status_code == 422

    # compute a decision via the JSON API
    r = client.post(f"/api/v1/exposures/{exposure_id}/decide",
                     json={"alpha": 0.12, "window_months": 6})
    assert r.status_code == 201, r.text
    decision = r.json()
    assert decision["decision_level"] in {
        "act", "borderline", "wait", "borderline-wait", "always", "episode"}
    decision_id = decision["id"]

    # decision history shows it
    r = client.get(f"/api/v1/exposures/{exposure_id}/decisions")
    assert len(r.json()) == 1

    # exposure detail page renders (exercises the Jinja2 template + reading fetch)
    r = client.get(f"/clients/{client_id}/exposures/{exposure_id}")
    assert r.status_code == 200
    assert "Bab-el-Mandeb" in r.text

    # standdown report reuses services.py's generator and returns real HTML
    r = client.get(f"/api/v1/decisions/{decision_id}/report/standdown")
    assert r.status_code == 200
    assert "Decision of record" in r.text
    assert "Adriatic Bulk Ltd" in r.text

    # attestation report too
    r = client.get(f"/api/v1/decisions/{decision_id}/report/attestation")
    assert r.status_code == 200

    # subscribe to alerts, toggle it off
    r = client.post(f"/clients/{client_id}/exposures/{exposure_id}/subscribe",
                     follow_redirects=False)
    assert r.status_code == 303
    r = client.post(f"/clients/{client_id}/exposures/{exposure_id}/subscribe",
                     follow_redirects=False)
    assert r.status_code == 303

    # issue an API key and use it from a cookie-less client
    r = client.post("/account/api-keys", data={"name": "integration"}, follow_redirects=False)
    assert r.status_code == 303
    raw_key = r.headers["location"].split("created=")[1]

    api_client = TestClient(app)
    r = api_client.get("/api/v1/clients", headers={"Authorization": f"Bearer {raw_key}"})
    assert r.status_code == 200
    assert len(r.json()) == 1

    # a bogus key is rejected
    r = api_client.get("/api/v1/clients", headers={"Authorization": "Bearer cx_not_a_real_key"})
    assert r.status_code == 401

    # a wrong-password login is rejected
    r = client.post("/login", data={"email": "broker@example.com", "password": "wrong"})
    assert r.status_code == 401

    # one user cannot see another user's client
    other = TestClient(app)
    other.post("/signup", data={"email": "other@example.com", "password": "different password"})
    r = other.get(f"/api/v1/clients/{client_id}")
    assert r.status_code == 404


def test_economic_scenario_walkthrough(client):
    client.post("/signup", data={"email": "analyst@example.com",
                                  "password": "correct horse battery staple"})

    # the template is the same shape economic_engine.py's own CLI writes
    r = client.get("/api/v1/economic-scenarios/template")
    assert r.status_code == 200
    template = r.json()
    assert template["disruption"]["corridor"] == "Strait of Hormuz"

    # compute via the JSON API reproduces engine.md's own worked examples
    r = client.post("/api/v1/economic-scenarios", json=template)
    assert r.status_code == 201, r.text
    body = r.json()
    result = body["result"]
    assert result["recommended_strategy"] == "Partial reroute"
    assert abs(result["expected_loss_across_scenarios"] - 1_290_000) < 1
    assert result["current_status"] == "MITIGATION_JUSTIFIED"
    # historical_context is populated from docs/readings.json (tracked in
    # git), not the raw GPR vintage this test process doesn't have
    assert result["historical_context"] is not None
    assert result["historical_context"]["corridor"] == "Strait of Hormuz"
    scenario_id = body["id"]

    # an unknown corridor is rejected before compute() ever runs
    bad = dict(template)
    bad["disruption"] = dict(template["disruption"], corridor="Not A Real Strait")
    r = client.post("/api/v1/economic-scenarios", json=bad)
    assert r.status_code == 422

    # the dashboard form flow: paste JSON, compute, land on the detail page
    r = client.get("/economic-scenarios/new")
    assert r.status_code == 200
    assert "scenario_json" in r.text

    r = client.post("/economic-scenarios",
                     data={"scenario_json": json.dumps(template)}, follow_redirects=False)
    assert r.status_code == 303
    detail_url = r.headers["location"]

    r = client.get(detail_url)
    assert r.status_code == 200
    assert "Partial reroute" in r.text
    assert "MITIGATION JUSTIFIED" in r.text

    r = client.get(detail_url + "/report")
    assert r.status_code == 200
    assert "Economic exposure" in r.text

    # malformed JSON in the form is rejected with a helpful error, not a 500
    r = client.post("/economic-scenarios", data={"scenario_json": "{not valid json"})
    assert r.status_code == 422
    assert "JSONDecodeError" in r.text or "error" in r.text.lower()

    # cross-user isolation holds for scenarios too
    other = TestClient(app)
    other.post("/signup", data={"email": "other-analyst@example.com",
                                 "password": "a different password"})
    r = other.get(f"/api/v1/economic-scenarios/{scenario_id}")
    assert r.status_code == 404


def test_alerts_job_runs_without_error(client):
    """alerts.run() opens its own session against app.db.engine, which in
    this process points at DATABASE_URL (the sqlite file), not the
    in-memory engine the other tests override get_session with — so this
    just confirms the corridor-reading path it depends on works standalone,
    rather than exercising the full job against the overridden DB."""
    from app import engine as eng
    reading = eng.current_reading("Suez Canal")
    assert "tar" in reading and "band" in reading

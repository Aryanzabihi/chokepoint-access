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
# Force, not setdefault: a stray ambient DATABASE_URL (e.g. left over from an
# unrelated project's shell profile) must never leak into what this test
# suite writes to -- tests always run against this local sqlite file,
# regardless of what's already in the environment.
os.environ["DATABASE_URL"] = "sqlite:///./smoke_test.db"

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
    assert r.headers["location"] == "/home"
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


def _scenario_form(template: dict, *, exposure_id: int | None = None,
                    uncertainty: dict | None = None) -> dict:
    """Converts the nested scenario.json shape (what the JSON API and the
    engine's own template() return) into the flat field names the "new
    scenario" dashboard form now posts -- app/routers/economic.py's
    _form_to_data() is the inverse of this."""
    d, cargo, t, ins, econ, ce = (template["disruption"], template["cargo"], template["transport"],
                                   template["insurance"], template["economic"],
                                   template["commodity_effect"])
    fields = {
        "scenario_id": template.get("scenario_id", ""), "currency": template.get("currency", "EUR"),
        "corridor": d["corridor"], "probability": d["probability"] * 100, "delay_days": d["delay_days"],
        "commodity": cargo["commodity"], "quantity": cargo["quantity"],
        "cargo_value": cargo["cargo_value"], "inventory_level": cargo["inventory_level"],
        "baseline_freight": t["baseline_freight"], "disrupted_freight": t["disrupted_freight"],
        "fuel_cost": t["fuel_cost"], "port_charges": t["port_charges"],
        "handling_costs": t["handling_costs"], "rerouting_premium": t["rerouting_premium"],
        "baseline_premium": ins["baseline_premium"], "war_risk_premium": ins["war_risk_premium"],
        "additional_surcharge": ins["additional_surcharge"],
        "delay_cost_rate": econ["delay_cost_rate"],
        "inventory_holding_cost_rate": econ["inventory_holding_cost_rate"],
        "additional_inventory_qty": template.get("additional_inventory_qty", 0),
        "disruption_attributable_price_change": ce["disruption_attributable_price_change"],
        "market_wide_price_change": ce["market_wide_price_change"],
        "mitigation_cost": template.get("mitigation_cost"),
        "loss_if_disrupted": template.get("loss_if_disrupted"),
    }
    for i, row in enumerate(template["scenarios"]):
        fields[f"scenario_{i}_probability"] = row["probability"] * 100
        fields[f"scenario_{i}_conditional_loss"] = row["conditional_loss"]
    for i, row in enumerate(template["strategies"]):
        fields[f"strategy_{i}_direct_cost"] = row["direct_cost"]
        fields[f"strategy_{i}_residual_loss_estimate"] = row["residual_loss_estimate"]
    if exposure_id is not None:
        fields["exposure_id"] = exposure_id
    if uncertainty is not None:
        fields["enable_uncertainty"] = "on"
        fields["unc_probability_low"] = uncertainty["probability"]["low"] * 100
        fields["unc_probability_high"] = uncertainty["probability"]["high"] * 100
        fields["unc_cost_multiplier_low"] = uncertainty["cost_multiplier"]["low"] * 100
        fields["unc_cost_multiplier_high"] = uncertainty["cost_multiplier"]["high"] * 100
        fields["unc_n_simulations"] = uncertainty.get("n_simulations", 2000)
    return {k: str(v) for k, v in fields.items() if v is not None}


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

    # the dashboard form flow: labeled fields, pre-filled with the example,
    # compute, land on the detail page
    r = client.get("/economic-scenarios/new")
    assert r.status_code == 200
    assert 'name="probability"' in r.text and 'name="cargo_value"' in r.text

    r = client.post("/economic-scenarios", data=_scenario_form(template), follow_redirects=False)
    assert r.status_code == 303
    detail_url = r.headers["location"]

    r = client.get(detail_url)
    assert r.status_code == 200
    assert "Partial reroute" in r.text
    assert "MITIGATION JUSTIFIED" in r.text

    r = client.get(detail_url + "/report")
    assert r.status_code == 200
    assert "Economic exposure" in r.text

    # an unknown corridor via the form is rejected with a helpful error, not a 500
    bad_fields = _scenario_form(template)
    bad_fields["corridor"] = "Not A Real Strait"
    r = client.post("/economic-scenarios", data=bad_fields)
    assert r.status_code == 422
    assert "ValueError" in r.text or "error" in r.text.lower()

    # cross-user isolation holds for scenarios too
    other = TestClient(app)
    other.post("/signup", data={"email": "other-analyst@example.com",
                                 "password": "a different password"})
    r = other.get(f"/api/v1/economic-scenarios/{scenario_id}")
    assert r.status_code == 404


def test_economic_scenario_v2_features(client):
    """Uncertainty ranges, the per-strategy TAR band check, subscribe/
    unsubscribe, and the portfolio page's corridor-overlap note."""
    client.post("/signup", data={"email": "v2-analyst@example.com",
                                  "password": "correct horse battery staple"})

    template = client.get("/api/v1/economic-scenarios/template").json()

    # the plain template has no "uncertainty" block -> both stay off,
    # exactly like the src/ selftest already proves for compute() itself
    r = client.post("/api/v1/economic-scenarios", json=template)
    plain_result = r.json()["result"]
    assert plain_result["uncertainty"] is None
    assert plain_result["sensitivity"] is None
    # every strategy still gets a TAR band check, since loss_if_disrupted
    # is already in the template regardless of the uncertainty block
    by_name = {s["name"]: s for s in plain_result["strategy_comparison"]}
    assert abs(by_name["Partial reroute"]["implied_alpha"] - 0.16) < 1e-9
    assert by_name["Continue"]["historically_favorable"] is None  # direct_cost 0, guarded

    # opting in via the JSON API
    with_unc = dict(template)
    with_unc["uncertainty"] = {
        "cost_multiplier": {"low": 0.8, "high": 1.3},
        "probability": {"low": 0.05, "high": 0.45},
        "n_simulations": 1500, "seed": 3,
    }
    r = client.post("/api/v1/economic-scenarios", json=with_unc)
    assert r.status_code == 201, r.text
    unc_body = r.json()
    unc_result = unc_body["result"]
    u = unc_result["uncertainty"]
    assert u["expected_exposure_p10"] <= u["expected_exposure_p50"] <= u["expected_exposure_p90"]
    assert unc_result["sensitivity"] is not None
    unc_scenario_id = unc_body["id"]

    # the dashboard form path also exercises this, via the "add uncertainty
    # ranges" checkbox section rather than hand-edited JSON
    r = client.post("/economic-scenarios",
                     data=_scenario_form(template, uncertainty=with_unc["uncertainty"]),
                     follow_redirects=False)
    assert r.status_code == 303
    detail_url = r.headers["location"]
    r = client.get(detail_url)
    assert "Uncertainty" in r.text and "sensitive to" in r.text

    # subscribe / unsubscribe toggles, mirroring the exposure-alert pattern
    r = client.post(f"{detail_url}/subscribe", follow_redirects=False)
    assert r.status_code == 303
    assert "Turn off monthly re-check" in client.get(detail_url).text
    r = client.post(f"{detail_url}/subscribe", follow_redirects=False)
    assert r.status_code == 303
    assert "Re-check this monthly" in client.get(detail_url).text

    # portfolio page: two exposures on the same corridor should trigger
    # the overlap note, and each exposure's latest scenario should roll
    # into the per-currency totals
    client.post("/clients", data={"name": "Portfolio Test Ltd"}, follow_redirects=False)
    client_id = client.get("/api/v1/clients").json()[0]["id"]

    exposure_ids = []
    for _ in range(2):
        r = client.post(f"/api/v1/exposures?client_id={client_id}", json={
            "corridor": "Bab-el-Mandeb", "crisis_replacement_cost": 5_000_000, "currency": "EUR",
        })
        exposure_ids.append(r.json()["id"])

    for exp_id in exposure_ids:
        scenario = dict(template)
        scenario["disruption"] = dict(template["disruption"], corridor="Bab-el-Mandeb")
        r = client.post("/economic-scenarios",
                         data=_scenario_form(scenario, exposure_id=exp_id), follow_redirects=False)
        assert r.status_code == 303

    r = client.get(f"/clients/{client_id}/portfolio")
    assert r.status_code == 200
    assert "Bab-el-Mandeb" in r.text
    assert "affect all of them together" in r.text  # the overlap note
    assert "EUR 1,692,000" in r.text or "1,692,000" in r.text  # 846,000 x 2 exposures


def test_economic_scenario_detail_survives_pre_v2_data(client):
    """Regression: scenarios saved before the v2 engine added implied_alpha/
    historically_favorable to each strategy_comparison row used to 500 the
    detail page -- Jinja's Undefined can't be passed through "{:.0%}".format().
    Simulates that old shape directly (crud.create_economic_scenario, not the
    API, since the API always produces new-shape results now)."""
    from app import crud

    client.post("/signup", data={"email": "legacy-analyst@example.com",
                                  "password": "correct horse battery staple"})
    session = next(app.dependency_overrides[get_session]())
    user = crud.get_user_by_email(session, "legacy-analyst@example.com")

    old_result = {
        "scenario_id": "OLD-2025-001", "currency": "EUR", "corridor": "Strait of Hormuz",
        "expected_exposure": 846000.0, "avoidable_loss": 846000.0,
        "recommended_strategy": "Partial reroute", "current_status": "MITIGATION_JUSTIFIED",
        "baseline_breakdown": {"transport": 530000}, "disrupted_breakdown": {"transport": 930000},
        "strategy_comparison": [
            # no implied_alpha / historically_favorable / band_note keys at all
            {"name": "Partial reroute", "direct_cost": 700000, "residual_loss": 750000,
             "expected_total_cost": 1450000, "eligible": True},
        ],
        "uncertainty": None, "sensitivity": None, "historical_context": None,
        "explain": ["Mitigation Justified"], "model_version": "economic-engine-0.1",
        "timestamp": "2025-01-01T00:00:00+00:00", "confidence": "customer_quotation",
    }
    row = crud.create_economic_scenario(
        session, user, scenario_id="OLD-2025-001", corridor="Strait of Hormuz",
        input_data={}, result=old_result)

    r = client.get(f"/economic-scenarios/{row.id}")
    assert r.status_code == 200, r.text
    assert "Partial reroute" in r.text


_PCT_INTAKE_FIELDS = {"wacc_pct", "carrying_cost_pct_pa", "gross_margin_pct"}


def _decision_form(sample: dict, *, exposure_id: int | None = None) -> dict:
    """Converts an intake.py-shaped document (what decision_engine.py's own
    _sample_intake() and the JSON API's template both return) into the flat
    field names the "new decision" dashboard form posts -- the inverse of
    app/routers/strategy_decisions.py's _form_to_intake()."""
    fields = {"scenario_id": sample["scenario_id"], "corridor": sample["corridor"],
              "incoterm": sample["incoterm"], "tier": 3}
    for name, v in sample.get("fields", {}).items():
        if v is None:
            continue
        fields[f"field_{name}"] = v * 100 if name in _PCT_INTAKE_FIELDS else v
    strategies = sample.get("strategies", [])
    baseline_idx = next((i for i, s in enumerate(strategies) if s.get("is_baseline")), 0)
    fields["baseline_strategy"] = baseline_idx
    for i, s in enumerate(strategies):
        fields[f"strategy_{i}_name"] = s["name"]
        fields[f"strategy_{i}_direct_cost"] = s["direct_cost"]
        effects = s.get("effects", {}) or {}
        fields[f"strategy_{i}_delay_days_delta"] = effects.get("delay_days_delta", 0)
        fields[f"strategy_{i}_capacity_restored"] = (effects.get("capacity_restored") or 0) * 100
        wrpm = effects.get("war_risk_premium_multiplier")
        if wrpm is not None:
            fields[f"strategy_{i}_war_risk_premium_multiplier"] = wrpm * 100
        fields[f"strategy_{i}_days_of_cover_delta"] = effects.get("days_of_cover_delta", 0)
        fields[f"strategy_{i}_notes"] = s.get("notes", "")
    if exposure_id is not None:
        fields["exposure_id"] = exposure_id
    return {k: str(v) for k, v in fields.items() if v is not None}


def test_strategy_decision_walkthrough(client):
    """Mirrors test_economic_scenario_walkthrough for the v2 engine: JSON
    template -> compute -> dashboard form -> detail -> report, plus
    validation and cross-user isolation."""
    client.post("/signup", data={"email": "hormuz-analyst@example.com",
                                  "password": "correct horse battery staple"})

    # the template is the same blank shape intake.py's own template() writes
    r = client.get("/api/v1/strategy-decisions/template")
    assert r.status_code == 200
    blank = r.json()
    assert {"cargo_value", "wacc_pct", "war_risk_premium_quote"} <= set(blank["fields"])

    # a blank template has no strategies -- rejected before it's ever saved,
    # not a 500 (build_decision() raises ValueError, caught as 422)
    blank["corridor"] = "Strait of Hormuz"
    r = client.post("/api/v1/strategy-decisions", json=blank)
    assert r.status_code == 422

    # a real intake reproduces decision_engine.py's own selftest scenario
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from decision_engine import _sample_intake
    sample = _sample_intake()

    r = client.post("/api/v1/strategy-decisions", json=sample)
    assert r.status_code == 201, r.text
    body = r.json()
    result = body["result"]
    assert result["corridor"] == "Strait of Hormuz"
    assert result["recommended"] in ("Continue", "Partial reroute")
    decision_id = body["id"]

    # an unknown corridor is rejected before build_decision() ever runs
    bad = dict(sample, corridor="Not A Real Strait")
    r = client.post("/api/v1/strategy-decisions", json=bad)
    assert r.status_code == 422

    # the dashboard form flow: labeled/tiered fields, compute, land on detail
    r = client.get("/strategy-decisions/new")
    assert r.status_code == 200
    assert 'name="field_cargo_value"' in r.text and 'name="strategy_0_name"' in r.text

    form = _decision_form(sample)
    r = client.post("/strategy-decisions", data=form, follow_redirects=False)
    assert r.status_code == 303, r.text
    detail_url = r.headers["location"]

    r = client.get(detail_url)
    assert r.status_code == 200
    assert result["recommended"] in r.text

    r = client.get(detail_url + "/report")
    assert r.status_code == 200
    assert "Decision engine v2" in r.text

    # an unknown corridor via the form is rejected with a helpful error, not a 500
    bad_fields = dict(form)
    bad_fields["corridor"] = "Not A Real Strait"
    r = client.post("/strategy-decisions", data=bad_fields)
    assert r.status_code == 422
    assert "ValueError" in r.text or "error" in r.text.lower()

    # cross-user isolation holds for strategy decisions too
    other = TestClient(app)
    other.post("/signup", data={"email": "other-hormuz-analyst@example.com",
                                 "password": "a different password"})
    r = other.get(f"/api/v1/strategy-decisions/{decision_id}")
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


def test_alerts_run_economic_against_real_db():
    """run_economic() specifically -- built against the real file-backed
    DATABASE_URL engine alerts.py actually uses (not the in-memory override
    the other tests use for the HTTP app), since alerts.py opens its own
    session independent of any request. Fixtures are created directly with
    SQLModel rather than through the app, mirroring how a real month-old
    subscription would look: a saved scenario whose recorded reading is
    from a different month than the current one."""
    from sqlmodel import SQLModel

    from app import alerts, economic
    from app.db import engine as file_engine
    from app.models import (
        EconomicScenario, EconomicScenarioSubscription, User,
    )

    SQLModel.metadata.create_all(file_engine)
    template = economic.template_scenario()
    stale_result = economic.compute_scenario(template)
    # force a different as_of than whatever's actually current, so
    # run_economic() is guaranteed to see this as "a new month"
    stale_result["historical_context"]["reading"]["as_of"] = "1999-01-01"

    with Session(file_engine) as session:
        user = User(email="alerts-econ-test@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)

        scenario = EconomicScenario(
            owner_user_id=user.id, scenario_id=template["scenario_id"],
            corridor=template["disruption"]["corridor"],
            input_json=json.dumps(template), result_json=json.dumps(stale_result))
        session.add(scenario)
        session.commit()
        session.refresh(scenario)

        sub = EconomicScenarioSubscription(user_id=user.id, economic_scenario_id=scenario.id)
        session.add(sub)
        session.commit()
        session.refresh(sub)
        sub_id, original_scenario_id = sub.id, scenario.id

    sent = alerts.run_economic()
    assert sent >= 0  # doesn't crash; whether it emailed depends on RESEND_API_KEY

    with Session(file_engine) as session:
        refreshed_sub = session.get(EconomicScenarioSubscription, sub_id)
        # the subscription now points at a freshly-computed row, not the
        # stale one -- proves run_economic() actually recomputed and
        # re-pointed it, not just read the old data back
        assert refreshed_sub.economic_scenario_id != original_scenario_id
        new_scenario = session.get(EconomicScenario, refreshed_sub.economic_scenario_id)
        assert new_scenario is not None
        new_result = json.loads(new_scenario.result_json)
        assert new_result["historical_context"]["reading"]["as_of"] != "1999-01-01"


def test_alerts_run_strategy_decision_against_real_db():
    """run_strategy_decision() -- mirrors test_alerts_run_economic_against_real_db
    exactly, for the v2 engine's alert loop (closes the gap where
    StrategyDecision had no monthly re-check at all)."""
    import sys
    from pathlib import Path

    from sqlmodel import SQLModel

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from decision_engine import _sample_intake

    from app import alerts, strategy_decision as sd
    from app.db import engine as file_engine
    from app.models import StrategyDecision, StrategyDecisionSubscription, User

    SQLModel.metadata.create_all(file_engine)
    sample = _sample_intake()
    stale_result = sd.compute_decision(sample)
    # force a different as_of than whatever's actually current, so
    # run_strategy_decision() is guaranteed to see this as "a new month"
    stale_result["reading"]["as_of"] = "1999-01-01"

    with Session(file_engine) as session:
        user = User(email="alerts-strategy-test@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)

        decision = StrategyDecision(
            owner_user_id=user.id, scenario_id=sample["scenario_id"],
            corridor=sample["corridor"],
            input_json=json.dumps(sample), result_json=json.dumps(stale_result))
        session.add(decision)
        session.commit()
        session.refresh(decision)

        sub = StrategyDecisionSubscription(user_id=user.id, strategy_decision_id=decision.id)
        session.add(sub)
        session.commit()
        session.refresh(sub)
        sub_id, original_decision_id = sub.id, decision.id

    sent = alerts.run_strategy_decision()
    assert sent >= 0  # doesn't crash; whether it emailed depends on RESEND_API_KEY

    with Session(file_engine) as session:
        refreshed_sub = session.get(StrategyDecisionSubscription, sub_id)
        # the subscription now points at a freshly-computed row, not the
        # stale one -- proves run_strategy_decision() actually recomputed and
        # re-pointed it, not just read the old data back
        assert refreshed_sub.strategy_decision_id != original_decision_id
        new_decision = session.get(StrategyDecision, refreshed_sub.strategy_decision_id)
        assert new_decision is not None
        new_result = json.loads(new_decision.result_json)
        assert new_result["reading"]["as_of"] != "1999-01-01"


def test_home_dashboard(client):
    """Every exposure across every client shows up on /home, in one row
    each -- an exposure with no strategy decision yet says so plainly
    (build-one CTA), one with a saved decision shows its recommendation."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from decision_engine import _sample_intake

    client.post("/signup", data={"email": "home-dashboard@example.com",
                                  "password": "correct horse battery staple"})

    client.post("/clients", data={"name": "Client A"})
    client.post("/clients", data={"name": "Client B"})
    clients_by_name = {c["name"]: c["id"] for c in client.get("/api/v1/clients").json()}
    client_a_id, client_b_id = clients_by_name["Client A"], clients_by_name["Client B"]

    client.post(f"/clients/{client_a_id}/exposures",
                data={"corridor": "Strait of Hormuz", "crisis_replacement_cost": "100000"})
    client.post(f"/clients/{client_b_id}/exposures",
                data={"corridor": "Suez Canal", "crisis_replacement_cost": "50000"})

    hormuz_exp = client.get(f"/api/v1/exposures?client_id={client_a_id}").json()[0]
    suez_exp = client.get(f"/api/v1/exposures?client_id={client_b_id}").json()[0]

    # before any decision exists, both rows show the "build one" CTA
    r = client.get("/home")
    assert r.status_code == 200
    assert "Client A" in r.text and "Client B" in r.text
    assert "Strait of Hormuz" in r.text and "Suez Canal" in r.text
    assert r.text.count("no decision yet") == 2

    # build a strategy decision for the Hormuz exposure via the dashboard form
    sample = _sample_intake()
    form = _decision_form(sample, exposure_id=hormuz_exp["id"])
    r = client.post("/strategy-decisions", data=form, follow_redirects=False)
    assert r.status_code == 303, r.text

    r = client.get("/home")
    assert r.status_code == 200
    assert r.text.count("no decision yet") == 1   # Suez still has none
    assert "Continue" in r.text or "Partial reroute" in r.text  # the recommended strategy shows somewhere
    assert suez_exp["corridor"] == "Suez Canal"   # sanity: the untouched exposure is the right one

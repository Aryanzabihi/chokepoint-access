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


def test_signup_company_name_is_optional(client):
    """company_name is a plain optional field on User -- blank stays None,
    supplied gets stored and normalized like everything else here (blank
    string collapses to None, same convention as _text() elsewhere)."""
    from app import crud
    from app.db import get_session as _get_session

    r = client.post("/signup", data={"email": "no-company@example.com",
                                      "password": "correct horse battery staple"},
                     follow_redirects=False)
    assert r.status_code == 303
    session = next(app.dependency_overrides[_get_session]())
    user = crud.get_user_by_email(session, "no-company@example.com")
    assert user.company_name is None

    r = client.post("/signup", data={"email": "with-company@example.com",
                                      "password": "correct horse battery staple",
                                      "company_name": "Adriatic Bulk Ltd"},
                     follow_redirects=False)
    assert r.status_code == 303
    user2 = crud.get_user_by_email(session, "with-company@example.com")
    assert user2.company_name == "Adriatic Bulk Ltd"


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
    """Uncertainty ranges, the per-strategy TAR band check, and subscribe/
    unsubscribe. Portfolio-page aggregation is tested against
    StrategyDecision instead, in test_strategy_decision_walkthrough --
    the portfolio page reads that engine now, not this one."""
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
    app/routers/strategy_decisions.py's _form_to_intake(). No strategy_*
    fields any more: Input no longer collects strategies (see
    default_strategies_for_stage in app/strategy_decision.py) -- whatever
    sample["strategies"] holds is only read by tests that call
    compute_decision()/the JSON API directly."""
    fields = {"scenario_id": sample["scenario_id"], "corridor": sample["corridor"],
              "incoterm": sample["incoterm"], "tier": 3}
    for name, v in sample.get("fields", {}).items():
        if v is None:
            continue
        fields[f"field_{name}"] = v * 100 if name in _PCT_INTAKE_FIELDS else v
    if exposure_id is not None:
        fields["exposure_id"] = exposure_id
    return {k: str(v) for k, v in fields.items() if v is not None}


def _create_decision(client, sample: dict, *, exposure_id: int | None = None,
                      order_id: int | None = None, decision_id: int | None = None):
    """Input -> TAR Exposure -> TAR Analysis, replacing the old single-POST
    helper now that a StrategyDecision is no longer created by one form
    submit: POST /strategy-decisions computes and renders the Exposure
    template directly (200, nothing persisted), then POST
    /strategy-decisions/analyze is the one call that actually inserts a row
    and redirects (303) -- mirrors exactly what the real form's hidden-field
    carry-forward does between the two pages."""
    form = _decision_form(sample, exposure_id=exposure_id)
    if order_id is not None:
        form["order_id"] = str(order_id)
    if decision_id is not None:
        form["decision_id"] = str(decision_id)
    r = client.post("/strategy-decisions", data=form)
    assert r.status_code == 200, r.text
    assert "TAR Exposure" in r.text
    return client.post("/strategy-decisions/analyze", data=form, follow_redirects=False)


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

    # a bare /strategy-decisions/new (no order_id/exposure_id/decision_id --
    # the only way to reach the old always-re-ask-everything form) now shows
    # an order picker instead, not the form directly -- no orders exist yet
    # at this point in the walkthrough, so this also covers that empty state
    r = client.get("/strategy-decisions/new")
    assert r.status_code == 200
    assert "Which order do you want to analyze?" in r.text
    assert "No orders yet" in r.text and 'href="/orders/new"' in r.text
    assert 'name="field_cargo_value"' not in r.text

    # the dashboard form flow: labeled/tiered fields (no strategy table --
    # strategies are generated by the engine at the Analyze step, not
    # constructed by hand on Input), compute, land on detail. Linked to a
    # bare order (no cargo_value on file) so field_cargo_value renders as
    # the normal editable input, same as this check always intended --
    # order_field()'s hidden-input path is covered separately, in
    # test_order_walkthrough.
    order_r = client.post("/orders", data={"corridor": "Strait of Hormuz", "stage": "pre_order",
                                            "sku": "walkthrough order", "quantity": "1",
                                            "quantity_unit": "MT"}, follow_redirects=False)
    bare_order_id = int(order_r.headers["location"].rsplit("/", 1)[-1])
    r = client.get(f"/strategy-decisions/new?order_id={bare_order_id}")
    assert r.status_code == 200
    assert 'name="field_cargo_value"' in r.text
    assert "strategy_0_name" not in r.text

    r = _create_decision(client, sample)
    assert r.status_code == 303, r.text
    detail_url = r.headers["location"]

    r = client.get(detail_url)
    assert r.status_code == 200
    # the form path uses default_strategies_for_stage(None) (Continue /
    # Partial reroute), not sample["strategies"]'s own hand-tuned effects --
    # verified separately that this still recommends "Continue" here, same
    # as the JSON-path result above, though the two are no longer
    # guaranteed to agree in general since the strategy sets differ
    assert "Continue" in r.text

    # "Why TAR recommends this" (Batch B) -- four-bucket synthesis, built
    # entirely from numbers already shown elsewhere on the page
    assert "Why TAR recommends this" in r.text
    assert "① Demand" in r.text and "② Supply" in r.text
    assert "③ Geopolitical exposure" in r.text and "④ Economic impact" in r.text

    # "Decision record" (Batch B) -- consolidated summary card. Decision
    # maker is the real owning user (this app has no separate approver
    # concept), not a fabricated title -- appears here AND in the nav stamp
    assert "Decision record" in r.text
    assert r.text.count("hormuz-analyst@example.com") >= 2
    assert "Estimated cost" in r.text and "Approved cost" not in r.text  # still draft

    # Time Recovery module (src/recovery.py) -- wired into the detail page
    # via strategy_decision.recovery_snapshot(). This reads the REAL, live
    # docs/readings.json (refreshed monthly by tar-monitor[bot]), so the
    # specific recovery_state can legitimately change month to month --
    # assert on the structural/fixed parts only, not today's live value.
    # Strait of Hormuz has had onset history since 1987, so >=2 completed
    # alarm episodes is a permanent historical fact, not something this
    # month's data could regress below.
    assert "Recovery state:" in r.text
    assert any(f">{s}</span>" in r.text for s in
              ("ESCALATING", "PEAKED", "RECOVERING", "STALLED", "RE_ESCALATING", "RECOVERED"))
    assert "completed alarm episodes since 1985" in r.text
    assert "global monthly TAR alarm series" in r.text        # scope_note, fixed text
    assert "never a projection of what happens next" in r.text  # trend_disclaimer, fixed text

    # low_warning_note() enrichment on corridor_note: unlike recovery_state
    # above, this describes 4 already-COMPLETED historical Hormuz onsets
    # (1987/1990/2003/2026), all in the past -- stable, safe to pin exactly,
    # not subject to the same monthly-drift caveat.
    assert ("2 of 4 recorded onset(s) occurred with no alarm active the prior month"
           in r.text)

    r = client.get(detail_url + "/report")
    assert r.status_code == 200
    assert "Decision engine v2" in r.text

    # an unknown corridor via the form is rejected with a helpful error, not a
    # 500 -- caught at the Input -> Exposure step, before /analyze ever runs
    bad_fields = _decision_form(sample)
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

    from app import strategy_decision as sd

    # portfolio page: two exposures on the same corridor should trigger the
    # overlap note, and each exposure's latest strategy decision should
    # roll into the per-currency totals -- the portfolio page reads
    # StrategyDecision now, not EconomicScenario (see
    # test_economic_scenario_v2_features's docstring).
    client.post("/clients", data={"name": "Portfolio Test Ltd"})
    client_id = next(c["id"] for c in client.get("/api/v1/clients").json()
                     if c["name"] == "Portfolio Test Ltd")

    exposure_ids = []
    for _ in range(2):
        r = client.post(f"/api/v1/exposures?client_id={client_id}", json={
            "corridor": "Bab-el-Mandeb", "crisis_replacement_cost": 5_000_000, "currency": "EUR",
        })
        exposure_ids.append(r.json()["id"])

    bab_sample = dict(sample, corridor="Bab-el-Mandeb")
    for exp_id in exposure_ids:
        r = _create_decision(client, bab_sample, exposure_id=exp_id)
        assert r.status_code == 303, r.text

    r = client.get(f"/clients/{client_id}/portfolio")
    assert r.status_code == 200
    assert "Bab-el-Mandeb" in r.text
    assert "affect all of them together" in r.text  # the overlap note
    # two identical decisions -> exactly double one decision's own avoidable figure
    bab_result = sd.compute_decision(bab_sample)
    expected_avoidable = round(bab_result["exposure"]["avoidable"] * 2)
    assert f"{expected_avoidable:,}" in r.text, (expected_avoidable, r.text)


def test_decision_detail_tolerates_missing_procurement_window(client):
    """Regression test for a live 500 on Render: a StrategyDecision saved
    before build_decision() started returning a "procurement_window" key
    has a result_json that lacks it entirely. strategy_decision_detail.html
    used to do `{% set pw = result.procurement_window %}` then read
    `pw.procurement_window_days` unconditionally, which raises
    jinja2.exceptions.UndefinedError on a dict missing that key (confirmed
    against the real Render logs, not guessed). decision_brief_html()
    (decision_engine.py) already guards this same field with
    `decision.get("procurement_window") or {}` -- the template now matches
    that same established pattern instead of assuming the key exists."""
    import sys
    from pathlib import Path

    from sqlmodel import select

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from decision_engine import _sample_intake

    from app import strategy_decision as sd
    from app.models import StrategyDecision, User

    client.post("/signup", data={"email": "old-decision-shape@example.com",
                                  "password": "correct horse battery staple"})

    sample = _sample_intake()
    result = sd.compute_decision(sample)
    assert "procurement_window" in result   # current engine always sets it
    del result["procurement_window"]        # simulate a pre-existing-field row

    with Session(TEST_ENGINE) as session:
        user = session.exec(
            select(User).where(User.email == "old-decision-shape@example.com")).first()
        decision = StrategyDecision(
            owner_user_id=user.id, scenario_id=sample["scenario_id"],
            corridor=sample["corridor"],
            input_json=json.dumps(sample), result_json=json.dumps(result))
        session.add(decision)
        session.commit()
        session.refresh(decision)
        decision_id = decision.id

    r = client.get(f"/strategy-decisions/{decision_id}")
    assert r.status_code == 200, r.text   # used to be a bare 500 on old rows
    assert "Decision record" in r.text    # rest of the page still renders


def test_order_walkthrough(client):
    """ProcurementOrder: the pre-order / PO-placed / in-transit / delivered
    lifecycle. Unlike exposure_id linking (which pre-fills nothing on the
    new-decision form -- see test_strategy_decision_walkthrough, which only
    checks field names appear), order_id linking's entire point is that it
    DOES pre-fill from the order, so this asserts actual value="..." content,
    not just field presence. Also covers the one genuinely mutating action
    in this whole app (advance-stage) and its audit trail, and the
    draft/approved status flip on StrategyDecision."""
    client.post("/signup", data={"email": "order-walkthrough@example.com",
                                  "password": "correct horse battery staple"})

    r = client.get("/orders/new")
    assert r.status_code == 200
    assert 'name="corridor"' in r.text and 'name="sku"' in r.text

    r = client.post("/orders", data={
        "corridor": "Strait of Hormuz", "stage": "pre_order", "sku": "Industrial component A",
        "quantity": "45000", "quantity_unit": "MT", "cargo_value": "5000000", "incoterm": "FOB",
        "supplier": "Acme Corp", "currency": "EUR",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    order_id = int(r.headers["location"].rsplit("/", 1)[-1])

    r = client.get(f"/orders/{order_id}")
    assert r.status_code == 200
    assert "Industrial component A" in r.text and "Pre-order" in r.text
    assert "Build a strategy decision from this order" in r.text

    # an unknown corridor is rejected with a helpful error, not a 500
    r = client.post("/orders", data={"corridor": "Not A Real Strait", "sku": "x",
                                      "quantity": "1", "quantity_unit": "MT"})
    assert r.status_code == 422

    # order-linking pre-fills the new-decision form -- the whole point of
    # order_id (unlike exposure_id, which pre-fills nothing today).
    # Stage-appropriate default strategies (Wait/Buy now for pre_order) are
    # no longer shown here -- Input has no strategy table any more, the
    # engine picks them at the Analyze step -- checked below on the detail
    # page instead, once a decision actually exists.
    r = client.get(f"/strategy-decisions/new?order_id={order_id}")
    assert r.status_code == 200
    assert f'value="{order_id}"' in r.text
    # corridor comes from the order -- shown read-only (hidden input + plain
    # text), never a re-editable dropdown, so the order stays authoritative
    assert 'type="hidden" name="corridor" value="Strait of Hormuz"' in r.text
    assert "from the order" in r.text
    assert 'value="45000' in r.text          # quantity pre-filled
    assert 'value="5000000' in r.text        # cargo_value pre-filled
    assert "strategy_0_name" not in r.text

    order_form = {
        "scenario_id": "ORDER-TEST-001", "corridor": "Strait of Hormuz", "incoterm": "FOB",
        "tier": "1", "order_id": str(order_id),
        "field_ship_date": "2026-11-01", "field_cargo_value": "5000000", "field_quantity": "45000",
        "field_quantity_unit": "MT", "field_contract_freight_rate": "400000",
        "field_contract_transit_time_days": "25", "field_days_of_cover": "10",
        "field_delay_days_estimate": "11",
    }
    r = client.post("/strategy-decisions", data=order_form)
    assert r.status_code == 200, r.text
    assert "TAR Exposure" in r.text
    r = client.post("/strategy-decisions/analyze", data=order_form, follow_redirects=False)
    assert r.status_code == 303, r.text
    decision_url = r.headers["location"]
    decision_id = int(decision_url.rsplit("/", 1)[-1])

    r = client.get(decision_url)
    assert r.status_code == 200
    assert "Procurement question: should you secure this requirement now?" in r.text
    assert f"/orders/{order_id}" in r.text            # linked back to the order
    assert ">draft<" in r.text and "Approve" in r.text
    # pre_order's stage-appropriate default strategies (Wait/Buy now), now
    # generated by the engine at Analyze rather than typed on Input, show up
    # as editable cards on the draft-mode detail page
    assert 'value="Wait"' in r.text and 'value="Buy now"' in r.text

    # the order's own detail page lists the decision built from it
    r = client.get(f"/orders/{order_id}")
    assert "ORDER-TEST-001" in r.text

    # advance the stage -- the one mutating action in this app -- with real
    # PO fields, and confirm both the visible change and the audit trail
    r = client.post(f"/orders/{order_id}/advance-stage", data={
        "new_stage": "po_placed", "po_number": "PO-9001", "unit_price": "111.5",
        "ship_date": "2026-11-01", "contract_transit_time_days": "25",
        "contract_freight_rate": "400000",
    }, follow_redirects=False)
    assert r.status_code == 303
    r = client.get(f"/orders/{order_id}")
    assert "PO placed" in r.text and "PO-9001" in r.text

    from sqlmodel import select

    from app.models import AuditEvent

    session = next(app.dependency_overrides[get_session]())
    order_events = session.exec(
        select(AuditEvent).where(AuditEvent.entity_type == "procurement_order")).all()
    assert any(e.action == "created" for e in order_events)
    stage_event = next(e for e in order_events if e.action == "stage_advanced")
    stage_detail = json.loads(stage_event.detail)
    assert stage_detail["from"] == "pre_order" and stage_detail["to"] == "po_placed"
    assert stage_detail["fields"]["po_number"] == "PO-9001"

    # approve the decision -- the other genuinely mutating action
    r = client.post(f"/strategy-decisions/{decision_id}/approve", follow_redirects=False)
    assert r.status_code == 303
    r = client.get(decision_url)
    assert ">approved<" in r.text

    # a decision built from an in_transit order gets the Reroute default,
    # not pre_order's Wait/Buy now -- verified on the draft-mode detail
    # page, since Input itself no longer shows any strategy at all
    r = client.post("/orders", data={"corridor": "Bab-el-Mandeb", "stage": "in_transit",
                                      "sku": "Widget B", "quantity": "1000",
                                      "quantity_unit": "units"}, follow_redirects=False)
    order2_id = int(r.headers["location"].rsplit("/", 1)[-1])
    r = client.get(f"/strategy-decisions/new?order_id={order2_id}")
    assert "strategy_0_name" not in r.text

    order2_form = {
        "scenario_id": "ORDER-TEST-002", "corridor": "Bab-el-Mandeb", "incoterm": "FOB",
        "tier": "1", "order_id": str(order2_id),
        "field_ship_date": "2026-11-01", "field_cargo_value": "1000000", "field_quantity": "1000",
        "field_quantity_unit": "units", "field_contract_freight_rate": "50000",
        "field_contract_transit_time_days": "20", "field_days_of_cover": "10",
        "field_delay_days_estimate": "7",
    }
    r = client.post("/strategy-decisions", data=order2_form)
    assert r.status_code == 200, r.text
    r = client.post("/strategy-decisions/analyze", data=order2_form, follow_redirects=False)
    assert r.status_code == 303, r.text
    r = client.get(r.headers["location"])
    assert 'value="Reroute"' in r.text

    # cross-user isolation holds for orders too
    other = TestClient(app)
    other.post("/signup", data={"email": "other-order-walkthrough@example.com",
                                 "password": "a different password"})
    r = other.get(f"/api/v1/orders/{order_id}")
    assert r.status_code == 404


def test_order_edit_and_delete(client):
    """Edit corrects the order's own descriptive fields (never the
    stage-transition ones advance-stage owns); delete is blocked while any
    StrategyDecision references the order, and only unblocked once none do
    -- this app's first real deletion, so the guard is the point of the
    test, not an afterthought."""
    client.post("/signup", data={"email": "order-edit-delete@example.com",
                                  "password": "correct horse battery staple"})

    r = client.post("/orders", data={
        "corridor": "Strait of Hormuz", "stage": "pre_order", "sku": "Widget A",
        "quantity": "100", "quantity_unit": "MT", "cargo_value": "50000",
    }, follow_redirects=False)
    order_id = int(r.headers["location"].rsplit("/", 1)[-1])

    r = client.get(f"/orders/{order_id}/edit")
    assert r.status_code == 200
    assert 'value="Widget A"' in r.text
    assert 'value="100' in r.text

    # unknown corridor rejected, same as create
    r = client.post(f"/orders/{order_id}/edit", data={
        "corridor": "Not A Real Strait", "sku": "Widget A", "quantity": "100",
        "quantity_unit": "MT",
    })
    assert r.status_code == 422

    r = client.post(f"/orders/{order_id}/edit", data={
        "corridor": "Bab-el-Mandeb", "sku": "Widget A2", "quantity": "250",
        "quantity_unit": "MT", "cargo_value": "75000", "supplier": "New Supplier Ltd",
    }, follow_redirects=False)
    assert r.status_code == 303
    r = client.get(f"/orders/{order_id}")
    assert r.status_code == 200
    assert "Widget A2" in r.text and "Bab-el-Mandeb" in r.text and "New Supplier Ltd" in r.text
    assert "250" in r.text

    from sqlmodel import select

    from app.models import AuditEvent

    session = next(app.dependency_overrides[get_session]())
    update_event = next(e for e in session.exec(
        select(AuditEvent).where(AuditEvent.entity_type == "procurement_order",
                                 AuditEvent.action == "updated")))
    changed = json.loads(update_event.detail)
    assert changed["sku"] == "Widget A2" and changed["corridor"] == "Bab-el-Mandeb"

    # build a real decision against this order, then confirm delete is blocked
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from decision_engine import _sample_intake

    sample = dict(_sample_intake(), corridor="Bab-el-Mandeb")
    r = client.post("/strategy-decisions/analyze",
                    data=_decision_form(sample) | {"order_id": str(order_id)},
                    follow_redirects=False)
    assert r.status_code == 303, r.text

    r = client.post(f"/orders/{order_id}/delete")
    assert r.status_code == 409
    assert "strategy decision" in r.text.lower()
    r = client.get(f"/orders/{order_id}")
    assert r.status_code == 200   # still there -- delete was refused, not silently ignored

    # a second order with no decisions against it deletes cleanly
    r = client.post("/orders", data={
        "corridor": "Suez Canal", "stage": "pre_order", "sku": "Widget B",
        "quantity": "10", "quantity_unit": "MT",
    }, follow_redirects=False)
    order2_id = int(r.headers["location"].rsplit("/", 1)[-1])

    r = client.post(f"/orders/{order2_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/orders"
    r = client.get(f"/orders/{order2_id}")
    assert r.status_code == 404

    session = next(app.dependency_overrides[get_session]())
    delete_event = next(e for e in session.exec(
        select(AuditEvent).where(AuditEvent.entity_type == "procurement_order",
                                 AuditEvent.action == "deleted")))
    assert "Widget B" in delete_event.detail

    # cross-user isolation holds for edit/delete too
    other = TestClient(app)
    other.post("/signup", data={"email": "other-order-edit-delete@example.com",
                                 "password": "a different password"})
    assert other.get(f"/orders/{order_id}/edit").status_code == 404
    assert other.post(f"/orders/{order_id}/edit", data={"corridor": "Suez Canal"}).status_code == 404
    assert other.post(f"/orders/{order_id}/delete").status_code == 404


def test_decision_requires_an_order(client):
    """The real gap behind "the Decision page re-asks what's already on the
    Order": a bare GET /strategy-decisions/new (no order_id, no exposure_id,
    no decision_id) is reachable from the Decisions list page's own "New
    decision"/"Build one" links, and used to render the full blank form --
    asking again for corridor/incoterm/quantity/quantity_unit/cargo_value
    even when a linked order already has them (verified directly, not
    assumed, before writing this fix: those fields already come through
    correctly as hidden/read-only when order_id IS passed -- see
    test_order_walkthrough's own assertions on this). The actual missing
    piece was that nothing stopped a decision from being started with NO
    order linked at all through the app's own normal navigation."""
    client.post("/signup", data={"email": "decision-needs-order@example.com",
                                  "password": "correct horse battery staple"})

    # empty state: no orders yet
    r = client.get("/strategy-decisions/new")
    assert r.status_code == 200
    assert "Which order do you want to analyze?" in r.text
    assert "No orders yet" in r.text
    assert 'href="/orders/new"' in r.text
    assert 'name="field_cargo_value"' not in r.text   # never the blank form directly

    # the Decisions LIST page's own creation links route through the same
    # guarded endpoint -- confirms the fix closes that entry point too,
    # not just a hypothetical direct URL
    r = client.get("/strategy-decisions")
    assert 'href="/strategy-decisions/new"' in r.text

    r = client.post("/orders", data={
        "corridor": "Strait of Hormuz", "stage": "pre_order", "sku": "Industrial Component A",
        "quantity": "1000", "quantity_unit": "MT", "incoterm": "FOB", "supplier": "Supplier A",
    }, follow_redirects=False)
    order_id = int(r.headers["location"].rsplit("/", 1)[-1])

    # now the picker lists it, one click away from a real analysis
    r = client.get("/strategy-decisions/new")
    assert r.status_code == 200
    assert "Industrial Component A" in r.text
    assert f'href="/strategy-decisions/new?order_id={order_id}"' in r.text

    # cargo_value is genuinely absent from this order (never supplied) --
    # missing-data UX offers BOTH "use it for this decision only" and a
    # direct link back to the order, rather than only the first option
    r = client.get(f"/strategy-decisions/new?order_id={order_id}")
    assert r.status_code == 200
    assert f"Not on order #{order_id}" in r.text
    assert f'href="/orders/{order_id}/edit"' in r.text
    assert "update the order instead" in r.text
    # and the fields the order DOES have are still hidden/read-only, not
    # re-asked -- the part that already worked, still does
    assert 'type="hidden" name="corridor" value="Strait of Hormuz"' in r.text
    assert 'type="hidden" name="field_quantity" value="1000' in r.text

    # exposure_id-linking is a separate, pre-existing provenance (a client
    # exposure, not a procurement order) and must still reach the real form
    # directly, unaffected by this guard
    client.post("/clients", data={"name": "Guard Test Client"})
    client_id = next(c["id"] for c in client.get("/api/v1/clients").json()
                     if c["name"] == "Guard Test Client")
    exp = client.post(f"/api/v1/exposures?client_id={client_id}",
                      json={"corridor": "Suez Canal", "crisis_replacement_cost": 1_000_000,
                            "currency": "EUR"}).json()
    r = client.get(f"/strategy-decisions/new?exposure_id={exp['id']}")
    assert r.status_code == 200
    assert "Which order do you want to analyze?" not in r.text


def test_decision_lifecycle(client):
    """draft -> reject, draft -> approve -> execute, and the recalculate
    loop (workflow.md's "go to market -> reassess with TAR"). The
    recalculate case uses a hand-verified quote change: raising
    war_risk_premium_quote from the sample's 340,000 to 3,500,000 actually
    flips decision_engine's own recommendation from Continue to Partial
    reroute (checked directly against src/decision_engine.py before writing
    this assertion, not guessed) -- proves the loop really recomputes,
    not just re-saves."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from decision_engine import _sample_intake

    client.post("/signup", data={"email": "lifecycle@example.com",
                                  "password": "correct horse battery staple"})
    sample = _sample_intake()

    # --- draft -> reject ---
    r = _create_decision(client, sample)
    assert r.status_code == 303, r.text
    decision1_url = r.headers["location"]
    decision1_id = int(decision1_url.rsplit("/", 1)[-1])

    r = client.get(decision1_url)
    assert ">draft<" in r.text and "Approve" in r.text and "Modify" in r.text
    # decision1 recommends the baseline ("Continue" -- see the recalculate section
    # below), i.e. a WAIT verdict -- Reject is deliberately hidden for WAIT
    # (workflow.md section 9: WAIT offers Approve/Modify only, ACT offers all 3).
    assert "Reject" not in r.text
    assert f'href="/strategy-decisions/new?decision_id={decision1_id}"' in r.text  # Modify, fixed

    r = client.post(f"/strategy-decisions/{decision1_id}/reject", follow_redirects=False)
    assert r.status_code == 303
    r = client.get(decision1_url)
    assert ">rejected<" in r.text

    # --- draft -> approve -> execute ---
    r = _create_decision(client, sample)
    decision2_url = r.headers["location"]
    decision2_id = int(decision2_url.rsplit("/", 1)[-1])
    client.post(f"/strategy-decisions/{decision2_id}/approve", follow_redirects=False)
    r = client.get(decision2_url)
    assert ">approved<" in r.text
    assert "Mark executed" in r.text and "Reassess with new quotes" in r.text

    r = client.post(f"/strategy-decisions/{decision2_id}/execute", follow_redirects=False)
    assert r.status_code == 303
    r = client.get(decision2_url)
    assert ">executed<" in r.text
    assert "Approved cost" in r.text and "Estimated cost" not in r.text   # Decision record

    # --- recalculate: a real quote change that really flips the recommendation ---
    r = _create_decision(client, sample)
    decision3_url = r.headers["location"]
    decision3_id = int(decision3_url.rsplit("/", 1)[-1])
    r = client.get(decision3_url)
    assert "Continue" in r.text   # the sample's original, base-rate-fallback recommendation

    r = client.get(f"/strategy-decisions/{decision3_id}/recalculate")
    assert r.status_code == 200
    assert 'value="340000.0"' in r.text   # the sample's current war_risk_premium_quote, pre-filled

    r = client.post(f"/strategy-decisions/{decision3_id}/recalculate",
                     data={"field_war_risk_premium_quote": "3500000"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    decision4_id = int(r.headers["location"].rsplit("/", 1)[-1])
    assert decision4_id != decision3_id   # a new row, not an in-place edit

    # decision-specific values are snapshots, not live references -- the
    # quote that changed for decision4 must NOT retroactively change what
    # decision3 itself is on record as having used
    from sqlmodel import select

    from app.models import StrategyDecision

    session = next(app.dependency_overrides[get_session]())
    decision3_row = session.get(StrategyDecision, decision3_id)
    decision3_input = json.loads(decision3_row.input_json)
    assert decision3_input["fields"]["war_risk_premium_quote"] == 340000.0
    decision4_row = session.get(StrategyDecision, decision4_id)
    decision4_input = json.loads(decision4_row.input_json)
    assert decision4_input["fields"]["war_risk_premium_quote"] == 3500000.0

    r = client.get(r.headers["location"])
    assert "Partial reroute" in r.text
    assert "Recalculated from" in r.text and "<strong>changed</strong>" in r.text
    assert "Reject" in r.text   # decision4 recommends ACT (Partial reroute, not the
                               # baseline) -- Reject is shown again for ACT verdicts

    # recalculating again with the SAME quote is correctly reported as unchanged
    r = client.post(f"/strategy-decisions/{decision4_id}/recalculate",
                     data={"field_war_risk_premium_quote": "3500000"}, follow_redirects=False)
    r = client.get(r.headers["location"])
    assert "recommendation is unchanged" in r.text

    # the decisions list surfaces each row's status as a badge
    r = client.get("/strategy-decisions")
    assert r.status_code == 200
    assert f'href="/strategy-decisions/{decision1_id}"' in r.text and ">rejected<" in r.text
    assert f'href="/strategy-decisions/{decision2_id}"' in r.text and ">executed<" in r.text

    # Active/Monitoring/History tabs filter by status (decision1=rejected,
    # decision2=executed, decision3/decision4 are still draft)
    r = client.get("/strategy-decisions?view=history")
    assert f'href="/strategy-decisions/{decision1_id}"' in r.text
    assert f'href="/strategy-decisions/{decision2_id}"' not in r.text

    r = client.get("/strategy-decisions?view=monitoring")
    assert f'href="/strategy-decisions/{decision2_id}"' in r.text
    assert f'href="/strategy-decisions/{decision1_id}"' not in r.text

    r = client.get("/strategy-decisions?view=active")
    assert f'href="/strategy-decisions/{decision3_id}"' in r.text
    assert f'href="/strategy-decisions/{decision1_id}"' not in r.text
    assert f'href="/strategy-decisions/{decision2_id}"' not in r.text

    r = client.get("/strategy-decisions?view=nonsense")
    assert r.status_code == 422

    # cross-user isolation holds for the new actions too
    other = TestClient(app)
    other.post("/signup", data={"email": "other-lifecycle@example.com",
                                 "password": "a different password"})
    r = other.post(f"/strategy-decisions/{decision2_id}/reject")
    assert r.status_code == 404


def test_act_or_wait(client):
    """Act/Wait is a derived read on the same recommendation the engine
    already computes -- WAIT when the baseline strategy wins, ACT with the
    strategy name otherwise -- plus the edge case a Plan-agent review
    surfaced: is_baseline isn't guaranteed to be set on any row (reachable
    by checking the radio on a blank/unused slot in the real edit UI, then
    Recompute), which must recover cleanly rather than mislabel or crash."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from decision_engine import _sample_intake

    client.post("/signup", data={"email": "act-or-wait@example.com",
                                  "password": "correct horse battery staple"})
    sample = _sample_intake()

    # --- WAIT: the sample's own baseline (Continue) already wins ---
    r = _create_decision(client, sample)
    assert r.status_code == 303, r.text
    decision_url = r.headers["location"]
    r = client.get(decision_url)
    assert r.status_code == 200
    assert "WAIT" in r.text
    assert "ACT &mdash;" not in r.text
    assert "Net benefit" in r.text

    # --- ACT: the same verified quote change test_decision_lifecycle uses,
    # which flips Continue -> Partial reroute ---
    r = client.post(f"/strategy-decisions/{decision_url.rsplit('/', 1)[-1]}/recalculate",
                     data={"field_war_risk_premium_quote": "3500000"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    r = client.get(r.headers["location"])
    assert r.status_code == 200
    assert "ACT &mdash; Partial reroute" in r.text

    # --- edge case: no strategy card's radio checked on a named row (blank
    # slot checked instead), then Recompute -- must not crash and must not
    # silently mislabel a real recommendation ---
    r = _create_decision(client, sample)
    decision2_id = int(r.headers["location"].rsplit("/", 1)[-1])
    r = client.post(f"/strategy-decisions/{decision2_id}/recompute", data={
        "baseline_strategy": "2",   # slot 2 is blank -- no row ends up is_baseline=True
        "strategy_0_name": "Continue", "strategy_0_direct_cost": "0",
        "strategy_1_name": "Partial reroute", "strategy_1_direct_cost": "700000",
        "strategy_1_capacity_restored": "40", "strategy_1_war_risk_premium_multiplier": "25",
        "strategy_2_name": "",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    r = client.get(r.headers["location"])
    assert r.status_code == 200, "must not 500 when no strategy row is flagged is_baseline"
    assert "WAIT" in r.text or "ACT" in r.text, "must resolve a real Decision, not go blank"


def test_strategy_decision_recompute(client):
    """TAR Analysis: editing strategy cards on the draft-mode detail page
    and recomputing UPDATES the existing row in place -- no duplicate
    insert -- and once the decision leaves draft, /recompute is refused."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from decision_engine import _sample_intake

    client.post("/signup", data={"email": "recompute@example.com",
                                  "password": "correct horse battery staple"})
    sample = _sample_intake()

    r = _create_decision(client, sample)
    assert r.status_code == 303, r.text
    decision_url = r.headers["location"]
    decision_id = int(decision_url.rsplit("/", 1)[-1])
    n_before = len(client.get("/api/v1/strategy-decisions").json())

    r = client.post(f"/strategy-decisions/{decision_id}/recompute", data={
        "baseline_strategy": "0",
        "strategy_0_name": "Continue", "strategy_0_direct_cost": "0",
        "strategy_1_name": "Partial reroute", "strategy_1_direct_cost": "999000",
        "strategy_1_capacity_restored": "40", "strategy_1_war_risk_premium_multiplier": "25",
        "strategy_1_notes": "recomputed in test", "strategy_2_name": "",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert r.headers["location"] == decision_url   # same row, not a new one

    n_after = len(client.get("/api/v1/strategy-decisions").json())
    assert n_after == n_before, "recompute must UPDATE in place, not INSERT"

    r = client.get(decision_url)
    assert r.status_code == 200
    assert "999,000" in r.text
    assert "recomputed in test" in r.text
    assert ">draft<" in r.text

    # once approved, /recompute is refused (only a draft can be recomputed)
    client.post(f"/strategy-decisions/{decision_id}/approve", follow_redirects=False)
    r = client.post(f"/strategy-decisions/{decision_id}/recompute",
                     data={"strategy_0_name": "Continue", "strategy_0_direct_cost": "0"})
    assert r.status_code == 409

    # cross-user isolation holds for recompute too
    other = TestClient(app)
    other.post("/signup", data={"email": "other-recompute@example.com",
                                 "password": "a different password"})
    r = other.post(f"/strategy-decisions/{decision_id}/recompute",
                    data={"strategy_0_name": "Continue", "strategy_0_direct_cost": "0"})
    assert r.status_code == 404


def test_forward_buy_exposed_on_recompute(client):
    """Partial commitment (forward_buy_fraction/forward_buy_early_days) was
    real, tested engine math (decision_engine.forward_buy_cost(), added
    straight into a strategy's direct_cost -- see build_decision()) but had
    no input anywhere on the dashboard form. This checks the new
    edit-strategies columns actually reach the engine and produce a real,
    graded ledger line -- not just that the page renders."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from decision_engine import _sample_intake

    client.post("/signup", data={"email": "forwardbuy@example.com",
                                  "password": "correct horse battery staple"})
    sample = _sample_intake()
    r = _create_decision(client, sample)
    assert r.status_code == 303, r.text
    decision_url = r.headers["location"]
    decision_id = int(decision_url.rsplit("/", 1)[-1])

    r = client.post(f"/strategy-decisions/{decision_id}/recompute", data={
        "baseline_strategy": "0",
        "strategy_0_name": "Continue", "strategy_0_direct_cost": "0",
        "strategy_1_name": "Partial reroute", "strategy_1_direct_cost": "700000",
        "strategy_1_capacity_restored": "40", "strategy_1_war_risk_premium_multiplier": "25",
        "strategy_1_forward_buy_fraction": "40", "strategy_1_forward_buy_early_days": "30",
        "strategy_2_name": "",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text

    # a pre-existing draft (no forward-buy set on any strategy) still
    # renders clean -- the exact missing-key UndefinedError class fixed
    # 2026-08-16 (fc97c78) must not come back for these two new keys.
    r = client.get(decision_url)
    assert r.status_code == 200
    assert 'name="strategy_1_forward_buy_fraction"' in r.text
    assert 'value="40' in r.text

    result = client.get(f"/api/v1/strategy-decisions/{decision_id}").json()["result"]
    fb_rows = [e for e in result["ledger"] if e["field"] == "forward_buy_cost[Partial reroute]"]
    assert len(fb_rows) == 1, "forward-buy must produce exactly one graded ledger line"
    assert fb_rows[0]["value"] > 0, "a 40%/30-day forward buy must cost something, not silently no-op"
    assert fb_rows[0]["grade"] != "ABSENT"

    reroute = next(s for s in result["strategies"] if s["name"] == "Partial reroute")
    assert reroute["direct_cost"] > 700_000, \
        "forward-buy financing cost must be added into the strategy's own direct_cost"


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
    r = _create_decision(client, sample, exposure_id=hormuz_exp["id"])
    assert r.status_code == 303, r.text

    r = client.get("/home")
    assert r.status_code == 200
    assert r.text.count("no decision yet") == 1   # Suez still has none
    assert "Continue" in r.text  # the form path's recommendation (verified), shows somewhere
    assert suez_exp["corridor"] == "Suez Canal"   # sanity: the untouched exposure is the right one

    # ACT/WAIT badges on the redesigned Home (Batch D): the Hormuz decision's
    # baseline IS the recommendation ("Continue"), so act_or_wait() resolves
    # it to WAIT; Suez still has no decision at all
    assert '<span class="badge warn">WAIT</span>' in r.text
    assert '<span class="badge">NO DECISION YET</span>' in r.text


def test_settings(client):
    """Company profile + financial-assumption defaults (Batch D). Defaults
    are scoped to the 4 Tier-2 economic_exposure fields only -- Tier-3
    market quotes must never be pre-filled from a stale saved default (see
    models.py's User docstring)."""
    client.post("/signup", data={"email": "settings-user@example.com",
                                  "password": "correct horse battery staple"})

    r = client.get("/settings")
    assert r.status_code == 200
    assert 'id="company_name"' in r.text
    assert "Not available yet" in r.text   # Users section, honestly stubbed, not silently omitted
    assert 'href="/account/api-keys"' in r.text

    r = client.post("/settings", data={
        "company_name": "Acme Shipping", "wacc_pct": "8", "carrying_cost_pct_pa": "11",
        "gross_margin_pct": "33", "penalty_per_day": "4321",
    }, follow_redirects=False)
    assert r.status_code == 303

    r = client.get(r.headers["location"])
    assert r.status_code == 200
    assert "Saved." in r.text
    assert 'value="Acme Shipping"' in r.text
    assert 'value="8.0"' in r.text and 'value="11.0"' in r.text and 'value="33.0"' in r.text
    assert 'value="4321.0"' in r.text

    # the saved defaults now pre-fill a FRESH decision form -- linked to a
    # bare order so /strategy-decisions/new renders the real form rather
    # than the order picker a completely bare request now shows
    order_r = client.post("/orders", data={"corridor": "Strait of Hormuz", "stage": "pre_order",
                                            "sku": "settings-test order", "quantity": "1",
                                            "quantity_unit": "MT"}, follow_redirects=False)
    settings_order_id = int(order_r.headers["location"].rsplit("/", 1)[-1])
    r = client.get(f"/strategy-decisions/new?order_id={settings_order_id}")
    assert r.status_code == 200
    assert 'id="field_wacc_pct"' in r.text
    assert 'value="8.0"' in r.text and 'value="11.0"' in r.text and 'value="33.0"' in r.text
    # provenance is explicit, not a silent pre-fill (spec: every input's
    # source should be visible) -- exactly the 4 Settings-defaulted fields,
    # not e.g. the order-sourced ones, which say "from the order" instead
    assert r.text.count("From your") == 4
    assert 'href="/settings"' in r.text.split("From your")[1][:50]

    # but never a Tier-3 market quote -- that stays a live, per-decision input
    assert ('id="field_disrupted_freight_quote" name="field_disrupted_freight_quote" step="any"\n'
           '             value="">') in r.text


def test_map_page(client):
    """/map runs on this app (Render), not the separate GitHub Pages site.
    An iframe version was tried and rejected -- a real problem (map.html's
    own content nested inside a nested-scrolling box), not a style
    preference -- so dashboard.py's _map_page_pieces() transplants
    src/map.html's actual body content directly into this page instead.
    No standalone nav (Act or wait / Track record) since this page's own
    base.html nav already covers navigation; no iframe, no nested scroll."""
    r = client.get("/map")
    assert r.status_code == 401   # not logged in yet -- gated like every other dashboard page

    client.post("/signup", data={"email": "map-user@example.com",
                                  "password": "correct horse battery staple"})

    r = client.get("/map")
    assert r.status_code == 200
    # the whole page's own content is native, not framed -- MarineTraffic's
    # own small live-AIS widget (a legitimate third-party embed inside real
    # content) is the only iframe expected here
    assert "/static/map.html" not in r.text
    assert 'title="JWC listed areas map"' not in r.text   # the old wrapper iframe's own title
    assert 'href="/map"' in r.text   # nav points here, not the old external github.io link
    assert "aryanzabihi.github.io" not in r.text

    # the real page content is genuinely present, not just linked to
    assert "Which waters are listed for war risk" in r.text
    assert 'id="map"' in r.text                       # the Leaflet mount point
    assert "leaflet.min.js" in r.text                  # the map actually initializes
    assert 'id="readings-data"' in r.text               # baked data, not a client-side 404

    # its own standalone nav/footer did NOT come along for the ride
    assert "Act or wait" not in r.text
    assert "Track record" not in r.text


def test_corridors_page(client):
    """Per-chokepoint threshold panel: same global band shown once, distinct
    per-corridor evidence never blended into one score. Suez is the sharpest
    check -- TAR_TRIP_note_corridor_base_rates.md's own documented example of
    the two grades disagreeing (0 recorded onsets -> onset-frequency grade
    STRUCTURAL, but real tested incidents -> response-character grade
    EPISODE_ANALOGUE) -- so asserting both on the same corridor confirms the
    page keeps them separate rather than merging them."""
    client.post("/signup", data={"email": "corridors@example.com",
                                  "password": "correct horse battery staple"})

    r = client.get("/corridors")
    assert r.status_code == 200

    for corridor in ["Strait of Hormuz", "Bab-el-Mandeb", "Adriatic",
                     "Turkish Straits / Black Sea", "Suez Canal",
                     "Strait of Malacca", "Taiwan Strait"]:
        assert corridor in r.text

    # the global band appears exactly once, not repeated per corridor as if
    # it varied by chokepoint
    assert r.text.count('<div class="k">Global band</div>') == 1

    # Suez: onset-frequency STRUCTURAL (0 of 8 headline onsets) vs.
    # response-character EPISODE_ANALOGUE (real tested incidents) -- both
    # present, neither overwriting the other
    suez_start = r.text.index("Suez Canal")
    suez_block = r.text[suez_start:suez_start + 3000]
    assert "0 <span" in suez_block and "of 8 since 1985" in suez_block
    assert "STRUCTURAL" in suez_block and "EPISODE_ANALOGUE" in suez_block

    # Forty years of readings (corridor_panel.history_timeline_svg): the
    # global history chart, shown once -- same reasoning as "Global band"
    # above, it's one series, not seven. Structural/fixed text only, not
    # today's live month count or alarm count (docs/readings.json updates
    # monthly, same caution test_strategy_decision_walkthrough already
    # applies to recovery_state -- asserting an exact live number here
    # would break next month for reasons unrelated to a real regression).
    assert r.text.count("Forty years of readings") == 1
    assert r.text.count('aria-label="Global TAR reading') == 1
    assert "Every threshold is built only from months before the one it judges" in r.text
    assert "not hindsight" in r.text

    # Attention-share meter (corridor_panel.share_meter_svg): at least one
    # corridor has a measurable share, so at least one meter renders --
    # not asserting a specific corridor's percentage, which is also live.
    assert "world risk coverage" in r.text

    # Hormuz: 4 of 8 onsets, both grades EPISODE_ANALOGUE (has both onsets
    # and tested incidents), proxy attribution (Iran unpublished)
    hormuz_start = r.text.index("Strait of Hormuz")
    hormuz_block = r.text[hormuz_start:hormuz_start + 3000]
    assert "4 <span" in hormuz_block and "of 8 since 1985" in hormuz_block
    assert hormuz_block.count("EPISODE_ANALOGUE") == 2
    assert "Iran unpublished" in hormuz_block

    # nav link present
    assert 'href="/corridors"' in r.text

"""alerts.py — the monthly cron entrypoint.

Runs as a Render Cron Job (see render.yaml), separate from the existing
GitHub Actions monthly workflow (which stays untouched and keeps publishing
readings.json / record.jsonl exactly as before). This just reads the result
of that pipeline.

Two independent loops, one per saved-analysis type:

  run() -- Decision rows (the simple alpha-ratio tool). For every
  subscribed exposure: recompute its decision at the same alpha and window
  as its last saved decision (i.e. "if nothing about your numbers changed,
  would the call change"), save the new decision if the reading has moved
  on to a new month, and email the owner only if the decision level
  actually changed.

  run_economic() -- EconomicScenario rows (the richer engine.md tool).
  Same shape: recompute the saved scenario's inputs against the current
  reading, save a new row if the month has moved on, email only if the
  recommended strategy or threshold status actually changed.

Both are safe to run as many times as you like in a month -- neither
duplicates a row already saved for the current as_of, and neither re-sends
an email for a result that was already the most recent one.

    python -m app.alerts
"""

from __future__ import annotations

import html
import json
import logging

from sqlmodel import Session, select

from . import crud, economic, engine
from .db import engine as db_engine
from .email import send_email
from .models import (
    AlertSubscription, EconomicScenario, EconomicScenarioSubscription, Exposure, User,
)

logger = logging.getLogger("chokepoint.alerts")


def run() -> int:
    sent = 0
    with Session(db_engine) as session:
        subs = session.exec(select(AlertSubscription)).all()
        for sub in subs:
            exposure = session.get(Exposure, sub.exposure_id)
            user = session.get(User, sub.user_id)
            if exposure is None or user is None:
                continue

            try:
                reading = engine.current_reading(exposure.corridor)
            except (FileNotFoundError, ValueError):
                logger.warning("no reading for corridor %s, skipping", exposure.corridor)
                continue

            prior = crud.latest_decision(session, exposure)
            if prior is not None and prior.as_of == reading["as_of"]:
                continue  # already computed for this month

            alpha = prior.alpha if prior else 0.12
            window = prior.window_months if prior else 6
            result = engine.decide(alpha, window, reading)

            new_decision = crud.create_decision(
                session, user, exposure, as_of=reading["as_of"], window_months=window,
                alpha=alpha, tar=reading["tar"], band=reading["band"],
                regime=reading["regime"], decision_level=result["level"],
                decision_margin_pp=result["margin_pp"],
                computed={"reading": reading, "decision": result, "source": "alert-run"},
            )

            if prior is not None and prior.decision_level != new_decision.decision_level:
                ok = send_email(
                    user.email,
                    f"{exposure.corridor}: {prior.decision_level.upper()} → "
                    f"{new_decision.decision_level.upper()}",
                    f"<p>Your saved exposure on <strong>{exposure.corridor}</strong> "
                    f"changed from <strong>{prior.decision_level.upper()}</strong> to "
                    f"<strong>{new_decision.decision_level.upper()}</strong> as of "
                    f"{reading['as_of']}.</p>"
                    f"<p>Current index value: {reading['tar']} ({reading['band']}).</p>"
                    f"<p>Log in to review: /clients</p>",
                )
                if ok:
                    sent += 1
    return sent


def run_economic() -> int:
    sent = 0
    with Session(db_engine) as session:
        subs = session.exec(select(EconomicScenarioSubscription)).all()
        for sub in subs:
            prior = session.get(EconomicScenario, sub.economic_scenario_id)
            user = session.get(User, sub.user_id)
            if prior is None or user is None:
                continue

            input_data = json.loads(prior.input_json)
            prior_result = json.loads(prior.result_json)

            new_result = economic.compute_scenario(input_data)
            new_ctx = new_result.get("historical_context")
            if new_ctx is None:
                logger.warning("no reading for corridor %s, skipping scenario %s",
                                prior.corridor, prior.scenario_id)
                continue
            new_as_of = new_ctx["reading"]["as_of"]
            prior_ctx = prior_result.get("historical_context")
            prior_as_of = prior_ctx["reading"]["as_of"] if prior_ctx else None
            if new_as_of == prior_as_of:
                continue  # already computed for this month

            changed = (new_result.get("recommended_strategy")
                       != prior_result.get("recommended_strategy")
                       or new_result.get("current_status") != prior_result.get("current_status"))

            new_row = crud.create_economic_scenario(
                session, user, scenario_id=prior.scenario_id, corridor=prior.corridor,
                input_data=input_data, result=new_result,
                client_id=prior.client_id, exposure_id=prior.exposure_id)

            # point the subscription at the new row, so next month compares
            # against this one rather than the original forever
            sub.economic_scenario_id = new_row.id
            session.add(sub)
            session.commit()

            if changed:
                esc = html.escape
                ok = send_email(
                    user.email,
                    f"{prior.corridor}: {prior.scenario_id} recommendation changed",
                    f"<p>Your saved scenario <strong>{esc(prior.scenario_id)}</strong> on "
                    f"<strong>{esc(prior.corridor)}</strong> changed from "
                    f"<strong>{esc(prior_result.get('recommended_strategy') or '—')}</strong> "
                    f"({esc(prior_result.get('current_status') or 'n/a')}) to "
                    f"<strong>{esc(new_result.get('recommended_strategy') or '—')}</strong> "
                    f"({esc(new_result.get('current_status') or 'n/a')}).</p>"
                    f"<p>Log in to review: /economic-scenarios/{new_row.id}</p>",
                )
                if ok:
                    sent += 1
    return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n1 = run()
    n2 = run_economic()
    logger.info("alerts run complete, %d decision email(s), %d economic scenario email(s)",
                n1, n2)

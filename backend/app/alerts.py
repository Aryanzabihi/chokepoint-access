"""alerts.py — the monthly cron entrypoint.

Runs as a Render Cron Job (see render.yaml), separate from the existing
GitHub Actions monthly workflow (which stays untouched and keeps publishing
readings.json / record.jsonl exactly as before). This just reads the result
of that pipeline.

For every subscribed exposure: recompute its decision at the same alpha and
window as its last saved decision (i.e. "if nothing about your numbers
changed, would the call change"), save the new decision if the reading has
moved on to a new month, and email the owner only if the decision level
actually changed. Run it as many times as you like in a month — it will not
duplicate a decision already saved for the current as_of, and it will not
re-send an email for a decision level that was already the most recent one.

    python -m app.alerts
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from . import crud, engine
from .db import engine as db_engine
from .email import send_email
from .models import AlertSubscription, Exposure, User

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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = run()
    logger.info("alerts run complete, %d email(s) sent", n)

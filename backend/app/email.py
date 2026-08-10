"""email.py — the one place that knows how outgoing mail gets sent.

Deliberately a single function. Swapping providers later (Postmark, SES,
whatever) means changing send_email()'s body, not hunting through routers/
and alerts.py for scattered HTTP calls.

With no RESEND_API_KEY set (e.g. local dev with no email account yet),
falls back to logging the email instead of sending it — so the rest of the
app is fully testable before any email vendor exists.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("chokepoint.email")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
FROM_ADDRESS = os.environ.get("EMAIL_FROM", "alerts@chokepoint-access.example")


def send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — logging instead of sending. "
                        "to=%s subject=%r", to, subject)
        return False
    r = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={"from": FROM_ADDRESS, "to": [to], "subject": subject, "html": html},
        timeout=10.0,
    )
    if r.status_code >= 400:
        logger.error("send_email failed: %s %s", r.status_code, r.text)
        return False
    return True

"""routers/reports.py — exportable decision reports.

This is almost entirely reuse: services.attestation_html() and
standdown_html() already generate exactly the documents upgrade.txt's
"exportable decision reports" item asks for (they're the same generators
the CLI --out flag writes to disk). The only new code here is reconstructing
their `p` argument from a saved Decision row instead of a fresh
point_in_time() call, and returning the result over HTTP instead of writing
it to a file.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from .. import crud
from ..db import get_session
from ..deps import current_principal
from ..models import User

SRC = Path(__file__).resolve().parents[3] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from services import attestation_html, standdown_html  # noqa: E402

router = APIRouter()


def _decision_owned(session: Session, user: User, decision_id: int):
    from ..models import Decision
    d = session.get(Decision, decision_id)
    if d is None:
        return None, None
    exposure = crud.get_exposure_owned(session, user, d.exposure_id)
    if exposure is None:
        return None, None
    return d, exposure


@router.get("/api/v1/decisions/{decision_id}/report/attestation", response_class=HTMLResponse)
def attestation_report(decision_id: int, user: User = Depends(current_principal),
                        session: Session = Depends(get_session)):
    d, exposure = _decision_owned(session, user, decision_id)
    if d is None:
        raise HTTPException(404, "decision not found")
    payload = json.loads(d.computed_json)["reading"]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "attestation.html"
        attestation_html(payload, out, requester=user.email)
        return HTMLResponse(out.read_text(encoding="utf-8"))


@router.get("/api/v1/decisions/{decision_id}/report/standdown", response_class=HTMLResponse)
def standdown_report(decision_id: int, user: User = Depends(current_principal),
                      session: Session = Depends(get_session)):
    d, exposure = _decision_owned(session, user, decision_id)
    if d is None:
        raise HTTPException(404, "decision not found")
    client = crud.get_client_owned(session, user, exposure.client_id)
    payload = json.loads(d.computed_json)["reading"]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "standdown.html"
        standdown_html(payload, d.alpha, out, client.name if client else "client")
        crud.audit(session, user.id, "decision", d.id, "exported", "standdown report")
        session.commit()
        return HTMLResponse(out.read_text(encoding="utf-8"))

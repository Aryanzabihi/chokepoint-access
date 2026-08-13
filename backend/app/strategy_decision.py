"""strategy_decision.py — the backend's point of contact with
src/decision_engine.py (the TAR Decision Engine v2, enginev2.md).

Plays the same role for the v2 decision engine that economic.py plays for
economic_engine.py — exactly one place imports from src/ for this module.

build_decision() needs a "reading" for the corridor — normally built from
the raw GPR vintage via services.point_in_time(), which this deployment
doesn't have (see engine.py's own docstring: the vintage is deliberately
gitignored and never redistributed). So compute_decision() below sources
that reading from engine.current_reading() instead — the same
docs/readings.json-backed function economic.py already uses — and passes it
into decision_engine.build_decision()'s reading= parameter rather than its
gpr_source= parameter.

warrisk.csv / warrisk_jwc.csv, unlike the GPR vintage, ARE committed
(src/warrisk.csv, src/warrisk_jwc.csv — see .gitignore's "keep: these ARE
the asset" section), so they resolve in a deployed checkout with no extra
plumbing.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import chokepoint_profiles  # noqa: E402
import decision_engine  # noqa: E402
import intake  # noqa: E402
from decision_engine import build_decision, decision_brief_html  # noqa: E402
from intake import FIELDS, INCOTERM_GROUPS, fields_for, template  # noqa: E402

from . import engine  # noqa: E402

WARRISK_PATH = SRC / "warrisk.csv"
JWC_PATH = SRC / "warrisk_jwc.csv"

CORRIDORS = chokepoint_profiles.all_corridors()


def compute_decision(data: dict, *, as_of: str | None = None) -> dict:
    corridor = data.get("corridor")
    reading = None
    if corridor:
        try:
            reading = engine.current_reading(corridor)
        except (FileNotFoundError, ValueError):
            reading = None
    return build_decision(
        data, as_of=as_of, reading=reading,
        warrisk_path=WARRISK_PATH if WARRISK_PATH.exists() else None,
        jwc_path=JWC_PATH if JWC_PATH.exists() else None)

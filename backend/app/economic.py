"""economic.py — the backend's point of contact with src/economic_engine.py.

Plays the same role for the economic engine that engine.py plays for the
decision engine: exactly one place imports from src/ for this module, so
the path-insertion hack isn't repeated per router.

historical_context() inside economic_engine.py needs a "reading" for the
corridor — normally built from the raw GPR vintage via services.point_in_time(),
which this deployment doesn't have (see engine.py's own docstring: the
vintage is deliberately gitignored and never redistributed). So
compute_scenario() below sources that reading from engine.current_reading()
instead — the same docs/readings.json-backed function the decision engine
already uses — and passes it into economic_engine.compute()'s
historical_reading= parameter rather than its source= parameter.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from economic_engine import (  # noqa: E402
    CORRIDORS, compute, economic_report_html, template_scenario,
)

from . import engine as decision_engine  # noqa: E402


def compute_scenario(data: dict) -> dict:
    corridor = data.get("disruption", {}).get("corridor")
    reading = None
    if corridor:
        try:
            reading = decision_engine.current_reading(corridor)
        except (FileNotFoundError, ValueError):
            reading = None
    return compute(data, historical_reading=reading)

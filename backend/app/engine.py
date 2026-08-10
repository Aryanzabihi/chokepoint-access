"""engine.py — the backend's one point of contact with the existing src/ engine.

Two different reuse strategies live here, because the two things the backend
needs behave differently in a deployed environment:

1. The pure cost-loss math (value_score, positive_band, the H/M/F/N counts)
   has no dependency on the raw GPR vintage. It's imported directly from
   services.py and used exactly as-is.

2. The *current reading* (this month's TAR, band, regime, onset state) is
   normally built by tar_ingest.load() from data/gpr_monthly.dta — a file
   that is deliberately gitignored and only ever exists transiently on the
   CI runner that fetches it (see .gitignore: "the vintage is fetched by the
   workflow, never redistributed from here"). A deployed backend has no
   access to it. What IS always present and always current is
   docs/readings.json — the same file the public site fetches, refreshed by
   the same monthly workflow, committed to git. So current_reading() below
   reads that instead of recomputing from the vintage. It returns the same
   shape services.point_in_time() would, so callers (report generation,
   decision compute) don't need to care which path produced it.

Nothing in src/ is modified. This file only imports from it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tar_ingest import CORRIDORS, BANDS  # noqa: E402
from services import (  # noqa: E402
    H, M, F, N_MONTHS, METHOD, LIMITS,
    value_score, positive_band, crowding, published_entry,
)

READINGS_PATH = REPO_ROOT / "docs" / "readings.json"
RECORD_PATH = REPO_ROOT / "docs" / "record.jsonl"

# Mirrors threshold-engine.html's WINDOWS constant exactly. The six-month
# window is the only one with a fully computed month-level curve; 3- and
# 12-month bands are reported bounds carried over, not independently
# computed — see tar_ingest.py's module docstring for why.
WINDOWS = {
    3: {"band": (0.04, 0.09), "computed": False},
    6: {"band": None, "computed": True},   # computed from positive_band()
    12: {"band": (0.16, 0.30), "computed": False},
}
BORDERLINE_PP = 1.5   # percentage points from a band edge counted as borderline


def load_readings() -> dict:
    if not READINGS_PATH.exists():
        raise FileNotFoundError(
            f"{READINGS_PATH} not found. It is produced by the monthly workflow "
            f"(src/run_month.py) and committed to docs/ — this deployment needs "
            f"that file present, typically via a fresh git checkout."
        )
    return json.loads(READINGS_PATH.read_text(encoding="utf-8"))


def current_reading(corridor: str) -> dict:
    """The current state of one corridor, shaped like services.point_in_time().

    grade is PUBLISHED when this month is in the committed record.jsonl (i.e.
    it was on the record before anyone could know the outcome), RECONSTRUCTED
    otherwise — identical meaning to services.py's evidence grade, just
    sourced from the baked JSON instead of a fresh recompute.
    """
    data = load_readings()
    match = next((r for r in data.get("readings", []) if r["corridor"] == corridor), None)
    if match is None:
        raise ValueError(f"unknown corridor {corridor!r}. Known: "
                          f"{[c for c in CORRIDORS]}")
    entry = published_entry(data["as_of"], RECORD_PATH) if RECORD_PATH.exists() else None
    return {
        "as_of": data["as_of"],
        "corridor": corridor,
        "tar": match["tar"],
        "band": match["band"],
        "percentile_range": match["percentile_range"],
        "horizon": match["horizon"],
        "regime": match["regime"],
        "onset": match["onset"],
        "months_since_onset": match["months_since_onset"],
        "alarm": data.get("alarm_now"),
        "recursive_cut": data.get("recursive_cut"),
        "method": data.get("specification", METHOD),
        "vintage_last_month": data["as_of"],
        "grade": "PUBLISHED" if entry else "RECONSTRUCTED",
        "record": ({"seq": entry["seq"], "hash": entry["hash"],
                     "published_at": entry["published_at"]} if entry else None),
    }


def quoted_band(window: int) -> tuple[float, float]:
    spec = WINDOWS[window]
    return positive_band() if spec["computed"] else spec["band"]


def decide(alpha: float, window: int, reading: dict) -> dict:
    """Port of threshold-engine.html's renderVerdict() decision logic.

    Kept here rather than called into via a headless-browser trick because
    this needs to run server-side on saved data with no browser involved.
    If the JS logic changes, this needs the matching change — there is
    exactly one other copy of this algorithm, in threshold-engine.html's
    renderVerdict().
    """
    if reading["regime"] == "in episode":
        return {"level": "episode", "lo": None, "hi": None, "margin_pp": None,
                "borderline": False, "inside": False}

    lo, hi = quoted_band(window)
    inside = lo <= alpha <= hi
    margin_pp = min(abs(alpha - lo), abs(alpha - hi)) * 100
    borderline = margin_pp < BORDERLINE_PP

    if inside:
        level = "borderline" if borderline else "act"
    elif alpha < lo:
        level = "always"
    else:
        level = "borderline-wait" if borderline else "wait"

    return {"level": level, "lo": lo, "hi": hi, "margin_pp": round(margin_pp, 2),
            "borderline": borderline, "inside": inside}

"""corridor_panel.py — per-chokepoint threshold panel: what's actually
validated per corridor, assembled from existing sources, not fitted.

The single global TAR band/cutoff stays global. Main text Section 5.6:
corridor-specific thresholds tested against real transit episodes score a
pooled AUC of 0.466 -- worse than chance. Confirmed independently twice
more in 2026-08-15 validation work: validation/report/v07_verdict.txt's
own permutation-tested rebuild ("no signal to threshold"), and the
manuscript's own internal corridor-attributed spec scoring AUC 0.527 at
p=0.4764 in its own panel-permutation test (final_output/table_panel_
permutation.csv) -- the paper's own authors' own analysis agreeing with
two independent replications. Nothing here fits a new number.

Every field below already exists somewhere in this codebase; this module's
only job is pulling them together into one place per corridor, each
labeled by what it actually is so distinct kinds of evidence never blend
into one score. In particular: onset_grade (base_rate_context, a raw
historical onset COUNT) and response_grade (chokepoint_profiles, whether
media coverage detectably responds to a KNOWN incident) share the same
EPISODE_ANALOGUE/STRUCTURAL vocabulary but answer different questions and
can disagree (TAR_TRIP_note_corridor_base_rates.md's own example: Adriatic
is onset-grade EPISODE_ANALOGUE but response-grade STRUCTURAL). Keep them
in separate fields; never merge them.
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
import recovery  # noqa: E402
import tar_ingest  # noqa: E402
import warrisk  # noqa: E402

from . import engine  # noqa: E402

WARRISK_BY_CORRIDOR = {e.corridor: e for e in warrisk.EPISODES}


def panel_for(corridor: str, readings: dict, history: dict | None) -> dict:
    """Everything real, currently known, about one corridor. readings/
    history are passed in rather than reloaded per corridor so a full
    7-corridor page does one file read each, not seven."""
    match = next((r for r in readings.get("readings", []) if r["corridor"] == corridor), None)
    if match is None:
        raise ValueError(f"unknown corridor {corridor!r}")

    base_rate = decision_engine.base_rate_context(corridor)
    profile = chokepoint_profiles.profile_for(corridor)
    low_warning = recovery.low_warning_note(history, corridor) if history else None
    wr = WARRISK_BY_CORRIDOR.get(corridor)

    return {
        "corridor": corridor,
        "regime": match["regime"],
        "onset": match["onset"],
        "months_since_onset": match["months_since_onset"],
        "attribution": match["attribution"],              # "proxy" | "direct"
        "proxy_note": tar_ingest.CORRIDORS[corridor]["note"] or None,
        "share": match.get("share"),
        "share_avg": match.get("share_avg"),
        "salience_z": match.get("salience_z"),
        "onset_count": base_rate["corridor_onsets"],
        "onset_grade": base_rate["corridor_onset_grade"],
        "onset_note": base_rate["corridor_note"],
        "response_grade": profile.evidence_grade,
        "response_character": profile.response_character,
        "tested_incident_count": len(profile.tested_incidents),
        "low_warning": low_warning,
        "warrisk_multiple": wr.multiple if wr else None,
        "warrisk_days": wr.days if wr else None,
        "warrisk_onset": str(wr.onset) if wr else None,
    }


def all_panels() -> tuple[dict, list[dict]]:
    """(global reading, [panel_for(c) for c in every corridor]). The global
    reading is returned once -- band/TAR/as_of are identical for every
    corridor by construction (tar_ingest.py's own selftest asserts this),
    not sourced from any one corridor's own entry, so it isn't repeated
    seven times or implied to vary."""
    readings = engine.load_readings()
    try:
        history = engine.history_snapshot()
    except FileNotFoundError:
        history = None
    panels = [panel_for(c, readings, history) for c in sorted(tar_ingest.CORRIDORS)]
    global_reading = {
        "as_of": readings["as_of"],
        "band": readings["band"],
        "percentile_range": readings["percentile_range"],
        "horizon": readings["horizon"],
        "alarm_now": readings.get("alarm_now"),
    }
    return global_reading, panels

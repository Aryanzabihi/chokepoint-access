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

import dataclasses
import math
import sys
from collections import Counter
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

    # episode_window_remaining is the one field of recovery.snapshot() that's
    # genuinely per-corridor (a fixed post-onset window on THIS corridor's own
    # known onset date) -- recovery_state/duration_analogues/conditional_remaining
    # describe the single global TAR series instead (recovery.py's own
    # SCOPE_NOTE) and are computed once in all_panels(), not repeated here.
    episode_window_remaining = None
    if history is not None:
        try:
            snap = recovery.snapshot(history, corridor)
        except ValueError:
            snap = None
        if snap and snap.episode_window_remaining:
            episode_window_remaining = dataclasses.asdict(snap.episode_window_remaining)

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
        "episode_window_remaining": episode_window_remaining,
        "warrisk_multiple": wr.multiple if wr else None,
        "warrisk_days": wr.days if wr else None,
        "warrisk_onset": str(wr.onset) if wr else None,
    }


def history_timeline_svg(history: dict) -> str:
    """Server-rendered SVG line chart of the full global TAR history --
    same no-client-JS pattern as strategy_decision.act_wait_dial_svg(), and
    the same underlying data recovery.py's recovery_state already reads
    (engine.history_snapshot() -> docs/readings.json['history']), just
    drawn as a line instead of reduced to a state label.

    The line is drawn in var(--ink-faint), except any run of months where
    history['alarm'] is true, which is drawn in var(--amber) -- so the 166-
    ish alarm months (of 488) read as visible episode bands rather than
    needing a separate tick mark on every one of them (dataviz skill:
    "never a number on every point"). history['cut'] (the walk-forward
    threshold alarm is computed against) is not drawn as its own line --
    it's what the amber coloring already encodes, and it is None for the
    early months before it's computable, so a second line would need its
    own gap-handling for no added information.
    """
    months, tar, alarm = history["months"], history["tar"], history["alarm"]
    n = len(months)
    W, H = 900, 200
    ML, MR, MT, MB = 34, 8, 10, 22
    plot_w, plot_h = W - ML - MR, H - MT - MB
    lo, hi = 0.0, max(tar) * 1.08

    def px(i: int) -> float:
        return ML + (i / (n - 1)) * plot_w

    def py(v: float) -> float:
        return MT + (1 - (v - lo) / (hi - lo)) * plot_h

    points = [(px(i), py(tar[i])) for i in range(n)]

    # Contiguous alarm/non-alarm runs, each starting one point into the
    # previous run so adjacent segments share their boundary point and the
    # line never visibly gaps at a color change.
    runs: list[tuple[bool, int, int]] = []
    start = 0
    for i in range(1, n):
        if alarm[i] != alarm[start]:
            runs.append((alarm[start], start, i - 1))
            start = i - 1
    runs.append((alarm[start], start, n - 1))

    segs = []
    for is_alarm, s, e in runs:
        d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in points[s:e + 1])
        color = "var(--amber)" if is_alarm else "var(--ink-faint)"
        segs.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" '
                    f'stroke-linejoin="round" stroke-linecap="round"/>')

    grid = []
    for gv in (lo, hi):
        gy = py(gv)
        grid.append(f'<line x1="{ML}" y1="{gy:.1f}" x2="{W - MR}" y2="{gy:.1f}" '
                    f'stroke="var(--rule-soft)" stroke-width="1"/>')
        grid.append(f'<text x="{ML - 6}" y="{gy + 3:.1f}" text-anchor="end" '
                    f'style="font:10px var(--mono)" fill="var(--ink-faint)">{gv:.1f}</text>')

    ticks = []
    seen_years: set[int] = set()
    for i, m in enumerate(months):
        yr = int(m[:4])
        if yr % 5 == 0 and yr not in seen_years and m[5:7] in ("01", "02", "03"):
            seen_years.add(yr)
            tx = px(i)
            ticks.append(f'<text x="{tx:.1f}" y="{H - 4}" text-anchor="middle" '
                        f'style="font:10px var(--mono)" fill="var(--ink-faint)">{yr}</text>')

    # Direct label on the endpoint only (dataviz skill: label the endpoint
    # or the extreme, never every point) -- "where are we now," in the same
    # color the line itself is currently drawn in.
    last_x, last_y = points[-1]
    end_color = "var(--amber)" if alarm[-1] else "var(--ink-faint)"
    endpoint = (f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" fill="{end_color}" '
               f'stroke="var(--sea)" stroke-width="2"/>'
               f'<text x="{last_x - 8:.1f}" y="{last_y - 10:.1f}" text-anchor="end" '
               f'style="font:600 11px var(--mono)" fill="{end_color}">{tar[-1]:.2f}</text>')

    return (f'<svg viewBox="0 0 {W} {H}" role="img" '
           f'aria-label="Global TAR reading, {months[0]} to {months[-1]}, current value '
           f'{tar[-1]:.2f}" style="width:100%;height:auto;display:block">'
           + "".join(grid) + "".join(segs) + endpoint + "".join(ticks) + "</svg>")


def share_meter_svg(share: float | None, share_avg: float | None,
                    max_share: float | None) -> str | None:
    """Meter (dataviz skill: "a single ratio against a limit" -> Meter, same-
    ramp track, not a 2-slice pie) for one corridor's current attention
    share against its own long-run average. max_share is computed once
    across every corridor in all_panels() and passed in here, so every
    corridor's meter shares one scale -- a corridor is never silently
    rescaled onto its own axis, which would make bars look comparable when
    they aren't (the same reasoning the dataviz skill gives against dual-
    axis charts). Purely the visual bar -- the numeric value/average
    already render as text next to this in corridors.html, so the SVG
    doesn't repeat the number a second time on top of itself.

    None when share (or max_share, which is None only when no corridor
    has a measurable share at all) is unmeasured -- the proxy corridors
    with no country-level coverage, same gating corridors.html already
    applies to the text version of this fact."""
    if share is None or max_share is None:
        return None
    W, H, TRACK_H = 200, 14, 8
    track_y = (H - TRACK_H) / 2

    def x(v: float) -> float:
        return (min(max(v, 0.0), max_share) / max_share) * W

    fill_w = x(share)
    parts = [
        f'<rect x="0" y="{track_y:.1f}" width="{W}" height="{TRACK_H}" '
        f'rx="{TRACK_H / 2}" fill="var(--rule-soft)"/>',
        f'<rect x="0" y="{track_y:.1f}" width="{fill_w:.1f}" height="{TRACK_H}" '
        f'rx="{TRACK_H / 2}" fill="var(--sound)"/>',
    ]
    aria = f"{share:.1f}% of world risk coverage"
    if share_avg is not None:
        ax = x(share_avg)
        parts.append(f'<line x1="{ax:.1f}" y1="0" x2="{ax:.1f}" y2="{H}" '
                    f'stroke="var(--ink-faint)" stroke-width="1.5"/>')
        aria += f", long-run average {share_avg:.1f}%"
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{aria}" '
           f'style="width:100%;max-width:200px;height:auto;display:block;margin-top:4px">'
           + "".join(parts) + "</svg>")


def recovery_trajectory_svg(history: dict, recovery_state) -> str:
    """Server-rendered SVG line from the current episode's own peak through
    to now -- the exact span recovery_state's peak_month/current_month
    already describe in words (recovery.py's RecoveryState); this draws it
    instead of leaving it to a sentence. Same no-client-JS pattern as
    history_timeline_svg/act_wait_dial_svg. One series, one color (the same
    status color the state badge already uses), so no legend -- the badge
    beside it already names what's plotted (dataviz skill: a single series
    needs no legend box).

    When peak_month == current_month (ESCALATING: this month IS the new
    high), a single point has no shape to draw -- widened to a short
    trailing lookback (up to 5 months before) purely for visual context.
    This never changes what "peak" or "months_since_peak" themselves mean;
    it only decides how much history the picture shows."""
    months, tar = history["months"], history["tar"]
    peak_idx = months.index(recovery_state.peak_month)
    current_idx = months.index(recovery_state.current_month)
    start_idx = max(0, min(peak_idx, current_idx - 5))

    win_months = months[start_idx:current_idx + 1]
    win_tar = tar[start_idx:current_idx + 1]
    n = len(win_months)
    peak_pos = peak_idx - start_idx

    color = ("var(--caution)" if recovery_state.state in ("ESCALATING", "RE_ESCALATING")
             else "var(--sound)" if recovery_state.state in ("RECOVERING", "RECOVERED")
             else "var(--amber)")   # STALLED

    W, H = 460, 120
    ML, MR, MT, MB = 8, 50, 14, 20
    plot_w, plot_h = W - ML - MR, H - MT - MB
    lo, hi = 0.0, max(win_tar) * 1.15

    def px(i: int) -> float:
        return ML + (i / (n - 1) if n > 1 else 0.0) * plot_w

    def py(v: float) -> float:
        return MT + (1 - (v - lo) / (hi - lo)) * plot_h

    pts = [(px(i), py(win_tar[i])) for i in range(n)]
    line_d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts)
    px_peak, py_peak = pts[peak_pos]
    px_cur, py_cur = pts[-1]

    parts = [
        f'<line x1="{ML}" y1="{py(lo):.1f}" x2="{W - MR}" y2="{py(lo):.1f}" '
        f'stroke="var(--rule-soft)" stroke-width="1"/>',
        f'<path d="{line_d}" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>',
    ]
    if peak_pos != n - 1:
        # text-anchor adapts to how close the peak sits to either edge --
        # a fixed "middle" anchor clips off the viewBox when the peak (as it
        # usually is, per start_idx's own definition above) sits right at
        # the plot's left edge, same collision this module's dot-plot
        # reference-line label already has to dodge (dataviz skill: a label
        # that won't fit doesn't get clipped, it gets repositioned).
        peak_anchor = "start" if px_peak < W * 0.15 else "end" if px_peak > W * 0.85 else "middle"
        peak_label_x = px_peak + 6 if peak_anchor == "start" else px_peak - 6 if peak_anchor == "end" else px_peak
        parts.append(f'<circle cx="{px_peak:.1f}" cy="{py_peak:.1f}" r="4" '
                    f'fill="var(--sea)" stroke="var(--ink-faint)" stroke-width="2"/>')
        parts.append(f'<text x="{peak_label_x:.1f}" y="{max(py_peak - 10, 10):.1f}" text-anchor="{peak_anchor}" '
                    f'style="font:9.5px var(--mono)" fill="var(--ink-faint)">peak</text>')
    parts.append(f'<circle cx="{px_cur:.1f}" cy="{py_cur:.1f}" r="4.5" fill="{color}" '
                f'stroke="var(--sea)" stroke-width="2"/>')
    parts.append(f'<text x="{px_cur + 9:.1f}" y="{py_cur + 4:.1f}" '
                f'style="font:600 12px var(--mono)" fill="{color}">{win_tar[-1]:.2f}</text>')
    parts.append(f'<text x="{ML}" y="{H - 4}" style="font:9.5px var(--mono)" '
                f'fill="var(--ink-faint)">{win_months[0]}</text>')
    parts.append(f'<text x="{W - MR}" y="{H - 4}" text-anchor="end" '
                f'style="font:9.5px var(--mono)" fill="var(--ink-faint)">{win_months[-1]}</text>')

    return (f'<svg viewBox="0 0 {W} {H}" role="img" '
           f'aria-label="TAR trajectory from peak {win_tar[peak_pos]:.2f} in '
           f'{months[peak_idx]} to current {win_tar[-1]:.2f} in {win_months[-1]}" '
           f'style="width:100%;height:auto;display:block">' + "".join(parts) + "</svg>")


def duration_dotplot_svg(duration_analogues, episode_months_so_far: int | None) -> str | None:
    """Server-rendered dot plot of every real completed alarm-episode
    duration (recovery.py's own DurationAnalogues.episodes -- plural, never
    averaged, same discipline as the text this supplements). One dot per
    episode, stacked at repeated duration values rather than overlapping
    invisibly -- the real shape of the distribution (e.g. a dozen 1-month
    episodes and one 65-month outlier), not just its min/median/max.

    episode_months_so_far (only set while alarm_now -- see recovery.py's
    RecoverySnapshot) draws a reference line at the current episode's own
    age and recolors episodes that already ran at least that long: the same
    "qualifying" set conditional_remaining() computes, shown directly rather
    than only stated. Drawn even when conditional_remaining itself is None
    (fewer than 2 qualifying episodes) so the reader can see *why* --
    how few dots sit at or past the line -- instead of just reading
    "insufficient_data"."""
    if duration_analogues is None:
        return None
    durations = [e.duration_months for e in duration_analogues.episodes]
    counts = Counter(durations)
    axis_max = max(math.ceil(max(durations) / 10) * 10, 10)

    W = 460
    ML, MR = 8, 8
    DOT_D, DOT_GAP = 8, 2
    max_stack = max(counts.values())
    plot_h = max_stack * (DOT_D + DOT_GAP) - DOT_GAP
    MT = 20 if episode_months_so_far is not None else 6
    MB = 20
    H = MT + plot_h + MB
    plot_w = W - ML - MR
    axis_y = MT + plot_h + 6

    def px(v: float) -> float:
        return ML + (v / axis_max) * plot_w

    parts = [f'<line x1="{ML}" y1="{axis_y}" x2="{W - MR}" y2="{axis_y}" '
            f'stroke="var(--rule-soft)" stroke-width="1"/>']

    if episode_months_so_far is not None:
        rx = px(min(episode_months_so_far, axis_max))
        parts.append(f'<line x1="{rx:.1f}" y1="4" x2="{rx:.1f}" y2="{axis_y}" '
                    f'stroke="var(--sea-edge)" stroke-width="1.5" stroke-dasharray="3,2"/>')
        anchor = "end" if rx > W * 0.7 else "start" if rx < W * 0.3 else "middle"
        dx = -4 if anchor == "end" else 4 if anchor == "start" else 0
        parts.append(f'<text x="{rx + dx:.1f}" y="12" text-anchor="{anchor}" '
                    f'style="font:600 9.5px var(--mono)" fill="var(--ink-soft)">'
                    f'{episode_months_so_far}mo so far</text>')

    for val, cnt in sorted(counts.items()):
        cx = px(val)
        qualifies = episode_months_so_far is not None and val >= episode_months_so_far
        fill = "var(--sound)" if qualifies else "var(--ink-faint)"
        for k in range(cnt):
            cy = axis_y - 6 - DOT_D / 2 - k * (DOT_D + DOT_GAP)
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{DOT_D / 2}" '
                        f'fill="{fill}" stroke="var(--sea)" stroke-width="2"/>')

    median = duration_analogues.median_months
    mx = px(median)
    parts.append(f'<path d="M {mx - 4:.1f} {axis_y + 3:.1f} L {mx + 4:.1f} {axis_y + 3:.1f} '
                f'L {mx:.1f} {axis_y - 3:.1f} Z" fill="var(--ink)"/>')
    parts.append(f'<text x="{ML}" y="{H - 4}" style="font:9.5px var(--mono)" '
                f'fill="var(--ink-faint)">0</text>')
    parts.append(f'<text x="{W - MR}" y="{H - 4}" text-anchor="end" '
                f'style="font:9.5px var(--mono)" fill="var(--ink-faint)">{axis_max}mo</text>')

    return (f'<svg viewBox="0 0 {W} {H}" role="img" '
           f'aria-label="{duration_analogues.n} completed episode durations, '
           f'{duration_analogues.min_months} to {duration_analogues.max_months} months, '
           f'median {median:g}"'
           f' style="width:100%;height:auto;display:block">' + "".join(parts) + "</svg>")


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

    shares = [v for p in panels for v in (p["share"], p["share_avg"]) if v is not None]
    max_share = math.ceil(max(shares) / 2) * 2 if shares else None
    for p in panels:
        p["share_meter_svg"] = share_meter_svg(p["share"], p["share_avg"], max_share)

    # recovery_state/duration_analogues/conditional_remaining (src/recovery.py)
    # describe the single global TAR series -- identical for every corridor by
    # construction (recovery.snapshot()'s own SCOPE_NOTE) -- so computed once
    # here, not per corridor. snapshot() still requires *a* corridor argument;
    # any real one gives the same global fields, confirmed directly against
    # recovery.py's source (corridor only gates the unknown-corridor check and
    # the per-corridor episode_window_remaining, dropped below since that
    # field is NOT global and panel_for() already attaches each corridor's own).
    recovery_global = None
    if history is not None:
        try:
            raw_snap = recovery.snapshot(history, next(iter(tar_ingest.CORRIDORS)))
        except ValueError:
            raw_snap = None
        if raw_snap:
            recovery_global = dataclasses.asdict(raw_snap)
            recovery_global.pop("episode_window_remaining", None)
            recovery_global.pop("corridor", None)
            recovery_global["trajectory_svg"] = (
                recovery_trajectory_svg(history, raw_snap.recovery_state)
                if raw_snap.recovery_state else None)
            recovery_global["duration_dotplot_svg"] = duration_dotplot_svg(
                raw_snap.duration_analogues, raw_snap.episode_months_so_far)

    global_reading = {
        "as_of": readings["as_of"],
        "band": readings["band"],
        "percentile_range": readings["percentile_range"],
        "horizon": readings["horizon"],
        "alarm_now": readings.get("alarm_now"),
        "history_svg": history_timeline_svg(history) if history else None,
        "history_months_total": len(history["months"]) if history else None,
        "history_months_alarm": sum(history["alarm"]) if history else None,
        "history_span": (f'{history["months"][0]} to {history["months"][-1]}'
                         if history else None),
        "recovery": recovery_global,
    }
    return global_reading, panels

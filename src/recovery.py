"""
recovery.py — the TAR Time Recovery module: describes how the global TAR
alarm episode is de-escalating, using only what has already been observed.

Takes `history` (the {"months", "tar", "cut", "alarm"} arrays already
sitting in docs/readings.json under "history", or produced fresh by
tar_ingest.build_readings()) directly, rather than a file path. Unlike
episodes.py, every real caller already has this in memory, and it keeps
selftest() pure-dict construction with no CSV/tempfile boilerplate. There
is exactly one definition of "where history comes from" (tar_ingest.py's
own `history` local / docs/readings.json's "history" key); this module
must not grow a second loader.

Why this exists, and what it deliberately does not do
-------------------------------------------------------
TAR is a single global monthly series -- tar_ingest.BANDS is one list, and
every corridor reads the same band/horizon in the same month (asserted by
tar_ingest.py's own selftest) -- so there is no per-corridor recovery
trajectory to estimate. This codebase also already tested a TAR
velocity/trend signal for onset PREDICTION and did not claim it (the
coefficient's own standard error was ~5x the level coefficient's). A
fitted "estimated days to recovery" with confidence bounds would repeat
that exact mistake under a new name.

What the data actually supports: tar_ingest.episodes() already groups the
global alarm series into episodes. Run it against 40 years of real history
and dozens of episodes have already completed, with real, observed
durations -- real analogue material, same spirit as episodes.py's
war-risk analogues (plural, never averaged, None -- never a number --
below n=2). That is what this module reports: how the current episode is
trending so far (never a forecast of what happens next), and how long
comparable past episodes actually lasted (real durations, plural, gated on
n>=2 -- the RULE, not today's row count).

Usage
-----
    python recovery.py snapshot --corridor "Strait of Hormuz" \\
        --readings ../docs/readings.json
    python recovery.py --selftest
"""

from __future__ import annotations

import argparse
import dataclasses
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tar_ingest import CORRIDORS, ONSETS, POST_ONSET_MONTHS, assign_band, episodes, regime  # noqa: E402

MODEL_VERSION = "recovery-1.0"

RECOVERY_STREAK = 2   # consecutive same-signed monthly moves required to call a
                       # trend sustained, rather than the transitional single-
                       # observation states (PEAKED/STALLED). Fixed, disclosed,
                       # not tunable at runtime -- but NOT "from the paper" the
                       # way POST_ONSET_MONTHS or EPISODE_GAP are. This is an
                       # invented descriptive threshold; claiming otherwise
                       # would itself be a fabrication (of provenance) inside
                       # the one module whose job is refusing exactly that
                       # move. It also makes no predictive claim: the paper's
                       # own TAR-velocity signal, tested for onset PREDICTION,
                       # was not claimed (coefficient SE ~5x the level
                       # coefficient's own SE) -- this constant only gates
                       # when the write-up may say "declining" instead of
                       # "ticked down once."
FLAT_EPS = 0.02        # a monthly |TAR delta| below this counts as flat, not a
                       # directional move. Checked against the real 488-month
                       # history: only ~7% of real month-over-month moves fall
                       # below this (median real move ~0.26), so it filters
                       # noise without flattening real signal.

SCOPE_NOTE = (
    "recovery_state and duration_analogues describe the single global "
    "monthly TAR alarm series -- every corridor reads the same trajectory "
    "this month. episode_window_remaining below is a DIFFERENT notion of "
    "'episode': the fixed post-onset window after THIS corridor's own known "
    "historical onset date. The two are not necessarily the same period.")
TREND_DISCLAIMER = (
    "Describes the trend through this reading only -- never a projection of "
    "what happens next. A TAR-velocity signal tested for onset prediction in "
    "the underlying research was not claimed (coefficient standard error "
    "~5x the level coefficient's own); this module makes no forward claim.")


@dataclass
class EpisodeDuration:
    start_month: str
    end_month: str
    duration_months: int   # end - start + 1, calendar-inclusive span
    n_alarm_months: int     # tar_ingest.episodes()'s own 3rd element -- may be
                             # less than duration_months when a grace-gap month
                             # (EPISODE_GAP) sits inside the span; both are kept,
                             # never collapsed into one figure


@dataclass
class DurationAnalogues:
    """Real, completed episode durations, plural, never averaged into a
    single point. Only ever constructed with n >= 2 -- see
    _duration_analogues() below, which returns None otherwise. The ongoing
    episode, if any, is never included."""
    episodes: list[EpisodeDuration]
    n: int
    min_months: int
    median_months: float
    max_months: int
    grade: str = "EPISODE_ANALOGUE"


@dataclass
class ConditionalRemaining:
    """"Given the current episode is already N months in, how much longer
    did comparable past episodes run?" Only ever constructed with n >= 2
    qualifying episodes -- see _conditional_remaining() below."""
    months_so_far: int
    qualifying: list[EpisodeDuration]
    n: int
    remaining_months: list[int]   # plural, one per qualifying episode, never averaged
    min_remaining: int
    median_remaining: float
    max_remaining: int
    grade: str = "EPISODE_ANALOGUE"


@dataclass
class RecoveryState:
    state: str   # ESCALATING | PEAKED | RECOVERING | STALLED | RE_ESCALATING | RECOVERED
    current_month: str
    current_value: float
    current_band: str   # via assign_band() -- reused, not reimplemented
    peak_month: str
    peak_value: float
    peak_band: str
    months_since_peak: int
    months_since_episode_end: int | None   # set only when state == RECOVERED
    trailing_run_sign: int   # -1/0/+1 -- the raw evidence the label came from,
                              # never hidden. 0 for RECOVERED: that state is
                              # resolved by the alarm flag itself, not a streak.
    trailing_run_len: int
    grade: str = "DERIVED"


@dataclass
class EpisodeWindowRemaining:
    corridor: str
    onset_month: str
    months_since_onset: int
    months_remaining: int   # POST_ONSET_MONTHS - months_since_onset; structurally
                             # >= 0, because regime() only returns "in episode"
                             # inside that window in the first place
    grade: str = "DERIVED"   # pure date arithmetic on an already-known onset
                              # date -- same class as decision_engine's own
                              # ProcurementWindow, not a data-driven estimate


@dataclass
class RecoverySnapshot:
    as_of: str
    corridor: str
    alarm_now: bool
    recovery_state: RecoveryState | None
    duration_analogues: DurationAnalogues | None
    conditional_remaining: ConditionalRemaining | None
    episode_window_remaining: EpisodeWindowRemaining | None
    scope_note: str = SCOPE_NOTE
    trend_disclaimer: str = TREND_DISCLAIMER
    model_version: str = MODEL_VERSION


# --------------------------------------------------------------------------
# Episode-duration analogues (reuses tar_ingest.episodes(), no reimplementation)
# --------------------------------------------------------------------------

def _episode_durations(eps: list[tuple], months: list[str]) -> list[EpisodeDuration]:
    return [EpisodeDuration(start_month=months[start], end_month=months[end],
                            duration_months=end - start + 1, n_alarm_months=n)
            for start, end, n in eps]


def _duration_analogues(completed: list[EpisodeDuration]) -> DurationAnalogues | None:
    """None -- never a number -- when fewer than 2 completed episodes exist.
    This is the RULE, checked every call, not a fact about today's row
    count: episodes.py's analogue() enforces the same n>=2 bar the same
    way, so a thin register can never silently upgrade into a false claim
    just because it happens to hold enough rows today."""
    if len(completed) < 2:
        return None
    durations = [e.duration_months for e in completed]
    return DurationAnalogues(episodes=completed, n=len(completed),
                             min_months=min(durations),
                             median_months=statistics.median(durations),
                             max_months=max(durations))


def _conditional_remaining(completed: list[EpisodeDuration],
                           months_so_far: int) -> ConditionalRemaining | None:
    """Same n>=2 gate, applied to the harder question. Deliberately a
    strict subset of _duration_analogues()'s own input, never a
    differently-sourced list."""
    qualifying = [e for e in completed if e.duration_months >= months_so_far]
    if len(qualifying) < 2:
        return None
    remaining = [e.duration_months - months_so_far for e in qualifying]
    return ConditionalRemaining(months_so_far=months_so_far, qualifying=qualifying,
                                n=len(qualifying), remaining_months=remaining,
                                min_remaining=min(remaining),
                                median_remaining=statistics.median(remaining),
                                max_remaining=max(remaining))


# --------------------------------------------------------------------------
# Recovery state -- peak/streak classification on the episode-to-date series
# --------------------------------------------------------------------------

def _sign(delta: float) -> int:
    if delta > FLAT_EPS:
        return 1
    if delta < -FLAT_EPS:
        return -1
    return 0


def _trailing_run(signs: list[int]) -> tuple[int, int]:
    """(sign, length) of the run of matching signs at the END of the list
    -- "sustained evidence right up to now", not "what happened right
    after the peak". Empty input -> (0, 0)."""
    if not signs:
        return 0, 0
    last = signs[-1]
    run = 1
    for s in reversed(signs[:-1]):
        if s == last:
            run += 1
        else:
            break
    return last, run


def _active_episode_state(months: list[str], tar: list[float],
                          start: int, current_idx: int) -> RecoveryState:
    series = tar[start:current_idx + 1]
    peak_val = max(series)
    # LAST occurrence of the max: the conservative direction. A series that
    # revisits its own high water mark resets the clock rather than
    # accumulating false "months since peak" credit -- this makes
    # RECOVERING harder to claim, never easier.
    peak_pos = max(i for i, v in enumerate(series) if v == peak_val)
    months_since_peak = len(series) - 1 - peak_pos
    peak_month, peak_value = months[start + peak_pos], peak_val
    current_month, current_value = months[current_idx], tar[current_idx]

    if months_since_peak == 0:
        state, run_sign, run_len = "ESCALATING", 0, 0
    elif months_since_peak == 1:
        state, run_sign, run_len = "PEAKED", 0, 0   # one observation is never
                                                      # sustained evidence
    else:
        post_peak = series[peak_pos:]
        deltas = [post_peak[i] - post_peak[i - 1] for i in range(1, len(post_peak))]
        signs = [_sign(d) for d in deltas]
        run_sign, run_len = _trailing_run(signs)
        if run_sign == -1 and run_len >= RECOVERY_STREAK:
            state = "RECOVERING"
        elif run_sign == 1 and run_len >= RECOVERY_STREAK:
            state = "RE_ESCALATING"
        else:
            state = "STALLED"

    return RecoveryState(
        state=state, current_month=current_month, current_value=current_value,
        current_band=assign_band(current_value)[0],
        peak_month=peak_month, peak_value=peak_value,
        peak_band=assign_band(peak_value)[0],
        months_since_peak=months_since_peak, months_since_episode_end=None,
        trailing_run_sign=run_sign, trailing_run_len=run_len)


def _recovered_state(months: list[str], tar: list[float], ep_start: int, ep_end: int,
                     current_idx: int) -> RecoveryState:
    ep_series = tar[ep_start:ep_end + 1]
    peak_val = max(ep_series)
    peak_pos = max(i for i, v in enumerate(ep_series) if v == peak_val)
    peak_month, peak_value = months[ep_start + peak_pos], peak_val
    current_month, current_value = months[current_idx], tar[current_idx]
    return RecoveryState(
        state="RECOVERED", current_month=current_month, current_value=current_value,
        current_band=assign_band(current_value)[0],
        peak_month=peak_month, peak_value=peak_value,
        peak_band=assign_band(peak_value)[0],
        months_since_peak=current_idx - (ep_start + peak_pos),
        months_since_episode_end=current_idx - ep_end,
        trailing_run_sign=0, trailing_run_len=0)


# --------------------------------------------------------------------------
# Interface
# --------------------------------------------------------------------------

def snapshot(history: dict, corridor: str, as_of: str | None = None) -> RecoverySnapshot:
    """The one entry point. `history` is the {"months", "tar", "cut",
    "alarm"} shape already sitting in docs/readings.json's "history" key
    (or freshly built by tar_ingest.build_readings()) -- this function
    does no file I/O itself; only the CLI's `snapshot` subcommand below
    touches a path.

    `as_of` (optional, "YYYY-MM") is for back-testing: "what would this
    have said as of month X", using only data available through that
    month -- same interface convention as episodes.py's own `analogue()`
    --as-of. Default (None) is "now" = history["months"][-1]. Deliberately
    NOT a free-form override: `as_of` must already be present in
    history["months"], checked up front and raising ValueError otherwise,
    same as the corridor check below -- a caller passing a date the
    history doesn't actually cover fails loudly rather than silently
    returning something inconsistent. Everything after the truncation
    below runs exactly as if `as_of` were the last month on record; no
    branching elsewhere in this function knows the difference."""
    if corridor not in CORRIDORS:
        raise ValueError(f"unknown corridor {corridor!r}. Known: {sorted(CORRIDORS)}")
    months, tar, alarm = history["months"], history["tar"], history["alarm"]
    if not (len(months) == len(tar) == len(alarm)):
        raise ValueError("history's months/tar/alarm arrays must be the same length")

    if as_of is not None:
        try:
            cutoff = months.index(as_of)
        except ValueError:
            raise ValueError(
                f"as_of {as_of!r} is not in this history's months "
                f"(range {months[0]!r}..{months[-1]!r})") from None
        months, tar, alarm = months[:cutoff + 1], tar[:cutoff + 1], alarm[:cutoff + 1]

    current_idx = len(months) - 1
    alarm_now = bool(alarm[current_idx])
    eps = episodes(pd.Series(alarm))

    recovery_state: RecoveryState | None = None
    duration_analogues: DurationAnalogues | None = None
    conditional_remaining: ConditionalRemaining | None = None

    if alarm_now:
        # episodes() groups every True index, including the last, into its
        # own trailing tuple -- eps is guaranteed non-empty here and eps[-1]
        # is the ongoing episode by construction.
        ep_start, ep_end, _ = eps[-1]
        completed = _episode_durations(eps[:-1], months)
        recovery_state = _active_episode_state(months, tar, ep_start, current_idx)
        months_so_far = current_idx - ep_start + 1
        duration_analogues = _duration_analogues(completed)
        conditional_remaining = _conditional_remaining(completed, months_so_far)
    elif eps:
        completed = _episode_durations(eps, months)   # all completed, none ongoing
        ep_start, ep_end, _ = eps[-1]
        recovery_state = _recovered_state(months, tar, ep_start, ep_end, current_idx)
        duration_analogues = _duration_analogues(completed)
        # conditional_remaining doesn't apply -- nothing is currently running

    episode_window_remaining: EpisodeWindowRemaining | None = None
    reg, onset_month, months_since = regime(corridor, pd.Timestamp(months[current_idx]))
    if reg == "in episode":
        episode_window_remaining = EpisodeWindowRemaining(
            corridor=corridor, onset_month=onset_month, months_since_onset=months_since,
            months_remaining=POST_ONSET_MONTHS - months_since)

    return RecoverySnapshot(
        as_of=months[current_idx], corridor=corridor, alarm_now=alarm_now,
        recovery_state=recovery_state, duration_analogues=duration_analogues,
        conditional_remaining=conditional_remaining,
        episode_window_remaining=episode_window_remaining)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_snapshot(s: RecoverySnapshot) -> None:
    print(f"{s.corridor} as of {s.as_of} -- alarm_now={s.alarm_now}")
    if s.recovery_state:
        rs = s.recovery_state
        print(f"  recovery_state: {rs.state}  (peak {rs.peak_value:g} {rs.peak_band!r} "
             f"at {rs.peak_month}, {rs.months_since_peak} month(s) ago; "
             f"now {rs.current_value:g} {rs.current_band!r})")
        print(f"  trailing run: sign={rs.trailing_run_sign} len={rs.trailing_run_len}")
        if rs.months_since_episode_end is not None:
            print(f"  months since episode end: {rs.months_since_episode_end}")
    else:
        print("  recovery_state: n/a -- no alarm episode on record")
    if s.duration_analogues:
        da = s.duration_analogues
        print(f"  duration_analogues: n={da.n} min={da.min_months} "
             f"median={da.median_months:g} max={da.max_months}")
    else:
        print("  duration_analogues: insufficient_data (fewer than 2 completed episodes)")
    if s.conditional_remaining:
        cr = s.conditional_remaining
        print(f"  conditional_remaining (at {cr.months_so_far} months in): n={cr.n} "
             f"min={cr.min_remaining} median={cr.median_remaining:g} max={cr.max_remaining}")
    elif s.alarm_now:
        print("  conditional_remaining: insufficient_data (fewer than 2 historical "
             "episodes ran at least this long)")
    if s.episode_window_remaining:
        ewr = s.episode_window_remaining
        print(f"  episode_window_remaining: {ewr.months_remaining} month(s) "
             f"(onset {ewr.onset_month}, {ewr.months_since_onset} month(s) ago)")
    print(f"  NOTE: {s.scope_note}")
    print(f"  NOTE: {s.trend_disclaimer}")


# --------------------------------------------------------------------------
# Low-warning history — a second, real sentence for decision_engine.py's own
# base_rate_context()/corridor_note, not a new signal. Answers "of this
# corridor's recorded onsets, how many had the alarm already active the
# prior month" using the SAME alarm/cut mechanism the global hit-rate
# already uses -- no new threshold invented, no fitting, a plain historical
# count exactly like corridor_note's own existing "N of 8 headline onsets"
# sentence. Some onsets are genuine low-warning events (Hormuz 1990: TAR
# 0.834, "Routine", the month before a real invasion) -- that fact belongs
# in the record, not smoothed over by only ever reporting counts that look
# reassuring.
# --------------------------------------------------------------------------

def low_warning_note(history: dict, corridor: str) -> str | None:
    """None when this corridor has no recorded onset in tar_ingest.ONSETS
    (nothing to compute -- decision_engine.py's own corridor_note already
    covers that case), or when every recorded onset predates this
    particular history's own coverage (can't look up "the month before" for
    a month that isn't in the series)."""
    onsets = ONSETS.get(corridor, [])
    if not onsets:
        return None
    months, alarm = history["months"], history["alarm"]
    flags: list[bool] = []
    for onset in onsets:
        try:
            idx = months.index(onset)
        except ValueError:
            continue   # this history's coverage doesn't reach this onset
        if idx == 0:
            continue   # no prior month in this history to check
        flags.append(bool(alarm[idx - 1]))
    if not flags:
        return None

    low_warning = sum(1 for f in flags if not f)
    if low_warning == 0:
        return f"All {len(flags)} recorded onset(s) had the alarm already active the prior month."
    return (f"{low_warning} of {len(flags)} recorded onset(s) occurred with no alarm active "
           f"the prior month -- a low-warning corridor for part of its history.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    sn = sub.add_parser("snapshot", help="recovery snapshot for one corridor")
    sn.add_argument("--corridor", required=True)
    sn.add_argument("--readings", type=Path,
                    default=Path(__file__).resolve().parent.parent / "docs" / "readings.json")
    sn.add_argument("--as-of", help="YYYY-MM, for back-testing -- same convention as "
                                    "episodes.py's own --as-of")

    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if a.cmd == "snapshot":
        import json
        data = json.loads(a.readings.read_text(encoding="utf-8"))
        _print_snapshot(snapshot(data["history"], a.corridor, as_of=a.as_of))
        return 0
    p.error("pass a subcommand (snapshot) or --selftest")
    return 1


# --------------------------------------------------------------------------
# Self-test — inline synthetic fixtures, mirroring episodes.py's own style
# --------------------------------------------------------------------------

def _build_synthetic_history(completed_durations: list[int], tail_tar: list[float] | None,
                             tail_alarm: bool = True, cut: float = 1.0) -> dict:
    """One completed alarm episode per entry in completed_durations (each
    duration_months long), separated by 2 non-alarm months -- comfortably
    more than EPISODE_GAP=1, so episodes stay distinct rather than merging.
    Then an optional trailing episode described by tail_tar (its own real
    values, alarm=tail_alarm throughout). Placeholder TAR values inside
    the completed episodes are never read by this module (only their
    alarm-boolean shape is, via tar_ingest.episodes()), so they're set to a
    flat value with no further meaning."""
    months: list[str] = []
    tar: list[float] = []
    alarm: list[bool] = []
    y, m = 1990, 1

    def _add(n: int, value: float, is_alarm: bool) -> None:
        nonlocal y, m
        for _ in range(n):
            months.append(f"{y:04d}-{m:02d}")
            tar.append(value)
            alarm.append(is_alarm)
            m += 1
            if m == 13:
                m = 1
                y += 1

    for dur in completed_durations:
        _add(dur, cut + 0.5, True)
        _add(2, cut - 0.5, False)

    if tail_tar:
        for v in tail_tar:
            _add(1, v, tail_alarm)

    return {"months": months, "tar": tar, "cut": [cut] * len(months), "alarm": alarm}


def _minimal_history(month: str, tar_value: float = 0.5, alarm: bool = False) -> dict:
    """A single-month history with no episodes at all -- for tests that
    only need a valid, non-empty history to hand to regime()/snapshot(),
    not any alarm data. _build_synthetic_history([], None, ...) is NOT
    this: with no completed_durations and no tail, it builds an empty
    history, which snapshot() cannot use (there is no "current month")."""
    return {"months": [month], "tar": [tar_value], "cut": [1.0], "alarm": [alarm]}


def selftest() -> int:
    # The real, independently-verified historical episode-duration
    # distribution (tar_ingest.episodes() run against the actual 488-month
    # docs/readings.json history, confirmed by hand): 28 completed alarm
    # episodes, durations n=28 min=1 median=2.0 max=65. Reproduced here by
    # duration shape only (see _build_synthetic_history's own docstring for
    # why the placeholder TAR values inside them don't matter).
    REAL_COMPLETED_DURATIONS = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2,
                                3, 3, 4, 4, 6, 6, 7, 8, 9, 9, 13, 13, 65]
    assert len(REAL_COMPLETED_DURATIONS) == 28

    # The real, independently-verified current-episode tail: peaks at 4.522
    # (position 10 of 17), then the real observed post-peak values through
    # to the current reading. The pre-peak build-up (positions 0-9) is a
    # placeholder ramp that stays below the peak -- only the post-peak tail
    # is asserted on, and it is the real, verified figure.
    REAL_TAIL = [0.5, 0.8, 1.2, 1.6, 2.0, 2.5, 3.0, 3.5, 4.0, 4.3,
                4.522, 2.792, 2.121, 2.229, 2.356, 2.323, 1.68]
    assert len(REAL_TAIL) == 17

    # --- normal recovery: real numbers, end to end through snapshot() ---
    hist = _build_synthetic_history(REAL_COMPLETED_DURATIONS, REAL_TAIL)
    s = snapshot(hist, "Strait of Hormuz")
    assert s.alarm_now is True
    assert s.recovery_state.state == "RECOVERING", s.recovery_state.state
    assert s.recovery_state.months_since_peak == 6
    assert abs(s.recovery_state.peak_value - 4.522) < 1e-9
    assert s.recovery_state.trailing_run_sign == -1 and s.recovery_state.trailing_run_len == 2
    assert s.duration_analogues is not None
    assert s.duration_analogues.n == 28
    assert s.duration_analogues.min_months == 1
    assert s.duration_analogues.median_months == 2.0
    assert s.duration_analogues.max_months == 65
    # months_so_far = 17: only the 65-month episode ran at least that long
    # (n=1) -- correctly insufficient, not a fabricated number.
    assert s.conditional_remaining is None

    # --- as_of back-testing: replays the exact real month-by-month sequence
    # independently hand-verified against the live data before this
    # parameter existed (2026-04 STALLED, 05 RE_ESCALATING, 06 STALLED, 07
    # RECOVERING) -- proves truncation reproduces what the unrestricted
    # snapshot() above already does at the final month, and that the
    # classifier genuinely responds to new evidence each month rather than
    # flip-flopping on noise. REAL_TAIL's last 4 entries (2.229/2.356/
    # 2.323/1.68) are the real 2026-04..2026-07 readings, in fixture months
    # hist["months"][-4:].
    as_of_04, as_of_05, as_of_06, as_of_07 = hist["months"][-4:]
    assert snapshot(hist, "Strait of Hormuz", as_of=as_of_04).recovery_state.state == "STALLED"
    assert snapshot(hist, "Strait of Hormuz", as_of=as_of_05).recovery_state.state == "RE_ESCALATING"
    assert snapshot(hist, "Strait of Hormuz", as_of=as_of_06).recovery_state.state == "STALLED"
    s_07 = snapshot(hist, "Strait of Hormuz", as_of=as_of_07)
    assert s_07.recovery_state.state == "RECOVERING"
    assert s_07.as_of == as_of_07 == hist["months"][-1]   # as_of=last month == the default (None)
    assert dataclasses.asdict(s_07) == dataclasses.asdict(s)   # truncating to everything changes nothing

    # as_of outside the history's range raises, rather than silently using
    # the nearest month or some other guessed behaviour
    try:
        snapshot(hist, "Strait of Hormuz", as_of="1899-01")
        assert False, "an as_of outside the history should have raised"
    except ValueError:
        pass

    # --- same real duration list answers a lower bar (the ANSWERABLE
    # branch of the same n>=2 gate, not just the insufficient branch) ---
    completed = _episode_durations(
        [(0, d - 1, d) for d in REAL_COMPLETED_DURATIONS], [str(i) for i in range(1000)])
    cr3 = _conditional_remaining(completed, months_so_far=3)
    assert cr3 is not None and cr3.n == 13, cr3
    cr13 = _conditional_remaining(completed, months_so_far=13)
    assert cr13 is not None and cr13.n == 3, cr13

    # --- ESCALATING / PEAKED boundary: 0 and 1 post-peak observations ---
    esc = snapshot(_build_synthetic_history([1, 2], [1.0, 2.0, 3.0]), "Strait of Hormuz")
    assert esc.recovery_state.state == "ESCALATING"
    assert esc.recovery_state.months_since_peak == 0
    peaked = snapshot(_build_synthetic_history([1, 2], [1.0, 2.0, 3.0, 2.5]), "Strait of Hormuz")
    assert peaked.recovery_state.state == "PEAKED"
    assert peaked.recovery_state.months_since_peak == 1

    # --- slow/oscillating recovery: no 2-month streak either direction -> STALLED ---
    stalled = snapshot(_build_synthetic_history([1, 2], [5.0, 4.0, 4.5, 4.0, 4.5]),
                       "Strait of Hormuz")
    assert stalled.recovery_state.state == "STALLED", stalled.recovery_state.state
    assert stalled.recovery_state.trailing_run_len == 1

    # --- stalled recovery: a flat post-peak plateau, all deltas inside FLAT_EPS ---
    plateau = snapshot(_build_synthetic_history([1, 2], [5.0, 3.0, 3.005, 2.995, 3.01]),
                       "Strait of Hormuz")
    assert plateau.recovery_state.state == "STALLED"
    assert plateau.recovery_state.trailing_run_sign == 0

    # --- renewed escalation: 2 consecutive rises that stay below the
    # original peak (a rise that exceeds it is ESCALATING again instead --
    # see the "revisits its own high" case below) ---
    reesc = snapshot(_build_synthetic_history([1, 2], [5.0, 2.0, 2.5, 3.0]), "Strait of Hormuz")
    assert reesc.recovery_state.state == "RE_ESCALATING", reesc.recovery_state.state
    assert reesc.recovery_state.trailing_run_sign == 1 and reesc.recovery_state.trailing_run_len == 2

    # --- a series that revisits its own high water mark resets the clock
    # (the conservative "last occurrence of max" rule) rather than reading
    # as a long-since-peaked RECOVERING run ---
    revisit = snapshot(_build_synthetic_history([1, 2], [3.0, 1.0, 3.0, 2.0, 1.0]),
                       "Strait of Hormuz")
    assert revisit.recovery_state.peak_value == 3.0
    assert revisit.recovery_state.months_since_peak == 2   # from the SECOND 3.0, not the first
    assert revisit.recovery_state.state == "RECOVERING"

    # --- insufficient historical data: fewer than 2 completed episodes ->
    # duration_analogues is None, not a number computed from 0 or 1 rows ---
    thin = snapshot(_build_synthetic_history([3], [5.0, 4.0, 3.0, 2.0]), "Strait of Hormuz")
    assert thin.duration_analogues is None
    assert thin.conditional_remaining is None
    never = snapshot(_minimal_history("1990-01"), "Strait of Hormuz")
    assert never.recovery_state is None
    assert never.duration_analogues is None
    assert never.alarm_now is False

    # --- recovered: episode has closed, alarm_now is False ---
    recovered = snapshot(_build_synthetic_history([1, 2, 5], None, tail_alarm=False),
                         "Strait of Hormuz")
    assert recovered.alarm_now is False
    assert recovered.recovery_state.state == "RECOVERED"
    assert recovered.recovery_state.months_since_episode_end == 2   # the 2-month gap after it
    assert recovered.conditional_remaining is None
    assert recovered.duration_analogues is not None and recovered.duration_analogues.n == 3

    # --- episode_window_remaining: real onset from tar_ingest.ONSETS ---
    onset = ONSETS["Strait of Hormuz"][-1]   # "2026-02"
    inside = snapshot(_minimal_history("2026-06"), "Strait of Hormuz")   # 4 months after the 2026-02 onset
    assert inside.episode_window_remaining is not None
    assert inside.episode_window_remaining.onset_month == onset
    assert inside.episode_window_remaining.months_remaining == POST_ONSET_MONTHS - 4
    far = snapshot(_minimal_history("2030-01"), "Strait of Hormuz")
    assert far.episode_window_remaining is None

    # --- scope_note / trend_disclaimer travel with every snapshot, not
    # just ones with populated recovery_state (structural, always present) ---
    assert never.scope_note == SCOPE_NOTE and never.trend_disclaimer == TREND_DISCLAIMER

    # --- strictness: unknown corridor and mismatched array lengths raise,
    # never silently return a partial object ---
    try:
        snapshot(_build_synthetic_history([1, 2], None, tail_alarm=False), "Not A Real Strait")
        assert False, "unknown corridor should have raised"
    except ValueError:
        pass
    try:
        bad = _build_synthetic_history([1, 2], None, tail_alarm=False)
        bad["tar"] = bad["tar"][:-1]
        snapshot(bad, "Strait of Hormuz")
        assert False, "mismatched array lengths should have raised"
    except ValueError:
        pass

    # --- low_warning_note(): reproduces the real, independently-verified
    # Hormuz pattern (2 of its 4 real recorded onsets -- 1987-07, 1990-08,
    # 2003-03, 2026-02 -- had no alarm active the prior month) from a
    # compact, hand-built fixture, not the live file ---
    hormuz_months = ["1987-06", "1987-07", "1990-07", "1990-08",
                     "2003-02", "2003-03", "2026-01", "2026-02"]
    hormuz_alarm = [False, True, False, True, True, True, True, True]
    hormuz_hist = {"months": hormuz_months, "tar": [1.0] * 8,
                   "cut": [1.0] * 8, "alarm": hormuz_alarm}
    note = low_warning_note(hormuz_hist, "Strait of Hormuz")
    assert note == ("2 of 4 recorded onset(s) occurred with no alarm active the prior "
                    "month -- a low-warning corridor for part of its history."), note

    # a corridor whose one recorded onset DID have prior warning -> the
    # "all had warning" branch, not silently omitted just because it's the
    # reassuring case
    all_warned_hist = {"months": ["2022-01", "2022-02"], "tar": [2.0, 3.0],
                       "cut": [1.0, 1.0], "alarm": [True, True]}
    all_warned = low_warning_note(all_warned_hist, "Turkish Straits / Black Sea")
    assert all_warned == "All 1 recorded onset(s) had the alarm already active the prior month."

    # a corridor with no recorded onset at all -> nothing to compute
    assert low_warning_note(all_warned_hist, "Suez Canal") is None

    # an onset that's the very first entry in the history -> no prior
    # month exists to check, skipped rather than raising or guessing
    edge_hist = {"months": ["1990-08"], "tar": [9.5], "cut": [1.0], "alarm": [True]}
    assert low_warning_note(edge_hist, "Strait of Hormuz") is None

    print("all checks passed")
    print("  duration_analogues and conditional_remaining both return None -- never a")
    print("  fabricated number -- below n=2 comparable episodes, same rule episodes.py's")
    print("  analogue() enforces")
    print("  recovery_state requires a sustained 2-month streak, not a single observation")
    print("  scope_note/trend_disclaimer travel on every snapshot, never left to the caller")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

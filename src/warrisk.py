"""
warrisk.py — the war-risk access channel: schema, validator, derived metrics.

This is the part of the product that isn't in any public dataset. The paper
established the ordering (escalation pressure leads premium repricing, peak
cross-correlation at six weeks) on a median of three observations per event
window. Turning that into a maintained series is the asset; this module is
the discipline around it.

Two rules are enforced rather than documented:

  1. No row without a source. An unsourced premium quotation is a number
     someone remembered, and the whole value of this series is that it can
     be audited years later.

  2. Entry is checked against the paper. Once episode observations are in,
     the derived multiple and duration must reproduce the published
     aggregates. If they don't, either the entry is wrong or the paper's
     figure needs revisiting — both worth knowing before a client sees it.

Usage
-----
    python warrisk.py --selftest
    python warrisk.py --template warrisk.csv          # write a blank template
    python warrisk.py --validate warrisk.csv          # schema + source check
    python warrisk.py --metrics warrisk.csv           # derived series + acceptance
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np

# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

COLUMNS = [
    "quote_date",        # ISO date the rate was quoted or reported
    "corridor",          # must match the corridor registry
    "instrument",        # hull_war | cargo_war | additional_premium | kr | breach
    "basis",             # pct_of_hull_value | pct_of_cargo_value | usd_per_transit | usd_per_7day
    "value",             # numeric
    "vessel_class",      # tanker | container | dry_bulk | lng | any
    "cover_period",      # single_transit | 7_day | annual | n/a
    "source_type",       # trade_press | broker_circular | jwc_circular | underwriter_direct
    "source",            # publication or issuer
    "source_ref",        # URL, circular number, or full citation — REQUIRED
    "confidence",        # firm | indicative | range_midpoint
    "notes",
]

INSTRUMENTS = {"hull_war", "cargo_war", "additional_premium", "kr", "breach"}
BASES = {"pct_of_hull_value", "pct_of_cargo_value", "usd_per_transit", "usd_per_7day"}
SOURCE_TYPES = {"trade_press", "broker_circular", "jwc_circular", "underwriter_direct"}
CONFIDENCE = {"firm", "indicative", "range_midpoint"}

CORRIDORS = [
    "Strait of Hormuz", "Bab-el-Mandeb", "Adriatic",
    "Turkish Straits / Black Sea", "Suez Canal",
    "Strait of Malacca", "Taiwan Strait",
]

# Separate register: the Joint War Committee's listed-area circulars. Dated,
# expert-adjudicated, and the decision this channel actually runs through.
JWC_COLUMNS = [
    "circular_date", "action",          # added | amended | removed
    "area_name", "geographic_definition",
    "notice_days", "circular_ref", "notes",
]
JWC_ACTIONS = {"added", "amended", "removed"}


# --------------------------------------------------------------------------
# Published aggregates the entry is checked against.
# Source: main text §5.4. These are the paper's own figures, not targets to
# be hit by adjusting the data.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Episode:
    corridor: str
    onset: date
    multiple: float      # peak premium as a multiple of the pre-onset baseline
    days: int            # days from onset to that peak
    tol_multiple: float = 0.20   # entry within 20% of the published multiple
    tol_days: int = 7


EPISODES = [
    Episode("Bab-el-Mandeb", date(2023, 11, 19), multiple=20.0, days=104),
    Episode("Strait of Hormuz", date(2026, 2, 28), multiple=37.0, days=18),
]

# Reported lead-lag of escalation pressure against premium repricing.
REPORTED_LEAD_WEEKS = 6
REPORTED_LEAD_R = 0.898


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

@dataclass
class Problem:
    row: int
    column: str
    message: str

    def __str__(self) -> str:
        return f"  row {self.row}: {self.column} — {self.message}"


def validate(rows: list[dict]) -> list[Problem]:
    out: list[Problem] = []

    def bad(i, col, msg):
        out.append(Problem(i, col, msg))

    seen: set[tuple] = set()
    for i, r in enumerate(rows, start=2):          # row 1 is the header
        missing = [c for c in COLUMNS if c not in r]
        if missing:
            bad(i, ",".join(missing), "column absent from file")
            continue

        try:
            d = datetime.strptime(r["quote_date"].strip(), "%Y-%m-%d").date()
        except ValueError:
            bad(i, "quote_date", f"not an ISO date: {r['quote_date']!r}")
            d = None

        if r["corridor"].strip() not in CORRIDORS:
            bad(i, "corridor", f"not in the registry: {r['corridor']!r}")
        if r["instrument"].strip() not in INSTRUMENTS:
            bad(i, "instrument", f"expected one of {sorted(INSTRUMENTS)}")
        if r["basis"].strip() not in BASES:
            bad(i, "basis", f"expected one of {sorted(BASES)}")
        if r["source_type"].strip() not in SOURCE_TYPES:
            bad(i, "source_type", f"expected one of {sorted(SOURCE_TYPES)}")
        if r["confidence"].strip() not in CONFIDENCE:
            bad(i, "confidence", f"expected one of {sorted(CONFIDENCE)}")

        try:
            v = float(r["value"])
            if v <= 0:
                bad(i, "value", "must be positive")
        except ValueError:
            bad(i, "value", f"not numeric: {r['value']!r}")

        # Rule 1. This is the one that matters.
        if not r["source_ref"].strip():
            bad(i, "source_ref", "required — an unsourced quotation is not an observation")
        if not r["source"].strip():
            bad(i, "source", "required")

        key = (r["quote_date"], r["corridor"], r["instrument"],
               r["vessel_class"], r["source_ref"])
        if key in seen:
            bad(i, "—", "duplicate of an earlier row on date/corridor/instrument/class/source")
        seen.add(key)

    return out


def validate_jwc(rows: list[dict]) -> list[Problem]:
    """The listed-area register. Same discipline as the premium register: a
    circular without its reference is not a record."""
    out: list[Problem] = []
    seen: set[tuple] = set()
    for i, r in enumerate(rows, start=2):
        missing = [c for c in JWC_COLUMNS if c not in r]
        if missing:
            out.append(Problem(i, ",".join(missing), "column absent from file"))
            continue
        try:
            datetime.strptime(r["circular_date"].strip(), "%Y-%m-%d")
        except ValueError:
            out.append(Problem(i, "circular_date", f"not an ISO date: {r['circular_date']!r}"))
        if r["action"].strip() not in JWC_ACTIONS:
            out.append(Problem(i, "action", f"expected one of {sorted(JWC_ACTIONS)}"))
        if not r["area_name"].strip():
            out.append(Problem(i, "area_name", "required"))
        if not r["circular_ref"].strip():
            out.append(Problem(i, "circular_ref",
                               "required — name the circular (e.g. JWLA-033)"))
        if r["notice_days"].strip():
            try:
                int(r["notice_days"])
            except ValueError:
                out.append(Problem(i, "notice_days", "must be an integer or blank"))
        key = (r["circular_date"], r["action"], r["area_name"])
        if key in seen:
            out.append(Problem(i, "—", "duplicate date/action/area"))
        seen.add(key)
    return out


# --------------------------------------------------------------------------
# Derived metrics
# --------------------------------------------------------------------------

def episode_series(rows: list[dict], ep: Episode,
                   baseline_window: int = 180) -> tuple[list[tuple[int, float]], float | None]:
    """Observations for one episode as (days_from_onset, value), plus the
    pre-onset baseline. Only comparable rows are pooled: a percentage of hull
    value and a dollar-per-transit figure are different quantities."""
    pick = [r for r in rows if r["corridor"].strip() == ep.corridor]
    if not pick:
        return [], None

    # dominant basis wins; mixing bases would manufacture a multiple
    bases = {}
    for r in pick:
        bases[r["basis"]] = bases.get(r["basis"], 0) + 1
    basis = max(bases, key=bases.get)
    pick = [r for r in pick if r["basis"] == basis]

    pre, post = [], []
    for r in pick:
        d = datetime.strptime(r["quote_date"].strip(), "%Y-%m-%d").date()
        delta = (d - ep.onset).days
        v = float(r["value"])
        if -baseline_window <= delta < 0:
            pre.append(v)
        elif delta >= 0:
            post.append((delta, v))

    base = float(np.median(pre)) if pre else None
    return sorted(post), base


def metrics(rows: list[dict]) -> tuple[list[str], list[str]]:
    lines, failures = [], []

    for ep in EPISODES:
        post, base = episode_series(rows, ep)
        lines.append(f"{ep.corridor} — onset {ep.onset}")
        if not post:
            lines.append("    no post-onset observations")
            continue
        if base is None:
            lines.append("    no pre-onset baseline within 180 days — multiple not computable")
            lines.append(f"    peak quoted {max(v for _, v in post):g} "
                         f"at day {max(post, key=lambda t: t[1])[0]}")
            continue

        day, peak = max(post, key=lambda t: t[1])
        mult = peak / base
        lines.append(f"    observations {len(post)} post-onset, baseline {base:g} "
                     f"(median of pre-onset window)")
        lines.append(f"    peak {peak:g} at day {day} -> {mult:.1f}x baseline")
        lines.append(f"    published: {ep.multiple:.0f}x over {ep.days} days")

        dm = abs(mult - ep.multiple) / ep.multiple
        if dm > ep.tol_multiple:
            failures.append(f"{ep.corridor}: multiple {mult:.1f}x is {dm:.0%} from the "
                            f"published {ep.multiple:.0f}x — check the entry or the figure")
        if abs(day - ep.days) > ep.tol_days:
            failures.append(f"{ep.corridor}: peak at day {day}, published {ep.days} "
                            f"(tolerance {ep.tol_days})")

    return lines, failures


def lead_lag(signal: dict[date, float], premium: dict[date, float],
             max_lag_weeks: int = 16, match_days: int = 10) -> tuple[int, float]:
    """Cross-correlation of a monthly signal against premium levels, by lag in
    weeks. Returns (best_lag, r). Positive lag means the signal leads.

    match_days is how far a premium quote may sit from the lagged signal date
    and still be paired with it. Loosen it and neighbouring lags become
    indistinguishable: with monthly signal spacing the resolution here is
    roughly +/- 2 weeks, so read the returned lag as a band, not a point.

    Descriptive only, as in the paper: with a handful of observations per
    window this establishes ordering, not a structural relationship.
    """
    if len(signal) < 3 or len(premium) < 3:
        return 0, float("nan")
    sd = sorted(signal)
    best, bestr = 0, -2.0
    for lag in range(0, max_lag_weeks + 1):
        xs, ys = [], []
        for d in sd:
            target = date.fromordinal(d.toordinal() + lag * 7)
            near = min(premium, key=lambda p: abs((p - target).days))
            if abs((near - target).days) <= match_days:
                xs.append(signal[d])
                ys.append(premium[near])
        if len(xs) >= 3 and np.std(xs) > 0 and np.std(ys) > 0:
            r = float(np.corrcoef(xs, ys)[0, 1])
            if r > bestr:
                best, bestr = lag, r
    return best, bestr


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

def read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_template(path: Path) -> None:
    if path.exists():
        sys.exit(f"{path} exists — refusing to overwrite collected data")
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
    jwc = path.with_name(path.stem + "_jwc.csv")
    if not jwc.exists():
        with jwc.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(JWC_COLUMNS)
    print(f"wrote {path} and {jwc}")
    print("\nBoth start empty on purpose. Enter the observations you can source;")
    print("--metrics will tell you whether they reproduce the published aggregates.")
    print("\nEpisodes with a published figure to check against:")
    for ep in EPISODES:
        print(f"  {ep.corridor:28} onset {ep.onset}  {ep.multiple:.0f}x over {ep.days} days")


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def selftest() -> int:
    ep = EPISODES[1]                       # Hormuz, 37x over 18 days
    base_rows = [
        dict(quote_date="2026-01-10", corridor=ep.corridor, instrument="hull_war",
             basis="pct_of_hull_value", value="0.05", vessel_class="tanker",
             cover_period="7_day", source_type="trade_press", source="Example Trade Press",
             source_ref="https://example.invalid/a", confidence="firm", notes=""),
        dict(quote_date="2026-02-05", corridor=ep.corridor, instrument="hull_war",
             basis="pct_of_hull_value", value="0.05", vessel_class="tanker",
             cover_period="7_day", source_type="trade_press", source="Example Trade Press",
             source_ref="https://example.invalid/b", confidence="firm", notes=""),
        dict(quote_date="2026-03-18", corridor=ep.corridor, instrument="hull_war",
             basis="pct_of_hull_value", value="1.85", vessel_class="tanker",
             cover_period="7_day", source_type="broker_circular", source="Example Broker",
             source_ref="circ-2026-14", confidence="firm", notes="day 18"),
    ]

    assert validate(base_rows) == [], validate(base_rows)

    # Rule 1: unsourced rows are rejected.
    unsourced = [dict(base_rows[0], source_ref="")]
    probs = validate(unsourced)
    assert any(p.column == "source_ref" for p in probs), "unsourced row accepted"

    # Bad enumerations are caught.
    assert any(p.column == "corridor" for p in validate([dict(base_rows[0], corridor="Panama")]))
    assert any(p.column == "basis" for p in validate([dict(base_rows[0], basis="vibes")]))
    assert any(p.column == "value" for p in validate([dict(base_rows[0], value="-3")]))

    # Duplicates are caught.
    assert any("duplicate" in p.message for p in validate(base_rows + [base_rows[0]]))

    # Acceptance: 0.05 -> 1.85 is 37x at day 18, which should pass.
    lines, failures = metrics(base_rows)
    assert not failures, failures
    assert any("37.0x baseline" in l for l in lines), lines

    # A wrong entry must fail rather than pass quietly.
    wrong = [dict(r) for r in base_rows]
    wrong[2]["value"] = "0.40"                     # 8x, not 37x
    _, failures = metrics(wrong)
    assert failures and "8.0x" in failures[0], failures

    # Mixed bases must not be pooled into a multiple.
    mixed = base_rows + [dict(base_rows[2], basis="usd_per_transit", value="500000",
                              source_ref="https://example.invalid/c")]
    post, base = episode_series(mixed, ep)
    assert all(v < 100 for _, v in post), "dollar and percentage figures were pooled"

    # JWC register: a circular without its reference is rejected.
    jwc_ok = [dict(circular_date="2026-03-03", action="added", area_name="Bahrain",
                   geographic_definition="", notice_days="",
                   circular_ref="JWLA-033", notes="")]
    assert validate_jwc(jwc_ok) == []
    assert any(p.column == "circular_ref"
               for p in validate_jwc([dict(jwc_ok[0], circular_ref="")]))
    assert any(p.column == "action"
               for p in validate_jwc([dict(jwc_ok[0], action="listed")]))
    assert any("duplicate" in p.message for p in validate_jwc(jwc_ok + jwc_ok))

    # Lead-lag recovers a known lead. A monotone series would correlate at
    # every consistent offset, so the test uses a non-monotone weekly signal:
    # only the true lag can align it.
    rng = np.random.default_rng(7)
    sdates = [date.fromordinal(date(2025, 6, 2).toordinal() + 7 * k) for k in range(24)]
    svals = rng.normal(size=24)
    sig = dict(zip(sdates, svals))
    prem = {date.fromordinal(d.toordinal() + 42): v for d, v in sig.items()}
    lag, r = lead_lag(sig, prem, match_days=3)
    assert lag == REPORTED_LEAD_WEEKS and r > 0.99, (lag, r)

    # Neighbouring lags must not also look perfect, or the reported lead is
    # an artefact of the pairing window rather than the data.
    for off in (4, 5, 7, 8):
        _, rn = lead_lag(sig, {d: v for d, v in prem.items()}, max_lag_weeks=off, match_days=3)
        if off < REPORTED_LEAD_WEEKS:
            assert rn < 0.9, f"lag {off} spuriously perfect: r={rn}"

    print("all checks passed")
    for l in lines:
        print(" ", l)
    print(f"  lead-lag recovers a {lag}-week lead at r={r:.3f} "
          f"(paper reports {REPORTED_LEAD_WEEKS} weeks, r={REPORTED_LEAD_R})")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--template", type=Path)
    p.add_argument("--validate", type=Path)
    p.add_argument("--validate-jwc", type=Path)
    p.add_argument("--metrics", type=Path)
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if a.template:
        write_template(a.template)
        return 0
    if a.validate:
        rows = read(a.validate)
        probs = validate(rows)
        if probs:
            print(f"{len(probs)} problem(s) in {len(rows)} row(s):")
            for pr in probs:
                print(pr)
            return 1
        print(f"{len(rows)} row(s) valid")
        return 0
    if a.validate_jwc:
        rows = read(a.validate_jwc)
        probs = validate_jwc(rows)
        if probs:
            print(f"{len(probs)} problem(s) in {len(rows)} row(s):")
            for pr in probs:
                print(pr)
            return 1
        print(f"{len(rows)} circular row(s) valid")
        return 0
    if a.metrics:
        rows = read(a.metrics)
        probs = validate(rows)
        if probs:
            print("fix validation problems first — run --validate")
            return 1
        lines, failures = metrics(rows)
        for l in lines:
            print(l)
        if failures:
            print("\nentry does not reproduce the published aggregates:")
            for f in failures:
                print("  " + f)
            return 1
        print("\nentry reproduces the published aggregates")
        return 0
    p.error("pass --selftest, --template, --validate, --validate-jwc or --metrics")


if __name__ == "__main__":
    raise SystemExit(main())

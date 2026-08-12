"""
episodes.py — the TAR Decision Engine v2's crisis multiplier (enginev2.md,
L3 "episode analogue" half; section 6).

The part no competitor can copy, because it runs on the war-risk premium
and JWC listed-area registers (warrisk.py's schema), not a fitted
deterioration curve. `analogue()` quotes observed episodes as analogues —
plural, always — and returns None, never a number, when the register holds
fewer than two comparable observations. A cost-of-waiting figure or a
crisis multiplier with no observed trajectory behind it is the single most
tempting fabrication in this whole design (enginev2.md section 8.5); this
module's job is to make that fabrication structurally impossible rather
than merely discouraged.

Episodes are pooled register-wide, not filtered to the requesting
corridor. With only two measured episodes in existence today (Hormuz,
Bab-el-Mandeb — see warrisk.EPISODES), scoping "comparable episodes" to the
requesting corridor would make analogue() return None for every corridor
except those two, defeating section 6.3's own worked example, which shows
a *different* corridor's shipment being told "if this behaves like
Hormuz... like Bab-el-Mandeb." `corridor` is used only to (a) label the
result and (b) run the regime check (section 6.3 rule 2) against the
*requesting* corridor's own current state — not to filter which episodes
are eligible.

Usage
-----
    python episodes.py analogue --corridor "Strait of Hormuz" \\
        --component war_risk_premium --day-offset 18 \\
        --warrisk ../data/warrisk.csv --jwc ../data/warrisk_jwc.csv
    python episodes.py --selftest

`data/warrisk.csv` and `data/warrisk_jwc.csv` do not exist yet (see
warrisk.py) — this module is correct and fully self-tested against
synthetic fixtures regardless, per enginev2.md section 14: the register is
filled in parallel, it does not block this module shipping.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tar_ingest import CORRIDORS, ONSETS, POST_ONSET_MONTHS, regime  # noqa: E402
from warrisk import Episode, episode_series, read as warrisk_read  # noqa: E402
from services import ONSET_DAYS, listing_stats  # noqa: E402

MODEL_VERSION = "episodes-2.0"

COMPONENTS = {"war_risk_premium", "freight", "listing_lag", "replacement_premium", "delay_days"}

# section 6.2: "Delay days and replacement premium stay None until the
# premium register is filled." Freight has no basis mapping in warrisk.py's
# schema at all (BASES is hull/cargo-value percentages and per-transit/
# per-7-day war-risk quotes — not freight rates). Gated explicitly, ahead
# of any register lookup, so the selftest can prove these return None even
# against a FULL synthetic fixture — this is a deliberate gate, not a data
# shortfall that happens to be empty today.
_UNSOURCED_COMPONENTS = frozenset({"freight", "replacement_premium", "delay_days"})


# --------------------------------------------------------------------------
# Shapes
# --------------------------------------------------------------------------

@dataclass
class EpisodeObservation:
    episode_label: str        # e.g. "Bab-el-Mandeb, onset 2023-11-19"
    onset: date
    value: float                # multiplier (war_risk_premium) or days (listing_lag)
    day_offset_requested: int
    day_offset_used: int        # the actual day/value the observation came from; never hidden
    source_ref: str
    n_rows: int


@dataclass
class Analogue:
    corridor: str                # the requesting corridor — labelling + regime check only
    component: str
    observations: list[EpisodeObservation] = field(default_factory=list)
    n: int = 0
    regime_note: str | None = None   # set when `corridor` is itself inside POST_ONSET_MONTHS
    grade: str = "EPISODE_ANALOGUE"

    def __post_init__(self) -> None:
        self.n = len(self.observations)


def _onset_date(corridor: str, onset_month: str) -> tuple[date, str]:
    """Day precision where the register has it (services.ONSET_DAYS),
    month precision otherwise — same precision-aware pattern
    services.listing_stats() already uses."""
    exact = ONSET_DAYS.get((corridor, onset_month))
    if exact is not None:
        return exact, "day"
    return datetime.strptime(onset_month, "%Y-%m").date(), "month"


# --------------------------------------------------------------------------
# War-risk premium (register-wide)
# --------------------------------------------------------------------------

def _war_risk_premium_observations(rows: list[dict], day_offset: int) -> list[EpisodeObservation]:
    """One EpisodeObservation per (corridor, onset) pair in tar_ingest.ONSETS
    that has warrisk.csv rows, register-wide (not filtered to one corridor).

    Builds a throwaway warrisk.Episode(corridor, onset, multiple=0, days=0)
    per onset — episode_series() only reads .corridor/.onset off it, so
    multiple/days (warrisk.metrics()'s acceptance-test fields) are unused
    placeholders here, not claims about that onset's magnitude.

    For each onset, picks the observation at-or-before `day_offset`
    (never a later, forward-looking reading relative to the requested
    horizon) and reports it as a multiple of the pre-onset baseline. Onsets
    whose earliest observation falls after `day_offset`, or with no
    pre-onset baseline at all, are skipped for this request rather than
    stretched to fit.
    """
    out: list[EpisodeObservation] = []
    for corridor, onset_months in ONSETS.items():
        for om in onset_months:
            onset_dt, precision = _onset_date(corridor, om)
            ep = Episode(corridor=corridor, onset=onset_dt, multiple=0.0, days=0)
            post, base = episode_series(rows, ep)
            if not post or base is None or base == 0:
                continue
            at_or_before = [(d, v) for d, v in post if d <= day_offset]
            if not at_or_before:
                continue
            day_used, value = max(at_or_before, key=lambda t: t[0])
            out.append(EpisodeObservation(
                episode_label=f"{corridor}, onset {onset_dt.isoformat()}",
                onset=onset_dt, value=value / base,
                day_offset_requested=day_offset, day_offset_used=day_used,
                source_ref=f"warrisk.csv register ({precision}-precision onset)",
                n_rows=len(post)))
    return out


# --------------------------------------------------------------------------
# Listing lag (register-wide, via services.listing_stats)
# --------------------------------------------------------------------------

def _listing_lag_observations(jwc_path: Path, day_offset: int) -> list[EpisodeObservation]:
    """Thin wrapper over services.listing_stats(), which already returns
    register-wide, per-corridor reaction lags with a "why" for onsets it
    cannot measure — reused, not reimplemented. A listing lag is a single
    scalar per onset (not a trajectory), so day_offset is accepted only for
    interface symmetry with the other components and does not filter
    anything here."""
    stats = listing_stats(jwc_path)
    out = []
    for l in stats["lags"]:
        onset_dt, _ = _onset_date(l["corridor"], l["onset"])
        out.append(EpisodeObservation(
            episode_label=f"{l['corridor']}, onset {l['onset']} ({l['onset_precision']} precision)",
            onset=onset_dt, value=float(l["lag_days"]),
            day_offset_requested=day_offset, day_offset_used=l["lag_days"],
            source_ref=l["circular"], n_rows=1))
    return out


# --------------------------------------------------------------------------
# Interface (section 6.2)
# --------------------------------------------------------------------------

def _regime_note(corridor: str, as_of: pd.Timestamp | None) -> str | None:
    """section 6.3 rule 2: inside POST_ONSET_MONTHS of the requesting
    corridor's own onset, the reading is concurrent, not anticipatory —
    suppress horizon language, keep the level. Reuses tar_ingest.regime()
    directly rather than re-deriving the window."""
    if as_of is None:
        return None
    reg, onset_month, months_since = regime(corridor, as_of)
    if reg != "in episode":
        return None
    return (f"{corridor} is itself inside the {POST_ONSET_MONTHS}-month post-onset window "
            f"(onset {onset_month}, {months_since} month(s) ago) — this reading is concurrent, "
            f"not anticipatory. No lead time is claimed.")


def analogue(corridor: str, component: str, day_offset: int, *,
             warrisk_path: Path | None = None, jwc_path: Path | None = None,
             as_of: str | None = None) -> Analogue | None:
    """section 6.2's interface. Returns an Analogue (n >= 2, observations
    always kept plural, never averaged — section 6.3 rule 1) or None —
    never a fabricated number — when the register holds fewer than two
    comparable observations for this component.
    """
    if component not in COMPONENTS:
        raise ValueError(f"unknown component {component!r}. Known: {sorted(COMPONENTS)}")
    if corridor not in CORRIDORS:
        raise ValueError(f"unknown corridor {corridor!r}. Known: {sorted(CORRIDORS)}")

    if component in _UNSOURCED_COMPONENTS:
        return None

    if component == "war_risk_premium":
        if warrisk_path is None or not warrisk_path.exists():
            return None
        rows = warrisk_read(warrisk_path)
        observations = _war_risk_premium_observations(rows, day_offset)
    elif component == "listing_lag":
        if jwc_path is None or not jwc_path.exists():
            return None
        observations = _listing_lag_observations(jwc_path, day_offset)
    else:  # pragma: no cover — guarded by the COMPONENTS check above
        raise ValueError(f"no lookup implemented for component {component!r}")

    if len(observations) < 2:
        return None

    as_of_ts = pd.Timestamp(as_of) if as_of else None
    return Analogue(corridor=corridor, component=component, observations=observations,
                    regime_note=_regime_note(corridor, as_of_ts))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_analogue(a: Analogue | None, corridor: str, component: str) -> None:
    if a is None:
        print(f"{corridor} / {component}: no analogue — fewer than two comparable "
              f"episodes in the register (or the register file was not supplied)")
        return
    print(f"{corridor} / {component}: n={a.n}")
    for o in a.observations:
        print(f"  {o.episode_label:45} day {o.day_offset_used:>4}  "
              f"value {o.value:g}  source {o.source_ref}")
    if a.regime_note:
        print(f"  NOTE: {a.regime_note}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    an = sub.add_parser("analogue", help="look up a crisis-multiplier analogue")
    an.add_argument("--corridor", required=True)
    an.add_argument("--component", required=True, choices=sorted(COMPONENTS))
    an.add_argument("--day-offset", type=int, required=True)
    an.add_argument("--warrisk", type=Path)
    an.add_argument("--jwc", type=Path)
    an.add_argument("--as-of", help="YYYY-MM, for the regime check")

    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if a.cmd == "analogue":
        result = analogue(a.corridor, a.component, a.day_offset,
                          warrisk_path=a.warrisk, jwc_path=a.jwc, as_of=a.as_of)
        _print_analogue(result, a.corridor, a.component)
        return 0
    p.error("pass a subcommand (analogue) or --selftest")
    return 1


# --------------------------------------------------------------------------
# Self-test — inline synthetic fixtures, mirroring warrisk.py's own style
# --------------------------------------------------------------------------

def _write_warrisk_csv(path: Path) -> None:
    import csv
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["quote_date", "corridor", "instrument", "basis", "value", "vessel_class",
                   "cover_period", "source_type", "source", "source_ref", "confidence", "notes"])
        # Hormuz: onset 2026-02-28 (day precision, services.ONSET_DAYS).
        # Pre-onset baseline 0.05, an early post-onset reading at day 5,
        # peak 1.85 (37x) at day 18 — matches warrisk.py's own oracle.
        for qdate, val, ref in [("2026-01-10", "0.05", "a"), ("2026-02-05", "0.05", "b"),
                                ("2026-03-05", "0.10", "c"), ("2026-03-18", "1.85", "d")]:
            w.writerow([qdate, "Strait of Hormuz", "hull_war", "pct_of_hull_value", val,
                       "tanker", "7_day", "trade_press", "Example Trade Press",
                       f"https://example.invalid/{ref}", "firm", ""])
        # Bab-el-Mandeb: onset 2023-11-19 (day precision). Pre-onset
        # baseline 0.04, an early post-onset reading at day 10, peak 0.80
        # (20x) at day 104 — matches warrisk.py's own oracle.
        for qdate, val, ref in [("2023-10-01", "0.04", "e"), ("2023-11-10", "0.04", "f"),
                                ("2023-11-29", "0.08", "g"), ("2024-03-02", "0.80", "h")]:
            w.writerow([qdate, "Bab-el-Mandeb", "hull_war", "pct_of_hull_value", val,
                       "tanker", "7_day", "trade_press", "Example Trade Press",
                       f"https://example.invalid/{ref}", "firm", ""])


def _write_jwc_csv(path: Path) -> None:
    # Byte-identical to services.py's own proven selftest rows (Cabo
    # Delgado, Southern Red Sea, Bahrain), so the 3-day/29-day listing-lag
    # oracle it already verifies is reproduced here, not re-invented. The
    # Cabo Delgado row matters beyond padding: it sets the register's
    # coverage_start early enough that the Bab-el-Mandeb and Hormuz onsets
    # below are not themselves excluded as "predates the register".
    import csv
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["circular_date", "action", "area_name", "geographic_definition",
                   "notice_days", "circular_ref", "notes"])
        w.writerow(["2021-04-29", "amended", "Cabo Delgado", "", "", "JWLA-027", ""])
        w.writerow(["2023-12-18", "amended", "Southern Red Sea", "", "", "JWLA-032", ""])
        w.writerow(["2026-03-03", "added", "Bahrain", "", "", "JWLA-033", ""])


def selftest() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        warrisk_path = Path(d) / "warrisk.csv"
        jwc_path = Path(d) / "warrisk_jwc.csv"
        _write_warrisk_csv(warrisk_path)
        _write_jwc_csv(jwc_path)

        # Unknown component / corridor raise, not silently return None.
        try:
            analogue("Strait of Hormuz", "not_a_component", 18, warrisk_path=warrisk_path)
            assert False, "unknown component should have raised"
        except ValueError:
            pass
        try:
            analogue("Not A Real Strait", "war_risk_premium", 18, warrisk_path=warrisk_path)
            assert False, "unknown corridor should have raised"
        except ValueError:
            pass

        # The three gated components return None even against a FULL
        # fixture — proving the gate is deliberate, not a data shortfall.
        for comp in ("freight", "replacement_premium", "delay_days"):
            assert analogue("Strait of Hormuz", comp, 18,
                            warrisk_path=warrisk_path, jwc_path=jwc_path) is None, comp

        # war_risk_premium: register-wide pooling — a request for a THIRD
        # corridor (Suez, with no rows of its own) still gets both Hormuz
        # and Bab-el-Mandeb as analogues, per section 6.3's worked example.
        a = analogue("Suez Canal", "war_risk_premium", 18, warrisk_path=warrisk_path)
        assert a is not None
        assert a.n == 2, a.n
        assert a.corridor == "Suez Canal"          # labelling only, not a filter
        labels = {o.episode_label.split(",")[0] for o in a.observations}
        assert labels == {"Strait of Hormuz", "Bab-el-Mandeb"}, labels
        # Requesting day 18: Hormuz's day-18 reading is used directly;
        # Bab-el-Mandeb's day-104 reading has not happened yet by day 18,
        # so its at-or-before value is the pre-peak baseline-era reading.
        hormuz_obs = next(o for o in a.observations if o.episode_label.startswith("Strait"))
        assert hormuz_obs.day_offset_used == 18
        assert abs(hormuz_obs.value - (1.85 / 0.05)) < 1e-9   # 37x, matches warrisk.py's own oracle

        # Never averaged: two distinct observations stay distinct, no
        # single blended multiplier is produced anywhere in the shape.
        values = {round(o.value, 2) for o in a.observations}
        assert len(values) == 2, values

        # Fewer than two comparable episodes -> None, never a number. Strip
        # the fixture down to one corridor's rows only.
        one_corridor_only = Path(d) / "warrisk_one.csv"
        rows = warrisk_read(warrisk_path)
        import csv
        with one_corridor_only.open("w", newline="", encoding="utf-8") as f:
            fieldnames = list(rows[0].keys())
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                if r["corridor"] == "Strait of Hormuz":
                    w.writerow(r)
        assert analogue("Suez Canal", "war_risk_premium", 18, warrisk_path=one_corridor_only) is None

        # No register file supplied at all -> None, not an exception.
        assert analogue("Strait of Hormuz", "war_risk_premium", 18) is None

        # listing_lag: reuses services.listing_stats()'s already-proven
        # 29-day Bab-el-Mandeb lag (day-precision onset) directly.
        lag = analogue("Turkish Straits / Black Sea", "listing_lag", 0, jwc_path=jwc_path)
        assert lag is not None and lag.n >= 2
        bem = next(o for o in lag.observations if o.episode_label.startswith("Bab-el-Mandeb"))
        assert bem.value == 29, bem.value   # matches services.selftest()'s own proven figure
        assert bem.source_ref == "JWLA-032"

        # Regime check (section 6.3 rule 2): inside POST_ONSET_MONTHS of
        # Hormuz's 2026-02-28 onset, the analogue carries a suppressive
        # note; well outside it, none.
        inside = analogue("Strait of Hormuz", "war_risk_premium", 18,
                          warrisk_path=warrisk_path, as_of="2026-04")
        assert inside is not None and inside.regime_note is not None
        assert "concurrent" in inside.regime_note

        outside = analogue("Strait of Hormuz", "war_risk_premium", 18,
                           warrisk_path=warrisk_path, as_of="2030-01")
        assert outside is not None and outside.regime_note is None

        no_as_of = analogue("Strait of Hormuz", "war_risk_premium", 18, warrisk_path=warrisk_path)
        assert no_as_of.regime_note is None   # can't run the check without a date

    print("all checks passed")
    print("  gated components (freight/replacement_premium/delay_days) return None by design")
    print("  war_risk_premium and listing_lag are pooled register-wide, not per-corridor")
    print("  fewer than two comparable episodes -> None, never a number")
    print("  regime check suppresses horizon language inside the post-onset window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

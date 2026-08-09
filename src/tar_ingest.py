"""
tar_ingest.py — turns the Caldara-Iacoviello monthly release into corridor
readings for the threshold engine.

Everything here is real-time by construction: the value assigned to month t
uses only information available through t. That is the property the paper's
own correction was about, so it is asserted in the self-test rather than
trusted.

Usage
-----
    # once you have the release (monthly vintage, xls or csv):
    python tar_ingest.py --source data_gpr_export.xls --out readings.json

    # verify the pipeline with no data at hand:
    python tar_ingest.py --selftest

Input columns
-------------
Needs threat and act coverage. Recognised names are listed in _ALIASES;
pass --threat-col / --act-col if the release uses something else.
Country columns (GPRC_*) are optional and only used for corridor salience.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Construction constants. All from the paper; none are tunable at runtime,
# because a threshold you can tune is a threshold you can calibrate with
# hindsight.
# --------------------------------------------------------------------------

BURN_IN = 12          # minimum months before the expanding mean is defined
PANEL_MA = 3          # moving average for the panel score
PANEL_LAG = 1         # value for month t uses data through t-1
ACT_FLOOR = 1.0       # the +1 in GPRA + 1

# Table S1 operational bands: (lower TAR bound, label, percentile, horizon).
BANDS = [
    (0.00, "Routine", "< P50", "Routine monitoring"),
    (0.99, "Elevated tension", "P50-P70", "90-180 days"),
    (1.42, "Procurement Watch", "P70-P85", "60-90 days"),
    (1.98, "High-risk escalation", "P85-P95", "30-60 days"),
    (2.87, "Critical transition", "P95-P99", "15-30 days"),
    (5.12, "Crisis regime", "> P99", "< 15 days"),
]

PRIMARY_TRIGGER = "Procurement Watch"   # the P70 band; the paper's action trigger
ALARM_PERCENTILE = 70                   # recursive cut for the episode rule
EPISODE_GAP = 1                         # months of relief tolerated inside an episode

_ALIASES = {
    "threat": ["GPRT", "gprt", "GPR_THREATS", "threats", "GPRT_MONTHLY"],
    "act": ["GPRA", "gpra", "GPR_ACTS", "acts", "GPRA_MONTHLY"],
    "date": ["month", "date", "DATE", "Month", "yearmonth", "time"],
}

# Corridor -> flanking-country index codes. Where the escalating state has no
# published country index the paper substitutes a proxy; those are marked.
# Salience is only computed for codes actually present in the release.
# Transit-verified onsets (main text Table 1). A band read inside the window
# after an onset is concurrent with a disruption, not anticipating one, so the
# horizon in Table S1 does not apply to it. The paper's own convention is to
# treat the twelve months after an onset as a separate regime (removing them
# RAISES discrimination, 0.745 -> 0.785), which is the default used here.
ONSETS: dict[str, list[str]] = {
    "Strait of Hormuz":            ["1987-07", "1990-08", "2003-03", "2026-02"],
    "Adriatic":                    ["1992-04", "1999-03"],
    "Turkish Straits / Black Sea": ["2022-02"],
    "Bab-el-Mandeb":               ["2023-11"],
}
POST_ONSET_MONTHS = 12

CORRIDORS: dict[str, dict] = {
    "Strait of Hormuz":            {"codes": ["SAU", "ARE", "KWT", "QAT", "OMN", "BHR"], "proxy": True,
                                    "note": "Iran unpublished; GCC coverage substituted"},
    "Bab-el-Mandeb":               {"codes": ["SAU", "EGY"], "proxy": True,
                                    "note": "Yemen unpublished; Saudi and Egyptian coverage substituted"},
    "Adriatic":                    {"codes": ["ITA", "TUR", "HUN"], "proxy": True,
                                    "note": "Balkan states unpublished; regional coverage substituted"},
    "Turkish Straits / Black Sea": {"codes": ["RUS", "UKR", "TUR"], "proxy": False, "note": ""},
    "Suez Canal":                  {"codes": ["EGY"], "proxy": False, "note": ""},
    "Strait of Malacca":           {"codes": ["MYS", "IDN"], "proxy": False, "note": ""},
    "Taiwan Strait":               {"codes": ["CHN", "TWN"], "proxy": False, "note": ""},
}


# --------------------------------------------------------------------------
# Indicator construction
# --------------------------------------------------------------------------

def expanding_mean_leakfree(x: pd.Series, burn_in: int = BURN_IN) -> pd.Series:
    """Mean of x through t inclusive, undefined before burn_in observations.

    Inclusive of t is what the paper specifies. The point is that it never
    reaches forward, not that it excludes the present.
    """
    m = x.expanding(min_periods=burn_in).mean()
    return m


def build_tar(threat: pd.Series, act: pd.Series) -> pd.DataFrame:
    """Compound TAR, threat share, and the lagged panel score.

    TAR_t = [GPRT_t / (GPRA_t + 1)] * [GPRT_t / mu_t(GPRT)]

    Threat coverage enters the numerator twice, so this is proportional to
    squared threat intensity deflated by acts and by historical threat
    volume. It is not a ratio and its scale should not be read as one.
    """
    mu = expanding_mean_leakfree(threat)
    tar = (threat / (act + ACT_FLOOR)) * (threat / mu)

    # Reference specification: needs no constant, no deflator, no burn-in.
    share = threat / (threat + act)

    out = pd.DataFrame({"threat": threat, "act": act, "mu": mu,
                        "tar": tar, "threat_share": share})
    out["panel_score"] = (out["tar"].rolling(PANEL_MA, min_periods=PANEL_MA)
                          .mean().shift(PANEL_LAG))
    return out


def recursive_percentile_cut(x: pd.Series, q: float, burn_in: int = BURN_IN * 2) -> pd.Series:
    """The q-th percentile of x through t-1, month by month.

    Through t-1, not t: a cut that includes the observation it is judging
    is the hindsight problem in miniature.
    """
    cuts = np.full(len(x), np.nan)
    vals = x.to_numpy(dtype=float)
    for i in range(len(vals)):
        hist = vals[:i]
        hist = hist[~np.isnan(hist)]
        if len(hist) >= burn_in:
            cuts[i] = np.percentile(hist, q)
    return pd.Series(cuts, index=x.index)


def assign_band(tar: float) -> tuple[str, str, str]:
    """Fixed Table S1 band. Returns (label, percentile range, horizon)."""
    label, pct, hz = BANDS[0][1], BANDS[0][2], BANDS[0][3]
    for lo, lb, pc, h in BANDS:
        if tar >= lo:
            label, pct, hz = lb, pc, h
    return label, pct, hz


def episodes(alarm: pd.Series, gap: int = EPISODE_GAP) -> list[tuple]:
    """Group alarm months into episodes, tolerating `gap` months of relief.

    Returns (start_index, end_index, n_alarm_months) per episode.
    """
    idx = [i for i, v in enumerate(alarm.to_numpy()) if v]
    if not idx:
        return []
    out, start, prev, n = [], idx[0], idx[0], 1
    for i in idx[1:]:
        if i - prev <= gap + 1:
            prev, n = i, n + 1
        else:
            out.append((start, prev, n))
            start, prev, n = i, i, 1
    out.append((start, prev, n))
    return out


# --------------------------------------------------------------------------
# Corridor attribution
# --------------------------------------------------------------------------

def salience(df: pd.DataFrame, codes: list[str]) -> pd.Series | None:
    """Share of flanking-country risk in total country-level risk, z-scored
    on an expanding window.

    Returns None when the release carries none of the requested countries,
    which is the honest outcome for corridors whose escalating states are
    unpublished and whose proxies are also absent.
    """
    cols = [c for c in df.columns
            if any(c.upper().endswith(k) or c.upper() == f"GPRC_{k}" for k in codes)]
    if not cols:
        return None
    total = df[[c for c in df.columns if c.upper().startswith("GPRC_")]].sum(axis=1)
    if (total == 0).all():
        return None
    frac = df[cols].sum(axis=1) / total.replace(0, np.nan)
    mean = frac.expanding(min_periods=BURN_IN).mean()
    sd = frac.expanding(min_periods=BURN_IN).std()
    return (frac - mean) / sd


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def regime(corridor: str, as_of: pd.Timestamp,
           window: int = POST_ONSET_MONTHS) -> tuple[str, str | None, int | None]:
    """Is this corridor inside the post-onset window?

    Returns (regime, onset_month, months_since_onset). In an episode the band
    is a description of an ongoing disruption; outside one it is a forecast.
    Presenting the same number with the same horizon in both cases is the
    single easiest way for this product to make a false claim.
    """
    best = None
    for o in ONSETS.get(corridor, []):
        om = pd.Timestamp(o)
        if om <= as_of:
            months = (as_of.to_period("M") - om.to_period("M")).n
            if months <= window and (best is None or months < best[1]):
                best = (o, months)
    if best is None:
        return "at risk", None, None
    return "in episode", best[0], best[1]


@dataclass
class Reading:
    corridor: str
    regime: str
    band: str
    percentile_range: str
    horizon: str
    tar: float | None
    threat_share: float | None
    onset: str | None
    months_since_onset: int | None
    salience_z: float | None
    attribution: str
    months_since_trigger: int | None
    validated: bool = False          # never true on public data. See note.
    caveats: list[str] = field(default_factory=list)


MAX_VINTAGE_LAG_DAYS = 75   # release lands in the first days of each month

# The benchmark index starts here. The release ships the historical index in
# the same file, so a monthly vintage spans 1900-2026 while benchmark threat
# and act coverage only exists from 1985. Leaving the earlier rows in lets the
# burn-in and the recursive percentile consume months the measure was never
# defined on, which changes every threshold without changing anything visible.
BENCHMARK_START = "1985-01"


def _to_month(col: pd.Series) -> pd.Series:
    """Parse the release's date column.

    The Stata vintage stores months as a %tm period, the Excel vintage as a
    timestamp, and some exports as an integer YYYYMM. All three appear in the
    wild; guessing wrong shifts every value by a month, which is invisible in
    the output and fatal in a lead-time claim.
    """
    if isinstance(col.dtype, pd.PeriodDtype):
        return col.dt.to_timestamp()
    if pd.api.types.is_datetime64_any_dtype(col):
        return col
    if pd.api.types.is_numeric_dtype(col):
        v = col.dropna()
        if not v.empty and v.between(190001, 220012).all():
            return pd.to_datetime(col.astype("Int64").astype(str),
                                  format="%Y%m", errors="coerce")
    return pd.to_datetime(col.astype(str), errors="coerce")


def load(source: Path, threat_col: str | None, act_col: str | None,
         start: str = BENCHMARK_START) -> pd.DataFrame:
    if not source.exists():
        here = source.parent if str(source.parent) not in ("", ".") else Path.cwd()
        found = sorted(f.name for f in here.glob("*")
                       if f.suffix.lower() in {".xls", ".xlsx", ".csv"})
        msg = [f"{source} not found (looked in {here})"]
        msg.append("  spreadsheets here: " + (", ".join(found) if found else "none"))
        msg.append("")
        msg.append("  The live monthly release is on the GPR webpage and is named")
        msg.append("  data_gpr_export_YYYYMM.xls — e.g. data_gpr_export_202608.xls.")
        msg.append("  data_gpr_export.xls without a date suffix is the 2022 replication")
        msg.append("  file, frozen at the published sample. Do not ingest that one.")
        sys.exit("\n".join(msg))

    suf = source.suffix.lower()
    if suf == ".dta":
        df = pd.read_stata(source)
    elif suf in {".xls", ".xlsx"}:
        engine = "xlrd" if suf == ".xls" else "openpyxl"
        df = pd.read_excel(source, engine=engine)
    else:
        df = pd.read_csv(source)

    def pick(kind: str, override: str | None) -> str:
        if override:
            if override not in df.columns:
                sys.exit(f"column {override!r} not in source. Columns: {list(df.columns)[:20]}")
            return override
        for a in _ALIASES[kind]:
            if a in df.columns:
                return a
        sys.exit(f"no {kind} column found. Tried {_ALIASES[kind]}. "
                 f"Pass --{kind}-col. Columns: {list(df.columns)[:20]}")

    dcol = pick("date", None)
    df[dcol] = _to_month(df[dcol])
    df = df.dropna(subset=[dcol]).sort_values(dcol).set_index(dcol)
    df.attrs["threat_col"] = pick("threat", threat_col)
    df.attrs["act_col"] = pick("act", act_col)

    tcol, acol = df.attrs["threat_col"], df.attrs["act_col"]
    full_first, full_last = df.index[0].date(), df.index[-1].date()

    before = len(df)
    df = df[df.index >= pd.Timestamp(start)]
    trimmed = before - len(df)

    have = df[[tcol, acol]].notna().all(axis=1)
    blank = int((~have).sum())
    df = df[have]
    if df.empty:
        sys.exit(f"no months with both {tcol} and {acol} at or after {start}")
    if len(df) < 60:
        sys.exit(f"only {len(df)} usable months — too few for a 12-month burn-in "
                 f"plus a recursive percentile. Check the column choice.")

    print(f"source {source.name}  file spans {full_first} to {full_last}")
    print(f"  sample {df.index[0].date()} to {df.index[-1].date()}  "
          f"({len(df)} months; dropped {trimmed} before {start}, "
          f"{blank} with no {tcol}/{acol})")
    print(f"  threat={tcol} act={acol}  "
          f"{len([c for c in df.columns if c.upper().startswith('GPRC_')])} country columns")

    # A frozen vintage reads identically to a current one and produces a
    # perfectly plausible band. Refuse it rather than warn.
    last = df.index[-1].date()
    lag = (pd.Timestamp.today().date() - last).days
    if lag > MAX_VINTAGE_LAG_DAYS:
        sys.exit(f"series ends {last} — {lag} days ago.\n"
                 f"  That is not a current vintage. Either this is the 2022 replication\n"
                 f"  file or the download is stale; a band computed from it would be\n"
                 f"  wrong and would not look wrong. Pass --allow-stale only for\n"
                 f"  back-testing, never for a reading shown to anyone.")
    return df


def build_readings(df: pd.DataFrame) -> dict:
    ind = build_tar(df[df.attrs["threat_col"]].astype(float),
                    df[df.attrs["act_col"]].astype(float))
    cut = recursive_percentile_cut(ind["tar"], ALARM_PERCENTILE)
    alarm = (ind["tar"] >= cut) & cut.notna()

    eps = episodes(alarm)
    last = ind.index[-1]
    tar_now = float(ind["tar"].iloc[-1])
    share_now = float(ind["threat_share"].iloc[-1])

    # months since the most recent crossing of the primary trigger
    trig = None
    fired = np.where(alarm.to_numpy())[0]
    if len(fired):
        trig = int((last.to_period("M") - ind.index[fired[-1]].to_period("M")).n)

    readings = []
    for name, meta in CORRIDORS.items():
        sal = salience(df, meta["codes"])
        sal_now = None if sal is None or pd.isna(sal.iloc[-1]) else round(float(sal.iloc[-1]), 3)
        band, pct, hz = assign_band(tar_now)
        reg, onset, since = regime(name, last)
        caveats = ["Band is a global reading. It does not indicate which theatre is moving."]
        if reg == "in episode":
            hz = None      # the Table S1 horizon is time-to-onset; onset already happened
            caveats.insert(0, f"Concurrent, not anticipatory: {since} months after the "
                              f"{onset} onset. The band describes an ongoing disruption; "
                              f"no lead time is implied.")
        if meta["proxy"]:
            caveats.append("Proxy attribution: " + meta["note"] + ".")
        if sal is None:
            caveats.append("No salience term: release carries none of this corridor's flanking countries.")
        readings.append(asdict(Reading(
            corridor=name, regime=reg, band=band, percentile_range=pct, horizon=hz,
            tar=round(tar_now, 3), threat_share=round(share_now, 4),
            onset=onset, months_since_onset=since,
            salience_z=sal_now,
            attribution=("proxy" if meta["proxy"] else "direct"),
            months_since_trigger=trig, validated=False, caveats=caveats,
        )))

    # The full series, so the site can show where this month sits in forty years
    # rather than asserting a level with no context. Alarm flags come from the
    # recursive cut, so each month is judged against thresholds built only from
    # months before it — the history is honest at every point, not just the end.
    hist = ind[["tar"]].copy()
    hist["cut"] = cut
    hist["alarm"] = alarm
    hist = hist[hist["tar"].notna()]
    history = {
        "months": [d.strftime("%Y-%m") for d in hist.index],
        "tar": [round(float(v), 3) for v in hist["tar"]],
        "cut": [None if pd.isna(v) else round(float(v), 3) for v in hist["cut"]],
        "alarm": [bool(v) for v in hist["alarm"]],
    }

    active = [r["corridor"] for r in readings if r["regime"] == "in episode"]
    return {
        "as_of": str(last.date()),
        "history": history,
        "onsets": {k: list(v) for k, v in ONSETS.items()},
        "band_cuts": [[lo, label] for lo, label, _, _ in BANDS],
        "post_onset_months": POST_ONSET_MONTHS,
        "corridors_in_episode": active,
        "regime_note": (
            "One or more corridors are inside the post-onset window, so an elevated "
            "global reading is partly a description of those episodes. Do not read it "
            "as anticipation elsewhere." if active else
            "No corridor is inside the post-onset window; bands read as anticipatory."),
        "specification": "compound TAR, real-time expanding-mean normaliser, 12-month burn-in",
        "primary_trigger": PRIMARY_TRIGGER,
        "recursive_cut": None if pd.isna(cut.iloc[-1]) else round(float(cut.iloc[-1]), 3),
        "alarm_now": bool(alarm.iloc[-1]),
        "episodes_to_date": len(eps),
        "readings": readings,
        "warning": ("Corridor level is a demonstrated architecture, not a validated "
                    "instrument. Do not present these as corridor forecasts."),
    }


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def verify(df: pd.DataFrame, reference: Path, tol: float = 1e-6) -> int:
    """Compare this pipeline's TAR against the series the paper produced.

    Two implementations of one measure will drift. The point of this mode is
    that the product is checked against the published series rather than
    quietly becoming a second, different source of truth.
    """
    ind = build_tar(df[df.attrs["threat_col"]].astype(float),
                    df[df.attrs["act_col"]].astype(float))
    ref = pd.read_csv(reference)

    dcands = [c for c in ref.columns if c.lower() in
              {"month", "date", "yearmonth", "time", "unnamed: 0"}]
    if not dcands:
        sys.exit(f"no date column in {reference.name}. Columns: {list(ref.columns)}")
    ref[dcands[0]] = _to_month(ref[dcands[0]])
    ref = ref.dropna(subset=[dcands[0]]).set_index(dcands[0]).sort_index()

    tcands = [c for c in ref.columns if "tar" in c.lower()]
    if not tcands:
        sys.exit(f"no TAR-like column in {reference.name}. Columns: {list(ref.columns)}")

    print(f"reference {reference.name}: {len(ref)} rows, "
          f"{ref.index[0].date()} to {ref.index[-1].date()}")
    worst = None
    for c in tcands:
        a, b = ind["tar"].align(pd.to_numeric(ref[c], errors="coerce"), join="inner")
        m = a.notna() & b.notna()
        if m.sum() < 24:
            print(f"  {c:28} too few overlapping months ({int(m.sum())}) to compare")
            continue
        d = (a[m] - b[m]).abs()
        r = float(a[m].corr(b[m]))
        n = int(m.sum())
        print(f"  {c:28} n={n:4d}  max|diff|={d.max():.6g}  "
              f"median|diff|={d.median():.6g}  r={r:.6f}")
        if worst is None or d.max() < worst[1]:
            worst = (c, d.max(), r)

    if worst is None:
        sys.exit("nothing comparable — check the reference file's columns")
    c, mx, r = worst
    if mx <= tol:
        print(f"\nmatch: {c} is identical to this pipeline within {tol:g}")
        return 0
    print(f"\nDIVERGENCE: closest column {c} differs by up to {mx:.6g} (r={r:.6f})")
    print("  Same measure, two implementations. Before shipping a reading to")
    print("  anyone, find out which is right — likely candidates are the")
    print("  burn-in length, whether the expanding mean includes month t, and")
    print("  whether the reference is the corrected or pre-correction series.")
    return 1


def selftest() -> int:
    rng = np.random.default_rng(11)
    n = 480
    idx = pd.date_range("1985-01-01", periods=n, freq="MS")
    threat = pd.Series(np.abs(rng.lognormal(4.4, 0.45, n)), index=idx)
    act = pd.Series(np.abs(rng.lognormal(3.9, 0.55, n)), index=idx)

    full = build_tar(threat, act)

    # 1. Leak-free construction. Recomputing on a truncated series must give
    #    the identical value at the truncation point.
    bad = []
    for k in (100, 240, 361, n - 1):
        trunc = build_tar(threat.iloc[:k + 1], act.iloc[:k + 1])
        a, b = full["tar"].iloc[k], trunc["tar"].iloc[k]
        if not np.isclose(a, b, rtol=1e-12):
            bad.append((k, a, b))
    assert not bad, f"normaliser leaks future information: {bad}"

    # 2. Burn-in. TAR undefined before 12 observations.
    assert full["tar"].iloc[:BURN_IN - 1].isna().all(), "TAR defined inside burn-in"
    assert full["tar"].iloc[BURN_IN:].notna().all(), "TAR missing after burn-in"

    # 3. Panel score uses no contemporaneous information.
    assert full["panel_score"].iloc[:PANEL_MA + PANEL_LAG - 1].isna().all()
    k = 200
    assert np.isclose(full["panel_score"].iloc[k],
                      full["tar"].iloc[k - 3:k].mean(), rtol=1e-12), \
        "panel score is not a 3-month mean lagged one month"

    # 4. Recursive cut never sees the month it judges.
    cut = recursive_percentile_cut(full["tar"], ALARM_PERCENTILE)
    k = 300
    hist = full["tar"].iloc[:k].dropna()
    assert np.isclose(cut.iloc[k], np.percentile(hist, ALARM_PERCENTILE), rtol=1e-12), \
        "recursive cut contaminated by the current month"

    # 5. Band monotonicity against Table S1.
    assert assign_band(0.5)[0] == "Routine"
    assert assign_band(1.42)[0] == "Procurement Watch"
    assert assign_band(1.41)[0] == "Elevated tension"
    assert assign_band(9.9)[0] == "Crisis regime"

    # 6. Episode grouping tolerates a single month of relief, not two.
    a = pd.Series([0, 1, 1, 0, 1, 0, 0, 0, 1], dtype=bool)
    assert [e[2] for e in episodes(a)] == [3, 1], episodes(a)

    # 7. Cost-loss engine reproduces the published table.
    def value(al, h=15, m=41, f=70, N=484):
        base = min(N * al, h + m)
        return (base - ((h + f) * al + m)) / (base - (h + m) * al)
    for al, want in [(0.05, -0.98), (0.08, -0.27), (0.11, 0.06), (0.12, 0.10),
                     (0.15, 0.05), (0.17, 0.01), (0.20, -0.05), (0.30, -0.27)]:
        got = value(al)
        assert abs(got - want) <= 0.006, \
            f"cost-loss at {al}: got {got:.3f}, table says {want}"

    # 8. End-to-end on synthetic data, no country columns present.
    df = pd.DataFrame({"GPRT": threat, "GPRA": act})
    df.attrs["threat_col"], df.attrs["act_col"] = "GPRT", "GPRA"
    out = build_readings(df)
    assert len(out["readings"]) == len(CORRIDORS)
    assert all(r["validated"] is False for r in out["readings"])
    assert all(r["salience_z"] is None for r in out["readings"]), \
        "salience invented without country columns"

    # 9. Episode state suppresses the horizon and only the horizon.
    assert regime("Strait of Hormuz", pd.Timestamp("2026-06-01"))[0] == "in episode"
    assert regime("Strait of Hormuz", pd.Timestamp("2027-06-01"))[0] == "at risk"
    assert regime("Strait of Malacca", pd.Timestamp("2026-06-01"))[0] == "at risk"
    assert regime("Strait of Hormuz", pd.Timestamp("2026-06-01"))[2] == 4
    hz_names = {r["corridor"]: r for r in out["readings"]}
    # synthetic run ends inside no episode window relative to its own last month,
    # so assert the mechanism directly rather than via the synthetic date
    assert all(r["horizon"] is None for r in out["readings"]
               if r["regime"] == "in episode")
    assert all(r["horizon"] is not None for r in out["readings"]
               if r["regime"] == "at risk")

    # 10. History is emitted, aligned, and each alarm flag respects its own
    #     recursive cut rather than a single end-of-sample threshold.
    h = out["history"]
    assert len(h["months"]) == len(h["tar"]) == len(h["cut"]) == len(h["alarm"])
    assert len(h["months"]) > 300, len(h["months"])
    assert h["months"][-1] == str(out["as_of"])[:7]
    for i, (t, c, al) in enumerate(zip(h["tar"], h["cut"], h["alarm"])):
        if c is not None:
            assert al == (t >= c), f"alarm flag disagrees with its own cut at {h['months'][i]}"
        else:
            assert al is False, f"alarm set before a cut existed at {h['months'][i]}"
    assert out["band_cuts"][0][0] == 0.0 and len(out["band_cuts"]) == len(BANDS)
    assert "Strait of Hormuz" in out["onsets"]

    print("all checks passed")
    print(f"  synthetic TAR now       {out['readings'][0]['tar']}")
    print(f"  band                    {out['readings'][0]['band']}")
    print(f"  recursive P70 cut       {out['recursive_cut']}")
    print(f"  alarm episodes to date  {out['episodes_to_date']}")
    print(f"  alarm this month        {out['alarm_now']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", type=Path, help="Caldara-Iacoviello monthly release")
    p.add_argument("--out", type=Path, default=Path("readings.json"))
    p.add_argument("--threat-col")
    p.add_argument("--act-col")
    p.add_argument("--start", default=BENCHMARK_START,
                   help=f"first month of the sample (default {BENCHMARK_START}, "
                        f"the benchmark index start)")
    p.add_argument("--verify", type=Path,
                   help="cross-check against an existing TAR csv (e.g. output/tar_monthly.csv)")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--allow-stale", action="store_true",
                   help="ingest a vintage that is not current (back-testing only)")
    a = p.parse_args()

    global MAX_VINTAGE_LAG_DAYS
    if a.allow_stale:
        MAX_VINTAGE_LAG_DAYS = 10 ** 6

    if a.selftest:
        return selftest()
    if not a.source:
        p.error("pass --source, or --selftest to verify the pipeline with no data")

    df = load(a.source, a.threat_col, a.act_col, a.start)
    if a.verify:
        return verify(df, a.verify)
    out = build_readings(df)
    a.out.write_text(json.dumps(out, indent=2))
    print(f"wrote {a.out}  as_of {out['as_of']}  "
          f"TAR {out['readings'][0]['tar']}  band {out['readings'][0]['band']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

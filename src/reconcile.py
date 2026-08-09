"""
reconcile.py — find out which construction produced output/tar_monthly.csv.

tar_ingest.py and 02_build_tar.py disagree. Rather than guess one variant at a
time, this enumerates the plausible construction choices, scores each against
the reference series, and prints a ranked table. Whichever line comes back at
max|diff| ~ 0 is what the paper pipeline actually computed.

    python reconcile.py --source ..\\data\\gpr_monthly.dta --reference ..\\output\\tar_monthly.csv

It changes nothing. It only reports.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from tar_ingest import _to_month, BENCHMARK_START   # noqa: E402


# --------------------------------------------------------------------------
# Candidate construction choices
# --------------------------------------------------------------------------

def deflators(t: pd.Series) -> dict[str, pd.Series]:
    """Every plausible mu_t. The paper specifies the expanding mean of threat
    coverage through t with a twelve-month minimum; the pre-correction version
    used the full-sample mean. Both are here, along with the near misses."""
    out = {
        "expanding incl t (paper)": t.expanding(min_periods=1).mean(),
        "expanding excl t": t.shift(1).expanding(min_periods=1).mean(),
        "full-sample mean (pre-correction)": pd.Series(t.mean(), index=t.index),
        "rolling 12": t.rolling(12, min_periods=12).mean(),
        "rolling 24": t.rolling(24, min_periods=24).mean(),
        "rolling 36": t.rolling(36, min_periods=36).mean(),
        "rolling 60": t.rolling(60, min_periods=60).mean(),
        "rolling 120": t.rolling(120, min_periods=120).mean(),
        "expanding median": t.expanding(min_periods=1).median(),
        "expanding incl t, min 12": t.expanding(min_periods=12).mean(),
        "expanding incl t, min 24": t.expanding(min_periods=24).mean(),
        "expanding incl t, min 36": t.expanding(min_periods=36).mean(),
    }
    return out


def ratios(t: pd.Series, a: pd.Series) -> dict[str, pd.Series]:
    return {
        "T/(A+1)": t / (a + 1.0),
        "T/A": t / a.replace(0, np.nan),
        "T/(A+1) share-form": (t / (t + a)) / (a + 1.0) * (t + a),   # algebraic cousin
    }


def candidates(t: pd.Series, a: pd.Series) -> dict[str, pd.Series]:
    """Compound forms: ratio x magnitude, plus a few non-compound variants."""
    out: dict[str, pd.Series] = {}
    for rname, r in ratios(t, a).items():
        for dname, mu in deflators(t).items():
            out[f"compound  {rname}  x  T/mu[{dname}]"] = r * (t / mu)
            out[f"ratio-deflated  {rname}  /  mu[{dname}]"] = r / mu
    for dname, mu in deflators(t).items():
        out[f"magnitude only  T/mu[{dname}]"] = t / mu
    out["threat share  T/(T+A)"] = t / (t + a)
    out["log ratio  log((T+1)/(A+1))"] = np.log((t + 1) / (a + 1))
    z = lambda x: (x - x.expanding(min_periods=12).mean()) / x.expanding(min_periods=12).std()
    out["z differential  z(T)-z(A)"] = z(t) - z(a)

    # The panel score is a 3-month moving average lagged one month (§4.1), so
    # a levels-only search cannot match a panel reference.
    for name in list(out):
        c = out[name]
        out[f"lag1  {name}"] = c.shift(1)
        out[f"ma3  {name}"] = c.rolling(3, min_periods=3).mean()
        out[f"ma3 lag1 (panel score)  {name}"] = (
            c.rolling(3, min_periods=3).mean().shift(1))
        out[f"ma6 lag1  {name}"] = c.rolling(6, min_periods=6).mean().shift(1)

    # An early version of this measure may have been standardized. A z-scored
    # series shares a candidate's shape while sharing none of its levels, which
    # is invisible to a levels comparison.
    for name in list(out):
        c = out[name]
        out[f"z-scored (full sample)  {name}"] = (c - c.mean()) / c.std()
        out[f"z-scored (expanding)  {name}"] = (
            (c - c.expanding(min_periods=12).mean()) / c.expanding(min_periods=12).std())
        with np.errstate(divide="ignore", invalid="ignore"):
            out[f"log  {name}"] = np.log(c.where(c > 0))
    return out


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def score(cand: pd.Series, ref: pd.Series) -> tuple[int, float, float, float, float]:
    a, b = cand.align(ref, join="inner")
    m = a.notna() & b.notna() & np.isfinite(a) & np.isfinite(b)
    n = int(m.sum())
    if n < 24:
        return n, np.nan, np.nan, np.nan, np.nan
    d = (a[m] - b[m]).abs()
    r = float(a[m].corr(b[m]))
    # ratio of scales, to separate "wrong shape" from "right shape, wrong scale"
    scale = float(np.median(b[m] / a[m].replace(0, np.nan)))
    return n, float(d.max()), float(d.median()), r, scale


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--column", help="reference column to reconcile (default: try TAR_mw then TAR_raw)")
    p.add_argument("--start", default=BENCHMARK_START)
    p.add_argument("--threat-col", default=None)
    p.add_argument("--act-col", default=None)
    p.add_argument("--top", type=int, default=12)
    a = p.parse_args()

    # ---- load source
    df = pd.read_stata(a.source) if a.source.suffix.lower() == ".dta" else (
        pd.read_excel(a.source) if a.source.suffix.lower() in {".xls", ".xlsx"}
        else pd.read_csv(a.source))
    dcol = next((c for c in df.columns if c.lower() in
                 {"month", "date", "yearmonth", "time"}), None)
    if dcol is None:
        sys.exit(f"no date column in source. Columns: {list(df.columns)[:20]}")
    df[dcol] = _to_month(df[dcol])
    df = df.dropna(subset=[dcol]).sort_values(dcol).set_index(dcol)

    tcol = a.threat_col or next((c for c in df.columns if c.upper() == "GPRT"), None)
    acol = a.act_col or next((c for c in df.columns if c.upper() == "GPRA"), None)
    if not tcol or not acol:
        sys.exit("could not find GPRT/GPRA — pass --threat-col and --act-col")

    # ---- load reference
    ref = pd.read_csv(a.reference)
    rdcol = next((c for c in ref.columns if c.lower() in
                  {"month", "date", "yearmonth", "time", "unnamed: 0"}), None)
    if rdcol is None:
        sys.exit(f"no date column in reference. Columns: {list(ref.columns)}")
    ref[rdcol] = _to_month(ref[rdcol])
    ref = ref.dropna(subset=[rdcol]).sort_values(rdcol).set_index(rdcol)

    # A panel reference repeats one global series across units, so the index
    # has duplicate dates and alignment would fan out. Collapse to one unit.
    if ref.index.duplicated().any():
        ucol = next((c for c in ref.columns if c.lower() in
                     ("unit", "corridor", "id")), None)
        if ucol:
            u = ref[ucol].astype(str).value_counts().index[0]
            ref = ref[ref[ucol].astype(str) == u]
            print(f"panel detected — collapsed to unit {u!r} ({len(ref)} rows)")
        else:
            ref = ref[~ref.index.duplicated(keep="first")]
            print(f"duplicate dates — kept first per month ({len(ref)} rows)")

    print(f"reference columns: {[c for c in ref.columns]}")
    print()

    # Descriptives first. A column whose median is negative cannot be a ratio,
    # and knowing that up front saves searching a space it was never in.
    tarlike = [c for c in ref.columns if "TAR" in c.upper()]
    if tarlike:
        print("reference series, as they actually are:")
        print(f"{'column':22} {'n':>5} {'min':>10} {'median':>10} {'max':>10}  reading")
        for c in tarlike:
            v = pd.to_numeric(ref[c], errors="coerce").dropna()
            if v.empty:
                continue
            note = ("mostly negative — standardized, not a ratio"
                    if v.median() < 0 else
                    "plausibly on the published band scale" if 0.2 < v.median() < 10
                    else "positive but off the band scale")
            print(f"{c:22} {len(v):5d} {v.min():10.4g} {v.median():10.4g} "
                  f"{v.max():10.4g}  {note}")
        print()

    # 2) Same vintage? The reference carries its own GPRT/GPRA in some builds.
    for col in ("GPRT", "GPRA"):
        if col in ref.columns and col in df.columns:
            a_, b_ = (pd.to_numeric(df[col], errors="coerce")
                      .align(pd.to_numeric(ref[col], errors="coerce"), join="inner"))
            m_ = a_.notna() & b_.notna()
            if m_.sum() >= 24:
                mx_ = float((a_[m_] - b_[m_]).abs().max())
                # CSV round-trips lose float precision; 1e-4 is rounding, not
                # a different vintage.
                verdict = ("same vintage" if mx_ < 1e-4
                           else f"DIFFERENT INPUTS (max|diff| {mx_:.6g})")
                if 0 < mx_ < 1e-4:
                    verdict += f" (max|diff| {mx_:.3g} — CSV rounding)"
                print(f"input check {col}: n={int(m_.sum())}  {verdict}")
    print()

    targets = ([a.column] if a.column else
               [c for c in ("TAR_mw", "TAR_raw", "TAR_share", "TAR_log", "tar", "ctar")
                if c in ref.columns])
    if not targets:
        sys.exit(f"none of TAR_mw/TAR_raw/TAR_share/TAR_log present. Pass --column.")

    # Run each target on BOTH sample windows: the paper's pipeline may have
    # built the series over the full 1900-2026 file, which changes every
    # expanding statistic even where the benchmark columns are blank.
    for start_label, start in (("benchmark 1985", a.start), ("full file", "1900-01")):
        sub = df[df.index >= pd.Timestamp(start)]
        t = pd.to_numeric(sub[tcol], errors="coerce")
        aa = pd.to_numeric(sub[acol], errors="coerce")
        keep = t.notna() & aa.notna()
        t, aa = t[keep], aa[keep]
        if len(t) < 60:
            continue
        cands = candidates(t, aa)

        for target in targets:
            r = pd.to_numeric(ref[target], errors="coerce")
            rows = []
            for name, c in cands.items():
                n, mx, md, corr, sc = score(c, r)
                if not np.isnan(mx):
                    rows.append((mx, md, corr, sc, n, name))
            rows.sort(key=lambda x: x[0])

            print(f"=== {target}  vs  candidates built on {start_label} "
                  f"({len(t)} months from {t.index[0].date()}) ===")
            print(f"{'max|diff|':>11} {'med|diff|':>10} {'r':>8} {'scale':>8} {'n':>5}  construction")
            for mx, md, corr, sc, n, name in rows[:a.top]:
                flag = "  <-- MATCH" if mx < 1e-6 else ""
                print(f"{mx:11.6g} {md:10.6g} {corr:8.4f} {sc:8.3f} {n:5d}  {name}{flag}")
            print()

    print("Reading this table:")
    print("  max|diff| ~ 0            that construction IS the reference. Done.")
    print("  r ~ 1 but scale != 1     right shape, wrong normaliser magnitude —")
    print("                           usually the deflator or the burn-in.")
    print("  r well below 1           genuinely different quantity, not a tweak.")
    print("  nothing close            the reference was not built from GPRT/GPRA")
    print("                           on this vintage — check 02_build_tar.py for")
    print("                           which input columns it actually reads.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

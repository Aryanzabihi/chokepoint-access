"""
v04_build_ctar.py — corridor-attributed TAR panel.

This is the object the whole corridor-threshold programme was missing. Your
existing panel_final.csv::tar is global: one number per month, repeated across
all seven units. A threshold estimated on it is by construction the same for
every corridor, so "do corridors differ?" was unanswerable, not unanswered.

Four specifications, matching the manuscript's factorial:
  ratio     T / max(A, 1)                       unbounded, the original
  share     T / (T + A)                         bounded
  logratio  log((T+1) / (A+1))                  bounded-ish, symmetric
  share_z   share x z(volume)                   magnitude-weighted, PRIMARY

share_z is primary because it was the only velocity definition to survive in
both samples in DIAG_velocity.py. The mechanism is magnitude weighting, not
boundedness -- the unweighted bounded specs failed everywhere. That was labelled
post hoc in the paper and stays post hoc here.

Normalisation is within-corridor and causal: at month t the mean and standard
deviation use only months <= t. Expanding is available but rolling-120 is the
default, because expanding broke on structural breaks in the historical
extension.

Outputs
  build/ctar_monthly.csv     corridor-month panel, all specs, all tier stacks
  build/ctar_daily.csv       corridor-day panel, primary spec only

Usage
  python v04_build_ctar.py
  python v04_build_ctar.py --cameo narrow --norm expanding
"""
from __future__ import annotations
import argparse
import os
import sys

import numpy as np
import pandas as pd

from vconfig import (BUILD, MIN_WARMUP_MONTHS, NORMALISATION, PRIMARY_CAMEO_SET,
                     ROLL_WINDOW_MONTHS, load_corridors, log)

COUNTS = os.path.join(BUILD, "corridor_daily_counts.csv.gz")

# C_proxy is gone on purpose. Sharing global-power actors across corridors
# pooled the series back into a single global one -- the very thing this
# build exists to escape. Location-based membership only.
TIER_STACKS = {
    "direct":   ["A_direct"],
    "regional": ["A_direct", "B_littoral"],
}


def causal_z(x: pd.Series, how: str, warmup: int, window: int) -> pd.Series:
    """Within-series standardisation using only information up to t."""
    v = np.log1p(x.clip(lower=0))
    if how == "expanding":
        m = v.expanding(min_periods=warmup).mean()
        s = v.expanding(min_periods=warmup).std()
    else:
        m = v.rolling(window, min_periods=warmup).mean()
        s = v.rolling(window, min_periods=warmup).std()
    return (v - m) / s.replace(0, np.nan)


def tar_specs(t: pd.Series, a: pd.Series) -> dict[str, pd.Series]:
    share = t / (t + a).replace(0, np.nan)
    return {
        "ratio":    t / a.clip(lower=1),
        "share":    share,
        "logratio": np.log((t + 1) / (a + 1)),
    }


def build(counts: pd.DataFrame, cameo: str, norm: str, freq: str) -> pd.DataFrame:
    tcol, acol = f"{cameo}_threat_n", f"{cameo}_act_n"
    tm, am = f"{cameo}_threat_mentions", f"{cameo}_act_mentions"
    for c in (tcol, acol, tm, am):
        if c not in counts.columns:
            raise SystemExit(f"column {c} missing -- rerun v03_attribute.py")

    roll_win = 12 if freq == "M" else 252
    log(f"  tar_share_z rolling window (existing code, freq={freq}): "
        f"{roll_win} periods, min_periods=6, trailing, shifted by 1")

    rows = []
    for stack_name, tiers in TIER_STACKS.items():
        d = counts[counts["tier"].isin(tiers)]
        if d.empty:
            continue
        g = (d.groupby(["corridor_id", "date"])[[tcol, acol, tm, am]]
               .sum().reset_index())

        for cid, sub in g.groupby("corridor_id"):
            sub = (sub.drop(columns=["corridor_id"])
                      .set_index("date").sort_index().astype(float))
            full = pd.date_range(sub.index.min(), sub.index.max(), freq="D")
            sub = sub.reindex(full, fill_value=0)

            if freq == "M":
                sub = sub.resample("MS").sum()

            t, a = sub[tcol].astype(float), sub[acol].astype(float)
            warm = MIN_WARMUP_MONTHS if freq == "M" else MIN_WARMUP_MONTHS * 30
            win = (ROLL_WINDOW_MONTHS if freq == "M"
                   else int(ROLL_WINDOW_MONTHS * 30.44))
            z = causal_z(t + a, norm, warm, win)

            out = pd.DataFrame(index=sub.index)
            out["corridor_id"] = cid
            out["tier_stack"] = stack_name
            out["threat"] = t
            out["act"] = a
            out["volume"] = t + a
            out["threat_mentions"] = sub[tm].astype(float)
            out["act_mentions"] = sub[am].astype(float)
            out["z_volume"] = z
            for k, v in tar_specs(t, a).items():
                out[f"tar_{k}"] = v

            # tar_share_z: proper trailing rolling z-score of tar_share, not a
            # product with z_volume (that was Defect A). The rolling window is
            # shifted by one period so day/month t's baseline never includes
            # t itself -- otherwise every observation contributes to its own
            # normalisation and outliers get partially absorbed into the mean
            # and sd that supposedly measure them.
            share = out["tar_share"]
            out["tar_roll_mean"] = share.rolling(roll_win, min_periods=6).mean().shift(1)
            out["tar_roll_sd"] = share.rolling(roll_win, min_periods=6).std().shift(1)
            out["tar_share_z"] = ((share - out["tar_roll_mean"])
                                   / out["tar_roll_sd"].replace(0, np.nan))

            # dynamics on the primary spec, computed within corridor
            p = out["tar_share_z"]
            lag = 3 if freq == "M" else 63
            out["tar_velocity"] = p.diff(lag)
            out["tar_acceleration"] = out["tar_velocity"].diff(lag)
            rows.append(out.reset_index().rename(columns={"index": "date"}))

    panel = pd.concat(rows, ignore_index=True)
    return panel.sort_values(["tier_stack", "corridor_id", "date"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cameo", default=PRIMARY_CAMEO_SET,
                    choices=["narrow", "baseline", "wide"])
    ap.add_argument("--norm", default=NORMALISATION,
                    choices=["expanding", "rolling"])
    ap.add_argument("--out-daily", default="ctar_daily.csv",
                    help="output filename for the daily panel, under build/")
    ap.add_argument("--out-monthly", default="ctar_monthly.csv",
                    help="output filename for the monthly panel, under build/")
    args = ap.parse_args()

    if not os.path.exists(COUNTS):
        raise SystemExit(f"missing {COUNTS} -- run v03_attribute.py first")

    log(f"loading {COUNTS}")
    counts = pd.read_csv(COUNTS, parse_dates=["date"])
    log(f"  {len(counts):,} corridor-tier-days")

    log(f"building monthly panel (cameo={args.cameo}, norm={args.norm})")
    m = build(counts, args.cameo, args.norm, "M")
    pm = os.path.join(BUILD, args.out_monthly)
    m.to_csv(pm, index=False)
    log(f"  -> {pm}  {len(m):,} rows, {m['corridor_id'].nunique()} corridors")

    log("building daily panel")
    d = build(counts, args.cameo, args.norm, "D")
    d = d[d["tier_stack"] == "direct"]
    pd_ = os.path.join(BUILD, args.out_daily)
    d.to_csv(pd_, index=False)
    log(f"  -> {pd_}  {len(d):,} rows")

    # sanity: the whole point is that corridors are no longer identical
    prim = m[m["tier_stack"] == "direct"].pivot_table(
        index="date", columns="corridor_id", values="tar_share_z")
    prim = prim.dropna(how="all")
    if prim.shape[1] > 1:
        cm = prim.corr().where(~np.eye(prim.shape[1], dtype=bool))
        log("")
        log(f"cross-corridor correlation of the primary series: "
            f"mean {cm.stack().mean():.3f}, max {cm.stack().max():.3f}")
        log("  If the mean is above ~0.9 the series is still effectively global")
        log("  and per-corridor thresholds will not separate. Check the tier mix:")
        log("  Littoral attribution pulls in whole-country conflict volume and")
    log("  pools corridors that share a large violent neighbour.")


if __name__ == "__main__":
    sys.exit(main())

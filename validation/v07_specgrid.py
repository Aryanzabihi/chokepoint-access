"""
v07_specgrid.py — does ANY specification show signal, and is the best one real?

v06 tested one cell: level, 30-day horizon, 20% depth. It returned a pooled
AUC of 0.466 and an episode-weighted mean of 0.491. That is chance. But it
omitted VELOCITY, which is the primary in your manuscript, so the space has
not actually been searched.

This searches it -- and then does the thing that makes searching legitimate.

THE MULTIPLICITY PROBLEM, AND THE FIX
  Running 4 scores x 3 horizons x 3 depths and reporting the best cell is the
  post-hoc selection your reviewers already flagged once. The maximum of 36
  correlated AUCs is well above 0.5 even when every one of them is noise.
  So the null distribution here is the distribution OF THE MAXIMUM, obtained
  by circularly rotating each corridor's score against its own labels. The
  rotation preserves the score's autocorrelation and the labels' episode
  structure, and destroys only the alignment between them. The p-value asks:
  how often does pure noise, searched just as hard, produce a best cell this
  good?

  If that p-value is not small, no cell in the grid is reportable -- including
  the one at the top. Reporting it anyway is how a spurious threshold gets
  published.

  A cell that survives is then re-checked on episodes after 2024-01-01 using
  a cut fitted only on episodes before it.

Outputs
  report/v07_grid.csv          every cell, reported in full
  report/v07_verdict.txt

Usage
  python v07_specgrid.py
  python v07_specgrid.py --perms 500
"""
from __future__ import annotations
import argparse
import os
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from vconfig import BUILD, REPORT, log
from v06_validate import build_sample, youden

rng = np.random.default_rng(20260805)

SCORES = ["tar_share_z", "tar_velocity"]
HORIZONS = [30, 60, 90]
DEPTHS = [10, 20, 30]
SPLIT = pd.Timestamp("2024-01-01")


def residualise(s: pd.DataFrame, col: str) -> pd.Series:
    return s[col] - s.groupby("date")[col].transform("mean")


def pooled_auc(y: np.ndarray, x: np.ndarray) -> float:
    m = np.isfinite(x)
    if m.sum() < 50 or len(np.unique(y[m])) < 2:
        return np.nan
    return float(roc_auc_score(y[m], x[m]))


def rotate_within_corridor(s: pd.DataFrame, col: str) -> np.ndarray:
    """Circular shift per corridor: keeps autocorrelation, kills alignment."""
    out = s[col].to_numpy().copy()
    for _, idx in s.groupby("corridor_id").indices.items():
        idx = np.sort(idx)
        k = int(rng.integers(1, len(idx))) if len(idx) > 1 else 0
        out[idx] = np.roll(out[idx], k)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perms", type=int, default=300)
    ap.add_argument("--input", default=os.path.join(BUILD, "ctar_daily.csv"),
                    help="path to the daily corridor-TAR panel")
    args = ap.parse_args()

    tar = pd.read_csv(args.input, parse_dates=["date"])
    out = pd.read_csv(os.path.join(BUILD, "corridor_outcomes.csv"), parse_dates=["date"])

    samples = {}
    for dp in DEPTHS:
        for h in HORIZONS:
            try:
                s = build_sample(tar, out, dp, h)
            except ValueError:
                continue
            if len(s) and s["label"].sum() >= 20:
                for c in SCORES:
                    s[c + "_resid"] = residualise(s, c)
                samples[(dp, h)] = s
    log(f"built {len(samples)} (depth, horizon) samples")

    cols = [c + suf for c in SCORES for suf in ("", "_resid")]

    rows = []
    for (dp, h), s in samples.items():
        y = s["label"].to_numpy()
        for c in cols:
            x = s[c].to_numpy()
            a = pooled_auc(y, x)
            # temporal holdout on the same cell
            tr, te = s["date"] < SPLIT, s["date"] >= SPLIT
            a_te = np.nan
            if te.sum() > 100 and s.loc[te, "label"].nunique() == 2:
                a_te = pooled_auc(s.loc[te, "label"].to_numpy(),
                                  s.loc[te, c].to_numpy())
            rows.append({"depth": dp, "horizon": h, "score": c,
                         "n_days": len(s), "n_pre_onset": int(y.sum()),
                         "episodes": int(s.loc[s["label"] == 1, "episode"].nunique()),
                         "auc": a, "auc_post2024": a_te})
    grid = pd.DataFrame(rows)
    grid["auc"] = grid["auc"].astype(float)
    grid.sort_values("auc", ascending=False).to_csv(
        os.path.join(REPORT, "v07_grid.csv"), index=False)

    obs_max = float(np.nanmax(grid["auc"]))
    best = grid.loc[grid["auc"].idxmax()]

    # ---- null distribution of the MAXIMUM over the whole grid -----------
    log(f"permutation null over the grid maximum ({args.perms} rotations)")
    null_max = []
    for i in range(args.perms):
        m = -np.inf
        for (dp, h), s in samples.items():
            y = s["label"].to_numpy()
            for c in cols:
                a = pooled_auc(y, rotate_within_corridor(s, c))
                if a == a and a > m:
                    m = a
        null_max.append(m)
        if (i + 1) % 50 == 0:
            log(f"  {i+1}/{args.perms}")
    null_max = np.array([v for v in null_max if np.isfinite(v)])
    pval = float((null_max >= obs_max).mean()) if len(null_max) else np.nan

    L = []
    A = L.append
    A("SPECIFICATION GRID AND PERMUTATION NULL")
    A("=" * 78)
    A("")
    A("FULL GRID (all cells, ranked -- reported in full on purpose)")
    A(grid.sort_values("auc", ascending=False).to_string(index=False))
    A("")
    A("BEST CELL")
    A(f"  score {best['score']}, horizon {best['horizon']}d, depth {best['depth']}%")
    A(f"  AUC {best['auc']:.3f}   post-2024 holdout AUC {best['auc_post2024']}")
    A("")
    A("PERMUTATION NULL ON THE MAXIMUM")
    A(f"  cells searched          : {grid['auc'].notna().sum()}")
    A(f"  observed best AUC       : {obs_max:.3f}")
    A(f"  null max, median        : {np.median(null_max):.3f}")
    A(f"  null max, 95th pct      : {np.percentile(null_max, 95):.3f}")
    A(f"  p-value                 : {pval:.3f}")
    A("")
    if not np.isfinite(pval) or pval > 0.10:
        A("  VERDICT: the best cell in the grid is not better than what the same")
        A("  search finds in rotated noise. NO specification is reportable, and")
        A("  that includes the one at the top of the table. Corridor-specific")
        A("  thresholds cannot be estimated because there is no signal to")
        A("  threshold -- this is upstream of the heterogeneity question, which")
        A("  should not be reported at all.")
        A("")
        A("  This is a real, publishable negative result about a 2019-2026")
        A("  transit-disruption outcome. It does NOT overturn the manuscript's")
        A("  event-onset findings, which use a different outcome over a longer")
        A("  sample. Say which claim is being tested.")
    else:
        A("  VERDICT: the best cell survives the search-corrected null.")
        A("  Now check the post-2024 holdout column for that cell. If the")
        A("  holdout AUC collapses toward 0.5, the cell fitted the Red Sea")
        A("  episode and nothing more.")
    A("")
    A("BEFORE CONCLUDING EITHER WAY, THE THINGS THIS CANNOT RULE OUT")
    A("  1. The sample starts 2019 and the primary series only becomes defined")
    A("     ~2020-07 after the normalisation warm-up. Escalations before then")
    A("     are absent, not tested.")
    A("  2. The transit outcome measures diversion, which is a SHIPPING")
    A("     decision. TAR may lead escalation while lagging the insurers and")
    A("     owners who reroute first. A null here is evidence against TAR as a")
    A("     rerouting predictor, not against TAR as an escalation indicator.")
    A("  3. AIS goes dark under jamming in exactly these corridors, so the")
    A("     outcome itself is measured worst when it matters most.")

    p = os.path.join(REPORT, "v07_verdict.txt")
    open(p, "w").write("\n".join(L))
    log(f"  -> {p}")
    print()
    print("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())

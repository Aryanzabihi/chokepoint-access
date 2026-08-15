"""
v10_reconcile_permutation.py — is v09's closest-to-published-AUC match real?

v09 searched 192 designs (lead x exclude x start x units x avg x sign) for
whichever one lands closest to the manuscript's own reported AUC and found
one within 0.0005 of 0.753 (chokepoint units, within-unit-averaged AUC,
3-month lead, sign +1). That is the same multiplicity problem v07 already
solved for the corridor-threshold grid, just pointed at a different
question: v07 asked "is the maximum AUC in this grid better than noise
searched equally hard"; this asks "does the closest cell in this grid land
nearer a fixed target than noise searched equally hard would, by chance."
Same fix either way -- the null distribution of the grid's own extremum,
built by circularly rotating each unit's own TAR series against the fixed
onset labels (preserves each series' autocorrelation, destroys only the
alignment), exactly mirroring v07's rotate_within_corridor.

Tests BOTH published figures the manuscript reports (0.753 pooled, 0.842
chokepoint-restricted), since v09 found a close match to only one of them
and it came from the units subset the README's own framing did not expect.

Outputs
  report/v10_verdict.txt

Usage
  python v10_reconcile_permutation.py
  python v10_reconcile_permutation.py --perms 300
"""
from __future__ import annotations
import argparse
import itertools
import os
import sys

import numpy as np
import pandas as pd

from vconfig import REPORT, log
from v09_reconcile import load_panel, label, auc, CHOKEPOINT_UNITS, TARGET

TARGET_CHOKEPOINT = 0.842
rng = np.random.default_rng(20260805)

LEADS = [3, 6, 12, 24]
EXCLUDES = [0, 6, 12]
STARTS = [None, "1985-01-01"]
UNIT_MODES = ["all", "chokepoint"]
AVGS = ["pooled", "within"]
SIGNS = [1, -1]


def rotate_within_unit(d: pd.DataFrame, col: str) -> np.ndarray:
    """Circular shift per unit: keeps each series' own autocorrelation,
    kills only its alignment with the fixed onset labels. Direct analogue
    of v07_specgrid.rotate_within_corridor, adapted to this panel's "unit"
    column and monthly (not daily) spacing."""
    out = d[col].to_numpy().copy()
    for _, idx in d.groupby("unit").indices.items():
        idx = np.sort(idx)
        k = int(rng.integers(1, len(idx))) if len(idx) > 1 else 0
        out[idx] = np.roll(out[idx], k)
    return out


def grid_min_gaps(d: pd.DataFrame, tarcol: str) -> tuple[float, float]:
    """One pass over the full 192-cell design grid on whatever panel is
    handed in (real or rotated). Returns (min gap to 0.753, min gap to
    0.842) across every cell -- labels are recomputed only once per
    (lead, exclude, start, units) tuple and reused across avg x sign, the
    same nesting v09_reconcile.main() itself uses."""
    best_pooled, best_chokepoint = np.inf, np.inf
    for lead, exclude, start, units in itertools.product(LEADS, EXCLUDES, STARTS, UNIT_MODES):
        s = d if start is None else d[d["date"] >= pd.Timestamp(start)]
        if units == "chokepoint":
            s = s[s["unit"].isin(CHOKEPOINT_UNITS)]
        if not len(s):
            continue
        s = label(s, lead, exclude)
        if s["label"].sum() < 3:
            continue
        for avg, sign in itertools.product(AVGS, SIGNS):
            if avg == "pooled":
                a = auc(s["label"].to_numpy(), s[tarcol].to_numpy(), sign)
            else:
                per = [auc(g["label"].to_numpy(), g[tarcol].to_numpy(), sign)
                       for _, g in s.groupby("unit")]
                per = [v for v in per if v == v]
                a = float(np.mean(per)) if per else np.nan
            if a != a:
                continue
            # Every cell competes for both targets regardless of its own
            # units-mode -- v09's own closest-overall match to 0.753 came
            # from units=chokepoint, not the units=all the README's framing
            # assumed, so gating the search by units-mode per target would
            # test a stricter (and wrong) question than the one v09 actually
            # answered.
            gap_pooled = abs(a - TARGET)
            gap_chokepoint = abs(a - TARGET_CHOKEPOINT)
            if gap_pooled < best_pooled:
                best_pooled = gap_pooled
            if gap_chokepoint < best_chokepoint:
                best_chokepoint = gap_chokepoint
    return best_pooled, best_chokepoint


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perms", type=int, default=300)
    args = ap.parse_args()

    d = load_panel()
    tarcol = "tar" if "tar" in d.columns else next(c for c in d.columns if "tar" in c.lower())

    obs_gap_pooled, obs_gap_chokepoint = grid_min_gaps(d, tarcol)
    log(f"observed: closest to {TARGET} (any units) = {obs_gap_pooled:.4f}, "
        f"closest to {TARGET_CHOKEPOINT} (any units) = {obs_gap_chokepoint:.4f}")

    log(f"permutation null over the grid minimum ({args.perms} rotations)")
    null_pooled, null_chokepoint = [], []
    dr = d.copy()
    for i in range(args.perms):
        dr[tarcol] = rotate_within_unit(d, tarcol)
        gp, gc = grid_min_gaps(dr, tarcol)
        null_pooled.append(gp)
        null_chokepoint.append(gc)
        if (i + 1) % 50 == 0:
            log(f"  {i + 1}/{args.perms}")

    null_pooled = np.array(null_pooled)
    null_chokepoint = np.array(null_chokepoint)
    # a REAL correspondence lands CLOSER (smaller gap) than noise searched
    # equally hard, so the p-value is "how often is null at least as close".
    # +1/+1 (Phipson & Smyth) so a zero-hit result reports as an upper bound
    # ("< 1/(perms+1)"), never as an impossible-sounding exact 0.
    n = len(null_pooled)
    p_pooled = float((null_pooled <= obs_gap_pooled).sum() + 1) / (n + 1)
    p_chokepoint = float((null_chokepoint <= obs_gap_chokepoint).sum() + 1) / (n + 1)

    L = []
    A = L.append
    A("RECONCILIATION GRID -- PERMUTATION NULL ON THE CLOSEST MATCH")
    A("=" * 78)
    A("")
    A("CAVEAT ON THE INPUT DATA, READ THIS FIRST")
    A("  final_output/panel_final.csv is not available on this machine (it is")
    A("  .gitignored, same as the manuscript itself). This run scores a")
    A("  RECONSTRUCTION: tar_ingest.build_tar() against data/gpr_monthly.dta,")
    A("  repeated across the 7 manuscript panel units -- reference.py confirms")
    A("  this construction matches the pinned fingerprint of the real")
    A("  panel's own tar column to better than 0.0005 (484 overlapping")
    A("  months). Treat this run as strong evidence, not a substitute for")
    A("  running it again against the authentic file.")
    A("")
    A(f"target 1: {TARGET} (README's 'pooled')")
    A(f"  observed closest gap    : {obs_gap_pooled:.4f}")
    A(f"  null gap,  median       : {np.median(null_pooled):.4f}")
    A(f"  null gap,  5th pct      : {np.percentile(null_pooled, 5):.4f}")
    A(f"  p-value                 : {p_pooled:.4f}")
    A("")
    A(f"target 2: {TARGET_CHOKEPOINT} (README's 'chokepoint-restricted')")
    A(f"  observed closest gap    : {obs_gap_chokepoint:.4f}")
    A(f"  null gap,  median       : {np.median(null_chokepoint):.4f}")
    A(f"  null gap,  5th pct      : {np.percentile(null_chokepoint, 5):.4f}")
    A(f"  p-value                 : {p_chokepoint:.4f}")
    A("")

    def verdict(name, p, obs, n):
        pstr = f"< {1 / (n + 1):.4f}" if p <= 1 / (n + 1) else f"{p:.4f}"
        if p < 0.05:
            return (f"  VERDICT ({name}): noise searched this hard lands at least as "
                    f"close to {name} only p={pstr} of the time (observed gap "
                    f"{obs:.4f}). This is a real correspondence, not a search "
                    f"artifact -- the design that produces it is worth naming plainly.")
        return (f"  VERDICT ({name}): noise searched this hard lands at least as close "
                f"to {name} p={pstr} of the time (observed gap {obs:.4f}). NOT "
                f"distinguishable from a search artifact -- do not report this match "
                f"as reproducing the published figure.")

    A(verdict(str(TARGET), p_pooled, obs_gap_pooled, n))
    A("")
    A(verdict(str(TARGET_CHOKEPOINT), p_chokepoint, obs_gap_chokepoint, n))

    p = os.path.join(REPORT, "v10_verdict.txt")
    open(p, "w").write("\n".join(L))
    log(f"  -> {p}")
    print()
    print("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())

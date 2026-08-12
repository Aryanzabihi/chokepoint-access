"""
v08_onset_test.py — the manuscript's own claim, on corridor-attributed data.

v07 showed TAR does not predict transit diversion. That is a different outcome
from the one the paper claims. This tests the paper's claim: do the months
BEFORE an escalation onset carry a higher corridor TAR than ordinary months?

THE COMPARISON THAT ACTUALLY MATTERS
  Three scores are run on one identical design:
    global    the cross-corridor mean at each month -- a stand-in for the
              manuscript's existing series, which is constant across units
    corridor  the corridor's own attributed TAR
    resid     corridor minus global
  If `corridor` does not beat `global`, then corridor attribution -- the whole
  point of the GDELT rebuild -- adds nothing to the paper, and the honest
  conclusion is that TAR is a global indicator that happens to be evaluated at
  chokepoints. If `resid` carries the signal, corridor attribution is doing
  real work. This single contrast is worth more than any threshold table.

COVERAGE GUARD, READ IT BEFORE TRUSTING ANY PRE-2000 RESULT
  GDELT's source base in the 1980s is a small number of wire services. Five of
  the eight onsets (Tanker War 1987, Gulf War 1990, Bosnia 1992, Kosovo 1999,
  Iraq 2003) sit in that thin era. The script reports events per corridor-month
  by era and flags any onset whose pre-window is too sparse to carry a TAR
  value. An onset flagged SPARSE is not evidence either way -- do not count it
  as a failure or a success.

Same permutation null as v07: the distribution of the grid MAXIMUM under
circular rotation, so a best cell has to beat noise searched equally hard.

Outputs
  report/v08_coverage.csv
  report/v08_grid.csv
  report/v08_verdict.txt

Usage
  python v08_onset_test.py --perms 300
"""
from __future__ import annotations
import argparse
import os
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from vconfig import BUILD, ONSETS, PLACEBOS, REPORT, REVERSALS, log

rng = np.random.default_rng(20260805)

HORIZONS = [3, 6, 12]          # months before onset
POST_BUFFER = 12               # months after onset excluded from the negatives
MIN_EVENTS_PER_MONTH = 5       # below this a corridor-month is not measured


def load_panel(tier: str = "direct", path: str | None = None) -> pd.DataFrame:
    path = path or os.path.join(BUILD, "ctar_monthly.csv")
    p = pd.read_csv(path, parse_dates=["date"])
    p = p[p["tier_stack"] == tier].copy()
    p["global"] = p.groupby("date")["tar_share_z"].transform("mean")
    p["corridor"] = p["tar_share_z"]
    p["resid"] = p["corridor"] - p["global"]
    p["global_v"] = p.groupby("date")["tar_velocity"].transform("mean")
    p["corridor_v"] = p["tar_velocity"]
    p["resid_v"] = p["corridor_v"] - p["global_v"]
    return p


def label_panel(p: pd.DataFrame, horizon: int, events) -> pd.DataFrame:
    p = p.copy()
    p["label"] = 0
    p["drop"] = False
    p["event_id"] = -1
    for k, (name, when, unit, *_rest) in enumerate(events):
        t = pd.Timestamp(when)
        pre = (p["corridor_id"] == unit) & (p["date"] < t) & \
              (p["date"] >= t - pd.DateOffset(months=horizon))
        post = (p["corridor_id"] == unit) & (p["date"] >= t) & \
               (p["date"] < t + pd.DateOffset(months=POST_BUFFER))
        p.loc[pre, "label"] = 1
        p.loc[pre, "event_id"] = k
        p.loc[post, "drop"] = True
    return p[~p["drop"]]


def auc(y, x):
    m = np.isfinite(x)
    if m.sum() < 50 or len(np.unique(y[m])) < 2 or y[m].sum() < 3:
        return np.nan
    return float(roc_auc_score(y[m], x[m]))


def rotate(p: pd.DataFrame, col: str) -> np.ndarray:
    out = p[col].to_numpy().copy()
    for _, idx in p.groupby("corridor_id").indices.items():
        idx = np.sort(idx)
        if len(idx) > 1:
            out[idx] = np.roll(out[idx], int(rng.integers(1, len(idx))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perms", type=int, default=300)
    ap.add_argument("--tier", default="direct", choices=["direct", "regional"])
    ap.add_argument("--input", default=None,
                    help="path to the monthly corridor-TAR panel")
    args = ap.parse_args()

    p = load_panel(args.tier, args.input)
    log(f"panel {len(p):,} corridor-months, "
        f"{p['date'].min():%Y-%m} to {p['date'].max():%Y-%m}")

    # ---------------------------------------------------- coverage guard
    p["era"] = pd.cut(p["date"].dt.year, [0, 1994, 2004, 2014, 2100],
                      labels=["pre-1995", "1995-2004", "2005-2014", "2015+"])
    cov = (p.groupby(["corridor_id", "era"], observed=True)["volume"]
             .median().unstack())
    cov.to_csv(os.path.join(REPORT, "v08_coverage.csv"))

    flags = []
    for name, when, unit, *_ in ONSETS:
        t = pd.Timestamp(when)
        w = p[(p["corridor_id"] == unit) & (p["date"] < t) &
              (p["date"] >= t - pd.DateOffset(months=12))]
        med = float(w["volume"].median()) if len(w) else 0.0
        flags.append({"event": name, "onset": when, "corridor": unit,
                      "median_events_per_month_pre": med,
                      "status": "SPARSE" if med < MIN_EVENTS_PER_MONTH else "ok"})
    fl = pd.DataFrame(flags)
    usable = fl[fl["status"] == "ok"]["event"].tolist()
    ev_ok = [e for e in ONSETS if e[0] in usable]

    # ---------------------------------------------------- grid
    scores = ["global", "corridor", "resid", "global_v", "corridor_v", "resid_v"]
    rows = []
    for h in HORIZONS:
        d = label_panel(p, h, ev_ok)
        y = d["label"].to_numpy()
        for c in scores:
            rows.append({"horizon": h, "score": c, "n_months": len(d),
                         "n_pre": int(y.sum()),
                         "events": int(d.loc[d["label"] == 1, "event_id"].nunique()),
                         "auc": auc(y, d[c].to_numpy())})
    grid = pd.DataFrame(rows)
    grid.sort_values("auc", ascending=False).to_csv(
        os.path.join(REPORT, "v08_grid.csv"), index=False)

    obs = float(np.nanmax(grid["auc"])) if grid["auc"].notna().any() else np.nan

    # ---------------------------------------------------- permutation null
    null_max = []
    if np.isfinite(obs):
        log(f"permutation null ({args.perms} rotations)")
        samples = {h: label_panel(p, h, ev_ok) for h in HORIZONS}
        for i in range(args.perms):
            m = -np.inf
            for h, d in samples.items():
                y = d["label"].to_numpy()
                for c in scores:
                    a = auc(y, rotate(d, c))
                    if a == a and a > m:
                        m = a
            null_max.append(m)
            if (i + 1) % 50 == 0:
                log(f"  {i+1}/{args.perms}")
    null_max = np.array([v for v in null_max if np.isfinite(v)])
    pval = float((null_max >= obs).mean()) if len(null_max) else np.nan

    # ---------------------------------------------------- placebos
    pl_rows = []
    for name, when, unit in PLACEBOS + [(n, w, u) for n, w, u in REVERSALS]:
        if unit is None:
            continue
        t = pd.Timestamp(when)
        w6 = p[(p["corridor_id"] == unit) & (p["date"] < t) &
               (p["date"] >= t - pd.DateOffset(months=6))]
        if not len(w6):
            continue
        pl_rows.append({"case": name, "corridor": unit, "date": when,
                        "mean_corridor_tar": round(float(w6["corridor"].mean()), 3),
                        "mean_resid": round(float(w6["resid"].mean()), 3)})
    pl = pd.DataFrame(pl_rows)

    # ---------------------------------------------------- report
    L = []
    A = L.append
    A("ONSET TEST ON CORRIDOR-ATTRIBUTED TAR")
    A("=" * 78)
    A(f"tier: {args.tier}    panel: {p['date'].min():%Y-%m} to {p['date'].max():%Y-%m}")
    A("")
    A("COVERAGE GUARD -- which onsets are measurable at all")
    A(fl.to_string(index=False))
    A("")
    A(f"  usable onsets: {len(usable)} of {len(ONSETS)}  ({', '.join(usable)})")
    if len(usable) < 4:
        A("  WARNING: fewer than 4 measurable onsets. Any AUC below is a")
        A("  description of a handful of events, not an estimate of anything.")
    A("")
    A("MEDIAN EVENTS PER CORRIDOR-MONTH BY ERA")
    A(cov.round(1).to_string())
    A("")
    A("GRID")
    A(grid.sort_values("auc", ascending=False).to_string(index=False))
    A("")
    A("DOES CORRIDOR ATTRIBUTION ADD ANYTHING?")
    for h in HORIZONS:
        g = grid[grid["horizon"] == h].set_index("score")["auc"]
        if g.notna().any():
            A(f"  horizon {h:>2}m:  global {g.get('global', np.nan):.3f}   "
              f"corridor {g.get('corridor', np.nan):.3f}   "
              f"resid {g.get('resid', np.nan):.3f}")
    A("")
    A("  If corridor <= global at every horizon, the GDELT rebuild does not")
    A("  improve the paper's claim and should be reported as such rather than")
    A("  quietly dropped.")
    A("")
    A("PERMUTATION NULL ON THE GRID MAXIMUM")
    if np.isfinite(obs) and len(null_max):
        A(f"  observed best AUC : {obs:.3f}")
        A(f"  null max median   : {np.median(null_max):.3f}")
        A(f"  null max 95th pct : {np.percentile(null_max, 95):.3f}")
        A(f"  p-value           : {pval:.3f}")
        A("")
        if pval > 0.10:
            A("  VERDICT: no specification beats an equally hard search of noise.")
            A("  Combined with v07, TAR does not discriminate on corridor-")
            A("  attributed data against either outcome. That is a finding about")
            A("  the indicator, and it needs to reach the manuscript before a")
            A("  referee reaches it.")
        else:
            A("  VERDICT: the best cell survives. Check WHICH score won. If it is")
            A("  'global', the paper's original series was already sufficient and")
            A("  corridor attribution is a null result. If 'corridor' or 'resid'")
            A("  won, corridor attribution is doing real work and belongs in the")
            A("  paper as the main contribution.")
    else:
        A("  not computed -- too few usable onsets")
    A("")
    A("PLACEBOS AND REVERSALS -- TAR MUST NOT BE ELEVATED HERE")
    A(pl.to_string(index=False) if len(pl) else "  (none in sample)")

    q = os.path.join(REPORT, "v08_verdict.txt")
    open(q, "w").write("\n".join(L))
    log(f"  -> {q}")
    print()
    print("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())

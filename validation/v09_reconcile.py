"""
v09_reconcile.py — why does your own series score 0.45 here and 0.753 in the paper?

THE PROBLEM THIS EXISTS TO SOLVE

v08 merged final_output/panel_final.csv::tar and scored it as the `global`
benchmark. It returned AUC 0.448 at a 3-month lead, 0.435 at 6, 0.383 at 12.
Your manuscript reports 0.753 pooled and 0.842 chokepoint-restricted on that
same column. Same data, so the difference is entirely DESIGN.

Until that is located, the v08 null means nothing about your claim -- it means
v08's design does not reproduce your design. Reporting "TAR fails" on the
strength of it would be wrong.

Note 0.383 in particular. That is not weak, it is INVERTED: 1 - 0.383 = 0.617.
Several cells look like sign flips of a real signal, which is one of the
candidate explanations below.

CANDIDATES, EACH ISOLATED AS ONE AXIS OF THE GRID
  sign        does higher TAR mean more or less onset risk in your design?
  lead        how many months before onset count as positive
  exclude     how many post-onset months are dropped rather than scored
  start       1979 vs the manuscript's sample start
  units       all 7 vs chokepoint-restricted (your 0.842 case)
  averaging   pooled across units vs mean of within-unit AUCs

Any cell reaching ~0.75 identifies the design. If NO cell does, then the
manuscript's number comes from something not in this list -- a different
outcome definition, a fitted model rather than the raw series, or a coding
difference in the event dates -- and that is worth knowing before submission.

Outputs
  report/v09_reconcile.csv     every design cell
  report/v09_reconcile.txt

Usage
  python v09_reconcile.py
"""
from __future__ import annotations
import os
import sys
import itertools

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from vconfig import ONSETS, REPORT, ROOT, log

PANEL = os.path.join(ROOT, "final_output", "panel_final.csv")
TARGET = 0.753
CHOKEPOINT_UNITS = {"hormuz", "bab_el_mandeb", "suez", "malacca", "taiwan_strait"}


def load_panel() -> pd.DataFrame:
    if not os.path.exists(PANEL):
        raise SystemExit(f"missing {PANEL}")
    d = pd.read_csv(PANEL)
    log(f"panel columns: {list(d.columns)}")
    dc = next((c for c in d.columns if c.lower() in ("date", "month", "ym", "time")), None)
    uc = next((c for c in d.columns
               if c.lower() in ("unit", "corridor", "corridor_id", "panel_unit")), None)
    if dc is None:
        raise SystemExit("no date column found")
    d["date"] = pd.to_datetime(d[dc], errors="coerce")
    d = d.dropna(subset=["date"])
    d["unit"] = d[uc].astype(str).str.strip().str.lower().str.replace(
        r"[ \-]+", "_", regex=True) if uc else "pooled"
    log(f"{len(d):,} rows, {d['unit'].nunique()} units, "
        f"{d['date'].min():%Y-%m} to {d['date'].max():%Y-%m}")
    log(f"units: {sorted(d['unit'].unique())}")
    return d


def label(d: pd.DataFrame, lead: int, exclude: int) -> pd.DataFrame:
    on = pd.DataFrame(ONSETS, columns=["event", "onset", "unit", "type", "note"])
    on["onset"] = pd.to_datetime(on["onset"])
    out = []
    for u, g in d.groupby("unit"):
        g = g.sort_values("date").reset_index(drop=True)
        lab = np.zeros(len(g), dtype=int)
        drop = np.zeros(len(g), dtype=bool)
        for _, e in on[on["unit"] == u].iterrows():
            pre = (g["date"] >= e["onset"] - pd.DateOffset(months=lead)) & \
                  (g["date"] < e["onset"])
            lab[pre.to_numpy()] = 1
            if exclude:
                post = (g["date"] >= e["onset"]) & \
                       (g["date"] <= e["onset"] + pd.DateOffset(months=exclude))
                drop[post.to_numpy()] = True
        out.append(g.assign(label=lab, drop=drop))
    r = pd.concat(out, ignore_index=True)
    return r[~r["drop"]]


def auc(y, x, sign):
    x = np.asarray(x, dtype=float) * sign
    m = np.isfinite(x)
    if m.sum() < 30 or len(np.unique(y[m])) < 2:
        return np.nan
    return float(roc_auc_score(y[m], x[m]))


def main():
    d = load_panel()
    tarcol = "tar" if "tar" in d.columns else None
    if tarcol is None:
        cands = [c for c in d.columns if "tar" in c.lower()]
        if not cands:
            raise SystemExit(f"no TAR column in {list(d.columns)}")
        tarcol = cands[0]
    log(f"scoring column: {tarcol}")

    rows = []
    for lead, exclude, start, units, avg, sign in itertools.product(
            [3, 6, 12, 24], [0, 6, 12], [None, "1985-01-01"],
            ["all", "chokepoint"], ["pooled", "within"], [1, -1]):
        s = d if start is None else d[d["date"] >= pd.Timestamp(start)]
        if units == "chokepoint":
            s = s[s["unit"].isin(CHOKEPOINT_UNITS)]
        if not len(s):
            continue
        s = label(s, lead, exclude)
        if s["label"].sum() < 3:
            continue

        if avg == "pooled":
            a = auc(s["label"].to_numpy(), s[tarcol].to_numpy(), sign)
        else:
            per = [auc(g["label"].to_numpy(), g[tarcol].to_numpy(), sign)
                   for _, g in s.groupby("unit")]
            per = [v for v in per if v == v]
            a = float(np.mean(per)) if per else np.nan

        rows.append({"lead": lead, "exclude": exclude,
                     "start": start or "full", "units": units,
                     "avg": avg, "sign": sign,
                     "n_pos": int(s["label"].sum()), "n": len(s), "auc": a})

    R = pd.DataFrame(rows).dropna(subset=["auc"])
    R["gap_to_paper"] = (R["auc"] - TARGET).abs()
    R = R.sort_values("gap_to_paper")
    R.to_csv(os.path.join(REPORT, "v09_reconcile.csv"), index=False)

    L = []
    A = L.append
    A("RECONCILIATION: v08 DESIGN vs THE MANUSCRIPT'S REPORTED AUC")
    A("=" * 78)
    A(f"scoring {tarcol} from {os.path.relpath(PANEL, ROOT)}")
    A(f"target  {TARGET} pooled / 0.842 chokepoint-restricted")
    A("")
    A("CLOSEST 15 DESIGNS TO THE PUBLISHED NUMBER")
    A(R.head(15).to_string(index=False))
    A("")
    A("BEST AND WORST OVERALL")
    A(R.sort_values("auc", ascending=False).head(5).to_string(index=False))
    A("...")
    A(R.sort_values("auc").head(5).to_string(index=False))
    A("")
    best = R.iloc[0]
    A(f"closest cell: AUC {best['auc']:.3f} (gap {best['gap_to_paper']:.3f})")
    A(f"  lead {best['lead']}m, exclude {best['exclude']}m, start {best['start']}, "
      f"{best['units']} units, {best['avg']}, sign {best['sign']}")
    A("")
    A("SIGN CHECK -- the most important line here")
    for sg in (1, -1):
        sub = R[R["sign"] == sg]
        A(f"  sign {sg:+d}: median AUC {sub['auc'].median():.3f}, "
          f"max {sub['auc'].max():.3f}")
    A("")
    if best["gap_to_paper"] < 0.05:
        A("  A design in this grid reproduces the published number. Adopt that")
        A("  design in v08 and rerun the corridor comparison under it. The v08")
        A("  null should NOT be reported until that has been done -- it was")
        A("  measured under a design that does not match your paper.")
    else:
        A("  NO design in this grid comes within 0.05 of the published number.")
        A("  That means the manuscript's AUC does not come from thresholding the")
        A("  raw series against onset-lead windows at all. Most likely it is a")
        A("  FITTED quantity -- a hazard model's predicted probability, or a")
        A("  specification selected on the same events it is scored against.")
        A("  Either would explain the gap, and the second is the one a referee")
        A("  will ask about. Check what FINAL_RUN.py actually feeds into the")
        A("  AUC that produces Table 18 before doing anything else.")
    A("")
    A("IF THE SIGN IS NEGATIVE")
    A("  A consistently sub-0.5 AUC on the raw series means that in this data")
    A("  acts rise faster than threats ahead of onset, so TAR FALLS into an")
    A("  escalation rather than rising. That would not be a bug -- it would be")
    A("  a substantive result pointing the opposite way to the paper's premise,")
    A("  and it is worth far more attention than the null.")

    p = os.path.join(REPORT, "v09_reconcile.txt")
    open(p, "w").write("\n".join(L))
    log(f"  -> {p}")
    print()
    print("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())

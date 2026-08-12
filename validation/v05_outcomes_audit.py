"""
v05_outcomes_audit.py — outcome construction + estimability audit.

Two jobs, deliberately together, because the second decides what the validation
stage is even allowed to attempt.

1. OUTCOME. Turn PortWatch daily transit calls into a corridor-specific
   disruption label. The baseline is a TRAILING 365-day median, computed within
   corridor and using only past data. That is deliberate: PortWatch's AIS
   receiver network expanded in 2021 and produced a sustained level shift at
   Malacca and elsewhere, so any fixed reference period would read a coverage
   change as a trade collapse. A trailing baseline absorbs level shifts and
   still catches the sharp drops that matter.

2. ESTIMABILITY. Count, per corridor, how many positives exist under each
   candidate outcome. A Youden threshold with 1 positive is not a threshold,
   it is that positive's TAR value. This report decides which corridors get
   their own threshold, which get partial pooling, and which get the global
   threshold with a corridor-specific baseline rate.

Outputs
  build/corridor_outcomes.csv
  report/estimability.txt
  report/estimability.csv

Usage
  python v05_outcomes_audit.py
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd

from vconfig import BUILD, DATA, ONSETS, REPORT, load_corridors, log

DROP_DAYS = ["2022-05-12", "2023-02-14", "2024-01-09"]   # known source blackouts
DEPTHS = [0.10, 0.20, 0.30, 0.50]                        # fractional shortfall
PERSIST = 5           # consecutive days below the line to open an episode
MIN_GAP = 14          # days of recovery required to call it a NEW episode


def build_outcomes() -> pd.DataFrame:
    daily_p = os.path.join(DATA, "portwatch_daily.csv")
    map_p = os.path.join(DATA, "portwatch_id_map.csv")
    if not (os.path.exists(daily_p) and os.path.exists(map_p)):
        log("PortWatch files absent -- skipping the transit outcome")
        return pd.DataFrame()

    d = pd.read_csv(daily_p, parse_dates=["date"])
    idmap = pd.read_csv(map_p)
    d.columns = [c.lower() for c in d.columns]

    # PortWatch names these n_total / n_cargo / n_tanker / capacity*.
    # n_total is all transiting vessels; n_cargo excludes passenger and
    # service traffic and is the cleaner trade signal, but n_total is the
    # published headline series, so it is the default.
    prefer = ["n_total", "n_cargo", "n_transit_calls", "transit_calls"]
    callcol = next((c for c in prefer if c in d.columns), None)
    if callcol is None:
        callcol = next((c for c in d.columns
                        if c.startswith("n_") and c != "n_total"), None)
    if callcol is None:
        raise SystemExit(f"no vessel-count column in {list(d.columns)}")
    log(f"  transit column: {callcol}")

    d = d.merge(idmap, on="portid", how="inner")
    d = d[~d["date"].dt.strftime("%Y-%m-%d").isin(DROP_DAYS)]

    out = []
    for cid, sub in d.groupby("corridor_id"):
        sub = (sub.set_index("date")[[callcol]].sort_index()
                  .resample("D").mean().interpolate(limit=3))
        s = sub[callcol]
        smooth = s.rolling(7, min_periods=4).mean()
        base = s.rolling(365, min_periods=180).median().shift(1)
        dev = smooth / base - 1.0

        o = pd.DataFrame({"date": s.index, "corridor_id": cid,
                          "transit_calls": s.values,
                          "transit_ma7": smooth.values,
                          "transit_base365": base.values,
                          "transit_dev": dev.values})
        for dp in DEPTHS:
            raw = (o["transit_dev"] <= -dp).astype(int)
            sustained = raw.rolling(PERSIST, min_periods=PERSIST).min().fillna(0)
            sustained = np.asarray(sustained.astype(int).to_numpy(), dtype=int).copy()
            # Close short gaps. Without this a single sustained disruption that
            # briefly noses above the line is counted as several independent
            # episodes, which inflates the effective sample size -- exactly the
            # error the block bootstrap exists to prevent.
            idx = np.flatnonzero(sustained)
            if len(idx):
                for a, b in zip(idx[:-1], idx[1:]):
                    if 1 < b - a <= MIN_GAP:
                        sustained[a:b] = 1
            o[f"disr_{int(dp*100)}"] = sustained
        out.append(o)

    res = pd.concat(out, ignore_index=True)
    p = os.path.join(BUILD, "corridor_outcomes.csv")
    res.to_csv(p, index=False)
    log(f"  -> {p}  {len(res):,} corridor-days, "
        f"{res['corridor_id'].nunique()} corridors")
    return res


def main():
    log("building transit-based outcomes")
    out = build_outcomes()

    ctar_p = os.path.join(BUILD, "ctar_monthly.csv")
    ctar = (pd.read_csv(ctar_p, parse_dates=["date"])
            if os.path.exists(ctar_p) else pd.DataFrame())

    cor = load_corridors()
    onset = pd.DataFrame(ONSETS, columns=["event", "onset", "unit", "type", "label"])
    onset["onset"] = pd.to_datetime(onset["onset"])

    rows = []
    for _, c in cor.iterrows():
        cid = c["corridor_id"]
        r = {"corridor_id": cid,
             "manuscript_unit": int(c["manuscript_panel_unit"])}

        if len(ctar):
            s = ctar[(ctar["corridor_id"] == cid) & (ctar["tier_stack"] == "direct")]
            sd = ctar[(ctar["corridor_id"] == cid) & (ctar["tier_stack"] == "direct")]
            r["tar_months"] = len(s)
            r["tar_start"] = f"{s['date'].min():%Y-%m}" if len(s) else ""
            r["direct_months_nonzero"] = int((sd["volume"] > 0).sum()) if len(sd) else 0
            r["median_monthly_volume"] = float(s["volume"].median()) if len(s) else np.nan
        r["onsets"] = int((onset["unit"] == cid).sum())

        if len(out):
            o = out[out["corridor_id"] == cid]
            r["portwatch_days"] = len(o)
            for dp in DEPTHS:
                k = f"disr_{int(dp*100)}"
                r[k + "_days"] = int(o[k].sum()) if len(o) else 0
                blocks = int((o[k].diff() == 1).sum()) if len(o) else 0
                r[k + "_episodes"] = blocks
        rows.append(r)

    est = pd.DataFrame(rows)

    def verdict(r):
        pw = r.get("disr_20_days", 0) or 0
        ep = r.get("disr_20_episodes", 0) or 0
        # Episodes, not days, set the effective sample size: days inside one
        # diversion are ~perfectly autocorrelated. Two episodes is the minimum
        # at which a cut is doing anything other than reproducing one number.
        if ep >= 2 and pw >= 45:
            return "own threshold estimable (weak, episode-clustered)"
        if ep >= 1 or (r.get("onsets", 0) or 0) >= 3:
            return "partial pooling only"
        return "global threshold + corridor baseline rate"

    est["verdict"] = est.apply(verdict, axis=1)
    est = est.sort_values(["manuscript_unit", "corridor_id"], ascending=[False, True])

    pc = os.path.join(REPORT, "estimability.csv")
    est.to_csv(pc, index=False)

    L = []
    A = L.append
    A("ESTIMABILITY AUDIT")
    A("=" * 78)
    A("")
    A("BINARY-ONSET DESIGN (the design in the manuscript)")
    A("  Onsets per unit, Table 3 of the v5 manuscript:")
    for u, n in onset["unit"].value_counts().items():
        A(f"    {u:<18} {n}")
    for cid in cor.loc[cor['manuscript_panel_unit'] == 1, 'corridor_id']:
        if cid not in set(onset["unit"]):
            A(f"    {cid:<18} 0")
    A("")
    A("  A per-corridor ROC needs positives and negatives in that corridor.")
    A("  Three of the seven panel units have zero onsets, so their AUC is")
    A("  undefined, and the largest unit has four. Bootstrap intervals around")
    A("  a Youden cut with four positives will span most of the support.")
    A("  Corridor-specific thresholds are NOT estimable on this design, and")
    A("  no amount of extra corridors fixes it -- adding units adds zeros.")
    A("")
    A("TRANSIT-OUTCOME DESIGN (what the new data buys)")
    A("")
    A(est.to_string(index=False))
    A("")
    A("VERDICT COUNTS")
    A(est["verdict"].value_counts().to_string())
    A("")
    A("HOW TO READ THIS")
    A("  'own threshold estimable' means at least two distinct disruption")
    A("  episodes and at least 45 disruption days. EPISODES, not days, set the")
    A("  effective sample size -- the 300-odd days of the Red Sea diversion are")
    A("  one draw, not 300. Every interval in the validation stage must be a")
    A("  BLOCK bootstrap clustered on episode. Treating disruption days as")
    A("  independent is the single easiest way to manufacture a corridor")
    A("  threshold that does not exist, and it would be invisible in the")
    A("  output: the intervals would just be reassuringly narrow.")
    A("")
    A("  'partial pooling only' means the corridor enters the hierarchical")
    A("  model and borrows strength, but gets no standalone reported cut.")
    A("")
    A("  The honest finding may well be that thresholds do NOT differ. That")
    A("  is a publishable result and it is the one the pre-registered order")
    A("  in your framework points at: test first, report a global threshold")
    A("  with corridor-specific baseline rates if the test does not reject.")
    A("")
    A("  Note the confound to handle in the validation stage: the transit")
    A("  outcome and the TAR series are both measured with instruments that")
    A("  degrade during the same episodes. AIS goes dark under jamming; news")
    A("  coverage surges. Both push toward a spurious association. The")
    A("  Panama drought and Ever Given placebos are the control -- physical")
    A("  disruptions with no escalation, where TAR must NOT fire.")

    p = os.path.join(REPORT, "estimability.txt")
    open(p, "w").write("\n".join(L))
    log(f"  -> {p}")
    log(f"  -> {pc}")
    print()
    print("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())

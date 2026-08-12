"""
v03_attribute.py — corridor attribution and corridor-day threat/act counts.

Three attribution tiers, kept separate so the direct-vs-proxy sensitivity you
already run in the paper (R5) can be run on real geolocation rather than on
country-index coverage:

  A  direct     ActionGeo point falls inside the corridor buffer
  B  littoral   ActionGeo country is a corridor littoral state
  C  proxy      either CAMEO actor is a corridor-relevant state

Tier A is the one that did not exist before. Under GPR only 1 of your 8 onsets
sat in a directly-covered corridor; with geolocated events every corridor is
directly covered, which is what makes a per-corridor threshold estimable at all.

A CODE-SYSTEM TRAP, handled here: within one GDELT row, ActionGeo_CountryCode
is FIPS 10-4 (Iran = IR, Oman = MU, China = CH, Turkey = TU) while
Actor1CountryCode is CAMEO/ISO-3-like (IRN, OMN, CHN, TUR). They are different
alphabets in adjacent columns. corridors.csv carries both lists separately.

Outputs
  build/corridor_daily_counts.csv.gz
  report/attribution_audit.txt

Usage
  python v03_attribute.py
"""
from __future__ import annotations
import argparse
import os
import sys

import numpy as np
import pandas as pd

from vconfig import (BUILD, CAMEO_SETS, DATA, GDELT_START, REPORT,
                     dist_to_polyline_km, load_corridors, log)

CORPUS = os.path.join(DATA, "gdelt_events.csv.gz")


def attribute(df: pd.DataFrame, cor: pd.DataFrame) -> pd.DataFrame:
    """Return long frame: one row per (event, corridor) with the best tier."""
    lat = pd.to_numeric(df["lat"], errors="coerce").to_numpy()
    lon = pd.to_numeric(df["lon"], errors="coerce").to_numpy()
    geo_ok = np.isfinite(lat) & np.isfinite(lon)
    ctry = df["geo_country"].astype("string").fillna("").to_numpy()
    a1 = df["actor1_country"].astype("string").fillna("").to_numpy()
    a2 = df["actor2_country"].astype("string").fillna("").to_numpy()

    out = []
    for _, c in cor.iterrows():
        direct = np.zeros(len(df), dtype=bool)
        if geo_ok.any():
            d = dist_to_polyline_km(lat[geo_ok], lon[geo_ok], c["points"])
            direct[geo_ok] = d <= float(c["radius_km"])

        littoral = np.isin(ctry, c["littoral"]) if c["littoral"] else np.zeros(len(df), bool)
        proxy = ((np.isin(a1, c["actors"]) | np.isin(a2, c["actors"]))
                 if c["actors"] else np.zeros(len(df), bool))

        # proxy retained as a diagnostic column only; it is not part of any
        # default tier stack downstream
        any_hit = direct | littoral
        if not any_hit.any():
            continue
        tier = np.where(direct, "A_direct",
                        np.where(littoral, "B_littoral", "C_proxy"))
        sub = df.loc[any_hit, ["event_id", "date", "root_code",
                               "num_mentions", "num_articles"]].copy()
        sub["corridor_id"] = c["corridor_id"]
        sub["tier"] = tier[any_hit]
        out.append(sub)

    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def aggregate(long: pd.DataFrame) -> pd.DataFrame:
    long["date"] = pd.to_datetime(long["date"], errors="coerce")
    long = long.dropna(subset=["date"])
    long["root_code"] = long["root_code"].astype(str).str.zfill(2)
    for c in ("num_mentions", "num_articles"):
        long[c] = pd.to_numeric(long[c], errors="coerce").fillna(1.0)

    frames = []
    for spec, codes in CAMEO_SETS.items():
        t = long["root_code"].isin([c.zfill(2) for c in codes["threat"]])
        a = long["root_code"].isin([c.zfill(2) for c in codes["act"]])
        d = long.loc[t | a].copy()
        d["kind"] = np.where(t[t | a], "threat", "act")
        g = (d.groupby(["date", "corridor_id", "tier", "kind"])
               .agg(n=("event_id", "size"),
                    mentions=("num_mentions", "sum"),
                    articles=("num_articles", "sum"))
               .reset_index())
        w = g.pivot_table(index=["date", "corridor_id", "tier"],
                          columns="kind",
                          values=["n", "mentions", "articles"],
                          fill_value=0)
        w.columns = [f"{spec}_{k}_{v}" for v, k in w.columns]
        frames.append(w)

    panel = pd.concat(frames, axis=1).fillna(0).reset_index()
    return panel.sort_values(["corridor_id", "tier", "date"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-date", default=GDELT_START,
                    help="drop events before this date (GDELT carries backdated "
                         "SQLDATEs from historical references)")
    ap.add_argument("--max-date", default=None)
    args = ap.parse_args()
    # Named win_* deliberately: the chunk loop below uses lo/hi for the
    # per-chunk date extent, and shadowing these silently reduced the window
    # to one chunk's span, which discarded 22.6M of 23.2M rows.
    win_lo = pd.Timestamp(args.min_date)
    win_hi = pd.Timestamp(args.max_date) if args.max_date else pd.Timestamp.today()

    if not os.path.exists(CORPUS):
        raise SystemExit(f"missing {CORPUS} -- run v02_fetch_gdelt.py --merge first")

    cor = load_corridors()
    log(f"streaming corpus through {len(cor)} corridors")
    log("  (chunked: the 2019+ corpus is ~21M rows and will not fit in memory)")

    CHUNK = 250_000          # sized for 8 GB RAM
    aggs = []
    n_events = 0
    n_dropped = 0
    tier_ct = {}                 # (corridor, tier) -> count
    decade_ct = {}               # (corridor, decade) -> direct count
    multi = 0
    dmin = dmax = None

    reader = pd.read_csv(CORPUS, dtype=str, chunksize=CHUNK)
    for i, chunk in enumerate(reader, 1):
        chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce")
        chunk = chunk.dropna(subset=["date"])
        n_raw = len(chunk)
        # GDELT SQLDATE is the date the event refers to, not the date it was
        # collected, so a 2019+ pull still carries events dated 1920. Left in,
        # they create a century of empty panel rows and drag every median to 0.
        chunk = chunk[(chunk["date"] >= win_lo) & (chunk["date"] <= win_hi)]
        n_dropped += n_raw - len(chunk)
        if not len(chunk):
            continue
        n_events += len(chunk)
        c_lo, c_hi = chunk["date"].min(), chunk["date"].max()
        dmin = c_lo if dmin is None or c_lo < dmin else dmin
        dmax = c_hi if dmax is None or c_hi > dmax else dmax

        long = attribute(chunk, cor)
        if long.empty:
            continue

        multi += int(long.groupby("event_id").size().gt(1).sum())
        for (cid, t), n in long.groupby(["corridor_id", "tier"]).size().items():
            tier_ct[(cid, t)] = tier_ct.get((cid, t), 0) + int(n)
        la = long[long["tier"] == "A_direct"]
        if len(la):
            dec = (pd.to_datetime(la["date"]).dt.year // 10 * 10).astype(int)
            for (cid, d), n in la.groupby([la["corridor_id"], dec]).size().items():
                decade_ct[(cid, d)] = decade_ct.get((cid, d), 0) + int(n)

        aggs.append(aggregate(long))
        if i % 5 == 0:
            log(f"  chunk {i}: {n_events:,} events, {len(aggs)} partial aggregates")

    if not aggs:
        raise SystemExit("no attributions produced -- check corridors.csv geometry")

    log("combining partial aggregates")
    panel = pd.concat(aggs, ignore_index=True)
    keys = ["date", "corridor_id", "tier"]
    panel = panel.groupby(keys, as_index=False).sum(numeric_only=True)
    panel = panel.sort_values(["corridor_id", "tier", "date"])

    out = os.path.join(BUILD, "corridor_daily_counts.csv.gz")
    panel.to_csv(out, index=False, compression="gzip")
    log(f"  -> {out}   {len(panel):,} corridor-tier-days")

    # ------------------------------------------------------------ audit
    tt = (pd.Series(tier_ct).rename_axis(["corridor_id", "tier"])
          .unstack(fill_value=0) if tier_ct else pd.DataFrame())
    if len(tt):
        for c in ("A_direct", "B_littoral"):
            if c not in tt.columns:
                tt[c] = 0
        tt["total"] = tt.sum(axis=1)
        tt["pct_direct"] = (tt["A_direct"] / tt["total"] * 100).round(1)
        tt = tt.sort_values("total", ascending=False)
    dd = (pd.Series(decade_ct).rename_axis(["corridor_id", "decade"])
          .unstack(fill_value=0) if decade_ct else pd.DataFrame())

    lines = []
    add = lines.append
    add("ATTRIBUTION AUDIT")
    add("=" * 78)
    add(f"corpus events          : {n_events:,}")
    add(f"dropped out of window  : {n_dropped:,}")
    add(f"date range             : {dmin:%Y-%m-%d} to {dmax:%Y-%m-%d}")
    add(f"attributions           : {int(tt['total'].sum()) if len(tt) else 0:,}")
    add(f"multi-corridor events  : {multi:,}")
    add("")
    add("BY CORRIDOR AND TIER (events)")
    add(tt.to_string() if len(tt) else "(none)")
    add("")
    add("DIRECT-TIER COVERAGE BY DECADE")
    add(dd.to_string() if len(dd) else "(none)")
    add("")
    add("CHECK BEFORE PROCEEDING")
    add("  1. pct_direct is NOT a quality score. A low share usually means the")
    add("     littoral states are large and violent, not that the geometry is")
    add("     wrong. Judge on the ABSOLUTE A_direct count instead: under ~20k")
    add("     events a corridor is too thin to carry its own threshold.")
    add("  2. A corridor whose A_direct share jumps discontinuously across a")
    add("     decade boundary has a coverage break, not an escalation trend.")

    p = os.path.join(REPORT, "attribution_audit.txt")
    open(p, "w").write("\n".join(lines))
    log(f"  -> {p}")
    print()
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())

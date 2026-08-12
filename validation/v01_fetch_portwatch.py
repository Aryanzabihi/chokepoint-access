"""
v01_fetch_portwatch.py - IMF PortWatch daily chokepoint transit calls.

Gives a CONTINUOUS, corridor-specific outcome at daily frequency for 28
chokepoints since 2019. Your event side has 8 binary onsets over 7 units with
three units at zero; that cannot support a per-corridor threshold. Transit
disruption can.

Free, no key. Rate-limited to 6000 request units per minute, which this script
respects rather than fights: it sleeps between pages, waits out quota errors,
caches the metadata, and saves each corridor's daily series as it arrives. If
it is interrupted, rerun it and it resumes.

Outputs
  data/portwatch_chokepoints.csv
  data/portwatch_id_map.csv
  data/pw_daily/<portid>.csv        per-corridor, written as fetched
  data/portwatch_daily.csv          concatenated

Usage
  python v01_fetch_portwatch.py
  python v01_fetch_portwatch.py --refresh-meta
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from vconfig import DATA, PORTWATCH_START, load_corridors, log

BASE = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
META_URL = f"{BASE}/PortWatch_chokepoints_database/FeatureServer/0/query"
DAILY_URL = f"{BASE}/Daily_Chokepoints_Data/FeatureServer/0/query"

PAGE = 1000
PAGE_SLEEP = 1.5        # stay inside the per-minute quota
QUOTA_WAIT = 65         # ArcGIS quota resets on a 60s window

META_PATH = os.path.join(DATA, "portwatch_chokepoints.csv")
DAILY_DIR = os.path.join(DATA, "pw_daily")
os.makedirs(DAILY_DIR, exist_ok=True)


def _get(url: str, params: dict) -> dict:
    """One request. Waits out quota errors instead of dying on them.

    A 429 can arrive two ways: as an HTTP status, or as a 200 carrying an
    error body. Both are handled here, which is why nothing above this
    function needs to know the quota exists.
    """
    full = f"{url}?{urllib.parse.urlencode(params)}"
    for attempt in range(10):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": "tar-validation/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                js = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log(f"    quota (HTTP 429), waiting {QUOTA_WAIT}s")
                time.sleep(QUOTA_WAIT)
                continue
            time.sleep(3 * (attempt + 1))
            continue
        except Exception:                                     # noqa: BLE001
            time.sleep(3 * (attempt + 1))
            continue

        err = js.get("error") if isinstance(js, dict) else None
        if err:
            if err.get("code") == 429 or "quota" in str(err).lower():
                log(f"    quota, waiting {QUOTA_WAIT}s")
                time.sleep(QUOTA_WAIT)
                continue
            raise RuntimeError(err)
        return js
    raise RuntimeError(f"gave up after 10 attempts: {full}")


def fetch_all(url: str, where: str = "1=1", out_fields: str = "*",
              order: str = "objectid") -> pd.DataFrame:
    rows, offset = [], 0
    while True:
        js = _get(url, {
            "where": where, "outFields": out_fields, "returnGeometry": "false",
            "resultOffset": offset, "resultRecordCount": PAGE,
            "orderByFields": order, "f": "json",
        })
        feats = js.get("features", [])
        rows.extend(f["attributes"] for f in feats)
        if len(feats) < PAGE:
            break
        offset += PAGE
        time.sleep(PAGE_SLEEP)
    return pd.DataFrame(rows)


def get_metadata(refresh: bool) -> pd.DataFrame:
    if os.path.exists(META_PATH) and not refresh:
        m = pd.read_csv(META_PATH)
        log(f"metadata from cache: {len(m)} chokepoints")
        return m
    log("fetching chokepoint metadata")
    m = fetch_all(META_URL)
    m.columns = [c.lower() for c in m.columns]
    m.to_csv(META_PATH, index=False)
    log(f"  {len(m)} chokepoints -> {META_PATH}")
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=PORTWATCH_START)
    ap.add_argument("--refresh-meta", action="store_true")
    args = ap.parse_args()

    meta = get_metadata(args.refresh_meta)
    meta.columns = [c.lower() for c in meta.columns]
    namecol = "portname" if "portname" in meta.columns else meta.columns[1]

    cor = load_corridors()
    lut = {str(r[namecol]).strip().lower(): str(r["portid"]) for _, r in meta.iterrows()}
    matched, unmatched = {}, []
    for _, r in cor.iterrows():
        want = str(r.get("portwatch_name") or "").strip().lower()
        if not want or want == "nan":
            continue
        if want in lut:
            matched[r["corridor_id"]] = lut[want]
        else:
            hit = [k for k in lut if want in k or k in want]
            if len(hit) == 1:
                matched[r["corridor_id"]] = lut[hit[0]]
            else:
                unmatched.append((r["corridor_id"], r["portwatch_name"]))

    log(f"matched {len(matched)} corridors")
    if unmatched:
        log(f"  UNMATCHED (fix portwatch_name in corridors.csv): {unmatched}")
        log(f"  available: {sorted(lut)}")
    pd.Series(matched, name="portid").rename_axis("corridor_id").to_csv(
        os.path.join(DATA, "portwatch_id_map.csv"))

    # ---- daily series, one corridor at a time, resumable ----------------
    inv = {v: k for k, v in matched.items()}
    todo = [p for p in sorted(set(matched.values()))
            if not os.path.exists(os.path.join(DAILY_DIR, f"{p}.csv"))]
    log(f"{len(matched)} corridors mapped, {len(todo)} still to fetch")

    for i, pid in enumerate(todo, 1):
        log(f"  [{i}/{len(todo)}] {inv[pid]} ({pid})")
        d = fetch_all(DAILY_URL, where=f"portid='{pid}'", order="date")
        d.columns = [c.lower() for c in d.columns]
        d.to_csv(os.path.join(DAILY_DIR, f"{pid}.csv"), index=False)
        log(f"      {len(d):,} rows")
        time.sleep(PAGE_SLEEP)

    parts = []
    for pid in sorted(set(matched.values())):
        f = os.path.join(DAILY_DIR, f"{pid}.csv")
        if os.path.exists(f):
            parts.append(pd.read_csv(f))
    if not parts:
        raise SystemExit("no daily data fetched")

    daily = pd.concat(parts, ignore_index=True)
    daily.columns = [c.lower() for c in daily.columns]
    datecol = next((c for c in ("date", "day", "obs_date") if c in daily.columns), None)
    if datecol is None:
        raise SystemExit(f"no date column in {list(daily.columns)}")
    if pd.api.types.is_numeric_dtype(daily[datecol]):
        daily["date"] = pd.to_datetime(daily[datecol], unit="ms", errors="coerce")
    else:
        daily["date"] = pd.to_datetime(daily[datecol], errors="coerce")
    daily = daily.dropna(subset=["date"])
    daily = daily[daily["date"] >= pd.Timestamp(args.since)]

    out = os.path.join(DATA, "portwatch_daily.csv")
    daily.to_csv(out, index=False)
    log(f"{len(daily):,} rows, {daily['portid'].nunique()} chokepoints, "
        f"{daily['date'].min():%Y-%m-%d} to {daily['date'].max():%Y-%m-%d}")
    log(f"  -> {out}")

    log("")
    log("DATA CAVEATS to carry into the validation stage:")
    log("  * AIS receiver coverage expanded in 2021 -> sustained level shift at")
    log("    Malacca and elsewhere. Use a within-corridor trailing baseline.")
    log("  * Blackout days: 2022-05-12, 2023-02-14, 2024-01-09.")
    log("  * GPS jamming, AIS spoofing and dark vessels in the Red Sea, Hormuz,")
    log("    Iran, Russia and Ukraine bias transit counts DOWN during exactly")
    log("    the episodes of interest, which inflates measured disruption.")


if __name__ == "__main__":
    sys.exit(main())

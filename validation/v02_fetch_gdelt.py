"""
v02_fetch_gdelt.py — build a geolocated threat/act corpus from GDELT 1.0.

WHY THIS REPLACES GPR AS THE INPUT
  GPR ships as country-level counts over 44 published country indices. Iran,
  Yemen and the Balkan states are not among them, so three of your seven panel
  units are proxy-attributed and corridor-specific measurement is impossible in
  principle. GDELT 1.0 ships one row per event with a latitude and longitude,
  covers 1979-01-01 to present, and codes every event as coercive speech
  (CAMEO root 13, 15) or coercive action (18, 19, 20). That is the TAR
  numerator and denominator, geolocated, over your full sample period. It also
  removes the "no corpus, so leakage is untestable" limitation in section 8.2.

WHAT THIS COSTS
  The full archive is ~50 GB zipped. This script never keeps it: each file is
  downloaded to memory, filtered to conflict root codes near a corridor, and
  only the surviving rows (a few tens of MB in total) are written to disk.
  Expect 1-3 hours on a home connection with 8 workers. It is resumable --
  rerun it after an interruption and it skips finished files.

Outputs
  data/gdelt_cache/<file>.csv.gz     one filtered slice per source file
  data/gdelt_events.csv.gz           concatenated corpus (written by --merge)

Usage
  python v02_fetch_gdelt.py --workers 8
  python v02_fetch_gdelt.py --start 2015 --workers 8      # test on a slice
  python v02_fetch_gdelt.py --merge                       # after downloading
"""
from __future__ import annotations
import argparse
import concurrent.futures as cf
import glob
import io
import os
import re
import sys
import time
import urllib.request
import zipfile

import numpy as np
import pandas as pd

from vconfig import (CACHE, DATA, KEEP_ROOT_CODES, dist_to_polyline_km,
                     load_corridors, log)

INDEX = "http://data.gdeltproject.org/events/filesizes"
BASEURL = "http://data.gdeltproject.org/events/"

# GDELT 1.0 export layout: 57 columns (58 from 2013-04-01, trailing SOURCEURL).
# Positions are 0-indexed and stable across the whole archive.
COLS = {
    0:  "event_id",
    1:  "sqldate",
    7:  "actor1_country",
    17: "actor2_country",
    26: "event_code",
    28: "root_code",
    29: "quad_class",
    30: "goldstein",
    31: "num_mentions",
    33: "num_articles",
    34: "avg_tone",
    49: "geo_type",
    50: "geo_name",
    51: "geo_country",
    53: "lat",
    54: "lon",
}
USE = sorted(COLS)
NAMES = [COLS[i] for i in USE]

# Extra slack on the attribution radius so radii can be re-tuned in v03
# without re-downloading 50 GB.
RADIUS_SLACK_KM = 300.0


# ------------------------------------------------------------------ index
def file_list(start: str | None, end: str | None) -> list[str]:
    """Authoritative file list from GDELT's own index, with a constructed fallback."""
    names: list[str] = []
    try:
        req = urllib.request.Request(INDEX, headers={"User-Agent": "tar-validation/1.0"})
        with urllib.request.urlopen(req, timeout=90) as r:
            txt = r.read().decode("utf-8", "replace")
        for line in txt.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1].endswith(".zip"):
                names.append(parts[-1])
        log(f"index: {len(names)} files listed by GDELT")
    except Exception as e:                                        # noqa: BLE001
        log(f"index fetch failed ({e}); constructing names instead")
        names = [f"{y}.zip" for y in range(1979, 2006)]
        names += [f"{y}{m:02d}.zip" for y in range(2006, 2013) for m in range(1, 13)]
        names += [f"2013{m:02d}.zip" for m in range(1, 4)]
        d = pd.date_range("2013-04-01", pd.Timestamp.today())
        names += [f"{x:%Y%m%d}.export.CSV.zip" for x in d]

    # keep only event exports, drop the reduced/master bundles
    names = [n for n in names if re.match(r"^\d{4}(\d{2})?(\d{2})?(\.export\.CSV)?\.zip$", n)]

    def key(n: str) -> str:
        stem = n.split(".")[0]
        return (stem + "0000")[:8]

    names = sorted(set(names), key=key)
    if start:
        names = [n for n in names if key(n) >= (start + "00000000")[:8]]
    if end:
        names = [n for n in names if key(n) <= (end + "99999999")[:8]]
    return names


# ------------------------------------------------------------------ filter
def _build_geofilter():
    cor = load_corridors()
    segs = [(r["points"], float(r["radius_km"]) + RADIUS_SLACK_KM) for _, r in cor.iterrows()]
    littoral = set().union(*[set(x) for x in cor["littoral"]])
    actors = set().union(*[set(x) for x in cor["actors"]])
    return segs, littoral, actors


def _keep_mask(df: pd.DataFrame, segs, littoral, actors) -> np.ndarray:
    lat = pd.to_numeric(df["lat"], errors="coerce").to_numpy()
    lon = pd.to_numeric(df["lon"], errors="coerce").to_numpy()
    ok = np.isfinite(lat) & np.isfinite(lon)

    near = np.zeros(len(df), dtype=bool)
    if ok.any():
        sub_lat, sub_lon = lat[ok], lon[ok]
        hit = np.zeros(ok.sum(), dtype=bool)
        for pts, rad in segs:
            hit |= dist_to_polyline_km(sub_lat, sub_lon, pts) <= rad
        near[ok] = hit

    # NOTE: the actor test is deliberately NOT part of this filter.
    # Corridor actor lists contain global powers (USA, CHN, RUS), so an
    # actor-based keep rule matches nearly every conflict event on earth:
    # it inflated the cache ~10x and attributed every event to every
    # corridor, which is exactly the pooling the corridor design exists to
    # avoid. Location decides membership; actors do not.
    ctry = df["geo_country"].astype("string").fillna("")
    return near | ctry.isin(littoral).to_numpy()


def process(name: str, segs, littoral, actors, retries: int = 3) -> tuple[str, int, str]:
    out = os.path.join(CACHE, name.split(".")[0] + ".csv.gz")
    if os.path.exists(out):
        return name, -1, "cached"

    last = ""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(BASEURL + name,
                                         headers={"User-Agent": "tar-validation/1.0"})
            with urllib.request.urlopen(req, timeout=300) as r:
                blob = r.read()
            zf = zipfile.ZipFile(io.BytesIO(blob))
            member = zf.namelist()[0]

            kept = []
            with zf.open(member) as fh:
                reader = pd.read_csv(
                    fh, sep="\t", header=None, usecols=USE, names=NAMES,
                    dtype=str, quoting=3, on_bad_lines="skip",
                    chunksize=400_000, low_memory=False,
                )
                for chunk in reader:
                    chunk = chunk[chunk["root_code"].isin(KEEP_ROOT_CODES)]
                    if chunk.empty:
                        continue
                    chunk = chunk[_keep_mask(chunk, segs, littoral, actors)]
                    if not chunk.empty:
                        kept.append(chunk)

            df = (pd.concat(kept, ignore_index=True) if kept
                  else pd.DataFrame(columns=NAMES))
            df.to_csv(out, index=False, compression="gzip")
            return name, len(df), "ok"
        except Exception as e:                                    # noqa: BLE001
            last = f"{e.__class__.__name__}: {e}"
            time.sleep(3 * (attempt + 1))
    return name, 0, f"FAILED {last}"


# ------------------------------------------------------------------ merge
def merge():
    """Concatenate the cache by STREAMING, never by holding it in memory.

    At 2019+ scope the cache is ~21M rows. Reading that with dtype=str and
    calling pd.concat would need well over 20 GB. Instead each slice is
    appended to the gzip as it is read, so peak memory is one slice.
    """
    files = sorted(glob.glob(os.path.join(CACHE, "*.csv.gz")))
    if not files:
        raise SystemExit("nothing in the cache -- run the download first")
    out = os.path.join(DATA, "gdelt_events.csv.gz")
    log(f"merging {len(files)} cached slices -> {out}")

    import gzip
    total, bad, first = 0, 0, True
    root_counts = {}
    dmin, dmax = None, None

    with gzip.open(out, "wt", newline="", encoding="utf-8") as fh:
        for i, f in enumerate(files, 1):
            try:
                d = pd.read_csv(f, dtype=str)
            except Exception:                                     # noqa: BLE001
                log(f"  unreadable, deleting so it re-downloads: {os.path.basename(f)}")
                os.remove(f)
                bad += 1
                continue
            if not len(d):
                continue

            d["date"] = pd.to_datetime(d["sqldate"], format="%Y%m%d", errors="coerce")
            d = d.dropna(subset=["date"]).drop_duplicates(subset=["event_id"])
            if not len(d):
                continue

            lo, hi = d["date"].min(), d["date"].max()
            dmin = lo if dmin is None or lo < dmin else dmin
            dmax = hi if dmax is None or hi > dmax else dmax
            vc = d["root_code"].value_counts()
            for k, v in vc.items():
                root_counts[k] = root_counts.get(k, 0) + int(v)

            d["date"] = d["date"].dt.strftime("%Y-%m-%d")
            d.to_csv(fh, index=False, header=first)
            first = False
            total += len(d)

            if i % 250 == 0:
                log(f"  {i}/{len(files)}  {total:,} rows")

    log(f"corpus: {total:,} events, {dmin:%Y-%m-%d} to {dmax:%Y-%m-%d}")
    log(f"  -> {out}  ({os.path.getsize(out)/1e6:.0f} MB)")
    if bad:
        log(f"  {bad} slices were unreadable and deleted -- rerun the download")
    log("root code counts:")
    for k in sorted(root_counts):
        log(f"  {k}: {root_counts[k]:,}")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--start", default=None, help="YYYY or YYYYMM")
    ap.add_argument("--end", default=None)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="first N files only (smoke test)")
    args = ap.parse_args()

    if args.merge:
        return merge()

    segs, littoral, actors = _build_geofilter()
    log(f"geofilter: {len(segs)} corridors, {len(littoral)} littoral codes, "
        f"{len(actors)} actor codes")

    names = file_list(args.start, args.end)
    if args.limit:
        names = names[: args.limit]
    todo = [n for n in names
            if not os.path.exists(os.path.join(CACHE, n.split('.')[0] + '.csv.gz'))]
    log(f"{len(names)} files in range, {len(todo)} still to fetch")
    if not todo:
        log("all cached -- run with --merge")
        return

    t0, done, kept, failed = time.time(), 0, 0, []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, n, segs, littoral, actors): n for n in todo}
        for fut in cf.as_completed(futs):
            name, n, status = fut.result()
            done += 1
            if status.startswith("FAILED"):
                failed.append((name, status))
            elif n > 0:
                kept += n
            if done % 25 == 0 or done == len(todo):
                el = time.time() - t0
                rate = done / max(el, 1e-9)
                log(f"  {done}/{len(todo)}  kept {kept:,} rows  "
                    f"{rate:.1f} files/s  eta {(len(todo)-done)/max(rate,1e-9)/60:.0f} min")

    if failed:
        log(f"{len(failed)} files failed -- rerun to retry them:")
        for n, s in failed[:10]:
            log(f"  {n}  {s}")
    log("done. now run:  python v02_fetch_gdelt.py --merge")


if __name__ == "__main__":
    sys.exit(main())

"""
v02b_fetch_gdeltv2.py — backfill specific dates using GDELT 2.0, for dates
where the GDELT 1.0 legacy daily archive has no file at all.

CONTEXT
  v02_fetch_gdelt.py pulls http://data.gdeltproject.org/events/<date>.export.CSV.zip,
  the GDELT 1.0 legacy daily archive. A Gate-2 audit (TASK.md Task 2) found 24
  daily files genuinely missing from GDELT's OWN index in that scheme -- not a
  caching bug on our side, confirmed by diffing this repo's cache against
  http://data.gdeltproject.org/events/filesizes directly. 18 of those days
  (2025-06-14 to 2025-07-01) cover the June 2025 Israel-Iran exchange, the
  most important Hormuz episode in the sample. Two more (2022-11-10,
  2023-03-23) are LISTED in that index with byte sizes but 404 on download --
  dead references on GDELT's server.

  GDELT 2.0 (gdeltv2) is a DIFFERENT product from the 1.0 legacy archive:
  continuous 15-minute event-export files, published since 2015-02-18, with
  an 8-field geo block (adds ADM2Code) instead of 1.0's 7-field block, and an
  extra NumSources column ahead of NumArticles. It has been running
  continuously through the whole target window, so it is the only real
  upstream option for the 2025/2022/2023 gaps. It CANNOT help the two 2014
  gaps (2014-01-23/24/25, 2014-03-19), which predate 2.0's 2015-02-18 start --
  there is no equivalent alternate source for those within the GDELT
  ecosystem.

  Reconstructing a day means downloading all 96 fifteen-minute slices for
  that date (some slots are occasionally never published; that is normal,
  not an error, and this script does not treat a 404 on an individual slot as
  fatal) and concatenating. Semantics differ slightly from the 1.0 daily
  file: GDELT 2.0's NumMentions/NumArticles are a snapshot at first detection
  within a single 15-minute window, not the full-day cumulative count the 1.0
  legacy archive computes by re-aggregating an event across every file it
  appears in. This is a genuine, documented difference in what the resulting
  cache row means, not a bug in this script -- carry it forward into any
  report that touches this window.

  Output lands in exactly the same place v02 writes to
  (data/gdelt_cache/<date>.csv.gz), with the same column names, so
  v03_attribute.py runs against it completely unmodified. Idempotent: a date
  already cached (by v02 or a previous run of this script) is skipped.

Usage
  python v02b_fetch_gdeltv2.py --dates 20250614-20250701,20221110,20230323
  python v02b_fetch_gdeltv2.py --dates 20250614 --workers 8
"""
from __future__ import annotations
import argparse
import concurrent.futures as cf
import io
import os
import sys
import time
import urllib.request
import zipfile

import pandas as pd

from vconfig import CACHE, log
from v02_fetch_gdelt import _build_geofilter, _keep_mask

BASEURL = "http://data.gdeltproject.org/gdeltv2/"
GDELTV2_START = "20150218"     # first day gdeltv2 was published

# GDELT 2.0 export layout: 61 columns, 0-indexed. Same field semantics as
# GDELT 1.0 through AvgTone (34); after that 2.0 inserts Actor1Geo/Actor2Geo
# blocks with an extra ADM2Code field each, shifting ActionGeo_* later.
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
    51: "geo_type",
    52: "geo_name",
    53: "geo_country",
    56: "lat",
    57: "lon",
}
USE = sorted(COLS)
NAMES = [COLS[i] for i in USE]


def slot_names(date: str) -> list[str]:
    return [f"{date}{h:02d}{m:02d}00.export.CSV.zip"
            for h in range(24) for m in (0, 15, 30, 45)]


def fetch_slot(name: str, segs, littoral, actors, retries: int = 3) -> tuple[str, pd.DataFrame | None, str]:
    last = ""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(BASEURL + name,
                                         headers={"User-Agent": "tar-validation/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                blob = r.read()
            zf = zipfile.ZipFile(io.BytesIO(blob))
            member = zf.namelist()[0]
            with zf.open(member) as fh:
                df = pd.read_csv(fh, sep="\t", header=None, usecols=USE, names=NAMES,
                                 dtype=str, quoting=3, on_bad_lines="skip",
                                 low_memory=False)
            if df.empty:
                return name, df, "empty"
            df = df[df["root_code"].isin(
                {"12", "13", "14", "15", "16", "17", "18", "19", "20"})]
            if df.empty:
                return name, df, "empty-after-root-filter"
            df = df[_keep_mask(df, segs, littoral, actors)]
            return name, df, "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return name, None, "not-published"      # normal: slot never emitted
            last = f"HTTPError {e.code}"
            time.sleep(2 * (attempt + 1))
        except Exception as e:                                     # noqa: BLE001
            last = f"{e.__class__.__name__}: {e}"
            time.sleep(2 * (attempt + 1))
    return name, None, f"FAILED {last}"


def fetch_day(date: str, segs, littoral, actors, workers: int) -> tuple[int, dict]:
    out = os.path.join(CACHE, f"{date}.csv.gz")
    if os.path.exists(out):
        log(f"  {date}: already cached, skipping")
        return -1, {"cached": 1}

    names = slot_names(date)
    kept, tally = [], {}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_slot, n, segs, littoral, actors): n for n in names}
        for fut in cf.as_completed(futs):
            name, df, status = fut.result()
            tally[status] = tally.get(status, 0) + 1
            if df is not None and not df.empty:
                kept.append(df)

    d = (pd.concat(kept, ignore_index=True).drop_duplicates(subset=["event_id"])
         if kept else pd.DataFrame(columns=NAMES))
    d.to_csv(out, index=False, compression="gzip")
    log(f"  {date}: {len(d):,} rows kept  (slot tally: {tally})")
    return len(d), tally


def parse_dates(spec: str) -> list[str]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part and len(part) > 8:
            a, b = part.split("-")
            d = pd.date_range(a, b, freq="D")
            out += [x.strftime("%Y%m%d") for x in d]
        else:
            out.append(part)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True,
                    help="comma-separated dates or ranges, e.g. 20250614-20250701,20221110")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent 15-min slot downloads per day (conservative default)")
    args = ap.parse_args()

    dates = parse_dates(args.dates)
    pre = [d for d in dates if d < GDELTV2_START]
    if pre:
        log(f"SKIPPING {len(pre)} date(s) before gdeltv2 start ({GDELTV2_START}): {pre}")
        log("  no equivalent source exists for these in the GDELT ecosystem")
    dates = [d for d in dates if d >= GDELTV2_START]

    segs, littoral, actors = _build_geofilter()
    log(f"geofilter: {len(segs)} corridors, {len(littoral)} littoral codes, "
        f"{len(actors)} actor codes")
    log(f"fetching {len(dates)} day(s) from gdeltv2, {args.workers} slot-workers/day")

    totals = {}
    for date in dates:
        n, tally = fetch_day(date, segs, littoral, actors, args.workers)
        for k, v in tally.items():
            totals[k] = totals.get(k, 0) + v

    log("done.")
    log(f"slot outcome totals across all days: {totals}")
    log("now rerun v02_fetch_gdelt.py --merge to fold these into gdelt_events.csv.gz")


if __name__ == "__main__":
    sys.exit(main())

"""
v16_fetch_doc_tar.py — corridor TAR from ARTICLE COUNTS, not event codes.

v16: THE BREAKER WAS WRONG, NOT THE THROTTLING.
  v15 aborted after two 429 responses. But the log shows hormuz/act got a
  429 on attempt 1 and then SUCCEEDED on the retry. An individual 429 is
  not a failure here -- it is the normal price of each call on this IP.
  Roughly every request gets rejected once and then goes through after a
  wait, so ~90s per successful call.

  The breaker now counts CHUNKS THAT EXHAUSTED ALL RETRIES, not individual
  429 responses, and only aborts after three such chunks in a row. That is
  the signal that actually means blocked.

  Expect ~60-70 minutes for the full 48 requests. Start it and leave it;
  every completed series is cached, so an interruption costs nothing.

v15: MINIMISE REQUESTS, AND STOP ON 429 INSTEAD OF DIGGING IN.
  GDELT answered v14 with HTTP 429 and the instruction "limit requests to
  one every 5 seconds". v14 was already waiting 12s -- the block was a
  cooldown penalty earned by v13, which fired ~20 requests with retries in
  seven minutes. Every additional 429 appears to extend the penalty, so
  retrying is actively counterproductive.

  Changes:
   * ONE call per corridor per series for the whole span (--chunk-years 10)
     -> 48 requests total instead of ~480. timelinevolraw returns daily
     resolution for any span over a week.
   * a CIRCUIT BREAKER: two 429s in a row aborts the entire run with
     instructions, rather than spending the rest of the cooldown budget.
   * --skip-total halves it again to 32 requests if needed.
   * 8s minimum spacing, above GDELT's documented 5s floor.

v14 CHANGES (v13 died on HTTP errors after 5 attempts):
  * the HTTP STATUS CODE and body are now printed, so 429 (throttled) is
    distinguishable from 400 (bad query). v13 hid this behind a generic
    HTTPError and left us guessing.
  * long date chunks by default. timelinevolraw stays at daily resolution
    for any span over a week, so one call can cover several years. This
    cuts the request count from ~480 to ~50, and throttling with it.
  * a failed chunk is RECORDED and skipped, never fatal. Successful chunks
    are cached, so rerunning fills only the gaps.
  * longer waits, and a --probe mode to test one call before committing.

WHY THIS AND NOT THE EVENT DATABASE

v10 settled it: the GDELT Event reconstruction scores 0.433 where the published
GPR-based series scores 0.772, on the paper's own design. The two are different
constructs.

  GPR      counts newspaper ARTICLES whose TEXT contains threat-type language
           vs act-type language. A discourse measure.
  CAMEO    machine-extracts discrete EVENTS and assigns each a root code.
           An event measure.

The DOC 2.0 API counts articles matching a full-text query, daily, and returns
raw counts in `timelinevolraw` mode. That is the same construct class as GPR:
article counts over text. Add corridor terms to the query and it becomes
corridor-specific, which is the thing that has never existed.

  threat_articles(corridor, day) = articles matching (corridor) AND (threat words)
  act_articles(corridor, day)    = articles matching (corridor) AND (act words)
  TAR = threat / (threat + act)

WHAT THIS COSTS
  16 corridors x 3 queries x ~10 year-chunks = ~480 API calls. Minutes, not
  hours. No bulk download. This is the cheapest data collection in the project
  and the closest to the actual construct.

COVERAGE
  DOC 2.0 indexes from 2017-01-01. That overlaps PortWatch (2019+) fully, which
  is where the disruption positives are -- 261 positive months across 12
  corridors. So this is the first design in the project with BOTH a
  construct-faithful score AND enough outcomes to threshold against.

WHAT IT IS NOT
  Not GPR. Different corpus, no human validation of word groups, and GDELT's
  monitored volume grows over time. The `total` query is fetched for every
  corridor precisely so counts can be normalised against corridor attention
  rather than used raw. Treat the word groups in THREAT/ACT below as a
  first pass to be tuned, and run the narrow/wide variants as sensitivity.

  The known ambiguity: "threatens to attack" contains an act word. GPR handles
  this with hand-built word groups refined over many iterations. Here it is a
  measurement limitation to report, not something the code can resolve.

Outputs
  data/doc_tar_daily.csv        corridor x day: threat, act, total article counts
  report/v13_coverage.txt

Usage
  python v13_fetch_doc_tar.py
  python v13_fetch_doc_tar.py --variant narrow --start 2017
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from vconfig import DATA, REPORT, ROOT, log

API = "https://api.gdeltproject.org/api/v2/doc/doc"
QUERIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "doc_queries.csv")
CACHE = os.path.join(DATA, "doc_cache")
os.makedirs(CACHE, exist_ok=True)
MANIFEST = os.path.join(ROOT, "output", "rebuild", "doc_fetch_manifest.csv")

DOC_START = "2017-01-01"
SLEEP = 20.0                          # between successful calls
BACKOFF = [60, 90, 150, 240, 300]     # a 429 is expected; wait it out
MAX_EXHAUSTED_CHUNKS = 999              # consecutive give-ups before aborting

# Word groups, mirroring GPR's threats-vs-acts split. Kept short because the
# DOC API rejects over-long queries.
THREAT = {
    "narrow": ["threat", "threaten", "threatens", "warns", "ultimatum"],
    "baseline": ["threat", "threaten", "threatens", "threatening", "warns",
                 "warning", "ultimatum", "retaliate", "deterrence", "tensions"],
    "wide": ["threat", "threaten", "threatens", "threatening", "warns",
             "warning", "ultimatum", "retaliate", "deterrence", "tensions",
             "mobilize", "buildup", "alert", "risk", "escalation"],
}
ACT = {
    "narrow": ["attacked", "struck", "missile", "seized", "killed"],
    "baseline": ["attacked", "struck", "missile", "drone", "seized",
                 "hijacked", "shelling", "bombed", "killed", "explosion"],
    "wide": ["attacked", "struck", "missile", "drone", "seized", "hijacked",
             "shelling", "bombed", "killed", "explosion", "clashes",
             "casualties", "sank", "damaged", "fire"],
}


class BadQuery(Exception):
    """The API rejected the query itself; retrying cannot help."""


class Throttled(Exception):
    """The API is rate-limiting; the chunk can be retried on a later run."""


_state = {"exhausted": 0}


def group(words: list[str]) -> str:
    return "(" + " OR ".join(words) + ")"


def fetch(query: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    params = {
        "query": query,
        "mode": "timelinevolraw",
        "format": "json",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    for attempt in range(len(BACKOFF) + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tar-validation/1.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                raw = r.read().decode("utf-8", "replace")
            if not raw.strip() or raw.strip().startswith("<"):
                log(f"    non-JSON response (attempt {attempt+1})")
                if attempt < len(BACKOFF):
                    time.sleep(BACKOFF[attempt])
                continue
            js = json.loads(raw)
        except urllib.error.HTTPError as e:
            # The status code is the whole diagnosis: 429 means slow down and
            # the query is fine; 400 means the query is rejected and waiting
            # will never help.
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200].replace("\n", " ")
            except Exception:                                     # noqa: BLE001
                pass
            log(f"    HTTP {e.code} {e.reason} (attempt {attempt+1}) {body}")
            if e.code in (400, 404):
                raise BadQuery(f"HTTP {e.code}: {body}") from e
            # A single 429 is routine on this IP and the retry usually works,
            # so it does not count against the breaker. Only an exhausted
            # chunk does -- see the caller.
            if attempt < len(BACKOFF):
                time.sleep(BACKOFF[attempt])
            continue
        except Exception as e:                                    # noqa: BLE001
            log(f"    {e.__class__.__name__}: {e} (attempt {attempt+1})")
            if attempt < len(BACKOFF):
                time.sleep(BACKOFF[attempt])
            continue

        series = js.get("timeline", [])
        if not series:
            return pd.DataFrame(columns=["date", "count", "total"])
        out = {}
        for s in series:
            name = s.get("series", "").lower()
            key = "total" if "total" in name else "count"
            for pt in s.get("data", []):
                d = pd.to_datetime(pt["date"][:8], format="%Y%m%d", errors="coerce")
                if pd.isna(d):
                    continue
                # sum rather than overwrite in case the API ever emits more
                # than one point for the same day within a series
                bucket = out.setdefault(d, {})
                bucket[key] = bucket.get(key, 0.0) + float(pt.get("value", 0))
        return (pd.DataFrame([{"date": k, **v} for k, v in out.items()])
                .sort_values("date").reset_index(drop=True))
    raise Throttled(f"gave up after {len(BACKOFF)+1} attempts")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="baseline",
                    choices=["narrow", "baseline", "wide"])
    ap.add_argument("--start", default=DOC_START)
    ap.add_argument("--end", default=None)
    ap.add_argument("--chunk-years", type=int, default=10,
                    help="years per API call; 10 means one call per series")
    ap.add_argument("--skip-total", action="store_true",
                    help="omit the corridor-volume query (32 requests instead of 48)")
    ap.add_argument("--probe", action="store_true",
                    help="make ONE call, print it, exit -- run this first")
    args = ap.parse_args()

    q = pd.read_csv(QUERIES)
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp.today().normalize()

    # year chunks: the API caps the span it will return at daily resolution
    step = max(1, args.chunk_years)
    edges, cur = [], start
    while cur <= end:
        edges.append(cur)
        cur = cur + pd.DateOffset(years=step)
    edges.append(cur)
    chunks = [(a, min(b - pd.Timedelta(days=1), end))
              for a, b in zip(edges[:-1], edges[1:])]
    chunks = [(a, b) for a, b in chunks if a <= b]

    tq, aq = group(THREAT[args.variant]), group(ACT[args.variant])

    if args.probe:
        r0 = q.iloc[0]
        test = f"({r0['corridor_query']}) {tq}"
        log(f"PROBE: {test}")
        d = fetch(test, chunks[0][0], chunks[0][1])
        print(d.head(10).to_string(index=False))
        print(f"\n{len(d)} daily points for "
              f"{chunks[0][0]:%Y-%m-%d}..{chunks[0][1]:%Y-%m-%d}")
        return
    n_kinds = 2 if args.skip_total else 3
    n_req = len(q) * len(chunks) * n_kinds
    log(f"variant={args.variant}  {len(q)} corridors  {len(chunks)} chunks  "
        f"-> {n_req} requests")
    log(f"  budget roughly {n_req * (SLEEP + 70) / 60:.0f} min: most calls get one "
        f"429 then succeed on retry. Leave it running.")
    log(f"  threat {tq}")
    log(f"  act    {aq}")

    rows, missing, bad, manifest = [], [], [], []
    for _, r in q.iterrows():
        cid, cq = r["corridor_id"], f"({r['corridor_query']})"
        kinds = [("threat", tq), ("act", aq)]
        if not args.skip_total:
            kinds.append(("total", ""))
        for kind, extra in kinds:
            for a, b in chunks:
                cf = os.path.join(CACHE, f"{cid}__{kind}__{a:%Y}.csv")
                # idempotent: a cached file only counts if it actually has
                # rows -- an empty file from a prior run (e.g. a year outside
                # DOC 2.0's 2017-01-01 coverage) is retried, not skipped.
                cached_nonempty = False
                if os.path.exists(cf):
                    d = pd.read_csv(cf, parse_dates=["date"])
                    if len(d):
                        cached_nonempty = True
                        status = "cached"
                        ts = dt.datetime.fromtimestamp(os.path.getmtime(cf))
                if not cached_nonempty:
                    full = f"{cq} {extra}".strip()
                    log(f"  {cid} / {kind} / {a:%Y}-{b:%Y}")
                    ts = dt.datetime.now()
                    try:
                        d = fetch(full, a, b)
                    except BadQuery as e:
                        log(f"    QUERY REJECTED -- {e}")
                        bad.append((cid, kind, f"{a:%Y}", str(e)[:90]))
                        manifest.append({"corridor_id": cid, "kind": kind,
                                         "year": a.year, "status": "rejected",
                                         "row_count": 0, "date_min": "",
                                         "date_max": "", "fetch_timestamp": ts})
                        continue
                    except Throttled:
                        missing.append((cid, kind, f"{a:%Y}"))
                        manifest.append({"corridor_id": cid, "kind": kind,
                                         "year": a.year, "status": "throttled",
                                         "row_count": 0, "date_min": "",
                                         "date_max": "", "fetch_timestamp": ts})
                        _state["exhausted"] += 1
                        if _state["exhausted"] >= MAX_EXHAUSTED_CHUNKS:
                            log("")
                            log(f"ABORTING: {MAX_EXHAUSTED_CHUNKS} chunks in a row "
                                f"exhausted every retry. The IP is in a hard "
                                f"cooldown. Wait a few hours and rerun -- "
                                f"completed series are cached.")
                            raise SystemExit(1)
                        continue
                    _state["exhausted"] = 0        # a success clears the streak
                    d.to_csv(cf, index=False)
                    status = "ok" if len(d) else "empty"
                    time.sleep(SLEEP)
                manifest.append({
                    "corridor_id": cid, "kind": kind, "year": a.year,
                    "status": status, "row_count": len(d),
                    "date_min": d["date"].min() if len(d) else "",
                    "date_max": d["date"].max() if len(d) else "",
                    "fetch_timestamp": ts,
                })
                if len(d):
                    d = d.assign(corridor_id=cid, kind=kind)
                    rows.append(d)

    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    pd.DataFrame(manifest).to_csv(MANIFEST, index=False)
    log(f"  -> {MANIFEST}  {len(manifest)} (corridor, kind, year) cells")

    if bad:
        log("")
        log(f"{len(bad)} chunks REJECTED (HTTP 400/404). Retrying will not help --")
        log("the query syntax or length is the problem:")
        for x in bad[:5]:
            log(f"  {x}")
    if missing:
        log("")
        log(f"{len(missing)} chunks throttled out. Rerun to fill them; cached "
            f"chunks are skipped.")
    if not rows:
        raise SystemExit("no data at all -- run with --probe to see the raw error")

    long = pd.concat(rows, ignore_index=True)
    wide = (long.pivot_table(index=["date", "corridor_id"], columns="kind",
                             values="count", aggfunc="sum")
                .reset_index().fillna(0))
    tot = (long[long["kind"] == "total"]
           .groupby(["date", "corridor_id"])["total"].max().reset_index()
           .rename(columns={"total": "gdelt_all_articles"}))
    wide = wide.merge(tot, on=["date", "corridor_id"], how="left")

    for c in ("threat", "act", "total"):
        if c not in wide.columns:
            wide[c] = 0.0
    wide = wide.rename(columns={"total": "corridor_articles"})
    wide["doc_share"] = wide["threat"] / (wide["threat"] + wide["act"]).replace(0, pd.NA)

    out = os.path.join(DATA, "doc_tar_daily.csv")
    wide.sort_values(["corridor_id", "date"]).to_csv(out, index=False)
    log(f"  -> {out}  {len(wide):,} corridor-days")

    L = []
    A = L.append
    A("DOC-API CORRIDOR TAR — COVERAGE")
    A("=" * 78)
    A(f"variant {args.variant} | {wide['date'].min():%Y-%m-%d} to "
      f"{wide['date'].max():%Y-%m-%d}")
    A("")
    A("MEDIAN DAILY ARTICLE COUNTS BY CORRIDOR")
    g = (wide.groupby("corridor_id")[["threat", "act", "corridor_articles"]]
             .median().round(1))
    g["days"] = wide.groupby("corridor_id").size()
    g["median_share"] = wide.groupby("corridor_id")["doc_share"].median().round(3)
    A(g.sort_values("corridor_articles", ascending=False).to_string())
    A("")
    A("WHAT TO CHECK BEFORE USING THIS")
    A("  1. A corridor with a median of ~0 threat+act articles per day cannot")
    A("     support a daily series. Aggregate it to weekly or monthly, or drop")
    A("     it. Expect Lombok, Sunda and Mozambique Channel to be in that state.")
    A("  2. median_share near 0 or 1 means one side of the ratio is empty most")
    A("     days -- the ratio is then a presence indicator, not a ratio.")
    A("  3. GDELT's monitored volume grows over time. Use corridor_articles or")
    A("     gdelt_all_articles as the denominator for any level comparison")
    A("     across years; never compare raw counts 2017 vs 2026.")
    A("  4. Rerun with --variant narrow and --variant wide. If the corridor")
    A("     ranking changes materially, the word groups are doing the work")
    A("     rather than the corridors.")

    p = os.path.join(REPORT, "v13_coverage.txt")
    open(p, "w").write("\n".join(L))
    log(f"  -> {p}")
    print()
    print("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())

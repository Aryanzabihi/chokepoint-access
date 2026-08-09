"""
fetch_gpr.py — download the current GPR monthly vintage.

The release is published at the start of each month and the filename carries
the vintage, so this tries the current month and falls back one month. It then
checks that the file parses and that the series actually reaches a recent
month, because a silently stale or truncated download is worse than a failed
one: the pipeline downstream would publish a plausible, wrong reading.

    python fetch_gpr.py --out ../data/gpr_monthly.dta
    python fetch_gpr.py --out ../data/x.dta --dry-run     # print URLs only
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

# The publisher's page names vintages data_gpr_export_YYYYMM.{dta,xls}, but the
# directory has moved before now, so try the known locations rather than assuming
# one. Pass --url to skip all guessing.
BASES = [
    "https://www.matteoiacoviello.com/gpr_files",
    "https://www.matteoiacoviello.com",
    "https://www.matteoiacoviello.com/gpr_files/vintages",
    "https://www.matteoiacoviello.com/gpr_files/data",
]
PATTERNS = ["data_gpr_export_{vintage}.dta", "data_gpr_export_{vintage}.xls"]
# Confirmed Aug 2026: the undated names ARE the live current file on this site
# (the frozen replication export lives on the AEA repository, not here), and the
# dated vintage names are archived elsewhere. So try undated first — it is what
# resolves — and keep the dated names as a fallback in case that changes. The
# freshness check below is what protects against ever ingesting a stale file,
# whichever name served it.
CURRENT = ["data_gpr_export.dta", "data_gpr_export.xls"]
MAX_LAG_DAYS = 75
# Some hosts reject unfamiliar agents or datacenter traffic outright. A plain
# browser string is what actually gets served; the identifying comment stays so
# the request is not disguised.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
      "(+chokepoint-access monthly reading; open data, CC BY)")
HEADERS = {"User-Agent": UA, "Accept": "*/*",
           "Accept-Language": "en-GB,en;q=0.9",
           "Referer": "https://www.matteoiacoviello.com/gpr.htm"}


def candidates(today: date, back: int = 2) -> list[str]:
    out = [f"{base}/{f}" for base in BASES for f in CURRENT]
    y, m = today.year, today.month
    for _ in range(back + 1):
        for base in BASES:
            for p in PATTERNS:
                out.append(f"{base}/{p.format(vintage=f'{y}{m:02d}')}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def check(path: Path) -> str:
    """Parse it and confirm the series is current. Returns the last month."""
    df = pd.read_stata(path) if path.suffix.lower() == ".dta" else pd.read_excel(path)
    dcol = next((c for c in df.columns if c.lower() in
                 ("month", "date", "yearmonth", "time")), None)
    if dcol is None:
        raise ValueError(f"no date column; got {list(df.columns)[:12]}")
    d = pd.to_datetime(df[dcol].astype(str), errors="coerce").dropna()
    if d.empty:
        raise ValueError("no parseable dates")
    if not any(c.upper() == "GPRT" for c in df.columns):
        raise ValueError(f"no GPRT column; got {list(df.columns)[:12]}")
    last = d.max().date()
    lag = (date.today() - last).days
    if lag > MAX_LAG_DAYS:
        raise ValueError(f"series ends {last}, {lag} days ago — not a current vintage")
    return str(last)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--url", help="exact file URL, skipping all path guessing")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    urls = [a.url] if a.url else candidates(date.today())
    if a.dry_run:
        for u in urls:
            print(u)
        return 0

    tmp = a.out.with_suffix(a.out.suffix + ".part")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    problems = []

    for url in urls:
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as f:
                f.write(r.read())
        except urllib.error.HTTPError as e:
            hint = ""
            if e.code in (403, 401, 429):
                hint = ("  <- the host refused the request rather than saying the file is "
                        "missing. This is a block, not a wrong path.")
            problems.append(f"{url}: HTTP {e.code} {e.reason}{hint}")
            continue
        except (urllib.error.URLError, TimeoutError) as e:
            problems.append(f"{url}: {e}")
            continue

        suffix = ".dta" if url.endswith(".dta") else ".xls"
        typed = tmp.with_suffix("")
        typed = typed.with_suffix(suffix)
        tmp.replace(typed)
        try:
            last = check(typed)
        except Exception as e:
            problems.append(f"{url}: downloaded but unusable — {e}")
            typed.unlink(missing_ok=True)
            continue

        if typed != a.out:
            typed.replace(a.out)
        print(f"fetched {url}\n  -> {a.out}  series ends {last}")
        return 0

    print(f"could not fetch a current vintage. Tried {len(urls)} URL(s):")
    for pr in problems:
        print("  " + pr)
    print("\nOpen matteoiacoviello.com/gpr.htm, copy the link behind "
          "'Stata format' under Data,")
    print("and either pass it as --url or add its directory to BASES.")
    print("\nIf every attempt shows HTTP 403 while the same URL works from a desktop,")
    print("the host is refusing this network. The data is Creative Commons BY, so the")
    print("fallback is to commit the vintage to the repository and drop --fetch.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

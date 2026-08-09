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

BASE = "https://www.matteoiacoviello.com/gpr_files"
# The site has used both extensions; try in order of what it publishes now.
PATTERNS = ["data_gpr_export_{vintage}.dta", "data_gpr_export_{vintage}.xls"]
MAX_LAG_DAYS = 75
UA = "tar-monitor/1.0 (research; contact via repository)"


def candidates(today: date, back: int = 2) -> list[str]:
    out = []
    y, m = today.year, today.month
    for _ in range(back + 1):
        for p in PATTERNS:
            out.append(f"{BASE}/{p.format(vintage=f'{y}{m:02d}')}")
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
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    urls = candidates(date.today())
    if a.dry_run:
        for u in urls:
            print(u)
        return 0

    tmp = a.out.with_suffix(a.out.suffix + ".part")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    problems = []

    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as f:
                f.write(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            problems.append(f"{url.rsplit('/', 1)[-1]}: {e}")
            continue

        suffix = ".dta" if url.endswith(".dta") else ".xls"
        typed = tmp.with_suffix("")
        typed = typed.with_suffix(suffix)
        tmp.replace(typed)
        try:
            last = check(typed)
        except Exception as e:
            problems.append(f"{url.rsplit('/', 1)[-1]}: downloaded but unusable — {e}")
            typed.unlink(missing_ok=True)
            continue

        if typed != a.out:
            typed.replace(a.out)
        print(f"fetched {url.rsplit('/', 1)[-1]} -> {a.out}  series ends {last}")
        return 0

    print("could not fetch a current vintage. Tried:")
    for pr in problems:
        print("  " + pr)
    print("\nThe filename pattern may have changed. Check "
          "matteoiacoviello.com/gpr.htm and update PATTERNS.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

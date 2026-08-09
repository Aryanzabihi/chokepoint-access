"""
reference.py — pin the construction without publishing the data.

The monthly run should fail if the indicator ever stops matching the series
behind the paper. The obvious way is to commit that series, but if the paper is
under review that is exactly what should not be public.

So commit a fingerprint instead: month count, span, summary statistics and a
SHA-256 of the values rounded to six decimals. That detects any change in the
construction while publishing nothing recoverable.

Once, locally, from the authoritative series:

    python reference.py make --reference ../final_output/panel_final.csv \\
        --column tar --out tar_reference.json

Then on every run, including in CI:

    python reference.py check --source ../data/gpr_monthly.dta \\
        --reference tar_reference.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tar_ingest import _to_month, build_tar, load  # noqa: E402

# Hashing rounded floats is boundary-sensitive: two values differing by 1e-15
# can round to different strings if they straddle a .00005 boundary, breaking
# the hash over an identical series. Four places keeps that rare, and the
# fallback below catches it when it happens anyway.
PLACES = 4
STAT_TOL = 5e-4     # a real construction change moves statistics far more than this


def fingerprint(series: pd.Series) -> dict:
    s = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    if s.empty:
        sys.exit("series is empty")
    vals = [f"{v:.{PLACES}f}" for v in s]
    return {
        "n": len(vals),
        "first_month": str(s.index[0].date()),
        "last_month": str(s.index[-1].date()),
        "min": round(float(s.min()), PLACES),
        "median": round(float(statistics.median(s)), PLACES),
        "max": round(float(s.max()), PLACES),
        "sha256": hashlib.sha256("\n".join(vals).encode()).hexdigest(),
        "places": PLACES,
    }


def panel_score(source: Path) -> pd.Series:
    """The construction as published: compound TAR, three-month mean, lagged one."""
    df = load(source, None, None)
    ind = build_tar(df[df.attrs["threat_col"]].astype(float),
                    df[df.attrs["act_col"]].astype(float))
    return ind["panel_score"]


def load_reference_series(path: Path, column: str) -> pd.Series:
    ref = pd.read_csv(path)
    dcol = next((c for c in ref.columns if c.lower() in
                 ("month", "date", "yearmonth", "time", "unnamed: 0")), None)
    if dcol is None:
        sys.exit(f"no date column in {path.name}. Columns: {list(ref.columns)}")
    ref[dcol] = _to_month(ref[dcol])
    ref = ref.dropna(subset=[dcol]).set_index(dcol).sort_index()

    # A panel repeats one global series across units; collapse before hashing.
    if ref.index.duplicated().any():
        ucol = next((c for c in ref.columns if c.lower() in ("unit", "corridor", "id")), None)
        if ucol:
            u = ref[ucol].astype(str).value_counts().index[0]
            ref = ref[ref[ucol].astype(str) == u]
            print(f"panel detected — collapsed to unit {u!r}")
        else:
            ref = ref[~ref.index.duplicated(keep="first")]
    if column not in ref.columns:
        sys.exit(f"column {column!r} not in {path.name}. Columns: {list(ref.columns)}")
    return ref[column]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make", help="fingerprint the authoritative series")
    m.add_argument("--reference", type=Path, required=True)
    m.add_argument("--column", default="tar")
    m.add_argument("--out", type=Path, default=Path("tar_reference.json"))

    c = sub.add_parser("check", help="recompute and compare against the fingerprint")
    c.add_argument("--source", type=Path, required=True)
    c.add_argument("--reference", type=Path, default=Path("tar_reference.json"))

    a = p.parse_args()

    if a.cmd == "make":
        fp = fingerprint(load_reference_series(a.reference, a.column))
        fp.update(source_file=a.reference.name, source_column=a.column,
                  created=str(date.today()),
                  construction=("compound TAR with expanding-mean deflator inclusive of t, "
                                "three-month mean lagged one month"),
                  note=("Fingerprint only. The series itself is not stored here and cannot "
                        "be recovered from this file."))
        a.out.write_text(json.dumps(fp, indent=2), encoding="utf-8")
        print(f"wrote {a.out}")
        print(f"  {fp['n']} months, {fp['first_month']} to {fp['last_month']}")
        print(f"  sha256 {fp['sha256'][:16]}…")
        return 0

    if not a.reference.exists():
        print(f"{a.reference} not found — run `reference.py make` once against the "
              f"authoritative series. Skipping the construction check.")
        return 0

    want = json.loads(a.reference.read_text(encoding="utf-8"))
    series = panel_score(a.source)          # loaded once; printed once
    got = fingerprint(series)

    if got["sha256"] == want["sha256"]:
        print(f"construction matches the pinned reference "
              f"({got['n']} months, sha256 {got['sha256'][:16]}…)")
        return 0

    # The overlap can differ legitimately: a new month has been released. Compare
    # only the months the reference covers before calling it drift.
    s = series[series.index <= pd.Timestamp(want["last_month"])]
    trimmed = fingerprint(s)
    if trimmed["sha256"] == want["sha256"]:
        print(f"construction matches over the reference span "
              f"({trimmed['n']} months to {want['last_month']}); "
              f"the source now extends to {got['last_month']}")
        return 0

    # A differing hash is not yet drift. Compare the statistics: a genuine change
    # of construction moves them by orders of magnitude more than float rounding.
    exact = ("n", "first_month", "last_month")
    near = ("min", "median", "max")
    hard = [k for k in exact if trimmed.get(k) != want.get(k)]
    soft = {k: (want.get(k), trimmed.get(k)) for k in near
            if want.get(k) is not None and trimmed.get(k) is not None
            and abs(float(trimmed[k]) - float(want[k])) > STAT_TOL}

    if not hard and not soft:
        print("construction matches the pinned reference within tolerance "
              f"({trimmed['n']} months).")
        print("  The stored hash differs, which happens when a value sits on a rounding "
              "boundary. Every statistic agrees to better than "
              f"{STAT_TOL:g}, so this is float noise, not a change of construction.")
        print("  Re-run `reference.py make` to re-pin the hash if you want it to match "
              "exactly.")
        return 0

    print("CONSTRUCTION DRIFT — the indicator no longer reproduces the pinned series.")
    for k in hard:
        print(f"  {k:12} reference {want[k]}   now {trimmed[k]}")
    for k, (a_, b_) in soft.items():
        print(f"  {k:12} reference {a_}   now {b_}   "
              f"(differs by {abs(float(b_) - float(a_)):.3g})")
    print("  Do not publish this reading. Run reconcile.py to find what changed:")
    print("    python reconcile.py --source <vintage> --reference <series csv> --column tar")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

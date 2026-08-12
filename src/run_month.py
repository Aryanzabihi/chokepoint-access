"""
run_month.py — the whole monthly operation, in one command.

    python run_month.py

Steps, in order, stopping at the first failure:
  1. ingest the current GPR vintage         (tar_ingest.py)
  2. cross-check against the frozen series  (reconcile via panel_final.csv)
  3. append to the public record            (publish.py)
  4. verify the hash chain and re-render     (publish.py --verify/--render)

Every step is a subprocess so a failure is loud and the run stops. The point of
having one command is that a monthly task done four ways eventually gets done
three ways.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data" / "gpr_monthly.dta"
PANEL = HERE.parent / "final_output" / "panel_final.csv"
READINGS = HERE / "readings.json"


def step(n: int, what: str, cmd: list[str], optional: bool = False) -> bool:
    print(f"\n[{n}] {what}")
    print("    $ " + " ".join(cmd))
    r = subprocess.run([sys.executable] + cmd, cwd=HERE)
    if r.returncode == 0:
        return True
    if optional:
        print(f"    !! step {n} failed (non-blocking) — continuing")
        return True
    print(f"\nSTOPPED at step {n}. Nothing was published.")
    return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", type=Path, default=DATA)
    p.add_argument("--panel", type=Path, default=PANEL)
    p.add_argument("--amend", metavar="REASON",
                   help="publishing a correction for a month already recorded")
    p.add_argument("--skip-reconcile", action="store_true")
    p.add_argument("--fetch", action="store_true",
                   help="download the current vintage first (used by CI)")
    p.add_argument("--if-new", action="store_true",
                   help="no-op instead of failing when this month is already published")
    a = p.parse_args()

    if a.fetch:
        if not step(0, "Fetch the current vintage",
                    ["fetch_gpr.py", "--out", str(a.source)]):
            return 1

    if not a.source.exists():
        print(f"{a.source} not found. Download this month's vintage first:")
        print("  data_gpr_export_YYYYMM.xls (or .dta) from matteoiacoviello.com/gpr.htm")
        return 1

    if not step(1, "Ingest the current vintage",
                ["tar_ingest.py", "--source", str(a.source), "--out", str(READINGS)]):
        return 1

    # The construction check is BLOCKING when a pinned reference exists: if the
    # indicator no longer reproduces the published series, the reading is wrong
    # and must not be published. Falls back to the full reconciler when only the
    # series itself is available, which is informative but non-blocking.
    ref = HERE / "tar_reference.json"
    if a.skip_reconcile:
        print("\n[2] Construction check skipped by request")
    elif ref.exists():
        if not step(2, "Check the construction against the pinned reference",
                    ["reference.py", "check", "--source", str(a.source),
                     "--reference", str(ref)]):
            return 1
    elif a.panel.exists():
        step(2, "Cross-check construction against the frozen panel",
             ["reconcile.py", "--source", str(a.source), "--reference", str(a.panel),
              "--column", "tar", "--top", "3"], optional=True)
    else:
        print("\n[2] No reference available — construction unchecked. Run "
              "`reference.py make` once against the authoritative series.")

    # publish.py writes track-record.html, so styles are inlined after it below.
    pub = ["publish.py", "--readings", str(READINGS)]
    if a.amend:
        pub += ["--amend", a.amend]
    if a.if_new:
        pub += ["--if-new"]
    if not step(3, "Append to the public record", pub):
        return 1

    if not step(4, "Inline styles into the pages", ["build_site.py"]):
        return 1

    if not step(5, "Verify the chain", ["publish.py", "--verify"]):
        return 1

    # Non-blocking, unlike steps 1-5: decision_engine.py's selftest is a
    # code-health check on the standalone v2 decision tool, not part of
    # the reading pipeline — a bug there should not withhold this month's
    # publication.
    step(6, "Decision engine v2 selftest", ["decision_engine.py", "--selftest"], optional=True)

    print("\nDone. Now commit the record so there are two independent timestamps:")
    print("    git add record.jsonl readings.json track-record.html")
    print('    git commit -m "reading: <month>"')
    print("    git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

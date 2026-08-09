"""
client_profile.py — turn a client's own spend history into a recommendation.

The threshold engine asks the client for one number: what acting early costs as
a share of crisis-price replacement. Almost nobody can answer that off the top
of their head. But it is sitting in their ledger — what they paid for cover in
calm months, and what the same cover cost once a corridor was closing.

This reads that ledger, derives the ratio, places them on the value curve, and
backtests three policies over their own history using their own money. Nothing
leaves the machine it runs on.

    python client_profile.py --template client_spend.csv
    python client_profile.py --spend client_spend.csv --source ../data/gpr_monthly.dta
    python client_profile.py --spend client_spend.csv --source ... --brief brief.html
    python client_profile.py --selftest

Ledger columns
--------------
    month        YYYY-MM
    corridor     must match the corridor registry in tar_ingest
    category     war_risk_premium | reroute | hedge | inventory | other
    amount       numeric, the client's own currency
    transits     optional; if given, costs are normalised per transit
    notes

The ratio is only reported where there are enough months on both sides of an
onset to estimate it. A number derived from two observations is not an
estimate, and this says so rather than printing one.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tar_ingest import (ALARM_PERCENTILE, CORRIDORS, POST_ONSET_MONTHS,  # noqa: E402
                        build_tar, load, recursive_percentile_cut, regime)

COLUMNS = ["month", "corridor", "category", "amount", "transits", "notes"]
CATEGORIES = {"war_risk_premium", "reroute", "hedge", "inventory", "other"}
MIN_MONTHS_PER_SIDE = 3          # below this the ratio is not reported
CURRENCY = "client currency"

# Frozen evaluation counts behind the published cost-loss curve (six-month
# action window). Same numbers as the threshold engine.
H, M, F, N_MONTHS = 15, 41, 70, 484


# --------------------------------------------------------------------------
# Cost-loss
# --------------------------------------------------------------------------

def value_score(alpha: float) -> float:
    base = min(N_MONTHS * alpha, H + M)
    return (base - ((H + F) * alpha + M)) / (base - (H + M) * alpha)


def positive_band() -> tuple[float, float]:
    xs = [i / 2000 for i in range(10, 1200)]
    pos = [x for x in xs if value_score(x) > 0]
    return (min(pos), max(pos)) if pos else (float("nan"), float("nan"))


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------

def read_spend(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    problems = []
    clean = []
    for i, r in enumerate(rows, start=2):
        missing = [c for c in COLUMNS if c not in r]
        if missing:
            problems.append(f"  row {i}: missing column(s) {', '.join(missing)}")
            continue
        try:
            m = datetime.strptime(r["month"].strip(), "%Y-%m")
        except ValueError:
            problems.append(f"  row {i}: month {r['month']!r} is not YYYY-MM")
            continue
        if r["corridor"].strip() not in CORRIDORS:
            problems.append(f"  row {i}: corridor {r['corridor']!r} not in the registry")
            continue
        if r["category"].strip() not in CATEGORIES:
            problems.append(f"  row {i}: category {r['category']!r} not in "
                            f"{sorted(CATEGORIES)}")
            continue
        try:
            amt = float(r["amount"])
        except ValueError:
            problems.append(f"  row {i}: amount {r['amount']!r} is not numeric")
            continue
        if amt < 0:
            problems.append(f"  row {i}: amount is negative")
            continue
        tr = None
        if (r.get("transits") or "").strip():
            try:
                tr = float(r["transits"])
                if tr <= 0:
                    raise ValueError
            except ValueError:
                problems.append(f"  row {i}: transits {r['transits']!r} must be positive")
                continue
        clean.append({"month": pd.Timestamp(m), "corridor": r["corridor"].strip(),
                      "category": r["category"].strip(), "amount": amt, "transits": tr})
    if problems:
        print(f"{len(problems)} problem row(s) in {path.name}:")
        for p in problems:
            print(p)
        sys.exit("fix the ledger and re-run — nothing was analysed")
    if not clean:
        sys.exit(f"{path.name} has no usable rows")
    return clean


def monthly_unit_cost(rows: list[dict]) -> dict[str, dict[pd.Timestamp, float]]:
    """Per corridor, per month: total spend, divided by transits where given.

    Normalising by transits matters: a month with twice the sailings costs twice
    as much at an unchanged rate, and comparing raw totals across calm and
    crisis months would read that volume change as a price change.
    """
    totals: dict[str, dict[pd.Timestamp, list[float]]] = defaultdict(
        lambda: defaultdict(list))
    transits: dict[str, dict[pd.Timestamp, float]] = defaultdict(dict)
    for r in rows:
        totals[r["corridor"]][r["month"]].append(r["amount"])
        if r["transits"] is not None:
            transits[r["corridor"]][r["month"]] = r["transits"]
    out: dict[str, dict[pd.Timestamp, float]] = {}
    for c, months in totals.items():
        out[c] = {}
        for m, amounts in months.items():
            total = sum(amounts)
            t = transits[c].get(m)
            out[c][m] = total / t if t else total
    return out


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

def alarm_series(source: Path, start: str = "1985-01") -> pd.Series:
    df = load(source, None, None, start)
    ind = build_tar(df[df.attrs["threat_col"]].astype(float),
                    df[df.attrs["act_col"]].astype(float))
    cut = recursive_percentile_cut(ind["tar"], ALARM_PERCENTILE)
    return (ind["tar"] >= cut) & cut.notna()


def analyse(unit_cost: dict[str, dict[pd.Timestamp, float]],
            alarms: pd.Series) -> list[dict]:
    """Per corridor: split months into calm and post-onset, derive the ratio,
    then price the three policies with the client's own C and L."""
    lo, hi = positive_band()
    out = []

    for corridor, months in sorted(unit_cost.items()):
        calm, crisis = [], []
        for m, cost in months.items():
            reg, _, _ = regime(corridor, m)
            (crisis if reg == "in episode" else calm).append(cost)

        row: dict = {"corridor": corridor, "months": len(months),
                     "calm_months": len(calm), "crisis_months": len(crisis),
                     "band_lo": lo, "band_hi": hi}

        if len(calm) < MIN_MONTHS_PER_SIDE or len(crisis) < MIN_MONTHS_PER_SIDE:
            row["status"] = "not estimable"
            row["why"] = (f"needs at least {MIN_MONTHS_PER_SIDE} months either side of an "
                          f"onset; has {len(calm)} calm and {len(crisis)} post-onset")
            out.append(row)
            continue

        C = statistics.median(calm)
        L = statistics.median(crisis)
        if L <= 0:
            row["status"] = "not estimable"
            row["why"] = "post-onset cost is zero, so no ratio exists"
            out.append(row)
            continue

        alpha = C / L
        row.update(C=C, L=L, alpha=alpha, value=value_score(alpha))

        # Policy pricing over the client's own covered months, using the frozen
        # counts scaled to their history length so the comparison is on their
        # horizon rather than the paper's.
        n = len(months)
        scale = n / N_MONTHS
        n_alarm = round((H + F) * scale)
        n_onset = round((H + M) * scale)
        n_missed = round(M * scale)
        row["policy"] = {
            "act every cycle": n * C,
            "never act": n_onset * L,
            "follow the alarm": n_alarm * C + n_missed * L,
        }
        best = min(row["policy"], key=row["policy"].get)
        row["best"] = best
        row["saving_vs_worst"] = max(row["policy"].values()) - row["policy"][best]

        if alpha < lo:
            row["status"] = "act every cycle"
            row["why"] = (f"acting early costs {alpha:.1%} of crisis replacement, below the "
                          f"{lo:.1%} floor — cheap enough that timing adds nothing. Make it "
                          f"standing policy and stop watching the signal.")
        elif alpha > hi:
            row["status"] = "do not act on the alarm"
            row["why"] = (f"acting early costs {alpha:.1%} of crisis replacement, above the "
                          f"{hi:.1%} ceiling — at this ratio the false alarms cost more than "
                          f"the missed onsets save. Absorb the exposure or move it elsewhere.")
        else:
            row["status"] = "follow the alarm"
            row["why"] = (f"acting early costs {alpha:.1%} of crisis replacement, inside the "
                          f"{lo:.1%}–{hi:.1%} band where the signal pays. Act reversibly: "
                          f"check alternates, extend hedge tenors, limited forward booking.")
        out.append(row)
    return out


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def print_report(rows: list[dict]) -> None:
    for r in rows:
        print(f"\n{r['corridor']}")
        print(f"  months in ledger        {r['months']} "
              f"({r['calm_months']} calm, {r['crisis_months']} post-onset)")
        if r["status"] == "not estimable":
            print(f"  ratio                   not estimable — {r['why']}")
            continue
        print(f"  cost of acting early    {r['C']:,.0f} per month (median calm)")
        print(f"  crisis replacement      {r['L']:,.0f} per month (median post-onset)")
        print(f"  ratio                   {r['alpha']:.1%}  "
              f"(band {r['band_lo']:.1%}–{r['band_hi']:.1%})")
        print(f"  value score             {r['value']:+.3f}")
        for k, v in sorted(r["policy"].items(), key=lambda kv: kv[1]):
            mark = "  <-- cheapest" if k == r["best"] else ""
            print(f"    {k:20} {v:>14,.0f}{mark}")
        print(f"  RECOMMENDATION          {r['status']}")
        print(f"    {r['why']}")


def render_brief(rows: list[dict], path: Path, client: str) -> None:
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    blocks = []
    for r in rows:
        if r["status"] == "not estimable":
            blocks.append(f"""<section><h2>{esc(r['corridor'])}</h2>
<p class="na">Not estimable. {esc(r['why'])}</p></section>""")
            continue
        pol = "".join(
            f'<tr><td>{esc(k)}</td><td class="m">{v:,.0f}</td>'
            f'<td>{"cheapest" if k == r["best"] else ""}</td></tr>'
            for k, v in sorted(r["policy"].items(), key=lambda kv: kv[1]))
        blocks.append(f"""<section><h2>{esc(r['corridor'])}</h2>
<div class="stats">
<div><b>{r['alpha']:.1%}</b><span>cost of acting early, as a share of crisis replacement</span></div>
<div><b>{r['band_lo']:.0%}&ndash;{r['band_hi']:.0%}</b><span>band where the signal pays</span></div>
<div><b>{r['value']:+.2f}</b><span>value score at this ratio</span></div>
</div>
<p class="rec">{esc(r['status'])}</p><p>{esc(r['why'])}</p>
<table><thead><tr><th>Policy</th><th>Cost over {r['months']} months</th><th></th></tr></thead>
<tbody>{pol}</tbody></table></section>""")

    path.write_text(f"""<!DOCTYPE html>
<meta charset="utf-8"><title>Chokepoint exposure — {esc(client)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{{--water:#E9EFF1;--ink:#10222E;--soft:#4A6070;--rule:#B4C3C8;
--caution:#C0157A;--sound:#1F5F6B;--paper:#F6F9FA}}
body{{margin:0;background:var(--water);color:var(--ink);
font:15px/1.55 "IBM Plex Sans",system-ui,sans-serif}}
.w{{max-width:920px;margin:0 auto;padding:34px 20px 64px}}
h1{{font:600 clamp(25px,4vw,36px)/1.12 Spectral,Georgia,serif;margin:8px 0 8px}}
.eyebrow{{font:11px/1 "IBM Plex Mono",monospace;letter-spacing:.16em;
text-transform:uppercase;color:var(--soft)}}
.lede{{font:italic 400 16px/1.5 Spectral,Georgia,serif;color:var(--soft);max-width:62ch}}
section{{background:var(--paper);border:1px solid var(--rule);padding:20px;margin-top:22px}}
h2{{font:600 18px/1.2 Spectral,Georgia,serif;margin:0 0 14px}}
.stats{{display:flex;gap:28px;flex-wrap:wrap;padding-bottom:14px;
border-bottom:1px solid var(--rule);margin-bottom:14px}}
.stats b{{display:block;font:500 21px/1 "IBM Plex Mono",monospace}}
.stats span{{font-size:11.5px;color:var(--soft);max-width:22ch;display:block}}
.rec{{font:600 15px/1.3 "IBM Plex Sans",sans-serif;color:var(--sound);
text-transform:uppercase;letter-spacing:.04em;margin:0 0 6px}}
.na{{color:var(--soft);font-style:italic;margin:0}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:14px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--rule)}}
th{{font:600 10px/1 "IBM Plex Mono",monospace;letter-spacing:.1em;
text-transform:uppercase;color:var(--soft)}}
.m{{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}}
.note{{font-size:12.5px;color:var(--soft);border-left:3px solid var(--rule);
padding-left:12px;margin-top:24px;max-width:74ch}}
</style>
<div class="w">
<div class="eyebrow">Chokepoint exposure review &middot; {esc(client)}</div>
<h1>Where your own numbers fall</h1>
<p class="lede">Derived from your spend history, not from an estimate. For most
exposures the honest answer is that watching the signal does not pay &mdash;
where that is the case, it says so.</p>
{''.join(blocks)}
<p class="note">The ratio is the median cost of a calm month against the median
cost of a month inside the {POST_ONSET_MONTHS}-month window after a
transit-verified onset. Policy costs price your own figures against the
published evaluation counts (15 hits, 41 misses, 70 false alarms over 484
at-risk months), scaled to the length of your history. One alarm in three
precedes a disruption, so what the middle case justifies is reversible action,
not commitment.</p>
</div>
""", encoding="utf-8")
    print(f"wrote {path}")


def write_template(path: Path) -> None:
    if path.exists():
        sys.exit(f"{path} exists — refusing to overwrite a client ledger")
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerow(["2023-06", "Bab-el-Mandeb", "war_risk_premium", "18000", "6",
                    "example row — delete before use"])
    print(f"wrote {path}")
    print(f"  corridors: {', '.join(sorted(CORRIDORS))}")
    print(f"  categories: {', '.join(sorted(CATEGORIES))}")


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def selftest() -> int:
    lo, hi = positive_band()
    assert 0.09 < lo < 0.12 and 0.16 < hi < 0.19, (lo, hi)

    # A client whose calm cost is a small fraction of crisis cost should be told
    # to act every cycle, not to watch the signal.
    cheap = {"Bab-el-Mandeb": {}}
    for k in range(24):
        m = pd.Timestamp("2021-01-01") + pd.DateOffset(months=k)
        reg, _, _ = regime("Bab-el-Mandeb", m)
        cheap["Bab-el-Mandeb"][m] = 200.0 if reg != "in episode" else 10000.0
    r = analyse(cheap, pd.Series(dtype=bool))[0]
    assert r["status"] == "not estimable", r["status"]   # 2021-22 has no onset window

    # Same shape but spanning the 2023-11 onset, so both sides exist.
    both = {"Bab-el-Mandeb": {}}
    for k in range(36):
        m = pd.Timestamp("2023-01-01") + pd.DateOffset(months=k)
        reg, _, _ = regime("Bab-el-Mandeb", m)
        both["Bab-el-Mandeb"][m] = 200.0 if reg != "in episode" else 10000.0
    r = analyse(both, pd.Series(dtype=bool))[0]
    assert r["calm_months"] >= 3 and r["crisis_months"] >= 3
    assert r["alpha"] == 200 / 10000
    assert r["status"] == "act every cycle", r["status"]
    assert r["best"] == "act every cycle", r["policy"]

    # A client for whom acting early costs nearly as much as crisis replacement
    # must be told not to act on the alarm.
    dear = {"Bab-el-Mandeb": {}}
    for k in range(36):
        m = pd.Timestamp("2023-01-01") + pd.DateOffset(months=k)
        reg, _, _ = regime("Bab-el-Mandeb", m)
        dear["Bab-el-Mandeb"][m] = 900.0 if reg != "in episode" else 1000.0
    r = analyse(dear, pd.Series(dtype=bool))[0]
    assert r["status"] == "do not act on the alarm", r["status"]

    # And one inside the band gets the middle recommendation.
    mid = {"Bab-el-Mandeb": {}}
    for k in range(36):
        m = pd.Timestamp("2023-01-01") + pd.DateOffset(months=k)
        reg, _, _ = regime("Bab-el-Mandeb", m)
        mid["Bab-el-Mandeb"][m] = 140.0 if reg != "in episode" else 1000.0
    r = analyse(mid, pd.Series(dtype=bool))[0]
    assert r["status"] == "follow the alarm", (r["alpha"], r["status"])

    # Transit normalisation: doubling sailings at an unchanged rate must not
    # move the ratio.
    rows = []
    for k in range(36):
        m = (pd.Timestamp("2023-01-01") + pd.DateOffset(months=k)).strftime("%Y-%m")
        reg, _, _ = regime("Bab-el-Mandeb", pd.Timestamp(m))
        rate = 140.0 if reg != "in episode" else 1000.0
        t = 2 if k % 2 else 4
        rows.append({"month": pd.Timestamp(m), "corridor": "Bab-el-Mandeb",
                     "category": "war_risk_premium", "amount": rate * t, "transits": t})
    uc = monthly_unit_cost(rows)
    r2 = analyse(uc, pd.Series(dtype=bool))[0]
    assert abs(r2["alpha"] - 0.14) < 1e-9, r2["alpha"]

    print("all checks passed")
    print(f"  positive band {lo:.1%}–{hi:.1%}")
    print("  cheap client -> act every cycle; dear client -> do not act;")
    print("  mid client -> follow the alarm; transit volume does not move the ratio")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--spend", type=Path)
    p.add_argument("--source", type=Path, help="GPR vintage, for the alarm history")
    p.add_argument("--brief", type=Path, help="write an HTML brief for the client")
    p.add_argument("--client", default="client")
    p.add_argument("--template", type=Path)
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if a.template:
        write_template(a.template)
        return 0
    if not a.spend:
        p.error("pass --spend, --template or --selftest")

    rows = read_spend(a.spend)
    print(f"{a.spend.name}: {len(rows)} rows, "
          f"{len({r['corridor'] for r in rows})} corridor(s)")
    alarms = alarm_series(a.source) if a.source else pd.Series(dtype=bool)
    result = analyse(monthly_unit_cost(rows), alarms)
    print_report(result)
    if a.brief:
        render_brief(result, a.brief, a.client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

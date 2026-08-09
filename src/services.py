"""
services.py — the four evidence services, on one spine.

Point-in-time reconstruction, dispute attestation, stand-down certification and
crowding advisory are not four products. They are one question asked four ways:
what did this method say on a given date, and how strongly can that be shown?

The whole value is in the answer to the second half, so this module never
blurs it. Every statement it emits carries an evidence grade:

    PUBLISHED     the month is in record.jsonl, hash-chained, committed before
                  the outcome was known. This is the only grade that survives
                  a hostile reading.
    RECONSTRUCTED recomputed from today's vintage using only observations up to
                  the date in question. Leak-free by construction, but it is
                  arithmetic done after the fact and says so.

A reconstruction presented as a contemporaneous record would be the single
worst thing this product could do, so the grade is on the face of every
document and cannot be suppressed by a flag.

    python services.py attest --as-of 2026-06 --corridor "Strait of Hormuz" \
        --source ../data/gpr_monthly.dta --out attestation.html
    python services.py standdown --as-of 2026-06 --alpha 0.22 --client "Name" \
        --source ../data/gpr_monthly.dta --out standdown.html
    python services.py crowding --alpha 0.13
    python services.py listing --register warrisk_jwc.csv
    python services.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tar_ingest import (ALARM_PERCENTILE, ONSETS, POST_ONSET_MONTHS,  # noqa: E402
                        assign_band, build_tar, load, recursive_percentile_cut,
                        regime)

RECORD = Path("record.jsonl")
METHOD = ("compound threat-to-act ratio, deflated by the expanding mean of threat "
          "coverage through the month in question; three-month moving average lagged "
          "one month for panel use")

# Frozen evaluation counts behind the published cost-loss curve.
H, M, F, N_MONTHS = 15, 41, 70, 484

# Adoption response multipliers used in the crowding advisory.
RESPONSES = {"none": 0.0, "unit": 1.0, "strong": 2.5}
ADOPTIONS = [0.0, 0.25, 0.50, 0.75, 1.00]

# The three limits that must appear on any document leaving this system.
LIMITS = [
    "The reading is global in origin. It does not identify which theatre is moving; "
    "corridor attribution is a demonstrated architecture, not a validated instrument.",
    "The method does not separate escalations that cascade into disruption from those "
    "that reverse. The largest reading in four decades preceded no disruption.",
    "The method does not anticipate vessel rerouting. No claim is made about transit "
    "volumes at any horizon.",
]


# --------------------------------------------------------------------------
# Cost-loss
# --------------------------------------------------------------------------

def value_score(alpha: float) -> float:
    base = min(N_MONTHS * alpha, H + M)
    return (base - ((H + F) * alpha + M)) / (base - (H + M) * alpha)


def positive_band() -> tuple[float, float]:
    xs = [i / 4000 for i in range(20, 2400)]
    pos = [x for x in xs if value_score(x) > 0]
    return (min(pos), max(pos))


# --------------------------------------------------------------------------
# The spine: what did the method say, and how strongly can it be shown
# --------------------------------------------------------------------------

def published_entry(as_of: str, path: Path = RECORD) -> dict | None:
    """The committed record for this month, if there is one."""
    if not path.exists():
        return None
    want = pd.Timestamp(as_of).strftime("%Y-%m")
    hit = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        if pd.Timestamp(e["as_of"]).strftime("%Y-%m") == want:
            hit = e            # last wins, so a correction supersedes
    return hit


def point_in_time(source: Path, as_of: str, corridor: str | None = None,
                  record: Path = RECORD) -> dict:
    """What the method said as of a month, with its evidence grade.

    The construction is leak-free — the deflator reaches only up to the month
    in question and the alarm cut only up to the month before — so truncating
    the series at as_of and recomputing gives the identical value. That is what
    makes reconstruction defensible at all. It is still not a contemporaneous
    record, and the grade says which one this is.
    """
    ts = pd.Timestamp(as_of)
    df = load(source, None, None)
    ind = build_tar(df[df.attrs["threat_col"]].astype(float),
                    df[df.attrs["act_col"]].astype(float))
    cut = recursive_percentile_cut(ind["tar"], ALARM_PERCENTILE)

    if ts.to_period("M") not in ind.index.to_period("M"):
        sys.exit(f"{as_of} is not in the source series "
                 f"({ind.index[0].date()} to {ind.index[-1].date()})")
    at = ind.index[ind.index.to_period("M") == ts.to_period("M")][0]

    tar = ind.loc[at, "tar"]
    if pd.isna(tar):
        sys.exit(f"{as_of} falls inside the burn-in; no reading exists for it")

    band, pct, horizon = assign_band(float(tar))
    entry = published_entry(as_of, record)

    out = {
        "as_of": at.strftime("%Y-%m"),
        "corridor": corridor,
        "tar": round(float(tar), 3),
        "band": band,
        "percentile_range": pct,
        "horizon": horizon,
        "alarm": bool(cut.notna().loc[at] and tar >= cut.loc[at]),
        "recursive_cut": None if pd.isna(cut.loc[at]) else round(float(cut.loc[at]), 3),
        "method": METHOD,
        "vintage_last_month": ind.index[-1].strftime("%Y-%m"),
        "grade": "PUBLISHED" if entry else "RECONSTRUCTED",
        "record": None,
    }

    if corridor:
        reg, onset, since = regime(corridor, at)
        out["regime"] = reg
        out["onset"] = onset
        out["months_since_onset"] = since
        if reg == "in episode":
            out["horizon"] = None      # a horizon is time-to-onset; onset happened

    if entry:
        out["record"] = {"seq": entry["seq"], "hash": entry["hash"],
                         "published_at": entry["published_at"],
                         "tar_as_published": entry.get("tar"),
                         "band_as_published": entry.get("band")}
        # A published figure that disagrees with today's recomputation means the
        # source vintage was revised. That is a fact about the data, not an
        # error, and hiding it would be the dishonest choice.
        if entry.get("tar") is not None and abs(entry["tar"] - out["tar"]) > 5e-4:
            out["vintage_revision"] = {
                "published": entry["tar"], "recomputed_today": out["tar"],
                "note": ("the release was revised after publication; the published "
                         "figure is what was on the record at the time")}
    return out


# --------------------------------------------------------------------------
# Crowding advisory
# --------------------------------------------------------------------------

def crowding(alpha: float) -> dict:
    """How the client's own position degrades as the market piles in.

    Anticipatory mitigation is priced in a market. If enough buyers act on the
    same signal, the quoted cost rises and the position that paid at low
    adoption stops paying. Every competitor's incentive is to leave this out,
    because it argues against acting on their own alert.
    """
    lo, hi = positive_band()
    grid = []
    for rname, k in RESPONSES.items():
        for a in ADOPTIONS:
            paid = alpha * (1 + k * a)
            grid.append({"response": rname, "adoption": a, "paid": paid,
                         "value": value_score(paid), "pays": lo <= paid <= hi,
                         "quotable_ceiling": hi / (1 + k * a)})
    # The adoption level at which this client leaves the band, per response.
    exits = {}
    for rname, k in RESPONSES.items():
        if k == 0:
            exits[rname] = None if lo <= alpha <= hi else 0.0
            continue
        exits[rname] = None
        for i in range(0, 401):
            a = i / 400
            if not (lo <= alpha * (1 + k * a) <= hi):
                exits[rname] = a
                break
    return {"alpha": alpha, "band": (lo, hi), "grid": grid, "exit_adoption": exits}


# --------------------------------------------------------------------------
# Listing statistics
# --------------------------------------------------------------------------

# ONSETS in tar_ingest carries month precision only, so a lag measured from a
# month start overstates it by the day of month. Where the paper dates an onset
# to the day, use that; otherwise report the precision alongside the lag rather
# than implying a day-accurate figure.
ONSET_DAYS = {
    ("Bab-el-Mandeb", "2023-11"): date(2023, 11, 19),
    ("Strait of Hormuz", "2026-02"): date(2026, 2, 28),
}

CORRIDOR_AREA_HINTS = {
    "Strait of Hormuz": ("hormuz", "persian", "arabian gulf", "iran", "bahrain",
                         "kuwait", "oman", "qatar", "saudi", "emirates"),
    "Bab-el-Mandeb": ("red sea", "aden", "yemen", "djibouti", "eritrea"),
    "Turkish Straits / Black Sea": ("black sea", "azov", "russia", "ukraine"),
    "Suez Canal": ("suez", "egypt"),
    "Strait of Malacca": ("malacca", "malaysia", "indonesia"),
    "Taiwan Strait": ("taiwan", "china"),
    "Adriatic": ("adriatic", "balkan", "croatia", "montenegro"),
}


def listing_stats(register: Path) -> dict:
    """Reaction lag from onset to the next listed-area change.

    This is descriptive. With a handful of onsets it establishes ordering and
    magnitude, not a hazard model, and the output refuses to state a
    probability it cannot support.
    """
    with register.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"{register} is empty")

    dated = []
    for r in rows:
        try:
            d = datetime.strptime(r["circular_date"].strip(), "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        dated.append((d, r.get("circular_ref", "").strip(),
                      r.get("area_name", "").strip().lower(),
                      r.get("action", "").strip()))
    circulars = sorted({(d, ref) for d, ref, _, _ in dated})

    # An onset predating the register cannot have its reaction measured: the
    # first circular on file is simply the first one collected, not the
    # response. Matching those would manufacture multi-decade "lags".
    coverage_start = circulars[0][0] if circulars else None

    lags, unmeasurable = [], []
    for corridor, onset_months in ONSETS.items():
        hints = CORRIDOR_AREA_HINTS.get(corridor, ())
        for om in onset_months:
            exact = ONSET_DAYS.get((corridor, om))
            onset = exact or pd.Timestamp(om).date()
            if coverage_start is None or onset < coverage_start:
                unmeasurable.append({"corridor": corridor, "onset": om,
                                     "why": "predates the register"})
                continue
            cands = [(d, ref) for d, ref, area, _ in dated
                     if d >= onset and any(h in area for h in hints)]
            if not cands:
                unmeasurable.append({"corridor": corridor, "onset": om,
                                     "why": "no matching listed-area change on file"})
                continue
            d, ref = min(cands)
            lags.append({"corridor": corridor, "onset": om, "circular": ref,
                         "circular_date": str(d), "lag_days": (d - onset).days,
                         "onset_precision": "day" if exact else "month",
                         "onset_used": str(onset)})

    by_action: dict[str, int] = {}
    for _, _, _, act in dated:
        by_action[act] = by_action.get(act, 0) + 1

    vals = [x["lag_days"] for x in lags]
    return {
        "rows": len(dated), "circulars": len(circulars),
        "span": (str(circulars[0][0]), str(circulars[-1][0])) if circulars else None,
        "coverage_start": str(coverage_start) if coverage_start else None,
        "by_action": by_action, "lags": sorted(lags, key=lambda x: x["onset"]),
        "unmeasurable": unmeasurable,
        "median_lag_days": statistics.median(vals) if vals else None,
        "lag_range": (min(vals), max(vals)) if vals else None,
        "n_matched": len(vals),
        "caveat": ("Descriptive only. With this many matched onsets the lag "
                   "establishes magnitude and ordering, not a forecastable hazard. "
                   "No listing probability is stated. Lags from a month-precision "
                   "onset are upper bounds by up to 30 days."),
    }


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------

CSS = """
:root{--water:#E9EFF1;--ink:#10222E;--soft:#4A6070;--rule:#B4C3C8;
--caution:#C0157A;--sound:#1F5F6B;--paper:#F6F9FA}
body{margin:0;background:var(--water);color:var(--ink);
font:15px/1.55 "IBM Plex Sans",system-ui,sans-serif}
.w{max-width:820px;margin:0 auto;padding:34px 20px 64px}
h1{font:600 clamp(24px,3.6vw,33px)/1.14 Spectral,Georgia,serif;margin:8px 0 10px}
h2{font:600 11px/1 "IBM Plex Mono",monospace;letter-spacing:.14em;
text-transform:uppercase;color:var(--soft);margin:0 0 12px;
padding-bottom:8px;border-bottom:1px solid var(--rule)}
.eyebrow{font:11px/1 "IBM Plex Mono",monospace;letter-spacing:.16em;
text-transform:uppercase;color:var(--soft)}
section{background:var(--paper);border:1px solid var(--rule);padding:20px;margin-top:20px}
.grade{display:inline-block;font:600 11px/1 "IBM Plex Mono",monospace;
letter-spacing:.14em;padding:6px 9px;border:1px solid currentColor;margin-top:14px}
.g-pub{color:var(--sound)} .g-rec{color:var(--caution)}
dl{display:grid;grid-template-columns:minmax(140px,32%) 1fr;gap:7px 16px;margin:0}
dt{font:11px/1.5 "IBM Plex Mono",monospace;letter-spacing:.06em;
text-transform:uppercase;color:var(--soft)}
dd{margin:0;font-variant-numeric:tabular-nums}
.m{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}
ol{margin:0;padding-left:20px} li{margin-bottom:8px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--rule)}
th{font:600 10px/1 "IBM Plex Mono",monospace;letter-spacing:.1em;
text-transform:uppercase;color:var(--soft)}
.verdict{font:600 clamp(19px,3vw,25px)/1.2 Spectral,Georgia,serif;margin:4px 0 10px}
.pos{color:var(--sound)} .neg{color:var(--caution)}
.note{font-size:12.5px;color:var(--soft);border-left:3px solid var(--rule);
padding-left:12px;margin-top:20px}
"""


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _grade_block(p: dict) -> str:
    pub = p["grade"] == "PUBLISHED"
    cls = "g-pub" if pub else "g-rec"
    if pub:
        r = p["record"]
        body = (f"<dl><dt>Record entry</dt><dd class=\"m\">#{r['seq']}</dd>"
                f"<dt>Entry hash</dt><dd class=\"m\" style=\"word-break:break-all\">"
                f"{esc(r['hash'])}</dd>"
                f"<dt>Committed at</dt><dd class=\"m\">{esc(r['published_at'])} UTC</dd></dl>"
                "<p class=\"note\">This month was published before the outcome was known. "
                "The entry is hash-chained to the one before it, so altering it breaks "
                "every hash after it. A third party can verify the chain independently "
                "from the published record.</p>")
        if "vintage_revision" in p:
            v = p["vintage_revision"]
            body += (f"<p class=\"note\">The source release was revised after "
                     f"publication: {v['published']} as published, {v['recomputed_today']} "
                     f"on today's vintage. The published figure is what was on the record "
                     f"at the time.</p>")
    else:
        body = ("<p class=\"note\">This month is <strong>not</strong> in the published "
                "record. The figures are recomputed from today's release using only "
                "observations up to the month stated. The construction reaches no further "
                "forward than that month, so the arithmetic is not contaminated by later "
                "data &mdash; but this is a reconstruction made after the fact, not a "
                "contemporaneous record, and carries correspondingly less weight.</p>")
    return f'<span class="grade {cls}">{p["grade"]}</span>{body}'


def attestation_html(p: dict, path: Path, requester: str | None = None) -> None:
    reg = ""
    if p.get("regime"):
        reg = (f"<dt>Regime</dt><dd>{esc(p['regime'])}"
               + (f" &mdash; {p['months_since_onset']} months after the "
                  f"{esc(p['onset'])} onset" if p.get("onset") else "") + "</dd>")
    hz = (f"<dt>Horizon</dt><dd>{esc(p['horizon'])}</dd>" if p.get("horizon") else
          "<dt>Horizon</dt><dd>none stated &mdash; the reading is concurrent with an "
          "ongoing disruption, so no lead time applies</dd>"
          if p.get("regime") == "in episode" else "")

    path.write_text(f"""<!DOCTYPE html>
<meta charset="utf-8"><title>Attestation — {esc(p['as_of'])}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style>
<div class="w">
<div class="eyebrow">Attestation of indicator state &middot; issued {date.today()}</div>
<h1>What the method stated for {esc(p['as_of'])}</h1>
{f'<p class="eyebrow">Prepared at the request of {esc(requester)}</p>' if requester else ''}

<section><h2>1. Evidence grade</h2>{_grade_block(p)}</section>

<section><h2>2. Indicator state</h2><dl>
<dt>Month</dt><dd class="m">{esc(p['as_of'])}</dd>
{f'<dt>Corridor</dt><dd>{esc(p["corridor"])}</dd>' if p.get('corridor') else ''}
<dt>Indicator value</dt><dd class="m">{p['tar']}</dd>
<dt>Band</dt><dd>{esc(p['band'])} ({esc(p['percentile_range'])})</dd>
{hz}{reg}
<dt>At or above trigger</dt><dd>{'yes' if p['alarm'] else 'no'}</dd>
<dt>Trigger level then</dt><dd class="m">{p['recursive_cut']}</dd>
</dl></section>

<section><h2>3. Method</h2>
<p>{esc(p['method'])}.</p>
<p class="note">Thresholds are recursive: the level a month is judged against is
computed from months strictly before it. No parameter is fitted to the outcome
being judged.</p></section>

<section><h2>4. Limits of this statement</h2><ol>
{''.join(f'<li>{esc(l)}</li>' for l in LIMITS)}
</ol></section>

<section><h2>5. Base rates</h2><dl>
<dt>Alarm episodes</dt><dd class="m">11</dd>
<dt>Followed by disruption</dt><dd class="m">3 &mdash; 0.27 (95% CI 0.10&ndash;0.57)</dd>
<dt>Month level</dt><dd class="m">15 hits, 41 misses, 70 false alarms</dd>
<dt>Unconditional rate</dt><dd class="m">0.24% per at-risk corridor-month</dd>
</dl>
<p class="note">Roughly one alarm in three is followed by a sustained disruption.
Any statement of what this indicator implies must be read against that rate.</p>
</section>

<p class="note">Vintage used: release ending {esc(p['vintage_last_month'])}.
Issued {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.</p>
</div>
""", encoding="utf-8")
    print(f"wrote {path}  grade {p['grade']}")


def standdown_html(p: dict, alpha: float, path: Path, client: str) -> None:
    """The document that certifies inaction. Nobody else sells this, because
    every alert vendor's revenue depends on urgency."""
    lo, hi = positive_band()
    v = value_score(alpha)
    inside = lo <= alpha <= hi
    in_ep = p.get("regime") == "in episode"

    if in_ep:
        verdict, cls = "Concurrent exposure — no lead-time claim", "neg"
        why = (f"The corridor is inside the {POST_ONSET_MONTHS}-month window following the "
               f"{esc(p['onset'])} onset. The band describes an ongoing disruption rather "
               f"than anticipating one, so no anticipatory action is justified by lead time. "
               f"Decisions here are about current exposure, not timing.")
    elif inside:
        verdict, cls = "Anticipatory action is justified this month", "pos"
        why = (f"At a cost ratio of {alpha:.1%}, inside the {lo:.1%}&ndash;{hi:.1%} band, "
               f"following the signal carries positive expected value. What is justified is "
               f"reversible: checking alternates, extending hedge tenors, limited forward "
               f"booking &mdash; not irreversible commitment.")
    elif alpha < lo:
        verdict, cls = "No timing decision applies — treat as standing policy", "neg"
        why = (f"At {alpha:.1%}, acting early is cheap enough relative to crisis-price "
               f"replacement that acting every cycle beats following any signal. The "
               f"correct posture is standing policy, not monitoring.")
    else:
        verdict, cls = "No anticipatory action justified this month", "neg"
        why = (f"At {alpha:.1%}, above the {hi:.1%} ceiling, the cost of acting on false "
               f"alarms exceeds what avoided disruptions would save. Declining to act is "
               f"the expected-value-maximising decision, not an omission.")

    path.write_text(f"""<!DOCTYPE html>
<meta charset="utf-8"><title>Stand-down certificate — {esc(client)} {esc(p['as_of'])}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style>
<div class="w">
<div class="eyebrow">Position certificate &middot; {esc(client)} &middot; {esc(p['as_of'])}</div>
<h1>Decision of record</h1>

<section><h2>1. Determination</h2>
<p class="verdict {cls}">{verdict}</p>
<p>{why}</p></section>

<section><h2>2. Inputs</h2><dl>
<dt>Month</dt><dd class="m">{esc(p['as_of'])}</dd>
{f'<dt>Corridor</dt><dd>{esc(p["corridor"])}</dd>' if p.get('corridor') else ''}
<dt>Indicator value</dt><dd class="m">{p['tar']}</dd>
<dt>Band</dt><dd>{esc(p['band'])}</dd>
<dt>At or above trigger</dt><dd>{'yes' if p['alarm'] else 'no'}</dd>
<dt>Cost of acting early</dt><dd class="m">{alpha:.1%} of crisis-price replacement</dd>
<dt>Band where signal pays</dt><dd class="m">{lo:.1%} &ndash; {hi:.1%}</dd>
<dt>Value score</dt><dd class="m">{v:+.3f}</dd>
</dl></section>

<section><h2>3. Evidence grade</h2>{_grade_block(p)}</section>

<section><h2>4. Limits</h2><ol>
{''.join(f'<li>{esc(l)}</li>' for l in LIMITS)}
<li>Roughly one alarm in three is followed by a sustained disruption
(3 of 11 episodes; 95% CI 0.10&ndash;0.57). A determination not to act is a
judgement about expected cost, not a prediction that nothing will happen.</li>
</ol></section>

<p class="note">Retain this alongside the month's exposure position. Issued
{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from the release ending
{esc(p['vintage_last_month'])}.</p>
</div>
""", encoding="utf-8")
    print(f"wrote {path}  {verdict}")


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def selftest() -> int:
    lo, hi = positive_band()
    assert 0.09 < lo < 0.12 and 0.16 < hi < 0.19, (lo, hi)

    # Crowding: a client inside the band must be pushed out by enough adoption,
    # and never pushed out when prices do not respond.
    c = crowding(0.13)
    assert c["exit_adoption"]["none"] is None
    assert 0 < c["exit_adoption"]["unit"] <= 1
    assert c["exit_adoption"]["strong"] < c["exit_adoption"]["unit"]
    at_zero = [g for g in c["grid"] if g["adoption"] == 0 and g["response"] == "unit"][0]
    at_full = [g for g in c["grid"] if g["adoption"] == 1.0 and g["response"] == "unit"][0]
    assert at_zero["pays"] and not at_full["pays"]
    assert at_full["quotable_ceiling"] < at_zero["quotable_ceiling"]

    # A client already above the ceiling is out at zero adoption, in every
    # response regime — crowding cannot rescue them.
    c2 = crowding(0.30)
    assert all(v == 0.0 for v in c2["exit_adoption"].values()), c2["exit_adoption"]

    # Evidence grade must key off the record, not off a flag.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        rec = Path(d) / "record.jsonl"
        rec.write_text(json.dumps({
            "seq": 0, "as_of": "2026-06-01", "published_at": "2026-08-08T10:00:00+00:00",
            "tar": 2.311, "band": "High-risk escalation", "prev_hash": "genesis",
            "hash": "a" * 64}) + "\n", encoding="utf-8")
        assert published_entry("2026-06", rec)["seq"] == 0
        assert published_entry("2026-05", rec) is None
        # a correction supersedes the original
        with rec.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"seq": 1, "as_of": "2026-06-01", "tar": 2.4,
                                "published_at": "x", "hash": "b" * 64,
                                "amends": 0}) + "\n")
        assert published_entry("2026-06", rec)["seq"] == 1

    # Stand-down wording must depend on which side of the band the client is on.
    base = {"as_of": "2026-06", "corridor": "Strait of Malacca", "tar": 2.3,
            "band": "High-risk escalation", "percentile_range": "P85-P95",
            "horizon": "30-60 days", "alarm": True, "recursive_cut": 1.9,
            "method": METHOD, "vintage_last_month": "2026-06",
            "grade": "RECONSTRUCTED", "record": None, "regime": "at risk",
            "onset": None, "months_since_onset": None}
    with tempfile.TemporaryDirectory() as d:
        for alpha, expect in ((0.05, "standing policy"), (0.13, "is justified"),
                              (0.30, "No anticipatory action")):
            out = Path(d) / "s.html"
            standdown_html(dict(base), alpha, out, "Test")
            assert expect in out.read_text(), (alpha, expect)
        # in-episode overrides all three
        ep = dict(base, regime="in episode", onset="2026-02", months_since_onset=4)
        out = Path(d) / "e.html"
        standdown_html(ep, 0.13, out, "Test")
        assert "no lead-time claim" in out.read_text().lower()

        # Attestation must print its grade and all three limits.
        out = Path(d) / "a.html"
        attestation_html(dict(base), out)
        t = out.read_text()
        assert "RECONSTRUCTED" in t and "not</strong> in the published" in t
        for l in LIMITS:
            assert esc(l[:40]) in t

    # Listing: pre-register onsets must be excluded, not given absurd lags,
    # and a day-precision onset must beat a month-precision one.
    with tempfile.TemporaryDirectory() as d:
        reg = Path(d) / "jwc.csv"
        with reg.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["circular_date", "action", "area_name",
                        "geographic_definition", "notice_days", "circular_ref", "notes"])
            # coverage must start before the onsets under test, or they are
            # correctly excluded as predating the register
            w.writerow(["2021-04-29", "amended", "Cabo Delgado", "", "", "JWLA-027", ""])
            w.writerow(["2023-12-18", "amended", "Southern Red Sea", "", "", "JWLA-032", ""])
            w.writerow(["2026-03-03", "added", "Bahrain", "", "", "JWLA-033", ""])
        st = listing_stats(reg)
        assert all(l["lag_days"] < 400 for l in st["lags"]), st["lags"]
        assert any(u["why"] == "predates the register" for u in st["unmeasurable"])
        got = {l["corridor"]: l for l in st["lags"]}
        assert got["Bab-el-Mandeb"]["lag_days"] == 29, got["Bab-el-Mandeb"]
        assert got["Bab-el-Mandeb"]["onset_precision"] == "day"
        assert got["Strait of Hormuz"]["lag_days"] == 3, got["Strait of Hormuz"]

    print("all checks passed")
    print(f"  band {lo:.1%}-{hi:.1%}; crowding exits at "
          f"{c['exit_adoption']['unit']:.0%} adoption (unit response), "
          f"{c['exit_adoption']['strong']:.0%} (strong)")
    print("  evidence grade keys off the record; corrections supersede;")
    print("  stand-down wording tracks the band; in-episode overrides it")
    print("  listing excludes pre-register onsets and uses day-precision dates")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    a1 = sub.add_parser("attest", help="attestation of indicator state for a month")
    a1.add_argument("--as-of", required=True)
    a1.add_argument("--source", type=Path, required=True)
    a1.add_argument("--corridor")
    a1.add_argument("--requester")
    a1.add_argument("--out", type=Path)
    a1.add_argument("--record", type=Path, default=RECORD)

    a2 = sub.add_parser("standdown", help="certificate of the month's decision")
    a2.add_argument("--as-of", required=True)
    a2.add_argument("--source", type=Path, required=True)
    a2.add_argument("--alpha", type=float, required=True,
                    help="cost of acting early as a share of crisis replacement")
    a2.add_argument("--client", default="client")
    a2.add_argument("--corridor")
    a2.add_argument("--out", type=Path)
    a2.add_argument("--record", type=Path, default=RECORD)

    a3 = sub.add_parser("crowding", help="how the position degrades as others act")
    a3.add_argument("--alpha", type=float, required=True)

    a4 = sub.add_parser("listing", help="listed-area reaction lag from the register")
    a4.add_argument("--register", type=Path, default=Path("warrisk_jwc.csv"))

    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()

    if a.cmd == "attest":
        pit = point_in_time(a.source, a.as_of, a.corridor, a.record)
        print(json.dumps(pit, indent=2))
        if a.out:
            attestation_html(pit, a.out, a.requester)
        return 0

    if a.cmd == "standdown":
        pit = point_in_time(a.source, a.as_of, a.corridor, a.record)
        standdown_html(pit, a.alpha, a.out or Path("standdown.html"), a.client)
        return 0

    if a.cmd == "crowding":
        c = crowding(a.alpha)
        lo, hi = c["band"]
        print(f"quoted cost {a.alpha:.1%};  band {lo:.1%}-{hi:.1%}\n")
        print(f"{'response':10} {'adoption':>9} {'paid':>8} {'value':>8} {'pays':>6} "
              f"{'max quotable':>13}")
        for g in c["grid"]:
            print(f"{g['response']:10} {g['adoption']:9.0%} {g['paid']:8.1%} "
                  f"{g['value']:+8.3f} {'yes' if g['pays'] else 'no':>6} "
                  f"{g['quotable_ceiling']:13.1%}")
        print("\nleaves the band at:")
        for k, v in c["exit_adoption"].items():
            print(f"  {k:8} {'never' if v is None else f'{v:.0%} adoption'}")
        return 0

    if a.cmd == "listing":
        s = listing_stats(a.register)
        print(f"{s['rows']} rows, {s['circulars']} circulars, "
              f"{s['span'][0]} to {s['span'][1]}")
        print(f"actions: " + ", ".join(f"{k} {v}" for k, v in sorted(s['by_action'].items())))
        print(f"\nonset -> next listed-area change ({s['n_matched']} measurable, "
              f"register starts {s['coverage_start']}):")
        for l in s["lags"]:
            print(f"  {l['corridor']:28} {l['onset_used']} -> {l['circular']:9} "
                  f"{l['circular_date']}  {l['lag_days']:4d} days  "
                  f"({l['onset_precision']} precision)")
        if s["unmeasurable"]:
            print(f"\nnot measurable ({len(s['unmeasurable'])}):")
            for u in s["unmeasurable"]:
                print(f"  {u['corridor']:28} {u['onset']}  {u['why']}")
        if s["median_lag_days"] is not None:
            print(f"\nmedian {s['median_lag_days']:.0f} days, "
                  f"range {s['lag_range'][0]}-{s['lag_range'][1]}")
        print(f"\n{s['caveat']}")
        return 0

    p.error("pass a subcommand or --selftest")


if __name__ == "__main__":
    raise SystemExit(main())

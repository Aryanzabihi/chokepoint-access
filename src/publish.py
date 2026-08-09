"""
publish.py — the append-only public record of every monthly reading.

The product's only defensible asset is a dated record of what was said before
the outcome was known. That is worth nothing if the file can be quietly edited
later, so each entry is hash-chained to the one before it: changing any past
entry breaks every hash after it, and --verify says so.

    python publish.py --readings readings.json      # append this month
    python publish.py --verify                      # check the chain
    python publish.py --render                      # write track-record.html
    python publish.py --selftest

Corrections are appended, never edited. Outcomes live in a separate file so
the record itself is never touched after publication.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

RECORD = Path("record.jsonl")
OUTCOMES = Path("outcomes.csv")
PAGE = Path("track-record.html")

OUTCOME_COLUMNS = ["as_of", "outcome", "onset_month", "corridor", "note", "source_ref"]
OUTCOMES_ALLOWED = {"disruption", "no disruption", "too soon"}


# --------------------------------------------------------------------------
# Chain
# --------------------------------------------------------------------------

def digest(entry: dict) -> str:
    """Hash of the entry with its own hash field excluded, keys sorted."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_record(path: Path = RECORD) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            sys.exit(f"{path}:{i} is not valid JSON — do not hand-edit this file. {e}")
    return out


def verify(path: Path = RECORD) -> tuple[bool, list[str]]:
    entries = read_record(path)
    problems: list[str] = []
    prev = "genesis"
    for i, e in enumerate(entries):
        if e.get("prev_hash") != prev:
            problems.append(f"entry {i} (as_of {e.get('as_of')}): prev_hash does not "
                            f"match the previous entry — a record before this was altered "
                            f"or removed")
        want = digest(e)
        if e.get("hash") != want:
            problems.append(f"entry {i} (as_of {e.get('as_of')}): contents changed after "
                            f"publication (hash {e.get('hash', '')[:12]} != {want[:12]})")
        if e.get("seq") != i:
            problems.append(f"entry {i}: seq is {e.get('seq')}")
        # Link to the RECOMPUTED hash, not the stored one. Otherwise altering an
        # entry's body breaks only that entry, and a chain that doesn't
        # propagate a break isn't a chain.
        prev = want
    return (not problems), problems


def append(readings: dict, path: Path = RECORD, amend_note: str | None = None,
           if_new: bool = False) -> dict | None:
    entries = read_record(path)
    ok, problems = verify(path)
    if not ok:
        for p in problems:
            print("  " + p)
        sys.exit("chain is broken — refusing to append on top of a record that has "
                 "already been altered. Restore it from version control first.")

    as_of = readings.get("as_of")
    if not as_of:
        sys.exit("readings file has no as_of")

    existing = [e for e in entries if e["as_of"] == as_of]
    if existing and if_new and amend_note is None:
        # Automation re-runs monthly; an already-published month is a no-op,
        # not an error, or every scheduled run after the first would fail.
        print(f"{as_of} already published (entry {existing[-1]['seq']}) — nothing to do")
        return None
    if existing and amend_note is None:
        sys.exit(f"{as_of} is already published (entry {existing[-1]['seq']}). The record "
                 f"is append-only: if it needs changing, pass --amend \"reason\" and a "
                 f"correction is appended instead.")

    first = readings["readings"][0] if readings.get("readings") else {}
    entry = {
        "seq": len(entries),
        "as_of": as_of,
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tar": first.get("tar"),
        "band": first.get("band"),
        "recursive_cut": readings.get("recursive_cut"),
        "alarm_now": readings.get("alarm_now"),
        "corridors_in_episode": readings.get("corridors_in_episode", []),
        "specification": readings.get("specification"),
        "amends": existing[-1]["seq"] if existing else None,
        "amend_note": amend_note,
        "prev_hash": entries[-1]["hash"] if entries else "genesis",
    }
    entry["hash"] = digest(entry)

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
    return entry


# --------------------------------------------------------------------------
# Outcomes, kept separate so the record is never edited
# --------------------------------------------------------------------------

def read_outcomes(path: Path = OUTCOMES) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        o = (r.get("outcome") or "").strip().lower()
        if o and o not in OUTCOMES_ALLOWED:
            sys.exit(f"outcome {o!r} not in {sorted(OUTCOMES_ALLOWED)}")
        out[(r.get("as_of") or "").strip()] = r
    return out


def write_outcomes_template(path: Path = OUTCOMES) -> None:
    if path.exists():
        sys.exit(f"{path} exists — refusing to overwrite recorded outcomes")
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(OUTCOME_COLUMNS)
    print(f"wrote {path} (outcome must be one of: {', '.join(sorted(OUTCOMES_ALLOWED))})")


# --------------------------------------------------------------------------
# Static page
# --------------------------------------------------------------------------

def render(path: Path = PAGE) -> None:
    entries = read_record()
    outcomes = read_outcomes()
    ok, problems = verify()

    resolved = [e for e in entries
                if (outcomes.get(e["as_of"], {}).get("outcome", "").strip().lower()
                    in {"disruption", "no disruption"})]
    hits = [e for e in resolved
            if outcomes[e["as_of"]]["outcome"].strip().lower() == "disruption"]
    alarms = [e for e in entries if e.get("alarm_now")]

    rows = []
    for e in reversed(entries):
        o = outcomes.get(e["as_of"], {})
        oc = (o.get("outcome") or "").strip() or "not yet resolved"
        ep = ", ".join(e.get("corridors_in_episode") or []) or "—"
        amend = (f' <span class="badge hot">correction to #{e["amends"]}</span>'
                 if e.get("amends") is not None else "")
        rows.append(
            f'<tr><td class="m">{e["seq"]}</td><td class="m">{esc(e["as_of"])}{amend}</td>'
            f'<td class="m">{e["tar"]}</td><td>{esc(e["band"] or "")}</td>'
            f'<td class="m">{"yes" if e.get("alarm_now") else "no"}</td>'
            f'<td>{esc(ep)}</td><td>{esc(oc)}</td>'
            f'<td class="m" style="color:var(--ink-faint);cursor:help" '
            f'title="{e["hash"]}">{e["hash"][:10]}</td></tr>')

    chain = ('<p class="ok">Chain verified: every entry hashes to the one after it.</p>'
             if ok else
             '<p class="bad">Chain broken:<br>' +
             "<br>".join(esc(p) for p in problems) + '</p>')

    rate = (f"{len(hits)}/{len(resolved)} = {len(hits)/len(resolved):.2f}"
            if resolved else "no resolved months yet")

    # A sparkline of what has actually been published. One point is not a
    # chart, so below two entries it is omitted rather than faked.
    spark = ""
    pts = [(i, e["tar"]) for i, e in enumerate(entries) if e.get("tar") is not None]
    if len(pts) >= 2:
        w, h, pad = 720, 120, 14
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        lo, hi = min(ys), max(ys)
        rng = (hi - lo) or 1.0
        X = lambda i: pad + (i - min(xs)) / max(1, (max(xs) - min(xs))) * (w - 2 * pad)
        Y = lambda v: h - pad - (v - lo) / rng * (h - 2 * pad)
        d = " ".join(("M" if k == 0 else "L") + f"{X(i):.1f} {Y(v):.1f}"
                     for k, (i, v) in enumerate(pts))
        dots = "".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="2.6" fill="#37C2A8"/>'
                       for i, v in pts)
        spark = (f'<div class="canvas" style="margin-top:var(--s4)">'
                 f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Published indicator '
                 f'value by month">'
                 f'<path d="{d}" fill="none" stroke="#37C2A8" stroke-width="4" '
                 f'stroke-opacity=".16"/>'
                 f'<path d="{d}" fill="none" stroke="#37C2A8" stroke-width="1.9"/>{dots}'
                 f'<text x="{pad}" y="{h-3}" font-family="IBM Plex Mono,monospace" '
                 f'font-size="9.5" fill="#7C97A3">{esc(entries[0]["as_of"])}</text>'
                 f'<text x="{w-pad}" y="{h-3}" text-anchor="end" '
                 f'font-family="IBM Plex Mono,monospace" font-size="9.5" fill="#7C97A3">'
                 f'{esc(entries[-1]["as_of"])}</text></svg></div>')

    path.write_text(f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><title>Track record: every war risk call, published in advance | Chokepoint Access</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Every monthly maritime war risk reading, published before
the outcome was known and tamper-proofed. Misses and false alerts shown alongside the hits.">
<meta name="robots" content="index,follow">
<meta property="og:type" content="website">
<meta property="og:title" content="Every war risk call, published in advance">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=Spectral:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="site.css">
</head><body>
<a class="skip" href="#record">Skip to the record</a>
<div class="wrap">

<nav class="nav">
  <a href="threshold-engine.html">Act or wait</a>
  <a href="map.html">Zone map</a>
  <span class="here">Track record</span>
  <span class="spacer"></span>
  <span class="stamp">generated {date.today()}</span>
</nav>

<header class="masthead">
  <div class="eyebrow">Track record &middot; published in advance, tamper-proofed</div>
  <h1>Every call we made, published before anyone knew the answer</h1>
  <p class="thesis">One month at a time, in order, with the misses and false alerts left in.
  Anyone can check that nothing here was edited after the fact &mdash; which is the only
  thing that makes a track record worth reading.</p>
</header>

<div class="status">
  <div class="cell"><div class="k">Months published</div><div class="v big">{len(entries)}</div></div>
  <div class="cell"><div class="k">Alerts raised</div><div class="v">{len(alarms)}</div></div>
  <div class="cell"><div class="k">Followed by disruption</div><div class="v">{rate}</div></div>
  <div class="cell"><div class="k">Chain</div>
    <div class="v {'ok' if ok else 'hot'}">{'verified' if ok else 'BROKEN'}</div></div>
</div>

{spark}

{'' if ok else '<p class="note hot">' + '<br>'.join(esc(p) for p in problems) + '</p>'}

<section class="panel" id="record">
  <h2>Month by month</h2>
  <div class="scroll"><table>
  <thead><tr><th class="m">#</th><th>Month</th><th class="m">Indicator</th><th>Band</th>
  <th class="m">Alarm</th><th>In episode</th><th>Outcome</th><th class="m">Hash</th></tr></thead>
  <tbody>{''.join(rows) or '<tr><td colspan="8">Nothing published yet.</td></tr>'}</tbody>
  </table></div>
  <p class="note">Months marked as already disrupted are describing a live event, not
  predicting one, and claim no warning time. Outcomes are logged separately so a published
  entry is never touched once an outcome is known &mdash; corrections are added as new,
  labelled entries.</p>
</section>

<section class="panel">
  <h2>Verify this yourself</h2>
  <p class="note">Each entry carries a fingerprint of the one before it, so changing any
  past month breaks every month after it &mdash; run the verifier over
  <code>record.jsonl</code> and it will tell you. The public commit history is a second,
  independent timestamp. Neither can be back-dated, so nobody has to take our word for
  what we said last year.</p>
</section>

<footer class="foot">
  <span>Chokepoint Access &mdash; maritime war risk timing</span>
  <a href="threshold-engine.html">Act or wait</a>
  <a href="map.html">Zone map</a>
  <span class="spacer"></span>
  <span>{len(entries)} entries &middot; chain {'ok' if ok else 'broken'}</span>
</footer>

</div></body></html>
""", encoding="utf-8")
    print(f"wrote {path}  ({len(entries)} entries, chain {'ok' if ok else 'BROKEN'})")


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def selftest() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        rec = Path(d) / "record.jsonl"

        def readings(as_of, tar, band, alarm=True, ep=None):
            return {"as_of": as_of, "recursive_cut": 1.4, "alarm_now": alarm,
                    "specification": "compound TAR", "corridors_in_episode": ep or [],
                    "readings": [{"tar": tar, "band": band}]}

        a = append(readings("2026-04-01", 2.0, "High-risk escalation"), rec)
        b = append(readings("2026-05-01", 2.2, "High-risk escalation"), rec)
        c = append(readings("2026-06-01", 2.311, "High-risk escalation",
                            ep=["Strait of Hormuz"]), rec)
        assert [e["seq"] for e in (a, b, c)] == [0, 1, 2]
        assert a["prev_hash"] == "genesis" and b["prev_hash"] == a["hash"]
        ok, probs = verify(rec)
        assert ok, probs

        # tampering with a past entry must break the chain
        lines = rec.read_text().splitlines()
        e0 = json.loads(lines[0]); e0["tar"] = 0.5
        lines[0] = json.dumps(e0, sort_keys=True, separators=(",", ":"))
        rec.write_text("\n".join(lines) + "\n")
        print("  (expected below — this is the tamper test firing on purpose)")
        ok, probs = verify(rec)
        assert not ok and len(probs) >= 2, probs
        assert any("changed after publication" in p for p in probs)
        assert any("altered" in p for p in probs), "downstream break not reported"

        # and appending on a broken chain must be refused
        rec.write_text("\n".join(lines[:1]) + "\n")   # keep the tampered entry only
        try:
            append(readings("2026-07-01", 2.4, "High-risk escalation"), rec)
        except SystemExit:
            pass
        else:
            sys.exit("appended on top of a broken chain")

        # republishing the same month is refused; an amendment is allowed
        rec.unlink()
        append(readings("2026-06-01", 2.311, "High-risk escalation"), rec)
        try:
            append(readings("2026-06-01", 2.5, "High-risk escalation"), rec)
        except SystemExit:
            pass
        else:
            sys.exit("republished a month silently")
        am = append(readings("2026-06-01", 2.5, "High-risk escalation"), rec,
                    amend_note="vintage revised by the publisher")
        assert am["amends"] == 0 and am["seq"] == 1
        assert verify(rec)[0]

    print("all checks passed")
    print("  hash chain holds, tampering detected, append on broken chain refused,")
    print("  duplicate month refused, correction appended rather than edited")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--readings", type=Path, help="readings.json from tar_ingest.py")
    p.add_argument("--amend", metavar="REASON",
                   help="append a correction for a month already published")
    p.add_argument("--if-new", action="store_true",
                   help="exit 0 without publishing if this month is already recorded")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--render", action="store_true")
    p.add_argument("--outcomes-template", action="store_true")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if a.outcomes_template:
        write_outcomes_template()
        return 0
    if a.verify:
        ok, probs = verify()
        print(f"{len(read_record())} entries")
        if ok:
            print("chain ok")
            return 0
        for pr in probs:
            print("  " + pr)
        return 1
    if a.readings:
        e = append(json.loads(a.readings.read_text(encoding="utf-8")),
                   amend_note=a.amend, if_new=a.if_new)
        if e is not None:
            print(f"published {e['as_of']}  seq {e['seq']}  TAR {e['tar']}  "
                  f"{e['band']}  hash {e['hash'][:12]}")
        render()
        return 0
    if a.render:
        render()
        return 0
    p.error("pass --readings, --verify, --render, --outcomes-template or --selftest")


if __name__ == "__main__":
    raise SystemExit(main())

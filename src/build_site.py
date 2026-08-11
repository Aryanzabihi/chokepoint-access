"""
build_site.py — inline site.css into the pages so they cannot arrive unstyled.

site.css stays the single place to edit styling. This copies it into each page so
the delivered files are self-contained: no missing-stylesheet failure, no broken
relative path, and each page opens correctly even straight from disk.

    python build_site.py            # inline into the pages
    python build_site.py --check    # verify every page carries current styles
    python build_site.py --unlink   # go back to <link> for editing

Run it after editing site.css. run_month.py calls it before publishing.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSS = HERE / "site.css"
PAGES = ["threshold-engine.html", "map.html", "track-record.html", "index.html"]

LINK = re.compile(r'[ \t]*<link rel="stylesheet" href="site\.css">\n?')
READINGS = HERE / "readings.json"
DATA_BLOCK = re.compile(
    r'[ \t]*<script type="application/json" id="readings-data"[^>]*>.*?</script>\n?', re.S)
BLOCK = re.compile(
    r'[ \t]*<style data-inlined="site\.css" data-css-hash="[0-9a-f]+">.*?</style>\n?',
    re.S)


def css_text() -> tuple[str, str]:
    if not CSS.exists():
        sys.exit(f"{CSS} not found — nothing to inline")
    t = CSS.read_text(encoding="utf-8")
    return t, hashlib.sha256(t.encode()).hexdigest()[:12]


def inline_data(path: Path) -> str | None:
    """Bake readings.json into the page.

    A page that fetches its data at runtime is one deployment mistake away from
    showing nothing, and it cannot work from disk at all. Baking the readings in
    means the page is correct the moment it is written; the runtime fetch stays
    as a refresh path, not a dependency.
    """
    if not READINGS.exists():
        return None
    raw = READINGS.read_text(encoding="utf-8")
    # </script> inside JSON would end the block early; none should occur, but a
    # silent broken page is not worth the risk.
    raw = raw.replace("</", "<\\/")
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    s = path.read_text(encoding="utf-8")
    s = DATA_BLOCK.sub("", s)
    block = (f'<script type="application/json" id="readings-data" data-hash="{h}">\n'
             f'{raw}\n</script>\n')
    if "</head>" not in s:
        return None
    s = s.replace("</head>", block + "</head>", 1)
    path.write_text(s, encoding="utf-8")
    return h


def inline(path: Path, css: str, h: str) -> str:
    s = path.read_text(encoding="utf-8")
    had = bool(LINK.search(s)) or bool(BLOCK.search(s))
    s = BLOCK.sub("", s)
    block = f'<style data-inlined="site.css" data-css-hash="{h}">\n{css}</style>\n'

    if LINK.search(s):
        # re.sub treats a *string* replacement's backslash-digit sequences
        # (\25, \a0, ...) as group backreferences -- and CSS unicode escapes
        # look exactly like that (see the disclosure-triangle content: "\25b8"
        # rules in site.css). A function replacement is inserted literally,
        # with no backslash processing, so this is the only safe form here.
        s = LINK.sub(lambda _m: block, s, count=1)
    elif "</head>" in s:
        s = s.replace("</head>", block + "</head>", 1)
    else:
        sys.exit(f"{path.name}: no <link> to site.css and no </head> to insert before")

    # A second link would re-introduce the dependency this exists to remove.
    s = LINK.sub("", s)
    if not had:
        print(f"  {path.name}: had no stylesheet reference — inserted one")
    path.write_text(s, encoding="utf-8")
    return s


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true")
    p.add_argument("--unlink", action="store_true")
    a = p.parse_args()

    css, h = css_text()
    pages = [HERE / n for n in PAGES if (HERE / n).exists()]
    if not pages:
        sys.exit("no pages found next to this script")

    if a.check:
        bad = []
        for path in pages:
            s = path.read_text(encoding="utf-8")
            m = re.search(r'data-css-hash="([0-9a-f]+)"', s)
            if not m:
                bad.append(f"{path.name}: styles not inlined")
            elif m.group(1) != h:
                bad.append(f"{path.name}: inlined styles are stale "
                           f"({m.group(1)} vs {h}) — re-run build_site.py")
            if path.name in ("threshold-engine.html", "map.html") and READINGS.exists():
                want = hashlib.sha256(READINGS.read_text(encoding="utf-8")
                                      .replace("</", "<\\/").encode()).hexdigest()[:12]
                dm = re.search(r'id="readings-data" data-hash="([0-9a-f]+)"', s)
                if not dm:
                    bad.append(f"{path.name}: no readings baked in")
                elif dm.group(1) != want:
                    bad.append(f"{path.name}: baked readings are stale — re-run build_site.py")
        for b in bad:
            print("  " + b)
        print(f"{len(pages) - len(bad)}/{len(pages)} pages carry current styles")
        return 1 if bad else 0

    if a.unlink:
        for path in pages:
            s = BLOCK.sub('<link rel="stylesheet" href="site.css">\n',
                          path.read_text(encoding="utf-8"), count=1)
            s = DATA_BLOCK.sub("", s)
            path.write_text(s, encoding="utf-8")
            print(f"  {path.name}: back to <link>, baked data removed")
        return 0

    for path in pages:
        s = inline(path, css, h)
        dh = inline_data(path) if path.name in ("threshold-engine.html", "map.html") else None
        size = path.stat().st_size / 1024
        print(f"  {path.name}: {size:.0f} KB, styles {h}"
              + (f", data {dh}" if dh else ""))
    if not READINGS.exists():
        print(f"  !! {READINGS.name} not found — pages will have no data baked in. "
              f"Run tar_ingest.py first.")
    print(f"inlined site.css ({len(css) / 1024:.0f} KB) into {len(pages)} page(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
vconfig.py — shared configuration for the TAR corridor-validation build.

Drop this whole `validation/` folder inside your project root (tar_phase1/).
Everything writes under validation/data/ and validation/build/ so nothing
touches your existing data/, output/ or final_output/ trees.

IMPORTANT PROVENANCE NOTE (carried over from the last audit):
  output/tar_monthly.csv is the SUPERSEDED phase-1 build.
  The manuscript's corrected primary series lives in
  final_output/panel_final.csv::tar and is GLOBAL — constant across units.
  This validation build does NOT reuse that column. It constructs a genuinely
  corridor-attributed series from scratch. The old series is used only as a
  benchmark to beat in the validation stage.
"""

from __future__ import annotations
import os
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- paths
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # project root (tar_phase1)

DATA = os.path.join(HERE, "data")                 # raw downloads
CACHE = os.path.join(DATA, "gdelt_cache")         # per-file filtered GDELT
BUILD = os.path.join(HERE, "build")               # derived panels
REPORT = os.path.join(HERE, "report")             # audit txt/figures

for _d in (DATA, CACHE, BUILD, REPORT):
    os.makedirs(_d, exist_ok=True)

CORRIDOR_FILE = os.path.join(HERE, "corridors.csv")

# ---------------------------------------------------------------- windows
GDELT_START = "1979-01-01"
GDELT_END   = None          # None = through the latest available file
PORTWATCH_START = "2019-01-01"

# ---------------------------------------------------------------- CAMEO sets
# GDELT codes actions with CAMEO. The TAR numerator is coercive *speech*,
# the denominator is coercive *action*. Three nested definitions so the
# threat/act boundary can be treated as a robustness dimension rather than
# an assumption.
#
#   13 Threaten            18 Assault
#   15 Exhibit force       19 Fight
#   17 Coerce              20 Unconventional mass violence
#   12 Reject   14 Protest 16 Reduce relations
CAMEO_SETS = {
    "narrow":   {"threat": ["13"],                     "act": ["19", "20"]},
    "baseline": {"threat": ["13", "15"],               "act": ["18", "19", "20"]},
    "wide":     {"threat": ["12", "13", "14", "15"],   "act": ["17", "18", "19", "20"]},
}
PRIMARY_CAMEO_SET = "baseline"

# Root codes to retain when filtering the raw GDELT files. Superset of every
# definition above, so the cache never has to be rebuilt to change spec.
KEEP_ROOT_CODES = ["12", "13", "14", "15", "16", "17", "18", "19", "20"]

# ---------------------------------------------------------------- TAR specs
# Matches the manuscript's four specifications. Primary is the
# magnitude-weighted bounded share (share x z), which was the only velocity
# spec to survive in both samples.
TAR_SPECS = ("ratio", "share", "logratio", "share_z")
PRIMARY_TAR_SPEC = "share_z"

# Normalisation for the z component. 'expanding' has no look-ahead but broke on
# structural breaks in the 1940s-era extension; 'rolling120' was the fix.
# Window and warm-up are sized to the sample, not inherited from the 1979
# design. The realised sample is 92 months (2019-01 to 2026-08); a 120-month
# window with a 36-month warm-up would leave the primary series undefined
# until late 2021 and throw away the entire pre-Ukraine period. 48/18 keeps
# causality (only months <= t) while making the series usable from ~2020-07.
NORMALISATION = "rolling"        # {"expanding", "rolling"}
ROLL_WINDOW_MONTHS = 48
MIN_WARMUP_MONTHS = 18

# ---------------------------------------------------------------- events
# Eight headline onsets, Table 3 of the v5 manuscript. Single source of truth.
ONSETS = [
    ("tanker_war",   "1987-07-01", "hormuz",        2, "shock, chokepoint"),
    ("gulf_war",     "1990-08-01", "hormuz",        2, "shock, chokepoint"),
    ("bosnia",       "1992-04-01", "adriatic",      3, "escalation, no chokepoint"),
    ("kosovo",       "1999-03-01", "adriatic",      4, "shock, no chokepoint"),
    ("iraq_war",     "2003-03-01", "hormuz",        1, "escalation, chokepoint"),
    ("ukraine",      "2022-02-01", "black_sea",     3, "escalation, no chokepoint"),
    ("red_sea",      "2023-11-01", "bab_el_mandeb", 1, "escalation, chokepoint"),
    ("hormuz_2026",  "2026-02-01", "hormuz",        1, "escalation, chokepoint"),
]
REVERSALS = [
    ("mh17",          "2014-07-01", "black_sea"),
    ("gulf_tankers",  "2019-06-01", "hormuz"),
    ("soleimani",     "2020-01-01", "hormuz"),
    ("taiwan_2022",   "2022-08-01", "taiwan_strait"),
]
PLACEBOS = [
    ("tianjin",           "2015-08-01", None),
    ("hanjin",            "2016-08-01", None),
    ("covid_demand",      "2020-03-01", None),
    ("ever_given",        "2021-03-01", "suez"),
    ("covid_congestion",  "2021-09-01", None),
    ("panama_drought",    "2023-08-01", "panama"),
]

# ---------------------------------------------------------------- corridors
def load_corridors(panel_only: bool = False) -> pd.DataFrame:
    """Read corridors.csv and parse geometry / code lists into usable objects."""
    df = pd.read_csv(CORRIDOR_FILE)
    if panel_only:
        df = df[df["manuscript_panel_unit"] == 1].copy()

    def _pts(s):
        out = []
        for p in str(s).split(";"):
            lat, lon = p.split(":")
            out.append((float(lat), float(lon)))
        return out

    def _codes(s):
        if pd.isna(s) or not str(s).strip():
            return []
        return [c.strip() for c in str(s).split("|") if c.strip()]

    df["points"] = df["polyline"].map(_pts)
    df["littoral"] = df["littoral_fips"].map(_codes)
    df["actors"] = df["actor_cameo"].map(_codes)
    return df.reset_index(drop=True)


# ---------------------------------------------------------------- geometry
EARTH_R = 6371.0088


def _to_xyz(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    cl = np.cos(la)
    return np.stack([cl * np.cos(lo), cl * np.sin(lo), np.sin(la)], axis=-1)


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    d = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_R * np.arcsin(np.sqrt(np.clip(d, 0, 1)))


def dist_to_polyline_km(lat, lon, points):
    """
    Minimum great-circle distance from each (lat, lon) to a polyline given as
    a list of (lat, lon) vertices. Segment distance is computed in 3-D chord
    space and converted back, which is accurate to well under a kilometre at
    these scales and vectorises cleanly over millions of points.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    P = _to_xyz(lat, lon)                                  # (n, 3)

    if len(points) == 1:
        return haversine_km(lat, lon, points[0][0], points[0][1])

    best = np.full(P.shape[0], np.inf)
    for (a_lat, a_lon), (b_lat, b_lon) in zip(points[:-1], points[1:]):
        A = _to_xyz(np.array([a_lat]), np.array([a_lon]))[0]
        B = _to_xyz(np.array([b_lat]), np.array([b_lon]))[0]
        AB = B - A
        denom = float(AB @ AB)
        if denom == 0:
            t = np.zeros(P.shape[0])
        else:
            t = np.clip(((P - A) @ AB) / denom, 0.0, 1.0)
        C = A + t[:, None] * AB                            # closest chord point
        n = np.linalg.norm(C, axis=1, keepdims=True)
        n[n == 0] = 1.0
        C = C / n                                          # project to sphere
        dot = np.clip(np.sum(P * C, axis=1), -1.0, 1.0)
        best = np.minimum(best, EARTH_R * np.arccos(dot))
    return best


# ---------------------------------------------------------------- misc
def log(msg: str):
    import datetime as _dt
    print(f"[{_dt.datetime.now():%H:%M:%S}] {msg}", flush=True)

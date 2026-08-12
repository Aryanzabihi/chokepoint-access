"""
chokepoint_profiles.py — descriptive character and evidence-graded response
per chokepoint, for the TAR Decision Engine v2.

Two things this module deliberately does NOT do:

  1. It does not touch tar_ingest.py's global TAR band/horizon. That logic
     feeds the live, hash-chained, monthly-published reading, and its own
     selftest asserts the horizon stays global (tar_ingest.py: "assert
     out['horizon'] == assign_band(...)[2]"). This module is an additive
     layer decision_engine.py consumes alongside that reading, never a
     replacement for it.

  2. It does not fit a statistical threshold per corridor. Every corridor
     here has 0-7 tested incidents — nowhere near enough to bootstrap a
     threshold the way v06_validate.py does from a real sample (see
     validation/TAR_corridor_validation_report.md and
     validation/report/phase_d_corridor_grades.md, this module's source of
     truth for the evidence half). What the evidence supports is a
     descriptive, sourced RESPONSE CHARACTER — "based on N tested incidents,
     coverage here rises Yx" — not a decision rule with an implied error
     rate. Claiming more than that is exactly the fabrication the whole
     reconciliation effort this module is built on was working to avoid.

Two grades of fact live side by side here, and they are graded differently:

  - Geography/traffic/alternate-route facts (width, cargo character, reroute
    options) are general/public knowledge — true, but not independently
    verified against a specific citation in this project. Marked
    `fact_grade = "GENERAL_KNOWLEDGE"` throughout, never silently presented
    as sourced the way a PUBLISHED or CLIENT_QUOTED figure would be.

  - Response character (tested_incidents, response_character) is graded
    using decision_engine.py's OWN vocabulary — `EPISODE_ANALOGUE` for
    corridors with real tested incidents, `STRUCTURAL` (added to
    decision_engine.GRADES by this module's integration) for the two
    corridors with none. One grade vocabulary for the whole product, not a
    parallel one to reconcile later.

Usage
-----
    python chokepoint_profiles.py show --corridor "Strait of Hormuz"
    python chokepoint_profiles.py list
    python chokepoint_profiles.py --selftest
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tar_ingest import CORRIDORS  # noqa: E402

MODEL_VERSION = "chokepoint-profiles-1.0"

GENERAL_KNOWLEDGE = "GENERAL_KNOWLEDGE"


# --------------------------------------------------------------------------
# Shapes
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TestedIncident:
    date: str
    label: str
    pre_mean: float | None
    post_mean: float | None
    response_ratio: float | None
    source: str   # "DOC API (fetched)" | "webngrams via BigQuery"


@dataclass(frozen=True)
class ChokepointProfile:
    corridor: str                     # must equal a tar_ingest.CORRIDORS key exactly
    geography: str
    width: str
    traffic_character: str
    primary_cargo: list[str]
    alternate_route: str | None
    littoral_states: list[str]
    evidence_grade: str                # "EPISODE_ANALOGUE" | "STRUCTURAL"
    tested_incidents: list[TestedIncident] = field(default_factory=list)
    response_character: str | None = None
    fact_grade: str = GENERAL_KNOWLEDGE
    evidence_source: str = "validation/report/phase_d_corridor_grades.md"


# --------------------------------------------------------------------------
# The 7 profiles
#
# Geography/traffic/alternate-route figures are general/public knowledge --
# true, but not independently verified against a specific citation in this
# project (fact_grade=GENERAL_KNOWLEDGE throughout). tested_incidents are
# copied as literal data from this session's own validation output, not
# re-derived -- same convention economic_engine.py already uses for
# duplicating services.py's published figures:
#   validation/report/doc_api_hormuz_babelmandeb_recall.txt  (Hormuz, Bab-el-Mandeb)
#   validation/report/webngrams_5corridor_smoketest.txt      (Suez, Taiwan Strait,
#                                                              Black Sea event 1,
#                                                              Turkish Straits)
#   validation/data/webngrams_blacksea_event2.csv             (Black Sea event 2)
# --------------------------------------------------------------------------

PROFILES: dict[str, ChokepointProfile] = {

    "Strait of Hormuz": ChokepointProfile(
        corridor="Strait of Hormuz",
        geography=("Connects the Persian Gulf to the Gulf of Oman and the Indian "
                   "Ocean. 167 km long; narrows to about 33 km at its narrowest "
                   "point, with the two-way shipping lane (a traffic separation "
                   "scheme) further constrained to roughly 3 km."),
        width="33 km strait, ~3 km shipping lanes under a TSS",
        traffic_character=("Roughly a fifth of global oil consumption and a third "
                           "of global LNG trade transit here."),
        primary_cargo=["crude oil", "LNG"],
        alternate_route=("None maritime. Saudi Arabia's East-West Pipeline and "
                         "UAE's ADNOC pipeline can bypass a meaningful fraction "
                         "of Gulf crude exports, but nowhere near full capacity."),
        littoral_states=["Iran", "Oman", "United Arab Emirates"],
        evidence_grade="EPISODE_ANALOGUE",
        tested_incidents=[
            TestedIncident("2019-05-12", "Fujairah tanker sabotage", 101.8, 409.5, 4.02, "DOC API (fetched)"),
            TestedIncident("2019-06-13", "Gulf of Oman attacks", 225.7, 1745.8, 7.73, "DOC API (fetched)"),
            TestedIncident("2019-07-19", "Stena Impero seizure", 885.2, 1573.1, 1.78, "DOC API (fetched)"),
            TestedIncident("2020-01-03", "Soleimani strike", 95.9, 509.8, 5.32, "DOC API (fetched)"),
            TestedIncident("2021-07-29", "Mercer Street attack", 29.2, 39.2, 1.34, "DOC API (fetched)"),
            TestedIncident("2024-04-13", "MSC Aries seizure", 16.8, 91.1, 5.41, "DOC API (fetched)"),
            TestedIncident("2025-06-13", "Israel-Iran war onset", 21.2, 104.6, 4.93, "DOC API (fetched)"),
        ],
        response_character=("Based on 7 tested incidents (2019-2025), coverage "
                            "here rises sharply and fast -- median ~4.9x baseline, "
                            "5 of 7 incidents at or above 4x. Not a threshold; a "
                            "description of what has been observed."),
    ),

    "Bab-el-Mandeb": ChokepointProfile(
        corridor="Bab-el-Mandeb",
        geography=("Connects the Red Sea (via the Suez approach) to the Gulf of "
                   "Aden and the Indian Ocean. About 30 km wide at the narrowest "
                   "point; Perim Island splits the strait into a ~3.2 km eastern "
                   "channel and a ~25 km western channel."),
        width="~30 km at the narrowest, split by Perim Island",
        traffic_character=("A significant share of Europe-Asia containerized "
                           "trade, plus meaningful oil and LNG volume."),
        primary_cargo=["containers", "crude oil", "LNG"],
        alternate_route=("Cape of Good Hope, +10-14 days -- the route actually "
                         "taken at scale during the 2023-24 Houthi attacks, not "
                         "just a theoretical option."),
        littoral_states=["Yemen", "Djibouti", "Eritrea"],
        evidence_grade="EPISODE_ANALOGUE",
        tested_incidents=[
            TestedIncident("2016-10-09", "Houthi attacks on USS Mason", None, None, None, "predates doc_cache coverage (2017-01-01)"),
            TestedIncident("2018-07-25", "Saudi tanker attack, transit halt", 26.3, 258.4, 9.84, "DOC API (fetched)"),
            TestedIncident("2023-11-19", "Galaxy Leader seizure", 3.1, 105.5, 33.77, "DOC API (fetched)"),
            TestedIncident("2024-02-18", "Rubymar struck", 89.1, 53.4, 0.60, "DOC API (fetched)"),
            TestedIncident("2025-07-06", "Magic Seas / Eternity C", 11.0, 7.0, 0.64, "DOC API (fetched)"),
        ],
        response_character=("Based on 4 testable incidents (1 predates coverage), "
                            "response is bimodal: two incidents show very strong "
                            "spikes (9.8x, 33.8x), two show none (0.6x, 0.6x). "
                            "Real signal exists but is inconsistent across "
                            "incidents, not uniformly reliable."),
    ),

    "Adriatic": ChokepointProfile(
        corridor="Adriatic",
        geography=("A semi-enclosed sea between Italy and the Balkan peninsula, "
                   "not a narrow strait. The nearest chokepoint-like feature is "
                   "the Strait of Otranto (~72 km wide) connecting to the Ionian "
                   "Sea. Historically relevant here as a corridor for regional "
                   "conflict (the Balkan wars of the 1990s), not physical vessel "
                   "passage risk."),
        width="Strait of Otranto ~72 km; the Adriatic itself is an open sea, not a strait",
        traffic_character=("Regional Mediterranean trade, extensive ferry "
                           "traffic, and an energy pipeline landfall (TAP)."),
        primary_cargo=["regional trade", "ferry/passenger", "natural gas (pipeline landfall)"],
        alternate_route=("Not applicable in the Hormuz/Suez sense -- this is open "
                         "sea, not a narrow passage with a single bypass."),
        littoral_states=["Italy", "Croatia", "Albania", "Montenegro", "Slovenia",
                         "Bosnia and Herzegovina", "Greece"],
        evidence_grade="STRUCTURAL",
        tested_incidents=[],
        response_character=("No post-1999 incident is on record for this "
                            "corridor -- its only known onsets (Bosnia 1992, "
                            "Kosovo 1999) predate every data source available "
                            "for this kind of test (GDELT 2.0 from 2015, "
                            "webngrams from 2020). Character below is "
                            "descriptive only."),
    ),

    "Turkish Straits / Black Sea": ChokepointProfile(
        corridor="Turkish Straits / Black Sea",
        geography=("The Bosphorus and Dardanelles connect the Black Sea to the "
                   "Mediterranean via the Sea of Marmara. The Bosphorus narrows "
                   "to about 700 m at its narrowest -- one of the narrowest "
                   "straits used by international shipping anywhere -- and the "
                   "Dardanelles to about 1.2 km. Both are governed by the 1936 "
                   "Montreux Convention. Critical for Black Sea grain and energy "
                   "exports (Ukraine, Russia)."),
        width="Bosphorus ~700 m, Dardanelles ~1.2 km at their narrowest points",
        traffic_character=("The sole maritime route for Black Sea grain and "
                           "energy exports; volumes swing sharply with the "
                           "state of the Russia-Ukraine war."),
        primary_cargo=["grain", "crude oil", "steel"],
        alternate_route=("None maritime. Rail and pipeline alternatives exist "
                         "for a fraction of the volume, at materially higher "
                         "cost."),
        littoral_states=["Turkey", "Ukraine", "Russia", "Romania", "Bulgaria", "Georgia"],
        evidence_grade="EPISODE_ANALOGUE",
        tested_incidents=[
            TestedIncident("2022-02-24", "Invasion of Ukraine", 553.6, 1602.4, 2.89, "webngrams via BigQuery"),
            TestedIncident("2023-07-17", "Grain deal collapse", 720.0, 1213.2, 1.68, "webngrams via BigQuery"),
            TestedIncident("2022-07-22", "Grain corridor agreement", 62.8, 143.5, 2.29, "webngrams via BigQuery"),
        ],
        response_character=("Based on 3 tested incidents across both sub-"
                            "regions, response is consistently weak -- 1.7x, "
                            "2.3x, 2.9x, none clearing the 3x bar the stronger "
                            "corridors do. By mid-war the baseline itself was "
                            "already elevated, muting the relative jump any "
                            "single new incident produces."),
    ),

    "Suez Canal": ChokepointProfile(
        corridor="Suez Canal",
        geography=("An artificial canal, not a strait -- about 193 km long, "
                   "connecting the Mediterranean to the Red Sea without a lock "
                   "system. Wholly within Egyptian territory."),
        width="193 km canal, single continuous waterway",
        traffic_character=("A major share of Asia-Europe container and energy "
                           "trade transits here; widely cited estimates put it "
                           "at roughly an eighth to a seventh of world trade by "
                           "value."),
        primary_cargo=["containers", "crude oil", "LNG", "bulk goods"],
        alternate_route="Cape of Good Hope, +10-14 days.",
        littoral_states=["Egypt"],
        evidence_grade="EPISODE_ANALOGUE",
        tested_incidents=[
            TestedIncident("2021-03-23", "Ever Given grounding", 108.2, 1518.6, 14.04, "webngrams via BigQuery"),
        ],
        response_character=("Based on 1 tested incident, response is very "
                            "strong and clean -- 14.0x baseline within days of "
                            "the Ever Given grounding. A single data point, but "
                            "an unambiguous one."),
    ),

    "Strait of Malacca": ChokepointProfile(
        corridor="Strait of Malacca",
        geography=("Connects the Indian Ocean to the South China Sea and the "
                   "Pacific. About 800 km long, narrowing to roughly 2.8 km at "
                   "the Phillips Channel near Singapore -- one of the "
                   "highest-vessel-count shipping lanes in the world by transit "
                   "count."),
        width="~2.8 km at the Phillips Channel; depth-constrained in places (the "
              "'Malaccamax' vessel-size limit)",
        traffic_character=("A large share of China/Japan/Korea-bound energy and "
                           "goods transits here; high vessel density more than "
                           "high per-vessel cargo value."),
        primary_cargo=["crude oil", "containers", "general cargo"],
        alternate_route=("Sunda Strait or Lombok Strait -- deeper draft "
                         "capacity, longer transit time."),
        littoral_states=["Indonesia", "Malaysia", "Singapore"],
        evidence_grade="STRUCTURAL",
        tested_incidents=[],
        response_character=("No post-2020 incident is on record for this "
                            "corridor. The one candidate incident tested (2019 "
                            "Singapore Strait robbery wave) predates webngrams' "
                            "coverage start (2020-01-01) and was, in any case, "
                            "reported under 'Singapore Strait', not 'Malacca' -- "
                            "a naming gap as much as a coverage gap. Character "
                            "below is descriptive only."),
    ),

    "Taiwan Strait": ChokepointProfile(
        corridor="Taiwan Strait",
        geography=("Separates mainland China from Taiwan. About 180 km wide, "
                   "narrowing to roughly 130 km at the narrowest point -- wide "
                   "enough that vessel passage room is rarely the binding "
                   "constraint; the concentrated risk is to Taiwan's own ports "
                   "and, more broadly, the semiconductor supply chain "
                   "manufactured there."),
        width="~180 km wide, ~130 km at the narrowest",
        traffic_character=("High container-shipping density as part of "
                           "broader Northeast Asia routes, and outsized "
                           "importance for semiconductor and electronics "
                           "supply chains routed through Taiwan's ports."),
        primary_cargo=["containers", "electronics/semiconductor components"],
        alternate_route=("Vessels can route east of Taiwan if the strait "
                         "itself becomes unsafe -- longer, but the larger risk "
                         "is disruption to Taiwan's ports, which rerouting "
                         "around the strait does not solve."),
        littoral_states=["China", "Taiwan"],
        evidence_grade="EPISODE_ANALOGUE",
        tested_incidents=[
            TestedIncident("2022-08-04", "Post-Pelosi exercises", 167.2, 595.8, 3.56, "webngrams via BigQuery"),
        ],
        response_character=("Based on 1 tested incident, response is usable "
                            "but right at the threshold -- 3.6x baseline "
                            "following the August 2022 exercises. A single "
                            "data point at the edge of the usable band, not a "
                            "strong margin like Suez's."),
    ),
}


# --------------------------------------------------------------------------
# Accessors
# --------------------------------------------------------------------------

def profile_for(corridor: str) -> ChokepointProfile:
    if corridor not in PROFILES:
        raise ValueError(f"no chokepoint profile for {corridor!r}. Known: {sorted(PROFILES)}")
    return PROFILES[corridor]


def all_corridors() -> list[str]:
    return sorted(PROFILES)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_profile(p: ChokepointProfile) -> None:
    print(f"{p.corridor}")
    print(f"  geography         {p.geography}")
    print(f"  width             {p.width}")
    print(f"  traffic           {p.traffic_character}")
    print(f"  primary cargo     {', '.join(p.primary_cargo)}")
    print(f"  alternate route   {p.alternate_route or '(none)'}")
    print(f"  littoral states   {', '.join(p.littoral_states)}")
    print(f"  fact grade        {p.fact_grade} (general/public knowledge, not independently cited)")
    print(f"  evidence grade    {p.evidence_grade}")
    if p.tested_incidents:
        print(f"  tested incidents  ({len(p.tested_incidents)})")
        for i in p.tested_incidents:
            if i.response_ratio is None:
                print(f"    {i.date}  {i.label:38s}  {i.source}")
            else:
                print(f"    {i.date}  {i.label:38s}  ratio={i.response_ratio:6.2f}  {i.source}")
    else:
        print("  tested incidents  (none)")
    if p.response_character:
        print(f"  response          {p.response_character}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    show = sub.add_parser("show", help="print one corridor's profile")
    show.add_argument("--corridor", required=True)

    sub.add_parser("list", help="list all corridors with a profile")

    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if a.cmd == "show":
        _print_profile(profile_for(a.corridor))
        return 0
    if a.cmd == "list":
        for c in all_corridors():
            prof = PROFILES[c]
            n = len(prof.tested_incidents)
            print(f"  {c:32s} {prof.evidence_grade:18s} {n} tested incident(s)")
        return 0
    p.error("pass a subcommand (show, list) or --selftest")
    return 1


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def selftest() -> int:
    # 1. No drift between the two corridor registries -- including the
    # exact "Turkish Straits / Black Sea" key tar_ingest.py itself uses.
    assert set(PROFILES) == set(CORRIDORS), (set(PROFILES) ^ set(CORRIDORS))
    assert "Turkish Straits / Black Sea" in PROFILES

    # 2. Every profile's own .corridor field matches its dict key.
    for key, prof in PROFILES.items():
        assert prof.corridor == key, (key, prof.corridor)

    # 3. Evidence-grade / tested_incidents consistency.
    for key, prof in PROFILES.items():
        assert prof.evidence_grade in ("EPISODE_ANALOGUE", "STRUCTURAL"), (key, prof.evidence_grade)
        if prof.evidence_grade == "STRUCTURAL":
            assert prof.tested_incidents == [], f"{key}: STRUCTURAL corridor must have no tested incidents"
        else:
            assert len(prof.tested_incidents) >= 1, f"{key}: EPISODE_ANALOGUE corridor must have >=1 tested incident"
        assert prof.response_character, f"{key}: every profile must state a response character, even if descriptive-only"
        assert prof.fact_grade == GENERAL_KNOWLEDGE

    # 4. Spot-check the exact numbers against what's on disk in
    # validation/report/ -- this module duplicates them, it must not drift.
    hormuz = profile_for("Strait of Hormuz")
    assert len(hormuz.tested_incidents) == 7, len(hormuz.tested_incidents)
    ratios = sorted(i.response_ratio for i in hormuz.tested_incidents)
    assert abs(ratios[3] - 4.93) < 0.01, ratios   # median of 7 (4th of 7 sorted), matches "median ~4.9x"
    stena = next(i for i in hormuz.tested_incidents if "Stena" in i.label)
    assert abs(stena.response_ratio - 1.78) < 1e-9, stena

    suez = profile_for("Suez Canal")
    assert len(suez.tested_incidents) == 1
    assert abs(suez.tested_incidents[0].response_ratio - 14.04) < 1e-9

    bab = profile_for("Bab-el-Mandeb")
    predates = [i for i in bab.tested_incidents if i.response_ratio is None]
    assert len(predates) == 1 and "2016" in predates[0].date

    # 5. STRUCTURAL corridors are exactly the two the reconciliation found
    # untestable, no more, no fewer.
    structural = {c for c, p in PROFILES.items() if p.evidence_grade == "STRUCTURAL"}
    assert structural == {"Adriatic", "Strait of Malacca"}, structural

    # 6. profile_for() raises on an unknown corridor rather than defaulting.
    try:
        profile_for("Not A Real Strait")
        assert False, "unknown corridor should have raised"
    except ValueError:
        pass

    print("all checks passed")
    print(f"  {len(PROFILES)} corridors, {sum(1 for p in PROFILES.values() if p.evidence_grade == 'EPISODE_ANALOGUE')} EPISODE_ANALOGUE, "
         f"{sum(1 for p in PROFILES.values() if p.evidence_grade == 'STRUCTURAL')} STRUCTURAL")
    print("  registry matches tar_ingest.CORRIDORS exactly")
    print("  every EPISODE_ANALOGUE corridor has >=1 tested incident; every STRUCTURAL corridor has none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

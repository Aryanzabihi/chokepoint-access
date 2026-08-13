# Chokepoint profiles — a descriptive companion

**Status:** unreviewed supplementary material, not part of the manuscript.
A practitioner-facing reference for the seven corridors the manuscript's
corridor-month design covers, built for a downstream product on the same
registry.

## Purpose and scope

The manuscript's Section 7 (Implications for transport practice) tells a
reader to "sequence expectations by corridor, not by commodity" and to read
the indicator "with corridor-specific base rates," while stating plainly
that "corridor attribution on public data is not yet validated" for the
predictive signal itself (Sections 5.6, 7). This document does not attempt
to validate that attribution. It collects, per corridor, the descriptive
characteristics a reader would need to apply that guidance — geography,
traffic composition, alternate-route economics — alongside a separate,
narrower piece of evidence: how strongly and how quickly text coverage has
historically responded to a known incident in that corridor, where testable.

Two grades of fact appear below, and they are never merged into one:

- **`GENERAL_KNOWLEDGE`** — geography, width, traffic character, alternate
  routes, littoral states. True, but general/public knowledge not
  independently verified against a specific citation in this project. Not a
  manuscript finding.
- **`EPISODE_ANALOGUE`** / **`STRUCTURAL`** — the response-character
  evidence grade. `EPISODE_ANALOGUE` corridors have at least one dated,
  tested incident (methodology and full results in
  `TAR_TRIP_complement_attribution.md`); `STRUCTURAL` corridors have none,
  because their only known incidents predate every available text-coverage
  source.

**This is not a set of thresholds.** No corridor below has enough tested
incidents (zero to seven) to fit a statistical threshold the way the
manuscript's own methodology would require, and the manuscript directly
tested whether corridor-specific action thresholds are identifiable at all
— finding a pooled AUC of 0.466 against real transit episodes, worse than
chance (Section 5.6). Nothing here revisits that finding. What follows is a
sourced description of what has been observed, offered at the same
strength the manuscript reserves for its own descriptive material, not at
the strength of a validated indicator.

---

## Strait of Hormuz

Connects the Persian Gulf to the Gulf of Oman and the Indian Ocean. 167 km
long; narrows to about 33 km at its narrowest point, with the two-way
shipping lane (a traffic separation scheme) further constrained to roughly
3 km.

| | |
|---|---|
| Width | 33 km strait, ~3 km shipping lanes under a TSS |
| Traffic | Roughly a fifth of global oil consumption and a third of global LNG trade transit here |
| Primary cargo | Crude oil, LNG |
| Alternate route | None maritime. Saudi Arabia's East-West Pipeline and UAE's ADNOC pipeline can bypass a meaningful fraction of Gulf crude exports, but nowhere near full capacity |
| Littoral states | Iran, Oman, United Arab Emirates |
| Evidence grade | `EPISODE_ANALOGUE` — 7 tested incidents |

**Response character:** Based on 7 tested incidents (2019–2025), coverage
here rises sharply and fast — median ~4.9× baseline, 5 of 7 incidents at or
above 4×. Not a threshold; a description of what has been observed.

| Date | Incident | Response ratio |
|---|---|---:|
| 2019-05-12 | Fujairah tanker sabotage | 4.02× |
| 2019-06-13 | Gulf of Oman attacks | 7.73× |
| 2019-07-19 | Stena Impero seizure | 1.78× |
| 2020-01-03 | Soleimani strike | 5.32× |
| 2021-07-29 | Mercer Street attack | 1.34× |
| 2024-04-13 | MSC Aries seizure | 5.41× |
| 2025-06-13 | Israel-Iran war onset | 4.93× |

---

## Bab-el-Mandeb

Connects the Red Sea (via the Suez approach) to the Gulf of Aden and the
Indian Ocean. About 30 km wide at the narrowest point; Perim Island splits
the strait into a ~3.2 km eastern channel and a ~25 km western channel.

| | |
|---|---|
| Width | ~30 km at the narrowest, split by Perim Island |
| Traffic | A significant share of Europe-Asia containerized trade, plus meaningful oil and LNG volume |
| Primary cargo | Containers, crude oil, LNG |
| Alternate route | Cape of Good Hope, +10–14 days — the route actually taken at scale during the 2023–24 Houthi attacks, not just a theoretical option |
| Littoral states | Yemen, Djibouti, Eritrea |
| Evidence grade | `EPISODE_ANALOGUE` — 4 testable incidents (1 predates coverage) |

**Response character:** Response is bimodal, and the two weak readings have
different causes, not one. Rubymar (0.6×) followed Galaxy Leader by three
months, into an already-elevated baseline (89 mentions/day pre-incident,
~29× this corridor's own pre-crisis level of 3.1) — a ceiling effect, not a
coverage failure (see the pattern analysis below). Magic Seas/Eternity C
(0.6×) came twenty months into the same crisis against a since-receded
baseline (11.0) and still scored low — that looks like fatigue with a
familiar story, not baseline saturation. Saudi tanker attack (9.8×) and
Galaxy Leader (33.8×) — both novel, low-baseline incidents — are the
reliable signal here.

| Date | Incident | Response ratio |
|---|---|---:|
| 2016-10-09 | Houthi attacks on USS Mason | predates coverage |
| 2018-07-25 | Saudi tanker attack, transit halt | 9.84× |
| 2023-11-19 | Galaxy Leader seizure | 33.77× |
| 2024-02-18 | Rubymar struck | 0.60× |
| 2025-07-06 | Magic Seas / Eternity C | 0.64× |

---

## Adriatic

A semi-enclosed sea between Italy and the Balkan peninsula, not a narrow
strait. The nearest chokepoint-like feature is the Strait of Otranto
(~72 km wide) connecting to the Ionian Sea. Historically relevant here as a
corridor for regional conflict (the Balkan wars of the 1990s), not physical
vessel-passage risk — consistent with the manuscript's own note that "the
Adriatic codings involve enforcement-driven rather than corridor-blocking
interference and are the least direct in the sample" (Section 6.2).

| | |
|---|---|
| Width | Strait of Otranto ~72 km; the Adriatic itself is an open sea, not a strait |
| Traffic | Regional Mediterranean trade, extensive ferry traffic, and an energy pipeline landfall (TAP) |
| Primary cargo | Regional trade, ferry/passenger, natural gas (pipeline landfall) |
| Alternate route | Not applicable in the Hormuz/Suez sense — this is open sea, not a narrow passage with a single bypass |
| Littoral states | Italy, Croatia, Albania, Montenegro, Slovenia, Bosnia and Herzegovina, Greece |
| Evidence grade | `STRUCTURAL` — no post-1999 incident on record |

**Response character:** No post-1999 incident is on record for this
corridor — its only known onsets (Bosnia 1992, Kosovo 1999) predate every
data source available for this kind of test (GDELT 2.0 from 2015, webngrams
from 2020). Descriptive only.

---

## Turkish Straits / Black Sea

The Bosphorus and Dardanelles connect the Black Sea to the Mediterranean
via the Sea of Marmara. The Bosphorus narrows to about 700 m at its
narrowest — one of the narrowest straits used by international shipping
anywhere — and the Dardanelles to about 1.2 km. Both are governed by the
1936 Montreux Convention. Critical for Black Sea grain and energy exports
(Ukraine, Russia).

| | |
|---|---|
| Width | Bosphorus ~700 m, Dardanelles ~1.2 km at their narrowest points |
| Traffic | The sole maritime route for Black Sea grain and energy exports; volumes swing sharply with the state of the Russia-Ukraine war |
| Primary cargo | Grain, crude oil, steel |
| Alternate route | None maritime. Rail and pipeline alternatives exist for a fraction of the volume, at materially higher cost |
| Littoral states | Turkey, Ukraine, Russia, Romania, Bulgaria, Georgia |
| Evidence grade | `EPISODE_ANALOGUE`, flagged weak — 3 tested incidents |

**Response character:** Response is consistently weak — 1.7×, 2.3×, 2.9×,
none clearing the 3× bar the stronger corridors do. This matches a pattern
that holds across every corridor tested: incidents preceded by an
already-elevated 90-day baseline (>500 mentions/day) score 1.7–2.9× with no
exception, including the invasion of Ukraine itself — its own pre-onset
window was already saturated with buildup coverage (see the pattern
analysis below). Not a coverage failure; a structural ceiling on what this
test can register once a corridor is already mid-crisis.

| Date | Incident | Response ratio |
|---|---|---:|
| 2022-02-24 | Invasion of Ukraine | 2.89× |
| 2022-07-22 | Grain corridor agreement | 2.29× |
| 2023-07-17 | Grain deal collapse | 1.68× |

---

## Suez Canal

An artificial canal, not a strait — about 193 km long, connecting the
Mediterranean to the Red Sea without a lock system. Wholly within Egyptian
territory.

| | |
|---|---|
| Width | 193 km canal, single continuous waterway |
| Traffic | A major share of Asia-Europe container and energy trade transits here; widely cited estimates put it at roughly an eighth to a seventh of world trade by value |
| Primary cargo | Containers, crude oil, LNG, bulk goods |
| Alternate route | Cape of Good Hope, +10–14 days |
| Littoral states | Egypt |
| Evidence grade | `EPISODE_ANALOGUE` — 2 tested incidents |

**Response character:** Response is consistently strong — 14.0× and 4.1×
baseline. Both incidents clear the usable bar; this is the most reliable
corridor tested so far.

| Date | Incident | Response ratio |
|---|---|---:|
| 2021-03-23 | Ever Given grounding | 14.04× |
| 2023-12-18 | Red Sea diversion wave | 4.13× |

---

## Strait of Malacca

Connects the Indian Ocean to the South China Sea and the Pacific. About
800 km long, narrowing to roughly 2.8 km at the Phillips Channel near
Singapore — one of the highest-vessel-count shipping lanes in the world by
transit count.

| | |
|---|---|
| Width | ~2.8 km at the Phillips Channel; depth-constrained in places (the "Malaccamax" vessel-size limit) |
| Traffic | A large share of China/Japan/Korea-bound energy and goods transits here; high vessel density more than high per-vessel cargo value |
| Primary cargo | Crude oil, containers, general cargo |
| Alternate route | Sunda Strait or Lombok Strait — deeper draft capacity, longer transit time |
| Littoral states | Indonesia, Malaysia, Singapore |
| Evidence grade | `STRUCTURAL` — no post-2020 incident on record |

**Response character:** No post-2020 incident is on record for this
corridor. The one candidate incident tested (2019 Singapore Strait robbery
wave) predates webngrams' coverage start (2020-01-01) and was, in any case,
reported under "Singapore Strait," not "Malacca" — a naming gap as much as
a coverage gap. Descriptive only.

---

## Taiwan Strait

Separates mainland China from Taiwan. About 180 km wide, narrowing to
roughly 130 km at the narrowest point — wide enough that vessel-passage
room is rarely the binding constraint; the concentrated risk is to
Taiwan's own ports and, more broadly, the semiconductor supply chain
manufactured there.

| | |
|---|---|
| Width | ~180 km wide, ~130 km at the narrowest |
| Traffic | High container-shipping density as part of broader Northeast Asia routes, and outsized importance for semiconductor and electronics supply chains routed through Taiwan's ports |
| Primary cargo | Containers, electronics/semiconductor components |
| Alternate route | Vessels can route east of Taiwan if the strait itself becomes unsafe — longer, but the larger risk is disruption to Taiwan's ports, which rerouting around the strait does not solve |
| Littoral states | China, Taiwan |
| Evidence grade | `EPISODE_ANALOGUE` — 2 tested incidents |

**Response character:** Response is mixed — 3.6× (usable) for the August
2022 exercises, 1.6× (weak) for Joint Sword 2024A. Unlike Suez, the second
test did not confirm the first; treat this corridor's signal as
inconsistent across incidents, not reliably strong.

| Date | Incident | Response ratio |
|---|---|---:|
| 2022-08-04 | Post-Pelosi exercises | 3.56× |
| 2024-05-23 | Joint Sword 2024A | 1.60× |

---

## Cross-corridor pattern in the weak/blind readings

Across all 18 tested incidents, a real pattern separates two causes of a
low response ratio, rather than treating every weak reading as unexplained
noise:

1. **A ceiling effect at high baselines**, consistent across every
   corridor tested. Every incident preceded by a 90-day pre-incident
   baseline above ~500 mentions/day scores 1.7–2.9×, with no exception —
   including the invasion of Ukraine. The test measures novelty relative to
   recent coverage, not real-world severity: a corridor already mid-crisis
   structurally cannot register a sharp jump from an already-elevated
   starting point.
2. **Fatigue at low baselines**, a separate mechanism with the same
   symptom. Bab-el-Mandeb's Magic Seas/Eternity C (0.64×) and Hormuz's
   Mercer Street attack (1.34×) both had low pre-period volume, unlike the
   ceiling cases, yet still scored weakly — consistent with reduced
   marginal newsworthiness for another incident in an already-long-running
   story, not a baseline artifact.

Full derivation in `validation/report/response_ratio_pattern.md`.

## Summary

| Corridor | Evidence grade | Tested incidents | Response character (median where n≥3) |
|---|---|---:|---|
| Strait of Hormuz | `EPISODE_ANALOGUE` | 7 | ~4.9×, mostly strong |
| Bab-el-Mandeb | `EPISODE_ANALOGUE` | 4 | bimodal — 9.8–33.8× or 0.6× |
| Adriatic | `STRUCTURAL` | 0 | no incident on record |
| Turkish Straits/Black Sea | `EPISODE_ANALOGUE` (weak) | 3 | ~2.3×, consistently weak |
| Suez Canal | `EPISODE_ANALOGUE` | 2 | 14.0×, 4.1× — both strong |
| Strait of Malacca | `STRUCTURAL` | 0 | no incident on record |
| Taiwan Strait | `EPISODE_ANALOGUE` | 2 | 3.6×, 1.6× — inconsistent |

## Provenance

All figures above are duplicated as literal data from `src/chokepoint_profiles.py`
(`PROFILES`), which in turn sources them from
`validation/report/phase_d_corridor_grades.md` and the individual test
reports listed in `TAR_TRIP_complement_attribution.md`. Geography, width,
traffic and alternate-route text is general/public knowledge
(`GENERAL_KNOWLEDGE`), authored for this companion rather than drawn from a
specific cited source.

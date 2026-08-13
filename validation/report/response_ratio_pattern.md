# Why some tested incidents show weak response ratios — a pattern, not noise

**Date:** 2026-08-13
**Prompted by:** Bab-el-Mandeb's bimodal result (2 strong, 2 blind) — is there a
reason, or is 4 incidents just too few to expect consistency?

## Method

All 18 tested incidents across the 5 `EPISODE_ANALOGUE` corridors, pre-incident
mean volume (90-day window) plotted against the response ratio.

```
  pre-mean    ratio  corridor / incident
       3.1    33.77  Bab-el-Mandeb / 2023-11-19 Galaxy Leader seizure
      11.0     0.64  Bab-el-Mandeb / 2025-07-06 Magic Seas / Eternity C
      16.8     5.41  Strait of Hormuz / 2024-04-13 MSC Aries seizure
      21.2     4.93  Strait of Hormuz / 2025-06-13 Israel-Iran war onset
      26.3     9.84  Bab-el-Mandeb / 2018-07-25 Saudi tanker attack
      29.2     1.34  Strait of Hormuz / 2021-07-29 Mercer Street attack
      62.8     2.29  Turkish Straits/Black Sea / 2022-07-22 Grain corridor agreement
      89.1     0.60  Bab-el-Mandeb / 2024-02-18 Rubymar struck
      94.7     1.60  Taiwan Strait / 2024-05-23 Joint Sword 2024A
      95.9     5.32  Strait of Hormuz / 2020-01-03 Soleimani strike
     101.8     4.02  Strait of Hormuz / 2019-05-12 Fujairah tanker sabotage
     108.2    14.04  Suez Canal / 2021-03-23 Ever Given grounding
     167.2     3.56  Taiwan Strait / 2022-08-04 Post-Pelosi exercises
     173.4     4.13  Suez Canal / 2023-12-18 Red Sea diversion wave
     225.7     7.73  Strait of Hormuz / 2019-06-13 Gulf of Oman attacks
     553.6     2.89  Turkish Straits/Black Sea / 2022-02-24 Invasion of Ukraine
     720.0     1.68  Turkish Straits/Black Sea / 2023-07-17 Grain deal collapse
     885.2     1.78  Strait of Hormuz / 2019-07-19 Stena Impero seizure
```

Pearson r (pre-mean vs. ratio): **-0.26**. On log(pre-mean): **-0.52** — a real,
moderate relationship, not a coincidence, but nowhere near deterministic.

## Two distinct effects, not one

**1. A ceiling effect at high baselines — consistent, n=3.** Every incident
with a pre-mean above 500/day scores 1.7-2.9x, never above 3x — including the
actual invasion of Ukraine (2.89x). This isn't the invasion being
under-covered; it's that the 90 days *before* it were already saturated with
buildup coverage, so the response-ratio test structurally cannot register a
sharp jump from an already-elevated starting point. **This test measures
novelty relative to recent coverage, not real-world severity** — a corridor
already mid-crisis will show muted ratios for its next incident even if that
incident is objectively worse.

**2. Low baseline is necessary but not sufficient — n=6, 4 strong / 2 weak.**
Mercer Street (1.34x) and Magic Seas/Eternity C (0.64x) both had low
pre-period volume, same as four incidents that scored strongly. Baseline
level alone doesn't explain them. Magic Seas in particular sits 20 months
into an already-long Red Sea crisis (Bab-el-Mandeb's onset is 2023-11) — by
mid-2025, "another ship attacked" may simply have lower marginal newsworthiness
even against a since-receded baseline. That's a different mechanism
(fatigue/desensitization to a familiar story) than the ceiling effect above,
even though both produce the same symptom (a weak ratio).

## What this changes

- **Black Sea's weak grade is now better explained, not just observed.** Its
  onset-day test (2.89x) was always going to be capped by pre-invasion
  buildup coverage — the ceiling effect, not a defect in the corridor's
  attribution.
- **Bab-el-Mandeb's two "blind" incidents have different causes.** Rubymar
  (pre=89.1, ~29x its own pre-crisis baseline of 3.1) is the ceiling effect,
  scoped to *this corridor's own* history, not an absolute cross-corridor
  threshold. Magic Seas/Eternity C (pre=11.0, genuinely low) is not — it looks
  like campaign fatigue instead.
- **A single tested incident's ratio should be read with this in mind.**
  Suez's two incidents (14.0x, 4.1x) are both from a low/moderate pre-baseline
  and both strong — a genuinely reliable corridor by this evidence. A future
  incident tested during an already-elevated period for Suez would predictably
  score lower, and that would not mean the corridor's attribution had gotten
  worse.

## Caveat

n=18 across 5 corridors. This is a real, checkable pattern in this session's
own data, not a validated general law — treat it as an interpretive lens for
reading the existing tested incidents, not grounds for a new correction
formula.

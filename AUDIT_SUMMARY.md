# Audit summary — Phases 0–7, bye modelling, F1–F8

Written 2026-08-29 at `main` = `17cfb69`, for someone who was not in the sessions. Every claim
below is backed by a phase findings document (`AUDIT_PHASE_*_FINDINGS.md`) or an `AUDIT_PLAN.md`
entry; this file is the map, not the territory. Process throughout: characterise (a failing test
committed first), fix, re-golden with deltas shown, full suite before and after with the count
reported, every constant sourced or marked unverified, one branch per phase from `main`, merged by
fast-forward only after the suite passed on `main` standalone. Anything touching baseline
computation additionally had to move the paired, seeded, points-level backtest on the real 2025
season in the direction it predicted.

**Suite:** 72 tests at the start (2026-08-27) → **232** now, `OK (skipped=1, expected failures=4)`.
The four expected failures are deliberate red characterisations of open items (Phase 2 finding 4
×2, Phase 4 finding 1 / F2, the Phase 7 rate/form record). **Golden master:** three scenarios
(`week01`, `week06`, `week15`), 27 hashed outputs each, three stages so failures localise.

## Running defect count

| stage | found | fixed | mitigated / interim | deliberately open | reported only |
|---|---|---|---|---|---|
| Phase 0 | 1 | 0 | 0 | 1 | 0 |
| Phase 1 | 8 | 7 | 0 | 0 | 1 |
| Phase 2 | 8 | 4 | 0 | 2 | 2 |
| Phase 3 | 9 (+10 fallback paths inventoried) | 6 | 2 | 0 | 1 |
| Phase 4 | 5 | 3 | 0 | 1 (F2) | 1 |
| Phases 5+6 | 7 | 4 | 0 | 0 | 3 |
| bye modelling (Phase 1 #7 + step 6) | 1 latent | 2 | 0 | 0 | 0 |
| F4 / F5 / F6 (absence chain) | 4 | 4 | 0 | 0 | 0 |
| Phase 7 | 2 | 1 | 0 | 1 | 0 |
| F3 | 1 prerequisite | 2 | 0 | 0 | 0 |
| **total** | **~46 findings** | **33 fixed** | **2** | **5 open, all tracked with numeric criteria** | **8 reported** |

"Open" means tracked with an acceptance criterion and a stated blocker, never silently dropped.
Fixed defects were each verified by a test that failed against the old behaviour; where a fix
was verified against real data and made calibration worse, it was reverted and the reason
recorded (this happened five times — see "Reverted on evidence").

---

## Phase 0 — Reproducibility harness (2026-08-27)

**Found.** The seeding question (sequential `np.random.seed(1000 + batch)`) was investigated to
the limit of available power: no batch correlation detectable. The real defect the question
surfaced: `Playoff_SE` is estimated from the standard deviation of ten batch means (9 df), so the
reported SE is itself ±45% at 95% — an estimator-choice error (i.i.d. draws have a closed-form
SE) rather than an RNG one.
**Built.** The golden master: two fixture scenarios, three hash stages (`run_simulation` args,
export files, champion-ranking re-run), proved load-bearing by mutation. A moment summary per key
for reading deltas — with the documented lesson that 30-season summaries detect ulp-vs-real
changes but do not size effects; effects are sized at ≥400 seasons or on the backtest.
**Left open (deliberately).** The closed-form `Playoff_SE` — reported, not implemented, per the
report-before-fixing rule; still open. 72 → 84 tests.

## Phase 1 — Conservation and invariants

**Found 8, fixed 7.** Mid-season normalisation divided by the full 14-week season instead of the
weeks actually simulated: the "Any Given Sunday" matrix deflated to 64% at week 6, the
schedule-luck index non-zero-sum (+142.86), points-against understated 36%, weekly percentiles
diluted by unplayed weeks (p10 = 0.00); `Expected_Points` included playoff weeks (~12%); a config
constant mutated in place by running the engine. **Finding 7** — the bye mechanism was dead code
(Sleeper's payload has no bye field; every player carried `bye: 0`) — became the root of the
absence chain below and is now fixed. **Reported only:** finding 8, a label/content mismatch on
`power_rankings_baseline_pts`. One caveat left on finding 2 (span mismatch). 84 → 110 tests.

## Phase 2 — Statistical core

**Found 8, fixed 4.** The environment multiplier was not mean-preserving (+2.8% mean, +17%
variance; a hardcoded 22.0 vs the schedule mean); `shared_z` injected +0.32 correlation into every
pass-catcher pair for 44% of team-weeks; the PSD repair was not renormalised; QB–receiver
correlation was non-monotone in rank. **Deliberately open:** finding 3 (copula targets calibrated
on scores, applied on z: 12–14% attenuation — it partially offsets finding 2 and is to be fixed
only after 2 is validated out of sample); finding 4 (the posterior is not conjugate — see Phase 7).
**Reverted on evidence:** finding 5 (skip zero-score weeks) raised real-2025 bias +4.3% because,
with byes unmodelled, those zeros were the only absence signal — re-applied and standing after
bye modelling. A −4.6% golden shift was traced to RNG reshuffle, not the fix (true −2.4%).

## Phase 3 — Data ingestion integrity

**Found 9 (+ a 10-row inventory of silent fallback paths), fixed 6, mitigated 2.** In-season
Vegas fallbacks left a stale week-1 file with no stamp (fixed: write + `_meta` stamp + engine
staleness refusal; `ODDS_API_KEY` remains the real fix); a failed schedule week silently
flattened that week (recorded in `_meta`, warned); position constants looked up by Sleeper's raw
position took anonymous defaults; `team: null` leaked into baselines; the player cache was never
refreshed; the defensive-prior fallback was on the wrong scale (now derived from the table mean).
**Mitigated:** name-keyed baselines with duplicate Sleeper names (Justin Jefferson ×2, Byron
Murphy ×2) — loud collision keys and a pid-tracked prior; the full pid rekey is **F1**. The
zero-projection silent drop (Jordyn Tyson: wrong whitelist team) — team fixed + runtime guard.
**The n₀ decision:** `DEF_RATING_SHRINKAGE_N0` 4 → 12, derived from 2025 variance components
(applied); the player-side conjugate form applied and **reverted** (+8.5% real bias) — the first
of three times that form failed the backtest. **Reported:** unread fields (weather, injury
status, standings) — `injury_status` later became F4.

## Phase 4 — Decision logic

**Found 5, fixed 3.** Won streamers were valued by league-wide bid rank and out-projected 105 of
156 rostered players (capped at the data-derived replacement level; backtest neutral); a
completed trade shrank the rich roster by one (2-for-2 with a throw-in); a won streamer for next
week's hole was discarded (fixed in bye-modelling step 3, one-week persistence). **Open, tracked
as F2:** trades effectively never complete (0 of 548 evaluations over 100 seasons) so
`MANAGER_PROFILES['trade_will']` is inert — sized, criterion ≥1.0 completed trades/season,
characterisation red. Decision questions were answered by brute force (Hungarian vs exhaustive
search, 1,700 rosters), not by the backtest.

## Phases 5 + 6 — Season mechanics and outputs

**Found 7, fixed 4.** The engine crashed on any `current_week ≥ 15` (interim refusal, then **F3**
below); a banked H2H tie was truncated (0.5 lost); playoff ties advanced the lower seed
(`_playoff_winner`, Sleeper's rule); `is_mathematically_eliminated` was a Monte Carlo zero, not a
proof (renamed to what it measures). **Reported:** exact-median tie (measure-zero), the unsourced
magic number 16, an orphan PNG. Every export field is recomputed from the engine's arguments by
tests.

## Bye modelling (steps 1–6) — the dependency three phases were blocked on

Byes are **derived** from the NFL schedule at sync (the one usable week a team appears in no
pairing; 32/32 teams for 2025 and 2026), written to `_meta.byes`, read by the engine from that
one place. The engine's three bye guards went live; the vacated-volume non-interaction was pinned
on the real engine (no pool for a team in its bye week, every recipient playing); won streamers
persist one week; the backtest harness got real 2025 pairings (byes only, totals flat). Measured
step by step on the paired backtest: byes alone overshoot to −1.8% (history zeros double-counted);
+ skip-zeros (5b) → +2.7% with a gradient cp3 −1.4% → cp12 +6.9%; + conjugate (5c) → +10.8%,
**reverted**, and the surprise that the posterior weight was already right (0.71 applied vs 0.68
target) — the bias was undrawn absence. Step 6 (fixtures carry byes) exposed and fixed a latent
defect: the streamer-need scan re-scanned week 14 from every week ≥ 15. The earlier 0.49 / 0.68 /
0.80 empirical-weight targets were each later found to be artefacts of how the prior was centred
and are withdrawn.

## F4 — initial injury state (merged)

Sleeper's `injury_status` and the league's IR slot (`reserve`) now reach the baselines; a player
who is out enters on a measured two-stage clock (return hazard **0.29** after the first week out,
**0.16** thereafter; real 2025, n = 101 / 62–29). `on_ir` is absent regardless of status (named
cost: two Questionable players parked on IR). A separate Doubtful mechanic was dropped — no
source for its 0.9, no live boundary case — and the reasoning recorded. First-week absence 0.0% →
5.6% vs 5.3% real; its gate (gradient ≤1.5 pts) missed and the level offset was shown, not
assumed, to be the forward model → F5.

## F5 — forward absence model (merged)

Onset rate and duration were shown to be coupled through the absence share (A = rD/(1+rD)) and
were judged on their own statistics: the constants were right (r 0.047 vs 0.050 real; D 2.90 vs
2.56 censored) and the engine was under-delivering them — the onset week was played at an
unsourced 0.35× and the clock burned its first unit the same week, so a drawn n gave n − 1
missed games. Reversed deliberately (onset week = missed game). Then the price of an absence: a
same-week zero is **two regimes** — 90% known before lock and bench-covered (already modelled),
10% locked-lineup zeros (`LOCKED_ONSET_PROBABILITY` 0.21, 13/61, Wilson 0.13–0.33). Built,
brute-force verified on 2,768 onsets; started-zero rate 0.099 vs 0.236 real, decomposed to a
denominator mismatch → F6. Not tuned.

## F6 — onset exposure (merged)

`INJURY_RATES` is per active player but was drawn uniformly across the roster. An intended
lineup (solved before the onset draw, resolving the PASS-1 circularity) scales the hazard 1.05 /
0.84 (real 2025 ratio 0.80, n = 14 bench onsets, a definition-mismatch correction recorded) and
the locked draw applies only to intended starters. Fixture-verified; gate missed (started-zero
0.099, starter-onsets 3.24 vs 4.7) and the miss shown to be the per-position **level** of
`INJURY_RATES` — Phase 7's. F6's factors and 0.21 are fixed inputs to Phase 7, not free
parameters.

## Phase 7 — calibration (step 1 merged; steps 2+3 recorded and reverted)

**Step 1, `INJURY_RATES`:** redefined as the all-cause weekly absence-onset hazard (what the
backtest scores against); WR 0.040 → **0.081** (n = 38) and QB 0.025 → **0.054** (n = 8) by the
rule "move only where the config lies outside the real 2025 Wilson interval"; RB, TE, K, IDP
unchanged with reasons. **Three predictions stated in advance all landed:** starter-onsets 4.3–4.5
(≈4.6 predicted, 4.7 real), started-zero 0.136 (≈0.14), absence 14.6% (14.7% real). Bias +1.51 →
+0.72 pts. The residual started-zero gap (0.06–0.10) is manager behaviour the engine does not
model. **Steps 2+3, `EPISTEMIC_ERROR_RATES` + conjugate form:** demonstrated to be a matched pair
(the rates alone under the old form → std_z 1.3–1.5; a wrong direction prediction recorded);
built jointly from variance components (rates 0.07/0.28/0.22/0.20/0.25) and **reverted** — neutral
on the calibration instrument, worse on the backtest in both configurations. **Open, named:** F8
within-season drift (std_z → 1.2 by cp9–12 under both forms; a static-mean assumption neither
could close) and F7 (no projection-error data). Phase 2 finding 4 is blocked on those, its weight
criterion already met.

## F7 — projection log (merged, filling)

Every sync appends one JSON line per rostered player (`sleeper_mean`, `espn_mean`,
`fallback_season`, …) to `data/projection_log.jsonl` — tracked in git (`data/*` + an exception,
verified by a real commit after a first wrong claim was corrected). Smoke-tested: **155 rows** read
back after the first real sync. `analyze_projection_error` is written so next season's
derivation is one call. Time is the constraint.

## F3 — simulate from inside the playoffs (merged)

Prerequisite defect found by the survey: sync banked playoff-week results (Sleeper returns
matchup_ids for weeks 15–16) into regular-season standings — now banked from weeks ≤ 14 only.
The bracket is seeded from banked standings with Sleeper's `/winners_bracket` as the authority
(fetched and stored each sync); week 16 uses the recorded semifinal winners; week 17+ refuses as
"season complete". Export tolerates zero simulated regular-season weeks (flagged
`regular_season_banked`). A third golden scenario, `week15`, pins it.

---

## Reverted on evidence (the list that justifies the process)

1. Phase 2 finding 5 (skip zero weeks) — +4.3% real bias with byes unmodelled; re-applied after
   bye modelling and standing.
2. Phase 3 player-side conjugate — +8.5%.
3. Bye-modelling 5c conjugate — +10.8%; the diagnosis changed (absence, not weight).
4. Phase 7 steps 2+3 joint rates + conjugate — −2.1% / +5.2% in the two configurations.
5. F6's first factor derivation (0.55) — a definition mismatch caught before use; corrected to 0.80.

Plus two wrong-direction predictions recorded as information (F6's null result; Phase 7's
"collapse" that rose instead), and one measurement (the 0.49 weight target) withdrawn twice.

## Open items, all tracked with numeric acceptance criteria

| item | what | blocker / when |
|---|---|---|
| F1 | rekey players by Sleeper `player_id` | engineering-shaped; pairs with Phase 8 |
| F2 | make trades live (≥1.0 completed/season on week01) | design question is Phase 7-adjacent |
| F8 | within-season drift of the true mean (random-walk prior) | after F7 has a season; blocks Phase 2 finding 4 |
| Phase 2 finding 3 | copula targets on z | after finding 2 validated out of sample |
| Phase 2 finding 4 | conjugate posterior | F7 + F8 |
| Phase 0 | closed-form `Playoff_SE` | reported, unimplemented |
| Phase 7 rate/form record | `test_calibration.py`, red by design | resolves with F7/F8 |
| R1 | **machine-level fault under multi-core load, not software**: concurrent pure-Python (stdlib-only) processes crash or return unsorted `sorted()` output on both Python 3.8 and 3.10; single processes never failed. Migration to Python 3.10 done on its own merits (EOL 3.8 stack; goldens byte-identical) but does not cure it. Rules: run jobs one at a time; a crashed or "impossible-error" run is void. Re-test = `scripts/probes` Arm D 6/6 on both interpreters after the hardware is addressed (memtest / XMP / thermals / AV hook). | re-run, never count as green; compare faulthandler frames |
| Phase 8 | engineering / decomposition | only with the golden master — which now exists |

## What was deliberately not done, and why (see `CLAUDE.md` for the full list)

`MEDIAN_SCORING_ENABLED = False` in the 2025 backtest (that season was pure H2H); ESPN blending
excludes K and IDP (scoring cannot be matched); `VACATED_VOLUME_CAPTURE_RATE` 0.65 carried over
unverified; mean-weighted vacated-volume apportionment known backwards for handcuffs (fix =
`depth_chart_order`, not weights by feel); `MANAGER_PROFILES` excluded from data-driven
calibration; the engine is one class by choice; and no constant in this audit was tuned to close
a gap attributed to a different mechanism.

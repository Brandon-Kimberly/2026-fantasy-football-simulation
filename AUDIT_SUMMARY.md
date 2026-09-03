# Audit summary — Phases 0–7, bye modelling, F1–F30

Written 2026-08-29 at `main` = `17cfb69`, for someone who was not in the sessions. Every claim
below is backed by a phase findings document (`docs/audit/AUDIT_PHASE_*_FINDINGS.md`) or a `docs/AUDIT_PLAN.md`
entry; this file is the map, not the territory. Process throughout: characterise (a failing test
committed first), fix, re-golden with deltas shown, full suite before and after with the count
reported, every constant sourced or marked unverified, one branch per phase from `main`, merged by
fast-forward only after the suite passed on `main` standalone. Anything touching baseline
computation additionally had to move the paired, seeded, points-level backtest on the real 2025
season in the direction it predicted.

**Suite:** 72 tests at the start (2026-08-27) → **481** at this summary's last update
(2026-09-03; the live count is stated and guarded in the README), `OK (skipped=1, expected
failures=3)`. The three expected failures are deliberate red characterisations of open items
(Phase 2 finding 4 ×2, the Phase 7 rate/form record); a fourth — the dead trade mechanism,
Phase 4 finding 1 / F2 — flipped from red characterisation to a guard when F2 commit 1
landed (2026-09-01). **Golden master:** three scenarios
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
| **phase-era total** | **~46 findings** | **33 fixed** | **2** | **5 open, all tracked with numeric criteria** | **8 reported** |
| F9–F30 (2026-08-30 → 09-03; see the F9–F30 section below) | 22 | 9 fixed / built | 6 measured & cleared | 7 open, tracked | 0 |
| **grand total** | **~68 findings and tracked follow-ups** | **42 fixed or built** | — | open set enumerated in the table below | — |

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
report-before-fixing rule. **Implemented 2026-08-31** (failing test first, then the one-line swap;
stage_a byte-identical, stage_b/c moved only in the three SE-carrying payloads). 72 → 84 tests.

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
correlation was non-monotone in rank. **Closed since (2026-09-03):** finding 3 was fixed by the copula pre-warp — realized
QB-WR1 correlation 0.284 → 0.40, points-backtest bias −0.98 → −0.81, goldens regenerated as
an intended change. Original text: **Deliberately open:** finding 3 (copula targets calibrated
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
| Phase 2 finding 4 | conjugate posterior | F7 + F8 |
| Phase 7 rate/form record | `test_calibration.py`, red by design | resolves with F7/F8 |
| F12 | `SystemError` in `_solve_optimal_assignment`, seen once | R1-linked; does not reproduce single-process |
| F17 | Commissioner-Exempt return timing (live data point) | event-driven — the week his status changes |
| F18 / F19 | decision retrospective; cross-week odds trajectory | season data (~weeks 3–4); predictions log is the authoritative input (F25) |
| F22 | IDP epistemic/volatility constants (tier caveat applied) | derive from F7's projection log after a season |
| F25 | team-week interval under-dispersion, bracketed r ∈ [1.15, 1.34] | quoted-vs-realized calibration from the predictions log, ~week 5–6 |
| F15 / F26 | draft realized-value row; the ten untested sync handler bodies | season data; fake-HTTP layer respectively |
| R1 | **machine-level fault under multi-core load — verdict: RMA.** MemTest86 clean, AV excluded, BIOS/microcode updated to 0x133 with Intel Default Settings — Arm D still fails 9/12 and 11/12, so the chip itself is degraded (Vmin Shift class). Load threshold is not safe even at 3 concurrent real engine processes (1/3 silent death, 2026-09-01, no Reliability Monitor trace). Rules: one engine process at a time; a crashed or impossible-error run is void. CI on a cloud Windows runner now provides an independent, fault-free machine certifying every commit. Re-test = Arm D 12/12 after the CPU is replaced. | AUDIT_PLAN.md R1 carries the full probe history |
| Phase 8 | engineering / decomposition | only with the golden master — which now exists |

## F9–F30 — follow-ups and measurements (2026-08-30 → 2026-09-03)

One line each; full entries in `docs/AUDIT_PLAN.md`. The distinctive pattern of this stretch:
five suspected defects were MEASURED and the claims retired rather than "fixed" (F13, F14,
F16, F20, F23, F24 — six, counting both weighting suspicions), because the measurement said
the code was right.

- **F9** data/ directory structure, season-long retention — DONE.
- **F10** audit-log / warnings retention — DONE (2026-08-31).
- **F11** test suite silently truncated real production data since the initial commit —
  FIXED; the data/logs integrity guard now proves every suite run leaves the logs
  byte-identical.
- **F12** one-time `SystemError` in the assignment solver — OPEN, R1-linked, never
  reproduces single-process.
- **F13** game-script / tail-asymmetric correlation — measured, NOT adopted; CLOSED.
- **F14** `MANAGER_PROFILES` sensitivity — measured, outcome-inert; CLOSED.
- **F15** draft retrospective — ingestion, at-draft analysis and report BUILT (2025 grade
  flagged as hindsight); realized-value row OPEN on season data.
- **F16** cross-roster same-game correlation — measured at n=20,000: sub-percent; CLOSED
  as inert, nothing built.
- **F17** Commissioner-Exempt return timing — OPEN, event-driven.
- **F18 / F19** decision retrospective; cross-week odds trajectory — OPEN, season-gated;
  the predictions log (canonical rows win) is the authoritative forecast record.
- **F20** paired-evaluation magnitude gap — decomposed into named channels; RESOLVED, no
  defect; SEs proven honest.
- **F21** 2025 season retrospective — BUILT; also delivered the historical all-play
  computation `schedule_luck_index` documents as missing.
- **F22** IDP constants sensitivity — measured: outcome channels inert, tier boundaries
  material; caveat applied; OPEN pending F7-data derivation. The standing concern (IDP
  volatility = the unknown-position fallback; epistemic 0.15 implies IDP projections 3×
  more trustworthy than RB) stands regardless.
- **F23** variance form — k·√mean MEASURED AND CLEARED on 2025 data; fitted k reproduces
  the calibrated constants on 4 of 5 positions; WR flagged for a 2026 re-check.
- **F24** handcuff mean-weighting — MEASURED CORRECT (the audit's oldest suspicion,
  retired); depth watchdog built.
- **F25** team-week interval calibration — diagnosed MIXED (~44%+ harness artifact);
  gate corrected with an optimal-lineup target; engine held; OPEN on 2026
  quoted-vs-realized calibration.
- **F26** coverage — BUILT (74% total / 85% package, committed-floor ratchet in CI); the
  real finding is the silent-failure map: 23 of 33 broad handler bodies never execute;
  the ten sync handlers are the tracked follow-up. The week-16 semifinal fallback was
  tested immediately.
- **F27** this document's own drift — REPAIRED and guarded (F-coverage, README↔totals,
  closed-not-open cross-checks); the named repeatable mistake: *checking a derived number
  against its stale origin*.
- **F28** IDP + K volatility constants measured on full-NFL 2025 stats (pipeline
  validated 1,891/1,891 player-weeks to the cent) and ADOPTED: DL 2.16 / LB 1.67 /
  DB 1.58 replacing the 1.5 placeholder, K 1.45 replacing a 1.57 calibrated under
  retired 2025 kicker scoring. Gate passed (bias delta 0.002); goldens byte-identical —
  the constants act at sync time, upstream of what the golden pins, a blind spot now
  stated in the release policy itself; the sync-stage golden that closes it was BUILT
  2026-09-02 as F29 pre-work (3 tests, sensitivity verified against the exact F28
  change the engine golden missed; ESPN parsing outside, stated).
  F22's epistemic half stays on F7.
- **F29** K/IDP epistemic disagreement from ESPN raw stat lines — the second source
  already carries them (the points-level exclusion was right about points, wrong about
  stat lines); shared subset 11/12 keys (ESPN id-100 identified as QB hits; id-112
  'Stuffs' excluded as narrower than TFL; ids 110/111 recorded as unreliable); signal
  clears the floor for 3/37 rostered K/IDP today (all LB, led by a real Brooks
  tackle-volume dispute) — BUILT same day, tests-first: epistemic-only (143 of 888
  fixture baselines widened, zero mean movements, zero K changes), sync-golden
  regenerated with deltas shown on its first live exercise, engine goldens
  byte-identical, gate inert as predicted. MAJOR. Floors stay F22's.
- **F30** VACATED_VOLUME_CAPTURE_RATE measured on F24's 8 real absence events —
  MEASURED AND HELD: capture mean +1.53 [0.87, 2.19], every event above the engine's
  0.65 (placebo-validated estimator), so the constant is directionally conservative —
  but n=8 spans 0.84–2.62 with two role-change contaminations, the denominator is not
  the model's unit, and >1.0 would change the conservation invariant's meaning. OPEN
  on the 2026 projection-log denominator (~5 events, mid-season).

## What was deliberately not done, and why (see `CLAUDE.md` for the full list)

`MEDIAN_SCORING_ENABLED = False` in the 2025 backtest (that season was pure H2H); ESPN blending
excludes K and IDP (scoring cannot be matched); `VACATED_VOLUME_CAPTURE_RATE` 0.65 carried over
unverified; mean-weighted vacated-volume apportionment — long suspected backwards for
handcuffs — was MEASURED CORRECT by F24 (2026-09-03: ties depth weighting on 8 real 2025
absence events, and in the one live chart-vs-mean disagreement the chart was the wrong
signal), so the suspicion is retired and a sync watchdog surfaces live disagreements
instead; `MANAGER_PROFILES` excluded from data-driven
calibration; the engine is one class by choice; and no constant in this audit was tuned to close
a gap attributed to a different mechanism.

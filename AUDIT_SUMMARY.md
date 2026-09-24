# Audit summary — Phases 0–7, bye modelling, F1–F35

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
| F9–F35 (2026-08-30 → 09-03; see the F9–F35 section below) | 27 | 11 fixed / built | 6 measured & cleared | 10 open, tracked | 0 |
| **grand total** | **~107 findings and tracked follow-ups** | **74 fixed or built** | — | open set enumerated in the table below | — |

"Open" means tracked with an acceptance criterion and a stated blocker.
Fixed defects were verified by tests that failed against the old behaviour. Where a fix
made real-data calibration worse, it was reverted and the reason recorded (five times —
see "Reverted on evidence").

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
| F22 | IDP epistemic constants (volatility half closed by F28) | derive from F7's projection log after a season |
| F25 | team-week interval under-dispersion, bracketed r ∈ [1.15, 1.34] | quoted-vs-realized calibration from the predictions log, ~week 5–6 |
| F15 / F26 | draft realized-value row; the ten untested sync handler bodies | season data; fake-HTTP layer respectively |
| R1 | **machine-level fault under multi-core load — verdict: RMA.** MemTest86 clean, AV excluded, BIOS/microcode updated to 0x133 with Intel Default Settings — Arm D still fails 9/12 and 11/12, so the chip itself is degraded (Vmin Shift class). Load threshold is not safe even at 3 concurrent real engine processes (1/3 silent death, 2026-09-01, no Reliability Monitor trace). Rules: one engine process at a time; a crashed or impossible-error run is void. CI on a cloud Windows runner now provides an independent, fault-free machine certifying every commit. Re-test = Arm D 12/12 after the CPU is replaced. | AUDIT_PLAN.md R1 carries the full probe history |
| Phase 8 | engineering / decomposition | only with the golden master — which now exists |

## F9–F35 — follow-ups and measurements (2026-08-30 → 2026-09-03)

One line each; full entries in `docs/AUDIT_PLAN.md`. Six suspected defects in this
stretch (F13, F14, F16, F20, F23, F24) were measured and the claims retired rather
than "fixed": the measurement said the code was right.

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
- **F26** coverage — BUILT (committed-floor ratchet in CI; floor now 75.5); the real
  finding was the silent-failure map: 23 of 33 broad handler bodies never executed. The
  week-16 semifinal fallback was tested immediately; the sync-handler cluster (grown to
  fifteen) was tested pre-kickoff 2026-09-03, each pinned to its documented degradation.
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
- **F31** simulated FAAB spending measured at ~31% of real (248 vs 728 of 800);
  F14's "3–6 of 100" was pre-bye and stale ever since — corrected. BUILT same day,
  tests-first: fitted lognormal bid curve (conviction tail included), upgrade channel
  at residual rates, two-parameter 2025-prior manager model blending from the decision
  log; deflation removed on evidence. Acceptance 684/800 in the [650, 800] band
  (aggregate calibration, not per-manager prediction); gate PASS with cover80
  IMPROVED (0.625 → 0.667); engine goldens regenerated, MAJOR. Trade evaluator
  records FAAB transfers as explicitly unpriced. Re-measure at ~100 attributed 2026
  claims (~weeks 8–10). A third stage-golden seam caught: the sandbox now blocks the
  updater's live-log read.
- **F32** the waiver claim premium — the one honest path to pricing waiver skill
  (realized post-claim value vs the AT-CLAIM projection the decision log already
  freezes; news the projections lag, measured as a league-level selection effect, never
  per-manager skill). OPEN, blocked on season data: measurable at ~60 contemporaneous
  2026 claims (~January); adoption bar and constraints fixed in the entry now, before
  results exist. A zero result would be decisive too — it makes the trade evaluator's
  unpriced FAAB block permanent.
- **F33** unsourced in-engine constants, grouped by the pre-season audit's sweep
  (game-script multipliers, replacement depth indices, streamer decay/ladder/ceiling,
  n_0, LEAGUE_AVG_PPG) — OPEN; derive from 2026 data or mark permanently-unverified
  with reasoning. The anonymous-default family was centralized same-day.
- **F34** missing churn channels: the free-add channel (122 real zero-cost adds — the
  audit's 152 counted transactions, 30 were drop-only; churn under-modeled ~2.0x) and
  IR-spot economics (rosters averaged 16.9 of 18 spots; the sim never frees one) —
  OPEN; owner disposition 2026-09-04: one build arc at the F32 unlock (volume + value
  + roster fidelity together, MAJOR), acceptance criteria fixed in the plan entry.
  The 2025 derivation is committed (`scripts.free_add_study` + its artifact) with a
  format caveat: 2025 was non-IDP, so rates transfer as priors, the position mix does
  not. An unmetered hole-only free channel already exists (simulation.py:~1476) — the
  finding is that it is unmetered and roster-inert, not that it is absent. F2 keeps
  its real calibration target: 11 trades in 2025 vs the sim's ~0.
- **F69** the week tools scored an unfillable slot as zero, not as a streamer — RESOLVED:
  `matchup_lineup` printed this week's opponent with 12 starters against a 13-slot league
  and reported 81.9% / +48.9; a real opponent claims someone before kickoff, which is what
  `run_simulation` already assumes via `STREAMER_<POS>_0`. The week tools and the season
  simulation disagreed about the same roster. Fixed by borrowing the engine's own streamer
  arithmetic (`decisions.streamer_mean` / `streamer_fill`) so they agree by construction.
  Three wrong turns, all recorded: streaming only the OPPONENT (caught by an existing
  fixture that inverted to P(win) 0.007 — both sides field thirteen men); folding the
  streamer into `expected_total`, whose comment pins it to `expected_pre_total` (reporting
  now uses rostered men, probabilities use the augmented total, and
  `expected_with_streamers` exposes the gap); and uncached draws, which would let one
  construction beat another on streamer noise. `league_week_outlook` shared the defect and
  had a team 9.4 expected points low in the weekly report's League table. Live: 81.9% →
  78.7%, within half a point of the hand-computed figure, and TWO teams carry a DL hole, so
  the median was biased too.
- **F68** a dual-eligible starter was reported at his primary position, not the slot he
  filled — RESOLVED: `trade_leverage` called a rival's LB slot 3.33 below replacement and
  named him the league's best buyer for this roster's LB surplus; two trades were sent on
  it before the paired simulation said each one COST that rival 0.4–0.7 expected wins. The
  backlog's hypothesis (week-vs-season basis) was wrong and is recorded as such. The cause:
  a DL-eligible linebacker (`config.DUAL_ELIGIBILITY`) legally covered a rival's empty DL
  slot, and `starters_by_position` resolved him to LB — so a 7.52 DL starter was measured
  against the LB bar of 10.86 instead of the DL bar of 6.83, where he is +0.69 ABOVE
  replacement. Fixed in one line: FLEX resolves to the player (a third WR at FLEX IS a WR
  starter, and a test pins that), every other slot resolves to itself. `market_sweep` shared
  the helper and was fixed with it. Verified live: the phantom hole is gone and the screen
  now agrees with the simulation about which rival is the real LB buyer. Recorded not acted
  on: `DUAL_ELIGIBILITY` is keyed by NAME against a cache with 220 collisions (B17).
- **F67** a failed odds fetch destroyed real same-week Vegas lines — RESOLVED: observed in
  production, not by reading code. A sync with a pre-rotation `ODDS_API_KEY` took a 401 and
  wrote the flat 21.5 fallback over real week-3 lines fetched hours earlier; `data/current/`
  is untracked so there was nothing to restore, and the decision work in that window ran on
  degraded numbers. The sync WARNED correctly (F57) and destroyed the data anyway — a loud
  warning is not a substitute for not doing the destructive thing. Writing unconditionally
  was NOT a bug: Phase 3 finding 1 exists because fallback paths that returned without
  writing left the WEEK-1 table on disk all season. So the rule is narrower than "do not
  overwrite" — a real file is kept only when it is real AND for the SAME week, and six tests
  pin that half. The keep is stamped `stale_since`, warned, and reported by
  `check_freshness` as DEGRADED rather than STALE, because real lines for the right week are
  not a reason to stop a run.
- **F66** five HTTP boundaries had no test of what they ASK for — RESOLVED for the top
  three: B26's sweep, prompted by F50/F52 sharing a shape (passing tests that patched the
  function whose INPUT was wrong, then asserted arithmetic on the input the test supplied).
  Of 12 functions that make an HTTP call, five lacked a request-pin, and three of those
  lacked an empty-return test too. Fixed: `fetch_league_wide_player_scores` (F54's feed —
  returns {} on failure BY DESIGN, so the request is the only thing left to pin; a wrong
  week silently reverts every free-agent comparison to a preseason prior),
  `ingest_transactions` (whose `week` is Sleeper's `leg` not the loop counter — F65's root
  cause, now written down), and `live_matchup._fetch_json`, which had NO test of any kind
  and whose raise-on-failure is what makes `locked_nfl_teams` safe to treat a missing clock
  as unlocked. COVERAGE, not regression tests — stated plainly, and each verified
  load-bearing by mutation (all three went red, all reverted). Still open and recorded
  rather than dropped: `generate_league_schedule` and `ingest_drafts`.
- **F65** the bid ledger could never resolve a claim: Sleeper counts the week
  differently — RESOLVED: two waivers were WON and `bid_review` still printed
  `resolved 0`, silently. The ledger stamps `current_week` at BID time (3); Sleeper
  stamps the `leg` at SUBMISSION (2), and with `daily_waivers: 1` a claim routinely sits
  across a week boundary, so the offset is this league's NORMAL case. `reconcile` matched
  on (player_id, week) and found nothing. Worst possible place for a silent failure: the
  empty result is character-for-character the honest "no waiver run yet" state this
  module was built to show, so it would have looked right all season while collecting
  nothing — and the plan records this ledger as "the only route to settling" B13 after
  F61. Fixed by matching on player_id plus TIME PROXIMITY (±4 days). Widening the week to
  ±1 was rejected: it breaks F64's rule that the same player a week apart is a different
  claim. Proximity NOT ordering, because `created` is the submission time and survives an
  edit — the real transaction predated its own ledger row by 18 hours. Also surfaced: the
  K was recorded at $1 and charged $2, so `bid_mismatch` now reports a won claim whose
  charge differs rather than scoring a bid that was never placed.
- **F64** a raised bid was two claims in the ledger, scored at a price that was never
  live — RESOLVED: found by using it. The owner raised a QB bid from $25 to $29 before
  the daily run; `record_bid` appends and `calibration` scored every ROW, so one claim
  would reconcile against one outcome twice. It matters because this ledger is recorded
  as "the only route to settling" B13 after F61 (correlation(VORP, winning bid) −0.136),
  and double-scoring a revision biases toward whichever price was typed first —
  systematically the LOWER one, since bids get raised far more than lowered. Fixed by
  SUPERSESSION, not mutation: `live_rows` keeps the latest row per (player_id, week) by
  `placed_at`, `superseded_rows` returns the rest, and the earlier row stays in the file
  because "how often is a bid revised, and which way" is a question worth keeping.
  Ordering is by timestamp, not file order — the mistake `decision_scorecard` made with
  paths the same day. An existing test had passed on an impossible fixture: two bare
  `_row()` calls sharing one player_id and week, asserted both won and lost.
- **F63** B21's designation log could not answer the one question it was built for —
  RESOLVED: found while working B10, the study B21 exists to feed. B10's test is "above
  the positional BASE RATE", a rate among the UNDESIGNATED, and `append_designations`
  wrote only players carrying a designation — deliberately, calling a healthy row "150
  rows of 'nothing happened'". The healthy men ARE the comparison group, and the roll
  call is unrecoverable afterwards (`live_rosters.json` is overwritten every sync, the
  players cache holds only today). It would not have surfaced as an error: the study
  still runs by borrowing a denominator from the LEAGUE-WIDE scored feed (~800/week vs
  ~152 rostered), where untracked players sit in the undesignated arm carrying unlogged
  designations — biasing the lift DOWNWARD. A plausible, quietly understated number.
  Fixed by writing the roll call (healthy = `injury_status: null`); the `(week, pid,
  status)` dedupe carries it at ~2,700 rows/season and still captures a Friday
  Questionable as a distinct key. Two committed tests had pinned the defect as the
  requirement and were amended in place with the reason, not deleted.
- **F62** the behavioral drift check calls Monte Carlo noise an engine behavior change —
  RESOLVED: found while regenerating the baseline for B7+B8, the act the check exists to
  police. It reported six drifted mechanics as "an engine behavior change"; at least two
  cannot be. `_compute_faab_bid` reads a budget, an externally-drawn normal, a
  2025-derived aggression multiplier and the league average — no baseline, no variance,
  no replacement level. B7/B8 change how many draws the score sampler consumes, which
  **re-phases the shared numpy stream**, so later draws resample the same distribution.
  Measured over 30 seasons / 3,263 bids: per-season `faab_spent` sd 72.86, SE ±2.1% —
  LARGER than the 2% tolerance. The two biggest deltas are 1.84 SE and 0.83 SE. Fixed in
  the REPORT, not the threshold: `rel_tol` stays 0.02 (pinned by a test, because raising
  it past one SE would hide real changes of the size these constants make) and the drift
  block now names the re-phasing alternative with its measured scale. Not claimed: that
  all six deltas are noise — only that the check cannot tell and said otherwise.
- **F61** this league's FAAB bids do not track model value — MEASURED: on all 26 logged
  2026 claims, using each claim's own FROZEN projection snapshot, correlation(VORP,
  winning bid) = **-0.136**; 20 of 26 claims went to players at VORP <= 0, 12 of those
  above $1, and the largest bid ($25) went to a player at VORP -2.57. The managers here
  bid on news and vacated roles, not on season-mean value. B13's acceptance criterion --
  "v2 closer to the clearing price than v1 on more than half" -- is therefore unmeetable
  by ANY VORP-based rule, and tuning v2 until it passed would be fitting noise at n=26.
  v2 ships beside v1, both labelled unvalidated, with this measurement in the `basis`
  string; B14's ledger is the only route to settling it. A first pass scored old claims
  against TODAY's replacement levels and was discarded as contaminated -- the same
  stale-projection error caught in `live_matchup --tail` the same day.
- **F60** a whitelisted missing asset was imputed as healthy and available, whatever the
  roster said — RESOLVED: found live while evaluating a three-way trade that `apply_trade`
  refused, claiming a team was over the active-roster limit before any trade. A rostered
  player with no projection is imputed from the hand-typed `KNOWN_MISSING_ASSETS`, which
  has no availability fields, and `self.meta` carries only pos/team — so `on_ir` and
  `injury_status` reached `engine.baselines` from nowhere. `_active_count` then overcounted
  the team (every trade involving it refused), and `_initial_absence_clock` gave the player
  NO absence clock, simulating an IR'd player as available all season (he now draws a
  15-week clock). Fixed by taking availability from the roster file, the same authority the
  block already uses for `bye` and cross-checks `team`/`pos` against. PATCH: the three
  engine golden fixtures carry `on_ir: None` so nothing moves (15/15 byte-identical).
- **F59** the model-health verdict threshold was an unsourced literal, and it is
  unreachable — RESOLVED (hygiene; the VALUE stays unverified with a measurement plan):
  `'Calibrated & Learning' if mae < 18.0` cited nothing (rule 5). Moved to
  `config.TEAM_MAE_HEALTH_THRESHOLD` with the derivation B9 asked for, and that derivation
  answers B9's open question: `team_scoring_mae` sums the first 13 players in ARBITRARY
  Sleeper roster order against SEASON means with no week adjustment, making it far cruder
  than the full simulation — whose own 2025 team-week MAE on the same target is 22.36
  (naive baseline 26.54). 18.0 therefore asks the crude estimator to beat the whole engine
  by 4.4 pts/team-week, so the favourable verdict is close to unreachable and the live
  29.64 at n=2 is NOT evidence of a sick model. Value deliberately unchanged so the commit
  moves the number without moving the verdict; not golden-pinned (the golden hashes the 17
  stage-A args and FIXTURE_INPUTS, and model_learning_report is neither), so PATCH.
- **F58** an empty projection payload silently overwrote every baseline — RESOLVED:
  found while enumerating B6's sites, whose grep (`except Exception:\s*$`) required the
  handler to end the line and so missed three inline `except Exception: pass` — two of
  them guarding the PRIMARY source. Both Sleeper projection endpoints failing left
  `projections` empty, so the baseline loop never ran and `save_json` overwrote
  `player_baselines.json` with `{}` — while nothing raised, so the manifest said ok:True
  and `check_freshness`, seeing a freshly-written file, said OK. The existing test asserted
  the right property (`no projections -> no invented baselines`) but patched `save_json`,
  so it watched the return value while the damage happened at the write. Now REFUSES
  (raises, naming both endpoints' causes); the previous sync's baselines survive. Never
  fired in production.
- **F57** an empty source and a quiet source produced the same manifest — RESOLVED: the
  manifest recorded warnings but no positive statement of what each source DELIVERED, which
  is the gap F52 hid in for a fortnight. Added a source ledger, a `sources` block
  (`{name: {ok, rows, fallback}}`) covering 12 sources, and `assess()` reading zero rows as
  DEGRADED with or without a warning. Five of B6's six silent fallbacks made loud, one
  (the dummy league's roster loop) deliberately left silent with the reason recorded —
  its success and failure are indistinguishable by construction, the F41 cry-wolf shape.
  Loop sites aggregate: one notice per source per sync, never one per iteration.
- **F56** the projection log could not say which build wrote it — RESOLVED: rows carried
  `synced_at` and no code identity, across 77 distinct sync stamps (24 inside week 2),
  so January's mandated partition at two non-coinciding boundaries (F49's scoring change,
  F52/F54's blend restoration) had to be hand-matched against `git log`. Fixed with a
  SIDECAR — `sync_provenance.jsonl`, one row per sync, joined on `synced_at` — rather
  than the per-row fields backlog B5 specified: `golden_sync` hashes the projection log
  byte-exactly, so widening that schema would have forced a MAJOR regeneration, and a git
  hash inside a byte-pinned file would have broken the golden on every subsequent commit.
  `espn_rows` makes the blend boundary mechanical (0/149 → 110/150 at
  2026-09-20T16:59:41Z). 77 historical syncs backfilled idempotently as
  `schema_version: 0`. Companion `docs/EVALUATION_BOUNDARIES.md`;
  `SEASON_2026_EVALUATION.md` untouched.
- **F55** weather is fetched every sync and read by nothing — OPEN, plan recorded: the
  Open-Meteo call at `sync.py:333-347` is live and populating `wind_mph`/`precip_prob`
  for 24 of 32 teams; every consumer site is a default-dict literal that never branches
  on them (Phase 3 finding 9, still "reported"). NOT fixed on intuition: the team-level
  effect is already priced by the Vegas total, so a multiplier would double-count. The
  real gap is POSITIONAL — `_script_multiplier` reads total and spread only, and weather
  redistributes within a total rather than scaling it. Offseason study designed and its
  adoption bar fixed in the entry. Three data faults to fix first: `precip_prob` is a
  probability not an amount, both values are daily maxima not game-time, and the fetch
  swallows failures so a dead endpoint reads as a calm day. **All three FIXED
  2026-09-23 (B18), the repair only and not the study:** `precip_in` is an accumulation
  in inches over the 3-hour window from kickoff (`precip_prob` kept beside it as the
  window max, since the two answer different questions); wind is the window MEAN, not the
  day's peak; and a failed lookup now stores NULLs with `weather_source` one of
  `forecast` / `dome` / `unavailable` / `no_game`, which also closes the live hazard
  without removing the fetch. Two things the fault list missed, both found by doing it: a
  night kickoff spans two API days (a single-date request would have dropped every night
  game, the population where wind matters most), and a DOME is known-calm rather than
  unknown — folding it in with failures would have discarded a third of the study's clean
  control group. Verified live: 24 forecast, 8 dome, 1 no_game, 0 unlabelled. Still OPEN
  because nothing reads the fields: the data is now worth studying; the study has not
  run. Live hazard: a populated field nothing reads looks exactly like a working feature — how F52 hid.
- **F54** the Bayesian blend reached 157 of ~1,140 players — RESOLVED, **MAJOR**:
  `_extract_weekly_player_scores` read `players_points`, which carries only ROSTERED
  players, so ~750 projected players kept an untouched preseason prior — exactly the
  pool every waiver claim is drawn from, making every free-agent comparison rigged
  against whoever had started producing (Mahomes, Shough, Lloyd, Van Ness all missed in
  one week). Second defect in the same area: replacement was computed three lines BEFORE
  the blend, so VORP compared a blended mean against an unblended replacement line.
  Fixed via a league-wide stats feed unioned under the authoritative matchup values, and
  by moving `_calc_replacement_levels()` after `_apply_bayesian_updates()`. Blend now
  reaches 919. RB/WR replacement falls, LB/DB rises. Goldens regenerated (week06 only;
  wins conserved, std 147.74 -> 141.87). **Open half CLOSED 2026-09-23** (backlog B8):
  `pass_catchers_meta`/`nfl_position_groups` now build below the blend, so vacated
  injury volume is apportioned on posterior rather than preseason means. The
  mean-weighted WEIGHTING RULE is untouched and pinned by a test — F24 measured it
  correct and CLAUDE.md lists it as deliberate; B8 changed its inputs, not the rule.
- **F53** the luck ledger: five pre-registered measurements — BUILT: three seasons of
  "am I actually cursed?" made answerable by fixing the definitions in writing BEFORE the
  data (`docs/LUCK_LEDGER.md`), since any specific sequence is improbable after the fact.
  Schedule luck, opponent luck, close games, DNP luck, scoring luck — each differenced
  against the LEAGUE, never an absolute, because the engine's own bias (−2.12, cover80
  0.654) would otherwise be reported as one team's luck. No combined score, by design.
  Standing result: schedule luck −1.14 in BOTH completed seasons, pooled z ≈ −0.88,
  p ≈ 0.38 — a real lean, still indistinguishable from chance. DNP luck runs the other
  way (fewer absences than the league). Not wired into the weekly report, at the owner's
  request. MINOR.
- **F52** the ESPN blend was silently off from week 2 — RESOLVED:
  `fetch_espn_projection_data` never passed `week` to `free_agents()`, so espn_api
  returned the inactive dummy league's `current_week` (0) plus week 1. `stats.get(1)`
  worked; every week after found nothing. Projection log: 5,656 week-1 rows carry an
  `espn_mean`, **0 of 3,020 week-2 rows do**. Killed three channels — the ESPN half of
  the QB/RB/WR/TE mean, the `source_disagreement` epistemic signal (std_epistemic fell
  back to positional defaults), and F29's K/IDP subscore. F36's silent-failure guard
  fired only on a raised exception, never on an empty return, so nothing was logged;
  that half is now closed too. Fixed by passing the week. **MAJOR pending** — changes
  `player_baselines.json` for every offensive player with no constant touched, which the
  goldens cannot detect; re-sync deliberately held to Tuesday's window rather than moved
  under a live matchup. January calibration: week 1 was blended, week 2 was not.
- **F51** the live tracker carries no availability discount — RECORDED, deliberate:
  a pre-game starter is carried at his FULL week expectation, with no inactive or
  in-game-injury haircut, so the tracker reads "if everyone plays" and sits ABOVE the
  simulation's expected_total. Kept on purpose: over a season availability is actuarial
  and the engine prices it; inside one live matchup it is a decision the owner hedges by
  hand. The optimism is near-symmetric when both rosters carry similar Questionable
  counts (week 2: Olave vs Nacua), so the margin survives — but check that before
  trusting a live win probability. Related standing hazard: `Questionable` is in NO
  tool's absence set anywhere in the repo.
- **F50** the live tracker quoted a number no other tool quotes — RESOLVED:
  `scripts.live_matchup` read `player_baselines.json` and used the raw season mean,
  skipping BOTH the engine's 4:1 Bayesian blend against observed scores AND
  `week_expectation()`'s environment/script adjustment. Error up to 9.9 points on one
  starter (Kenneth Walker 15.26 vs 25.14) and asymmetric across rosters, so margins
  moved, not just totals: week 2's median leg was reported 46.5% when it was 64.4%.
  The sd was understated too (aleatoric only, no epistemic). Fixed by a new
  `week_projections()` seam; F43's tests had pinned the clock arithmetic but never the
  source of the mean.
- **F49** mid-season IDP scoring change: the evaluation boundary — RECORDED, not a
  defect: this league's IDP categories stack, so one solo sack was worth 8.5 (sack 4.0 +
  TFL 2.0 + solo 1.5 + QB hit 1.0) — more than a receiving touchdown — which is almost
  certainly what produced T.J. Watt's +4.84 sd week 1. The league cut sack to 2.0 and QB
  hit to 0.5, effective the following week. No code change needed (sync reads scoring
  live from the league object), and SEASON_2026_EVALUATION.md is deliberately NOT edited.
  The January analysis must PARTITION criterion 1 at the change date, may read criterion 2
  unchanged, and must not read any criterion-3 coverage improvement as success — cutting
  the fattest scoring tail narrows real dispersion on its own.
- **F48** hand-run reports were pseudonymous, and the flag only ever added a key —
  FIXED: SHOW_REAL_TEAM_NAMES was opt-in (so always forgotten) and, even when set,
  appended a "LOCAL VIEW" legend rather than substituting, leaving every table reading
  the fictional name. Local reports now substitute throughout with a PRIVATE banner; the
  default lives in the CLI, not the library, because putting it in the library made the
  hermetic test suite start doing live fetches mid-render. GITHUB_ACTIONS hard-blocks
  ahead of any explicit flag. Closed a real hole in passing: make_sample_report defended
  the published page by POPPING the variable — sufficient only while unset meant off —
  and its forbidden list never contained the real names at all.
- **F47** two alarms promised to clear themselves and could not — FIXED: four
  workflows raise an `Automation failure` issue, and the body each writes says it
  auto-closes on the next success, but canonical-run and pages-sample had no
  clear-on-success step at all. Both stale alarms were closed by hand after the fault was
  already fixed (#8 after F40, #10 after F46) — the same invisible-monitor-state class as
  F41 and F44, and the one that ends with alarms being ignored. Both now clear; a new
  workflow test holds every alarm to the promise its own issue body makes.
- **F46** the public sample builder was pinned to week 1 — FIXED: make_sample_report
  read its generated embed back from `data/decisions/week_01/archive` while the weekly
  report writes to the engine's CURRENT week, so every scheduled pages-sample run died on
  FileNotFoundError from the moment the NFL week rolled. Only the public sample's
  freshness was at risk (Pages keeps serving the previous build), which is why it would
  have served a frozen week-1 sample for sixteen weeks had the failure alarm not been
  read. Now globs week_*/archive and takes the newest timestamped embed; a FAILED digest
  raises instead of being skipped.
- **F45** the waiver table ranked a WEEK decision on a SEASON number — FIXED, caught
  mid-decision: it recommended a DB with the better season VORP over one whose team had
  the league's highest week-2 implied total and who the engine put ahead at 54.6% for
  one less FAAB. In an 8-team league 83% of projected players are free agents, so the
  season spread among top free agents (0.5-1.2 pts) is smaller than the matchup swing it
  ignored (3.4-8.8) at every position without a moat. VORP still selects; the week now
  orders, each row carries its season rank, and the sampling pool doubled — which
  surfaced a season-rank-14 kicker as the joint-best claim of the week. Season-level
  columns in both tools are now labelled szn mean / szn VORP / szn rep.
- **F44** run3_tuesday could never be covered, and would have reported MISSED all
  season — FIXED, found on the season's first Tuesday: run3 sits after the week's games,
  so the report it triggers prices the NEXT week and its canonical row is stamped week
  N+1, while the cycle still targets week N and coverage matched the target week exactly.
  Both runner fires had succeeded with 32/32 vegas lines and the window still read
  uncovered. Left alone: 16 false MISSED verdicts, a weekly desktop alarm, and a
  "permanent gap" comment written into the audit trail every week about a record that
  was never missing. run3 now accepts a target-week OR next-week row; run1/run2 stay
  strict because nothing has rolled before a week's games.
- **F43** live in-game tracking existed only as throwaway scripts — BUILT, after
  week 1 was tracked all day from ad-hoc scratch files and the first answer given was
  wrong (a naive projection credited nothing for the remainder of in-progress games:
  read 58% when the honest number was 25%). scripts.live_matchup models banked points as
  certain and only the remaining clock as variance (mean*f, sd*sqrt(f)), draws the median
  leg jointly over all eight rosters, and is deliberately read-only — a halftime number
  is not a pre-registered quote. 20 pure tests.
- **F42** the report fetch filtered on run SUCCESS and hid the week's primary record —
  FIXED: week 1's Sunday canonical run committed its row and uploaded a 6.7 MB artifact,
  then failed on F40's cosmetic summary step, so --status success skipped it and the
  archive silently lacked the week's market-informed record. A run's conclusion is not a
  proxy for "produced an artifact"; the filter is now --status completed and the download
  itself is the existence test. F40's second bill, in a different tool, the same week.
- **F41** local coverage was blind to windows the runner covered — FIXED, found live
  the same hour: the desktop popup called week 1's Sunday window MISSED while the
  runner-side watcher had it covered, because the local checker read untracked digest
  FILENAMES and the runner read the committed predictions log. Correct when only humans
  ran reports; wrong the moment F36 tier 2 started covering windows itself, and it would
  have cried wolf after every runner-covered window. The local checker now unions both,
  deduplicated on timestamp.
- **F40** a false `[ ... ] &&` test failed the canonical run after it had succeeded —
  FIXED, found live on the first automated canonical run that proceeded: the cosmetic
  job-summary step returned 1 because both of its `MODE` tests were false on an `auto`
  run, and a bash step's final exit status is the step result — so the job went red and
  alarmed with the canonical predictions row already committed and pushed. Same class as
  the kickoff-day parse error, invisible to the `bash -n` guard because the line parses.
  Rewritten as `if` blocks; a new exit-status lint forbids statement-level `[ ... ] &&`
  in workflow bash unless neutralised, and caught two latent occurrences in
  windows-watch.
- **F39** the odds payload is the whole season; sync kept the wrong week — FIXED,
  found live two games in: the odds endpoint returns every remaining game (213 across 54
  dates), the unfiltered loop left each team holding its LAST listed game, and the
  engine's Phase-3 guard then refused all 32 lines — so every forecast from the odds
  gate opening onward, the pre-registered baseline included, ran matchup-blind. Sync now
  keeps only games matching the week's schedule (the engine's own rule at write time):
  28 of 32 teams carry real week-1 lines, up from 0. Prediction rows also gained
  vegas_lines_used/total, so provenance can no longer claim lines the engine discarded.
  Goldens byte-identical, gate unmoved.
- **F38** the vegas fallback warning does not say WHY a team has no line — OPEN,
  found live on kickoff night: a completed game leaves its teams without a market line,
  and the unclassified warning would have refused a canonical row at every remaining
  Sunday/Tuesday quote. Classified benign the same night (a real odds failure is still
  caught by the separate source check, test-pinned); the tracked residual is that bye,
  already-played, and a genuinely partial payload all share one warning text.
- **F37** league-identity pseudonymization — BUILT: league IDs to env/secrets (a
  committed Sleeper ID resolves to real identities via the public API), TEAM_NAME_MAP
  re-keyed by roster_id, fictional team names throughout HEAD (code, docs, fixtures,
  committed logs) via a committed migration whose real-name map lives only in the
  owner's untracked data/local/. Goldens and sync golden regenerated; the behavior
  baseline regenerated with ZERO drift (rename measured behavior-inert). The
  pre-registered evaluation re-locked with a dated names-only note before any game.
  The owner's local reports overlay real names via an env-gated LOCAL VIEW legend
  that runners never see. History retains the pre-migration record by design.
- **F36** canonical runs on GitHub Actions (tier 2) — BUILT: scheduled canonical runs
  gated mechanically (ABORT / REPORT_ONLY-with-artifact / CANONICAL_OK by explicit
  allowlists; unrecognized blocks conservatively), every failure opening an issue whose
  remediation gives the verbatim command, and the canonical row carrying durable
  provenance (vegas source, degraded count, runner flag). The owner-required replay
  against all 8 recorded real sync states caught a false blocker (the whitelisted-
  Tyson warning) and a gate-by-construction abort (the export criterion) before either
  could ship; live post-fix verdict CANONICAL_OK. Also surfaced: silent ESPN-failure
  fixed. The gate also carries a projection-pool floor (700, derived from the two
  recorded populations 888/964, one-sided) for the partial-fetch case nothing else
  detects. Retention: 90-day artifacts + milestone release assets, no orphan branch.
  Reliability review after real weeks of runner evidence stays on.
- **F35** the behavioral-plausibility harness — BUILT: sim mechanic rates measured
  against the real 2025 league (report-only, filed gaps annotated, never wallpaper)
  plus a deterministic committed baseline whose drift fails — the F31-class discovery
  method made permanent. First run: 8 of 9 mechanics in-band; trades under, as filed.
  The sandbox's live-log severance is now pinned by a regression test.

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

# Systematic Audit Plan

Working document for a full mathematical and software-engineering audit of this codebase.
Structured as independent phases so each can be run as its own session with a clean context.

**Codebase at time of writing:** 5,251 lines across 17 Python files (~2,900 production,
~1,900 tests, ~80 scripts). 72 tests passing.

---

## Guiding principle

The defects found so far did not come from reading code top to bottom. Every one came from
asking a *property* question and checking it empirically:

| Defect found | Property that exposed it |
|---|---|
| Vacated volume awarded 3x over | Conservation: does total out == total in? |
| Vacated volume overwritten on 2nd injury | Conservation: is the pool accumulated? |
| H2H matrix exported transposed | Consistency: does export rank-agree with outcomes? |
| Injury pass order-dependence | Invariance: does output depend on iteration order? |
| Unbounded per-player score | Bounds: what is the 99.999th percentile? |
| `player_scores` always empty | Liveness: does this field ever hold real data? |
| `h2h_win` hardcoded 0 | Liveness: same. |

So the audit is organised **by property class**, not by file. Each phase names the invariant,
the way to test it, and the artifact it produces.

---

## Phase ordering and why

**Phase 0 must come first.** Two methods dominate the engine — `run_simulation` (~445 lines)
and `export_and_visualize` (~333 lines) — and there is currently no golden-master test. Any
refactor of those methods today is unfalsifiable: nothing would prove behaviour was preserved.
Item 4 in the earlier work is the cautionary case — a refactor widened the blast radius of two
latent bugs before they were found.

**Engineering comes last, not first.** The instinct is to decompose the big methods early to
make auditing easier. Resist it. Refactoring before the maths is understood risks freezing a
wrong model into a prettier shape, and refactoring before a golden master risks silent
behavioural drift. Audit → understand → then restructure with a net.

---

## Phase 0 — Reproducibility harness

**Invariant:** the same inputs produce byte-identical outputs, and any intended change is
measurable in magnitude.

- Audit seeding. `np.random.seed(1000 + batch)` uses sequential seeds across batches. Sequential
  Mersenne Twister seeds are not guaranteed to yield independent streams; if batch streams are
  correlated, the cross-batch standard error (`Playoff_SE`) understates true uncertainty. Test
  explicitly; migrate to `np.random.default_rng(SeedSequence)` spawned children if confirmed.
- Build a golden-master test: fixed seed + committed fixture inputs → hash of all outputs.
- Establish a runtime/memory baseline (current: 10 batches x 1,000 sims).
- Add a `--seed` CLI flag so runs are reproducible from the command line.

Gap 1: the hashes are platform-locked

On Linux/Python 3.12, 6 of 84 fail with every moment delta exactly 0 — sum identical to six decimals. Pure last-ulp representation difference. Regenerating locally gives 84/84.

The harness's own error message anticipates this, which is good design, but it isn't resolved. Consequences: Phase 8's CI item fails on any Linux runner out of the box, and anyone cloning your repo sees 6 red tests — a bad look for the showcase goal.

Gap 2: the sync pipeline is completely uncovered — and this isn't in the findings' gaps list

I perturbed VOLATILITY_CONSTANTS['QB'] by 0.6% and the golden master stayed green. That turned out not to be a defect: the constant is referenced zero times in the engine. It's applied in sync.py and baked into the fixtures as a derived value (std_aleatoric: 5.31).

So the golden master covers run_simulation and export_and_visualize only. VOLATILITY_CONSTANTS, EPISTEMIC_ERROR_RATES, PRESEASON_DEFENSIVE_PRIOR, and DEF_RATING_SHRINKAGE_N0 can all change with the suite fully green.

That matters because Phase 7 is explicitly about recalibrating those exact constants. Walking into it believing you have a safety net you don't have is the specific failure mode this whole audit structure exists to prevent.

**Deliverable:** `tests/test_golden_master.py`, a committed fixture set, a perf baseline number.

---

## Phase 1 — Conservation and invariants

**Invariant:** nothing is created or destroyed that shouldn't be.

- Points: team weekly total == sum of its 13 starters, always.
- Vacated volume: total apportioned <= total vacated (now fixed; lock it with a property test).
- FAAB: budget never negative, spend + remaining == starting budget.
- Roster slots: exactly 13 starters every week; streamers fill exactly the unfilled slots.
- Probability normalisation: `Playoff_Pct` sums to 400, `Champ_Pct` to 100 (verified holding;
  make it an assertion, not a spot-check).
- Injury clocks: monotonically decrease, never negative, never exceed 16.
- Bye weeks: a player on bye never scores and never absorbs vacated volume.

**Method:** property-based testing with Hypothesis. This class of bug has the highest historical
yield in this codebase and property testing is built exactly for it.

**Deliverable:** `tests/test_invariants.py`.

**Status: complete.** See `AUDIT_PHASE_1_FINDINGS.md`. 26 tests added (84 → 110), suite green.
Every invariant listed above holds except the bye-week one, which could not be tested at all
because no player has a bye (finding 7).

Eight findings. Six were defects and all six are fixed; two remain open and are recorded rather
than fixed, because neither is a code change:

1. FIXED. H2H "Any Given Sunday" matrix divided by a hardcoded 14 rather than weeks actually
   simulated — every cell deflated to 64% of true value at week 6, correct at week 1. The
   replacement window was measured, not assumed: h2h/all_play/pts_against accumulate inside
   `if week_num <= 14`, giving exactly 9.0000 implied weeks at week 6 against candidates of
   9, 11 and 14.
2. FIXED. `schedule_luck_index` not zero-sum mid-season (+142.86 at week 6, 0.00 at week 1);
   every team reported as lucky. Same hardcoded 14, plus a hardcoded 7 and a 28.0 that also
   assumes `MEDIAN_SCORING_ENABLED`; all three now derived. **Caveat left open:** the two terms
   still cover different spans mid-season (`actual_exp_pct` is full-season including banked
   weeks, `true_win_pct` covers simulated weeks only). Reconciling them needs historical
   all-play from `weekly_actuals` — a feature, not a divisor change.
3. FIXED. `avg_points_against_per_game` divided by 14 regardless of weeks played (113.75 vs
   176.94).
4. FIXED. `weekly_score_percentiles` and the KDE chart computed over an array that is 35.7%
   structural zeros at week 6; `p10_floor` was exactly 0.00 for every team, and the chart's
   median-cut line read 112.82 against a true 175.50.
5. FIXED. `Expected_Points` included playoff weeks 15–16 for all 8 teams (+12%), including the
   four eliminated at week 14. Affected week 1 too. Confirmed to touch nothing else: of
   `stage_a`'s 17 outputs only `points` moved, so no standing, seed, berth or championship
   outcome changed.
6. `KNOWN_MISSING_ASSETS` is aliased into `self.baselines` rather than copied, so
   `_apply_bayesian_updates` overwrites a sourced config constant in place. Makes results
   order-dependent and compounds across repeated runs — `std_epistemic` collapses 87% in three
   runs on double-counted evidence. FIXED (deepcopy at imputation; moves no exported number).
   **Phase 0 gap 3:** the golden master passed only because its
   scenario and module ordering happen to be safe; reverse it and all six tests fail.
7. FIXED (bye modelling steps 1–6, 2026-08-28; byes derived from the NFL schedule at sync, engine guards live, fixtures carry byes, `TestByeWeekLiveness` inverted). History of the finding, kept for the record — it WAS: OPEN — **blocks two independent, measured findings.** The bye-week mechanism was dead code end to end.
   Both Phase 2 finding 5 (exclude zero-score weeks from the posterior) and Phase 2 finding 4
   (conjugate posterior update) are statistically correct per-player changes that were applied
   and then REVERTED because the same paired, seeded, points-level backtest on the real 2025
   season showed each making the engine worse against reality (+4.3% and +7.4% points bias
   respectively): the zeros in history and the extra shrinkage were each an accidental
   compensation for absences the engine cannot represent (zero-week share rises 9.6% → 25.3%
   from weeks 1–5 to 6–11). Whatever lands byes must re-attempt both, and the acceptance test
   was originally set at empirical weight ≈0.49 (QB 0.64, RB 0.71, WR 0.11) and REVISED to ≈0.68 in
   bye-modelling step 5a (reasoning under Phase 2 finding 4 below); points bias must not rise.
   STEP 5a RESULT (byes alone, paired at 300 sims): bias +1.47 → −2.29 pts (+1.1% → −1.8%), cover80 0.62 → 0.65 —
   through zero and past it, because the draw side now skips a bye the history side still scored as a game.
   STEP 5b RESULT (+ Phase 2 finding 5, skip zero weeks): bias −2.29 → +3.45 pts (+2.7%), cover80 0.65, i.e. the
   predicted direction, but with a gradient: cp3 −1.4%, cp6 +1.9%, cp9 +3.4%, cp12 +6.9%. The remaining zeros are
   injuries, and a player who is out NOW (last two non-bye weeks both 0) carries 1.0% of rostered prior mean at cp3,
   6.4% at cp6, 8.9% at cp9, 8.2% at cp12 — the same shape as the gradient. Those zeros were the only current-injury
   signal the engine had: neither sync nor the backtest harness ingests Sleeper's `injury_status`, so with finding 5
   fixed an IR player is projected at full strength for the rest of the season. NEW FOLLOW-UP: current-injury-status
   ingestion (F4 below). Finding 5's fix stands: it is correct per game played and the residual is a missing input.
   STEP 5c RESULT (+ Phase 2 finding 4, conjugate posterior, run as written and REVERTED — 2026-08-28): bias +3.45 →
   +13.96 pts (+2.7% → +10.8%), mean z −0.64, cover80 0.65 → 0.56, gradient cp3 +4.3% … cp12 +17.2%. Misses the
   ±1.0-pt bound by 9.5 pts. THE SURPRISE: the weight gate is NOT what it misses. On the real player set the
   conjugate form actually applies w = 0.71 at n≈4 (QB 0.58, RB 0.76, WR 0.75) — inside 0.68 ± 0.05 — while the
   old n₀=4 form applies 0.47, not the 0.71 the formula table implied (real sample variances exceed the floor).
   And re-measuring the empirical target on the engine's own inputs (non-zero pre weeks, as after 5b): vs weeks
   6–11 *excluding* injury zeros it is 0.80 (conjugate 0.81 — calibrated); vs weeks 6–11 *including* them 0.57
   (post/pre 0.884 vs 1.012). So the conjugate posterior is right per game played and the +10.8% is entirely the
   absence the engine does not draw: forward injury onsets under `INJURY_RATES` plus no current-IR input (F4)
   remove far less than the 12.4% of weeks 6–11 that real rostered players actually missed — MEASURED: the engine
   draws 4.1% of bye-excluded rostered player-weeks as absent in weeks 6–11 (0.0% at week 6, 5.9% by week 11; 300
   seasons, cp6) vs 14.7% real zero weeks for the same players and weeks. Under the old form that 10.6-point gap
   was hidden by an under-weighted posterior: the blank-slate prior is far below per-game means (RB 9.0 vs 12.79),
   so w = 0.47 pulled posteriors down (RB 11.05 vs conjugate 12.09) by about the absence factor reality applies
   (12.09 × 0.884 ≈ 10.7) — two errors of opposite sign, and the conjugate form removed one. Finding 4 stays open, its target restated: any posterior
   change is gated on the backtest, which cannot pass until F4 lands; the weight criterion is already met.
   Patch retained at scratch `conjugate_5c.patch` (= 948902f's engine/backtest_player hunks).
   STEP 6 (fixtures carry byes, re-golden, liveness tests inverted): weekly means −1.9% (week01) / −2.3% (week06) at
   30 seasons, direction consistent with 5a. It exposed a LATENT DEFECT: the streamer-need scan looked ahead with
   `min(14, week_num + 1)`, so every week ≥ 15 re-scanned week 14 and counted each rostered bye-14 player as next
   week's hole — phantom bids in weeks 15–17, unreachable while every bye was 0. Characterised (week 16 of week01:
   2 bids, 1 hole, no bye nearby) then fixed in step 6b (`[week_num, week_num + 1]`); the restated
   `TestStreamerNeedsMatchRealHoles` is the guard (equality away from byes, divergence only next to a bye, every hole
   coverable every week). 6b's golden deltas are RNG-reshuffle-sized (the phantom bids consumed uniform draws). Sleeper's payload has no `team_bye` key
   (0 of 12,225 cache entries), so every player has `bye: 0` and the engine's three bye guards
   can never fire. The existing sync test passes only because its fixture invents the field.
   Needs a real bye-week source, which Sleeper does not supply — not a code change.
   `depth_chart_order`, which Phase 7 wants, *is* present (1,812 non-null) and is being discarded.
   Note `_apportion_vacated_volume` has no bye awareness even in principle (it is never told the
   week), so whatever makes byes live must fix that in the same change or a bye-week player will
   be counted in the apportionment denominator and his share destroyed.
8. OPEN, Phase 6. `power_rankings_baseline_pts` is labelled "Optimal Valid Starting Lineup
   Baseline" but `get_optimal_score` returns lineup + 10% of bench (166.8 true vs 173.1
   reported). Deliberate depth reward, undisclosed label. Reported, not fixed.

Findings 1–4 were invisible at week 1 and would have activated from week 2 — production is at
week 1 now, so they were caught latent.

---

## Phase 2 — Statistical core

**Invariant:** the sampler draws from the distribution it claims to.

- Covariance matrix: verify positive semi-definite in all cases; check the Cholesky fallback path.
- Verify realised correlations in simulated output match `SIM_CONFIG['CORRELATIONS']` targets.
- Lognormal parameterisation: confirm `E[X]` equals the intended `mean_val` after the
  `mu = log(mean) - sigma^2/2` correction, including near `mean_val -> 0`.
- Epistemic/aleatoric separation: confirm epistemic is drawn once per season and held, aleatoric
  redrawn weekly — and that this actually widens season-level spread as intended.
- Bayesian update (`_apply_bayesian_updates`): verify the shrinkage weight and posterior variance
  against closed-form conjugate normal results.
- `env_var` is a second multiplicative noise source stacked on the lognormal draw. Quantify the
  total variance it contributes and confirm it is intended, not double-counting.
- Re-examine `MAX_REALISTIC_WEEKLY_SCORE = 80` interaction with the cap on `env_var` tails.

**Deliverable:** `tests/test_distributions.py` plus a short written finding on variance budget.

**Status: complete except deliberate deferrals.** See `AUDIT_PHASE_2_FINDINGS.md`. Findings 1, 2, 6, 7 fixed; 5 fixed then reverted (see below); 3 left in place (partially offsets 2 — fix it after 2 has been validated out of sample); 4 deferred to Phase 3 jointly with `DEF_RATING_SHRINKAGE_N0`, which uses the identical `n_0` construct. Suite: 124 tests, `OK (expected failures=3)` — findings 4 and 5 stay characterised-red under `expectedFailure` with their dependencies recorded.
14 tests added (110 → 124); 7 lock verified properties, 7 characterised defects, 4 of which now pass as regression guards.

Verified and locked: lognormal `E[X] = mean` (engine-level), the `env_var` variance model,
epistemic drawn once per season and held (within-season week correlation 0.247 vs 0.252
predicted; 0 with epistemic off), covariance PSD over 3000 fuzzed rosters, and the cap's tail
behaviour (max exceedance 4.3e-3, mean loss ≤ 0.06 pts/week — no change needed).

Variance budget: `env_var` is **not** a material double-count (~1%). The realised per-player
weekly variance is +17% over `std_aleatoric²` (sd +8.3%, mean +2.8%), and the dominant cause is
the hardcoded `v_tot / 22.0` normaliser against a schedule mean of 22.6 (finding 1).

Eight findings:

1. HIGH, FIXED. Environment multiplier `v_tot / 22.0` averaged 1.028 over the real schedule, not 1;
   `22.0`, `LEAGUE_AVG_PPG = 21.5` and the ratings' ~22.6 mean disagree. Every player, every week.
2. HIGH, FIXED (mix removed). `shared_z` gate was literally "opponent implied total > 23" (open 44% of team-weeks) and
   injects +0.32 score correlation into every same-team QB/WR/TE pair, including WR–WR whose
   calibrated target is −0.004. Confirmed through the real engine (+57 variance, SE 6).
3. LOW–MED. `CORRELATIONS` were measured on scores but are applied on `z`; realised score
   correlations run 12–14% below target even with the gate closed.
4. HIGH (mid-season), OPEN — conjugate fix applied in Phase 3 and reverted on real-data evidence (+8.5% bias); was blocked on bye modelling. TARGET REVISED 0.49 → ≈0.68 (bye-modelling step 5a, 2026-08-28): the 0.49 was measured on bye-contaminated data — each player's bye week sat as a 0 in both the first-five and the weeks-6–11 windows. With the bye week excluded from both windows, which is what the engine now does on the draw side, the same regression (post − prior = w·(pre − prior), slope through the origin, 74 players with ≥4 weeks each side) gives w = 0.68 (QB 0.91, RB 0.85, WR 0.30); the zero-week share becomes 8.1% → 17.6% instead of 9.7% → 25.6% and the weeks-6–11 drop 9% instead of 17%. The two forms already tested sit on OPPOSITE sides of the new target: old n₀=4 gives 0.71 (over by 0.03), conjugate gives 0.81 (over by 0.13). 5c is therefore a genuine recalibration, not a revert-and-reapply of the conjugate form. [SUPERSEDED the same day by the 5c run: the conjugate form APPLIES 0.71 on real players (the 0.81 was the formula-table value at the prior's stated variances, not what real n≈4 histories produce) and the old form applies 0.47; the miss is +10.8% points bias from unmodelled absence, not weight — see the bye-modelling entry, step 5c, and F4.] WR's residual 0.30 is not absence-driven (QB/RB sit at 0.85–0.91 on the same data); it is the `EPISTEMIC_ERROR_RATES` mis-specification, Phase 7's. `_apply_bayesian_updates` is not conjugate: `n_0 = 4` quadruples prior
   precision and the likelihood variance is a 5-sample variance floored at half the prior, not
   `std_aleatoric²`. Offense under-updated (data weight 0.60 vs 0.80), IDP over-updated, posterior
   sd 0.69× closed-form everywhere — which narrows the per-season epistemic draw downstream.
5. MED, FIX REVERTED on real-data evidence (points bias +4.3%, >5 SE); blocked on bye modelling (Phase 1 #7). Zero-score weeks (20/780 in the fixture) are ingested as observed games; the backtest
   excludes them as byes/DNPs. Related to Phase 1 finding 7.
6. LOW, FIXED. PSD repair added δI without renormalising: sd × √(1+δ) for every player on the roster,
   correlations ÷ (1+δ). Never fires on fixture rosters; max δ 0.51 in fuzz.
7. LOW, FIXED. WR3+ received `QB_TE = 0.35`, above WR2's 0.315 — non-monotone in rank.
8. Negligible. `std_aleatoric` not re-derived after a posterior mean shift; contingency points
   not environment-scaled in `expected_pre` (≈ 2% of contingency).

---

## Phase 3 — Data ingestion integrity

**Invariant:** every field that looks live is live; every fallback is loud.

- Enumerate every `except: pass` / `.get(default)` and classify: legitimate degradation vs
  silent data loss. (Two "always empty" bugs already came from this class.)
- ESPN name-matching coverage: what % of rostered players actually match? Unmatched players
  silently lose their second projection source.
- Vegas: verify fallback to `WEEK_1_VERIFIED_VEGAS` and `DEFAULT_FALLBACK_TOTALS` triggers only
  when intended; confirm staleness is detectable.
- Defensive ratings: verify the n_0=4.0 shrinkage against the preseason prior behaves as claimed
  as `games_sampled` grows.
- `team == "FA"` and `team is None` handling across all consumers.

**Deliverable:** a fallback inventory table + assertions on ESPN match rate.

**Status: characterisation complete, awaiting triage.** See `AUDIT_PHASE_3_FINDINGS.md`.
20 tests added (124 → 144); 5 lock verified behaviour, 14 characterise defects, 1 live ESPN
match-rate check behind `RUN_LIVE_INGESTION_TESTS=1`. Nothing fixed. Fallback inventory: 27 sites
classified. ESPN match rate measured live at 97% (rostered) / 99% (all eligible).

Nine findings, plus the bounded `n_0` decision kept separate:

1. HIGH, FIXED (write on every path + `_meta` week stamp + engine refuses stale lines loudly; `ODDS_API_KEY` documented as the real fix). In-season Vegas fallbacks returned
   the flat table but never write `vegas_totals.json`, so the engine applies the week-1 table to
   the current week all season. No week stamp, no warning; detectable via `nfl_schedule` but not
   detected.
2. MED / 2b HIGH-latent, BOTH FIXED (failed weeks recorded and warned; league schedule keeps one entry per week). A failed ESPN schedule week silently flattens that week and drops its
   games from the defensive sample; a failed Sleeper league-schedule week shifts every later
   week's fantasy matchups one index earlier.
3. MED, FIXED (`normalize_position` moved to config; sync applies it first). `VOLATILITY_CONSTANTS`/`EPISTEMIC_ERROR_RATES` were looked up by raw Sleeper position
   (DE/DT/CB/S/FB) → anonymous defaults. 5 rostered DEs affected today.
4. LOW, FIXED. `team: null` reached baselines (2 today); consumers tolerated it individually.
5. MED, MITIGATED (sole rostered claimant keeps the plain name, others suffixed `(pid)`, warnings, raise on two rostered; prior blend now pid-tracked). Full rekey tracked as follow-up F1. Name-keyed baselines/rosters; 2 duplicate names today, last pid won — Byron
   Murphy's committed baseline is the wrong player's.
6. MED, PARTLY FIXED (whitelist team corrected to NO; engine warns on whitelist/roster mismatch; the silent drop itself is still open). Zero-projection rostered player silently dropped, then hand-imputed with team `FA`
   where Sleeper says NO (Jordyn Tyson).
7. MED, FIXED (refresh past 24h or on force=True; loud on failure). Player cache was never refreshed after first fetch.
8. LOW, FIXED (fallback derived from the table mean). Defensive prior fallback was 21.5 vs prior-table mean 22.8 (and 2025 real 23.0).
9. LOW, CLOSED as reported (no code change; `injury_status` is a Phase 4/7 modelling question). Weather, `injury_status`, standings `h2h_wins`/`points_scored` ingested and never read.

`n_0` (bounded piece): the two uses are different constructs — a pseudo-count *is* the
defensive prior's variance (none is stated), but multiplies an already-stated variance on the
player side. Real 2025 data: within-team var 91.4, between-team 7.7 → empirical n₀ ≈ 12, not 4;
the code trusts early games ~3× too much. Player priors already imply ≈1 pseudo-game (offence)
/ ≈10 (IDP) before the ×4. OUTCOME: defensive half APPLIED (`DEF_RATING_SHRINKAGE_N0` 4.0 → 12.0, 2025 derivation as source,
one-season caveat, "consistency" comment replaced). Player half APPLIED THEN REVERTED on the
paired real-data backtest: real-2025 points bias +1.1% → +8.5% (mean z −0.51). Empirical data
weight after five weeks is ≈0.49 (WR 0.11) vs the conjugate 0.81 — absences (zero-week share
9.6% → 25.3%) and a mis-specified prior variance. Blocked on bye modelling (Phase 1 #7) and
Phase 7 re-derivation of `EPISTEMIC_ERROR_RATES`; acceptance target ≈0.49 on that backtest — REVISED to ≈0.68 once bye weeks are excluded (bye-modelling step 5a; full reasoning under Phase 2 finding 4).

---

## Phase 4 — Decision logic

**Invariant:** decisions are optimal given information legitimately available at decision time.

- Hungarian assignment: verify true optimality against brute force on small rosters, including
  dual-eligibility players (`DUAL_ELIGIBILITY`) and FLEX interaction.
- Confirm no lookahead leakage: lineups must use `expected_pre`, never realised `final_score`.
  (Spot-checked as correct; make it an enforced test.)
- Streamer economics: `STREAMER_DECAY_RATE`, `replacement_levels`, and the interaction with
  `won_streamers` from FAAB.
- FAAB bidding: `_compute_faab_bid` is already injectable — test the bid curve directly.
- Trade logic and the 2-week deficit lookahead.

**Deliverable:** `tests/test_lineup_optimality.py` with brute-force cross-checks.

**Status: characterisation complete, awaiting triage.** See `AUDIT_PHASE_4_FINDINGS.md`.
7 tests added (154 → 161); 4 lock verified properties, 3 characterise defects. Nothing fixed.

Verified: the Hungarian assignment is exactly optimal (1,700 random rosters vs exhaustive
search, 0 suboptimal, incl. dual-eligibility and FLEX); no lookahead (49,920 candidate values all
equal the baseline mean while draws varied); streamer needs equal the assignment's unfilled
slots every week on both fixtures. The 2-week deficit lookahead is a no-op until byes exist;
FAAB spend is 3–6 of 100 per season (no bite).

Findings:

1. MED, OPEN — tracked as follow-up F2 (sized). Trades effectively never complete: 0 of 548 evaluations accepted on week01 (100 seasons),
   16 of 691 on week06. The rich team's 6th/7th-best are starters and the offered player is a QB
   99% of the time; the rich side's optimal score falls every time on week01 (max −3.2).
   `MANAGER_PROFILES['trade_will']` therefore has no observable effect.
2. MED (HIGH if 1 is fixed), FIXED (2-for-2: the dropped player is the throw-in; both rosters conserved). A completed trade shrank the rich roster by one (gives two,
   receives one, drops nothing); the desperate side is conserved. Reproduced on a crafted league.
3. MED-HIGH, FIXED (capped at the position's data-derived replacement level; backtest +1.1% → +1.1%, production-like −0.43%). Won streamers were valued by league-wide bid rank (12.0, 11.5, …) regardless of
   position; a rank-1 streamer beats the replacement level everywhere but QB and out-projects
   105 of 156 rostered players. A roster hole at DB/DL/TE/K is an upgrade for ~3.5 FAAB.
4. LOW, FIXED (bye-modelling step 3, f058307: won streamers persist one week via `carried_streamers`; pinned in `tests/test_byes.py::TestStreamerPersistence`). Was: a streamer won for next week's hole was discarded (won_streamers rebuilt
   weekly) while the FAAB was spent this week; unreachable until byes made the lookahead live.
5. CLOSED. Stale `sim_meta` entries after trades removed (no hash movement); non-playoff teams still bid in weeks 15–16 — harmless, and stopping it would reshuffle the RNG stream for no output change.

---

## Phase 5 — Season and playoff mechanics

**Invariant:** league rules are implemented as written.

- Median scoring: 2 decisions/team/week when enabled, 1 when disabled.
- Seeding, tiebreakers, playoff bracket, toilet bowl.
- Schedule-luck decomposition and `all_play_wins`.
- Week indexing: confirm `range(current_week - 1, 16)` and the 14-week regular season line up
  with the real league calendar at every entry point.

**Deliverable:** rule-conformance tests.

**Status (Phases 5 + 6 together): characterisation complete, awaiting triage.** See
`AUDIT_PHASE_5_6_FINDINGS.md`. 12 tests in `tests/test_season_mechanics.py` (161 → 173); 9 lock
verified rules and export consistency, 3 characterise defects. Nothing fixed.

Rules confirmed against Sleeper's live settings: 8 teams, 4 playoff teams, playoffs start week
15 (two rounds, no reseeding), `league_average_match = 1` (median on → 2 decisions/week), trade
deadline week 11. Verified: exactly 8 decisions league-wide every week; seeding by (wins,
points) recomputed per sim equals `seed_matrix`; berths = seeds 1–4; last = seed 8; one champion
per sim from the field; regular-season entry points simulate exactly the remaining weeks.

Findings:

1. HIGH, latent (week 15). INTERIM FIX: `run_simulation` refuses with a ValueError naming F3; graceful bracket-from-banked-standings tracked as follow-up F3 (sized). The engine crashed for any `current_week` ≥ 15 — IndexError at 15
   (`top4` never seeded), KeyError at 16, UnboundLocalError at 17 — and sync writes Sleeper's
   playoff-week numbers straight into `league_state.json`. No bracket-from-banked-standings path,
   no explicit refusal.
2. LOW, FIXED (float). `actual_wins_banked` and the magic number used `int()`, truncating a banked H2H tie (0.5);
   the forecast record then does not add up (banked + future ≠ final).
3. LOW-MED, FIXED by rename (`no_playoff_appearances_in_sample`; no elimination math built). `is_mathematically_eliminated` was `Playoff_Pct == 0.0`: a sample zero. Flags 1 team
   at 16 sims and 3 at 2 sims on the same week06 season.
4. LOW, measure-zero, FIXED (`_playoff_winner`, tested; no outcome change). Tied playoff games advanced the lower seed (strict `>`); Sleeper advances
   the higher.
5. LOW, measure-zero. A score exactly on the 8-team median awards five median wins (`>=`).
6. LOW. `approximate_magic_number = 16 − banked`: unsourced heuristic, labelled approximate.
7. Housekeeping, DONE (deleted locally). `data/Week_1_Scoring_Density_KDE.png` was an orphan from a rename; `data/` is
   gitignored — delete locally.

---

## Phase 6 — Outputs and reporting

**Invariant:** what is exported equals what was computed.

- Audit every `to_dict()`, DataFrame reindex, and `.loc[]` for orientation and alignment.
  (One transposed-export bug already found here; assume siblings.)
- Verify exported percentiles against direct recomputation.
- Confirm chart data matches the JSON for the same run.
- Housekeeping: `data/` contains both `Week_1_Scoring_Density_KDE.png` and
  `Week_1_Weekly_Scoring_Density.png` — likely a stale orphan from a rename. Confirm and clean.

**Deliverable:** export round-trip tests.

**Status:** done together with Phase 5 — see the Phase 5 status block above and
`AUDIT_PHASE_5_6_FINDINGS.md`. Every `to_dict` / reindex / `.loc` in `export_and_visualize` is
covered by the export-equals-computation tests; percentiles, seed probabilities, the H2H matrix
and the forecast record are recomputed directly. The stale `Week_1_Scoring_Density_KDE.png` is
confirmed an orphan (finding 7).

---

## Phase 7 — Backtest and calibration validity

**Invariant:** calibration claims are supported by out-of-sample evidence.

- CRPS implementation vs brute-force reference (already cross-checked once; re-verify).
- Add PIT histograms and coverage tests — sharper calibration diagnostics than CRPS alone.
- Confront the circularity problem directly: constants tuned on 2025 data cannot be validated on
  2025 data. Define what genuine out-of-sample validation looks like using the live 2026 season.
- Re-derive the `INJURY_RATES` for TE/QB/DL/LB/DB, which are currently the least well-sourced
  constants in the model (documented as such in `config.py`).
- Revisit `VACATED_VOLUME_CAPTURE_RATE = 0.65` — carried over, never independently derived.
- Evaluate ingesting Sleeper's `depth_chart_order` to replace mean-weighted apportionment, which
  is known to be backwards in the handcuff case.

**Deliverable:** a calibration report and a written out-of-sample validation protocol.

---

## Phase 8 — Software engineering

**Invariant:** the code is as good as the model behind it.

- Decompose `run_simulation` (~445 lines) and `export_and_visualize` (~333 lines) — only with the
  Phase 0 golden master in place.
- Type hints throughout + `mypy`; `ruff` for lint.
- Replace bare `except Exception: pass` with typed, logged handling.
- Structured logging to replace `print`.
- CI (GitHub Actions): tests + lint + type check on push.
- Performance profile: 10,000 sims x 14 weeks x 8 teams is the hot path; identify whether
  vectorisation is worth it.
- Docstring and README pass; document the model's assumptions and known limitations in one place.

**Deliverable:** CI badge, clean type check, decomposed engine.

---

## Session sequencing

| Session | Phase | Rough weight |
|---|---|---|
| 1 | Phase 0 | Medium — foundational, unblocks everything |
| 2 | Phase 1 | Heavy — highest expected defect yield |
| 3 | Phase 2 | Heavy — hardest mathematics |
| 4 | Phase 3 | Medium |
| 5 | Phase 4 | Heavy |
| 6 | Phases 5 + 6 | Medium — can combine |
| 7 | Phase 7 | Heavy — needs live-season data |
| 8 | Phase 8 | Heavy — largest code churn |

Phases 1–6 are independent once Phase 0 exists and can be reordered or parallelised.
Phase 7 partly depends on accumulating real 2026 results, so it can run late or continuously.

---

## Reproducibility watch — open

### R1 — Intermittent `setUpClass` error in `test_distributions` controlled seasons (first seen 2026-08-28)

**Symptom.** In a full `python -m unittest discover tests` run, `TestWeeklyDrawMoments` and/or
`TestEpistemicStructure` fail in `setUpClass` → `controlled_season` → `run_simulation`, at the
PASS-1 onset scan (`p_pos = normalize_position(p_meta.get('pos', p_info.get('pos', 'FLEX')))`)
with `TypeError: descriptor 'get' for 'dict' objects doesn't apply to a 'str' object`. One
further run terminated with process exit code 5 and no verdict. Frequency: 3 of ~9 ordinary
full runs on branch `audit/f5-forward-absence` (working tree, `data/` present); the rest were
`Ran 206 tests, OK (skipped=1, expected failures=5)`.

**What is ruled out (all 0 failures):** `test_distributions` alone ×3; the modules that run
before it (`test_backtest_player`, `test_backtest_season`, `test_byes`) + `test_distributions`
under `PYTHONHASHSEED` 0–5; the full suite on a clean detached worktree of `main` (a4368c7)
×3 without and ×3 with `data/` copied in; the full suite ×4 in this tree with a diagnostic
`RuntimeError` inserted immediately before the failing line (checking `sim_meta[t_name]` and
`self.baselines` are dicts) — it never fired; the failing pair ×3 under `-X faulthandler`.
The new F5 tests (`test_injury_status`) run AFTER `test_distributions` in discovery order and
cannot affect it at run time; the failing statement is preceded by `isinstance(…, dict)` guards
on both operands, so the message is not consistent with the code as read. Nothing in the
working tree rebinds `dict`; no test patches `builtins` other than `open` in `test_sync`
(which runs later).

**Standing instruction.** Count every full-suite run from here on; if it recurs, capture the
run with `-X faulthandler -v` to a file and record the test that ran immediately before the
failing class. Do not mark this closed on the strength of clean runs alone — it was 0/16 under
observation and 3/9 without.

## Tracked follow-ups (outside any phase's branch)

### F1 — Rekey players by Sleeper `player_id` instead of full name

**Origin:** Phase 3 finding 5. Every player-keyed structure in the pipeline is keyed by
`f"{first_name} {last_name}"`, and Sleeper has duplicate names (two Justin Jeffersons, two
Byron Murphys as of 2026-08-28). The interim guard — `sync.resolve_player_keys`, which gives the
sole rostered claimant the plain name, suffixes the rest as `"Name (pid)"`, warns on every
collision and raises if two rostered players collide — makes corruption loud and self-correcting,
and stores `player_id` inside each baseline so the sync-to-sync prior follows the player across a
key flip. It does not remove the limitation: two rostered same-name players cannot be represented.

**Scope (measured, not estimated):**

| area | what changes | size |
|---|---|---|
| `sync.py` | key `baselines` and `weekly_actuals.player_scores` by pid (keep `"name"` inside the entry); add `"player_id"` to each `live_rosters` entry (additive); resolve `DUAL_ELIGIBILITY` (8 names) and `KNOWN_MISSING_ASSETS` (1) name→pid at sync so config stays readable | 3 minting sites already have the pid in hand |
| `simulation.py` | key = pid throughout — 62 lines / ~14 dicts, all opaque to the engine; display name via `baselines[pid]["name"]` at the three output sites (audit-log starters and `injury_ward`, MVP list, whitelist warning) | mechanical |
| `backtest_season.py` | pass pids through (it has them); blank baselines keyed by pid | small |
| `backtest_player.py` | standalone name-keyed analysis: leave, add the collision guard | small |
| `storage.py`, clients | nothing | 0 |
| golden fixtures | regenerate both scenarios' `live_rosters` (156 entries each), `player_baselines` (964 keys), `weekly_actuals.player_scores` (780 keys, week06) from `data/`; re-golden | scripted |
| `tests/test_simulation.py` | 52 name entries / 47 literal baseline dicts gain a `player_id` | the bulk of the churn |
| other test files | `test_distributions` 4/3, `test_ingestion` 3/3, `test_sync` 4/1, backtest tests ~1 each | small |

Roughly 6 production files and 150–250 lines, plus fixture regeneration. The risk is in the
test churn, not the logic.

**Sequencing:** one branch, one behaviour-changing commit for sync + engine + fixtures (an
intermediate state with only one side rekeyed cannot run). Preceded by the existing collision
characterisation test; verified not by the golden hashes — key strings change the canonical
JSON, so they move regardless — but by a one-off equivalence run: pre-rekey and post-rekey
engines on the same real data, every `stage_a` output asserted identical after mapping
pid→name. Regenerate goldens only after that equivalence holds.

**When:** after Phase 3 closes. Engineering-shaped, data-integrity motivated; pairs naturally
with Phase 8 if it has not been done by then.

### F2 — Make the trade mechanism live

**Origin:** Phase 4 finding 1. Every offer is "the desperate team's best player for the rich
team's 6th- and 7th-best". In a 13-starter format the rich team's 6th/7th-best are starters
(medians 12.7 / 12.2), the offered player is a QB 99% of the time (highest means), and the rich
team already starts an equal-or-better QB in 49–78% of cases — so the rich side's optimal score
falls on essentially every evaluation. Measured: **0 of 548** evaluations accepted over 100
week01 seasons, 16 of 691 on week06. `MANAGER_PROFILES['trade_will']` therefore has no
observable effect; the characterisation test `test_trades_are_live_on_the_preseason_fixture`
stays red until this lands. Roster conservation (finding 2) is already fixed, so a live mechanism
will not shrink rosters.

**Scope (sized, not implemented):**

| piece | what | size |
|---|---|---|
| offer construction | replace "best-for-6th/7th" with a position-aware search: the desperate side offers the player whose loss costs its own optimal lineup least while filling a rich-side lineup hole (or upgrading the rich side's weakest starter at that position); the rich side gives bench depth at positions where the desperate side is short | ~40 lines, inside the existing week-6–10 block |
| acceptance | keep "both optimal scores improve" (`get_optimal_score` already includes the 0.1 × bench term, so depth is valued) | unchanged |
| RNG | the block's `rand()` calls already depend on the standings; any change here reshuffles the stream from week 6 on, so size effects at ≥400 seasons, not from the 30-season summaries | — |
| acceptance criterion for the work | **Numbers, so a future session knows what done is.** (a) On the week01 fixture over 100 seasons (2 × 50), completed trades per simulated season **≥ 1.0 and ≤ 4.0**, league-wide. Today: 0.00 on week01, 0.16 on week06; the block evaluates ~5.5 offers per season, so 1.0 is ≈ 18% of evaluations — real activity for an 8-team league, and 4.0 caps a degenerate churn. (b) `test_a_completed_trade_conserves_roster_sizes` still passes. (c) The paired, seeded, points-level 2025 backtest (`bt_points` procedure, 300 sims) changes by **≤ 0.5 pts in mean bias and ≤ 0.05 in mean z** versus the commit immediately before — the measured run-to-run noise of that procedure on identical inputs is well inside this, so any larger move means the redesign leaked into scoring. (d) Effects sized at ≥ 400 seasons, not from the 30-season golden summaries | measured, not asserted |
| tests | flip `test_trades_are_live_on_the_preseason_fixture` from characterisation to guard; keep `test_a_completed_trade_conserves_roster_sizes` | small |

Roughly one file, ~40–60 lines, plus a golden regeneration in any scenario where a trade
completes (both, once it works). The design question that is *not* engineering — what offers
real managers make — is Phase 7-adjacent; the sizing above assumes the simplest symmetric
lineup-improving search, not a calibrated behavioural model.

**When:** any time after Phase 4 closes; independent of F1.

### F3 — Simulate from inside the playoffs (bracket seeded from banked standings)

**Origin:** Phase 5 finding 1. `run_simulation` seeds the playoff bracket (`top4`) only by
simulating week 14, so a run starting at `current_week ≥ 15` had nothing to seed from and
crashed (IndexError at 15, KeyError at 16, UnboundLocalError at 17). Sleeper reports 15–18
during and after the playoffs and sync writes that straight to `league_state.json`. The
interim guard — a `ValueError` at the top of `run_simulation` naming this entry — turns the
crash into a statement; it does not make playoff-week forecasts possible.

**Scope (sized, not implemented):**

| piece | what | size |
|---|---|---|
| bracket from banked standings | when `current_week ≥ 15`, rank teams by banked `(h2h + median wins, points)` from `weekly_actuals` / `league_standings` — the same `(wins, points)` key the week-14 block uses — and set `top4` before the week loop; when `current_week == 16`, also resolve week 15 from `weekly_actuals` (the semi-final results are real by then) to set `w1`/`w2`. **Reuse note:** the *seeding* sort is new logic (nothing ranks banked totals today); the *game resolution* — a banked semi-final from real points, or any still-simulated round — is exactly `_playoff_winner(a, b, scores, top4)` from Phase 5 finding 4 (score, then higher seed on a tie, which is also Sleeper's rule for real results). Do not write a second tie rule. | ~25 lines before the loop |
| loop guards | the week-14 seeding block and the week-15 resolution must not re-run for weeks already banked; `assert week_num >= 16` after the loop stays valid | ~5 lines |
| regular-season outputs | `wins`, `trajectories`, `seed_matrix`, `b_playoffs`, `b_toilets`, all-play, h2h and the schedule-luck fields are regular-season quantities; from week 15 they are fully banked, so the exporter must treat `weeks_simulated = 0` for them (the Phase 1 divisor `REGULAR_SEASON_WEEKS − (current_week − 1)` goes to 0 or negative — the `assert weeks_simulated > 0` added in Phase 1 fires today) and report banked values rather than divide | ~20 lines in `export_and_visualize`, plus the two charts that assume 14 columns |
| `current_week ≥ 17` | season over: refuse, or export the banked final state with `Champ_Pct` ∈ {0, 100} | decision |
| tests | flip `test_playoff_and_post_season_entry_points_fail_loudly_not_with_an_internal_error` to "these weeks run"; add a fixture at `current_week = 15` with banked week-1–14 actuals (a third golden scenario) and assert `b_playoffs` ∈ {0, 1} per team and Σ`b_champs` = sims | a new committed fixture set |

**Acceptance criterion:** on a `current_week = 15` fixture, `b_playoffs[t]` is exactly 0 or 1
for every team (the field is banked), `Playoff_Pct` sums to 400 and `Champ_Pct` to 100 (the
Phase 1 normalisation tests, unchanged), every export field the Phase 5/6 tests recompute still
matches, and the two regular-season golden scenarios are byte-identical (nothing before week 15
changes). Roughly 50–70 lines across `run_simulation` and `export_and_visualize`, plus the
fixture. Touches no baseline computation; the backtest gate does not apply.

**When:** before **Tuesday 2026-12-15** if playoff-week forecasts are wanted. That is the day
Sleeper's `/state/nfl` rolls to week 15 — NFL 2026 week 14's last game kicks off Monday
2026-12-14 (8:15 pm ET) and week 15's first game is Thursday 2026-12-17 (8:15 pm ET), per
ESPN's published 2026 schedule (`scoreboard?week=15&seasontype=2&dates=2026`, 16 games, fetched
2026-08-28). The first sync on or after 2026-12-15 will hit the interim refusal. Otherwise any
time.

### F4 — Ingest current injury status (a player who is out now is not a full-strength draw)

**Origin:** bye-modelling step 5b (2026-08-28). With byes modelled and Phase 2 finding 5 fixed
(zero weeks no longer scored as games), the paired real-2025 backtest bias runs −1.4% at cp3 but
+1.9%, +3.4%, +6.9% at cp6/9/12. The gradient tracks the prior mean carried by players who are
out *now* (last two non-bye completed weeks both 0.0): 1.0% of rostered prior mean at cp3, 6.4% at
cp6, 8.9% at cp9, 8.2% at cp12. Until 5b those zeros were, by accident, the only current-injury
signal the posterior saw; the engine models injury *onset* (`INJURY_RATES`, `injury_clocks`) but
nothing tells it a player is already on IR, so from the checkpoint on he is drawn at full strength
every week. Neither `sync.py` nor `backtest_season.py` reads Sleeper's per-player `injury_status`, although the
data is there: the committed `sleeper_players_cache.json` carries 110 `IR`, 41 `PUP`, 8 `Out`, 10 `Sus`,
451 `Questionable` entries (grep, 2026-08-28) and no production module references the key.

**Scope (sized, not implemented):**

| piece | what | size |
|---|---|---|
| sync | carry Sleeper `injury_status` (`IR`, `Out`, `Doubtful`, `Questionable`, `PUP`, …) and `injury_start_date` into each baseline (additive fields) | ~10 lines |
| **step 1 — DONE (2026-08-28)** | `_build_roster_player_entry` and the baselines carry `injury_status` (Sleeper's field; `injury_start_date` is populated on 0 of 12,225 cache entries and is not carried) and `on_ir` (the league roster payload's `reserve` list). **`on_ir` is treated as absent regardless of status** — a manager who moved a player to IR has removed him from the lineup, which is what the engine models. **Accepted, named cost:** a player parked on IR while only Questionable/Doubtful is modelled as out. On 2026-08-28 the four IR-slot players were Micah Parsons (PUP), Zach Charbonnet (PUP), Jordyn Tyson (Doubtful), Alec Pierce (Questionable): two of four are this case. Goldens byte-identical (fixtures carry no status). | done |
| engine | at sim start, a player with `IR`/`Out`/`PUP` status enters with an `injury_clocks` entry drawn from the existing duration model (`INJURY_DURATION_*`) rather than healthy; `Questionable`/`Doubtful` need a game-time probability or nothing — a source is required, not a guess | ~20 lines |
| **step 2 design — decided 2026-08-28** | **One clock, two entry points; no separate Doubtful mechanic.** A separate "Doubtful → out with p = 0.9 for week 1 only" mechanic was proposed and DROPPED: (a) the 0.9 had no data source and would have entered as an unverified constant; (b) there is no live boundary case to gate it against — the only rostered Doubtful player on 2026-08-28 (Jordyn Tyson) is also on the IR slot, and the 2025 backtest has no status history at all (Sleeper serves current status only), so Doubtful / Out / IR cannot be told apart retrospectively; (c) what the data DOES separate is elapsed time, not the label: P(zero next | 1 trailing zero) = 0.71, P(zero next | ≥ 2) = 0.84 and flat. The replacement: absence certain in the first simulated week for `IR`/`PUP`/`Out`/`Sus`/`DNR`/`on_ir`, then a two-stage weekly return hazard measured from real 2025 — **0.29 after the first week out, 0.16 per week thereafter**. `IR`/`PUP` (already ≥ 2 weeks in) enter at stage 2; a fresh `Out` enters at stage 1. `Doubtful` and `Questionable` off the IR slot are drawn healthy until a game-time-probability source exists (bounded, named cost: 1 rostered Doubtful today, on IR anyway). Do not re-introduce a status-specific probability without a citable source. | — |
| **step 3 — DONE** | `backtest_season.mark_out_now` (k = 2 trailing non-bye zeros → `injury_status: "IR"`, stage 2). Marks 1 / 7 / 10 / 10 players at cp3/6/9/12 on real 2025. | done |
| **step 4 — MEASURED 2026-08-28, gate NOT met; stopped, not iterated** | Paired 300-sim backtest, same inputs and seed as 5b plus the marks: bias **+3.45 → +1.84 pts** (+2.7% → +1.4%), mean z −0.164 → −0.086, cover80 0.65 → **0.63**, cover50 0.36 → 0.35. Per checkpoint: cp3 −1.5%, cp6 +1.0%, cp9 +1.4%, cp12 +4.9% — gradient **10.7 → 8.2 pts** wide (cp3–cp9 alone: 3.9 pts; cp12 is 24 observations, SE ≈ 5 pts). Criterion was ≤ 1.5 pts and cover80 ≥ 0.65: missed on both. **What F4 itself was built to fix is verified:** realised absence in the first simulated week 0.0% → **5.6%** against 5.3% real out-now (7/133), and week-by-week bye-excluded absence 4.1% → **7.9%** over weeks 6–11. **What remains is a level offset, not an initial-state error:** the realised rate plateaus at 8.2–8.6% from week 8 on while real rostered absence is 14.7% — the forward onset/duration model, F5 below, exactly the split pre-committed before this step. Empirical data weight re-measured with the seven week-6 absentees excluded: unchanged (0.57 vs weeks 6–11 with injury zeros, 0.80 per game played). Do not tune F4's constants to close the remaining gap; they are measured. | stopped |
| backtest harness | the historical equivalent: a player whose last k completed weeks are 0.0 enters the checkpoint injured (k and the clock draw to be justified against 2025 return times, not tuned to the bias) | ~20 lines |
| tests | guard: an `IR` player contributes no starter points in week `current_week`; conservation of `injury_clocks` unchanged; goldens move only in fixtures that carry a status (fixture regeneration from `data/`) | small |

**Acceptance criterion:** on the paired real-2025 points backtest at 300 sims, the cp3→cp12 bias
gradient flattens to within ±1.5 pts across checkpoints (it is 10.7 pts wide after 5b) with
cover80 not below 0.65, and the empirical data weight (step 5a diagnostic, bye-aware) is
re-measured — it may move, since currently-out players are in that regression's weeks-6–11 window.

**When:** after bye modelling merges and BEFORE Phase 7 recalibrates `EPISTEMIC_ERROR_RATES`.
Step 5c made this a measured requirement, not a precaution: the engine realises 4.1% absence in
weeks 6–11 against 14.7% real, and that gap alone is +10.8% points bias once the posterior is
calibrated per game played. Phase 7 fits rostered-player variance on the same backtest; with F4
unbuilt it would absorb a ~10% scoring gap into `EPISTEMIC_ERROR_RATES` (and into any re-tuned
`INJURY_RATES`) as spurious uncertainty and tune the wrong constants by a measurable amount.
Phase 2 finding 4 (conjugate posterior) is gated on F4 for the same reason and should be
re-run immediately after it, with the weight criterion already met. Interacts with F1 only through key names.

### F5 — Forward absence model: the engine draws about half the in-season absence reality shows

**Origin:** F4 step 4 (2026-08-28), the pre-committed split. With byes modelled, zero weeks
out of the posterior (5b) and players out at the checkpoint entering on a measured clock
(F4), the engine realises **7.9%** bye-excluded absence over weeks 6–11 of real 2025 against
**14.7%** real (upper bound: a real 0.0 is any absence — injury, healthy scratch, suspension).
The first simulated week now matches (5.6% vs 5.3%), so the shortfall is the forward model:
the realised rate plateaus at 8.2–8.6% from week 8 (≈5 new onsets/week on 133 players, each
played at 0.35× in the onset week, then the two-component duration mixture) while real
absence keeps accumulating. Conditional on being out, the engine's duration model returns
next week with P = 0.32 (length-biased) against the measured 0.16; the memoryless tail is
too short. This gap, not the posterior, is what the conjugate update (Phase 2 finding 4)
exposes: +10.8% points bias when applied on top of 5b.

**Candidates, none pre-selected:** (a) `INJURY_RATES` are calibrated to "% of players missing
≥ 1 game per season", a season-level quantity, not a weekly onset hazard — check the
conversion; (b) the onset-week convention (0.35× play) against the real data, where an onset
week is mostly a full zero; (c) `INJURY_TYPICAL_DURATION_SCALE` / `INJURY_SEVERE_*` against the
measured 0.16 return hazard (a one-season, censored measurement — the F4 constants carry the
same caveat); (d) the vacated-volume pathway for initial absences on blank-slate priors.
Each is a change to baseline computation and takes the paired backtest gate.

**Interaction scoping (2026-08-28, before any implementation).** Candidates (a) onset rate and
(c) duration are NOT separately identifiable from the absence gate: in steady state the absent
share is A = r·D / (1 + r·D), so any change to `INJURY_RATES` shifts what the duration scales
must be to hit the same A, and vice versa — the same coupling the n₀ split had between the
player and defensive halves. They ARE separately identifiable from the direct statistics, so
that is how each must be gated: r from onsets (P(zero | previous non-bye week > 0)) and D from
absence spells (maximal runs of zeros), never from A. Measured on real 2025, weeks 1–11, 117
rostered players with ≥ 2 recorded non-bye weeks:

| | real 2025 | engine constants | note |
|---|---|---|---|
| weekly onset hazard r | **0.050** (39 onsets / 782 present player-weeks; RB 0.041, WR 0.068, QB 0.039, TE 0.063) | 0.041 roster-weighted | close; RB matches exactly |
| spell length D (weeks) | **2.56**, P(1 week) 0.54, 14 of 39 spells right-censored (true D higher) | mixture mean 3.11, P(1) 0.40 | nominally close |
| absence share A | 0.116 (weeks 1–11); 0.147 (weeks 6–11, cp6 rosters) | r·D/(1+r·D) = **0.113** analytic | the engine's constants already imply ≈ the real share |
| realised in simulation | — | **0.079** (weeks 6–11); out-on-clock ÷ newly-hurt = 10.1 ÷ 4.85 = **2.08** weeks | the engine does not deliver its own D |

So the constants are approximately right and the engine under-delivers them. Cause, read
from the code and confirmed by the 2.08: the clock is set to `weeks_missed` in the onset
week, the player then PLAYS that week at 0.35×, and the clock is decremented at the end of
that same week — so a spell of `weeks_missed` = n produces n − 1 fully absent weeks plus one
reduced game. 40% of onsets draw n = 1 and are never absent at all. The calibration target
(64% of injuries ≤ 2 games missed, mean 3.1 games missed) counts games MISSED; the engine
delivers mean 2.11 misses plus a 0.35× game. Candidate (b) and this off-by-one are the same
defect, and it is worth ≈ r × 1 × (1 − A) ≈ 4 points of absence — most of the 14.7 − 7.9 gap.
The remainder is non-stationarity (censored long spells accumulate through the season; real
A rises from 11.6% over weeks 1–11 to 14.7% over 6–11) which the severe component should
reproduce once the onset week counts.

**Sequencing that follows from the coupling:** (1) fix the onset-week semantics first — it
changes effective D without touching either constant, so it must land before (a) or (c) can
be judged; re-measure realised r and D from the simulation the same way as from the real
data (onsets per present player-week; out-on-clock ÷ newly-hurt). (2) Only then compare (a)
against real r and (c) against real D, each on its own statistic. (3) A is the acceptance
check, never the calibration target. (d) vacated volume on blank-slate priors is independent
of all three (it changes who scores, not who is absent) and is judged on the backtest bias.

**Acceptance criterion:** on the paired real-2025 points backtest at 300 sims, realised
bye-excluded absence in weeks 6–11 within 2 points of the real rate (14.7%, or the injury-only
rate once a real 2025 injury list separates scratches from injuries), the cp3→cp12 bias
gradient ≤ 1.5 pts, cover80 ≥ 0.65 — the F4 criterion, moved here where the cause is. Then
re-run Phase 2 finding 4 (conjugate posterior): the weight criterion is already met; the
backtest bias is what it waits on.

**When:** after F4 merges and BEFORE Phase 7, for the reason F4 carried: Phase 7 fits
`EPISTEMIC_ERROR_RATES` on this backtest, and an unmodelled ~7-point absence gap would be
absorbed into it as spurious uncertainty.

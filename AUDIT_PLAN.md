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

**Status (2026-08-29): IN PROGRESS on `audit/phase-7-calibration`; findings in `AUDIT_PHASE_7_FINDINGS.md`.**
Order fixed before starting: (1) per-position `INJURY_RATES` — DONE: redefined as the all-cause weekly
absence-onset hazard; WR 0.040 → 0.081 (n = 38) and QB 0.025 → 0.054 (n = 8) by the rule "move only
where the config lies outside the real 2025 Wilson interval"; RB/TE/K/IDP unchanged with reasons.
Prediction held (starter-onsets 4.3–4.5 vs ≈ 4.6 predicted / 4.7 real; started-zero 0.136 vs ≈ 0.14;
absence 14.6% vs 14.7% real); bias +1.51 → +0.72 pts. The started-zero residual (0.06–0.10) is the
manager behaviour F5 named; the checkpoint gradient (10 pts, early-negative / late-positive) is now
the prior/posterior question. (2)+(3) `EPISTEMIC_ERROR_RATES` + conjugate form — DONE AS A JOINT CHANGE AND
REVERTED (2026-08-29): the rates and the form are a matched pair (demonstrated by the 2 × 2 on the instrument);
the joint pair is neutral on the instrument and worse on the backtest in both configurations (−2.1% with the
true spread; +5.2% with the prior widened by its centring error). Not "calibration fixed": the rate/form
mismatch is understood and recorded; within-season drift of the true mean (F8) and the absence of
projection-error data (F7) are the open items, and Phase 2 finding 4 is now blocked on those, not on absence.
A wrong direction prediction (rates alone would "collapse" std_z; they raised it to 1.3–1.5) is recorded. F6's 1.05 / 0.84 and 0.21 and
F4's 0.29 / 0.16 are fixed inputs throughout.

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

## Absence modelling — the arc, consolidated (2026-08-28)

Every entry below exists in full elsewhere in this file or the phase findings; this section is
the reading order and the numbers at each stage, so the chain is followable without the
transcript. All backtest figures are the paired, seeded, points-level backtest on the real 2025
season (scratch `bt_points.py`; 300 sims; checkpoints 3/6/9/12; bias = simulated − real weekly
team points; cover80 = share of real scores inside the simulated 10–90% band).

| stage | what changed | bias (all) | cover80 | absence, wks 6–11, bye-excl. | notes |
|---|---|---|---|---|---|
| baseline (Phase 3 close) | — | +1.47 (+1.1%) | 0.62 | not modelled | zero weeks in history were the only absence signal |
| Phase 2 f5 alone (reverted) | skip zero weeks | +4.3% | — | — | reverted: removed the accidental signal |
| Phase 3 f4 alone (reverted) | conjugate posterior | +8.5% | — | — | reverted: "over-confident" — the wrong diagnosis, see 5c |
| bye modelling 5a | byes from the NFL schedule, draw side | −2.29 (−1.8%) | 0.65 | byes only | overshoot: byes double-counted with history zeros |
| 5b | + skip zero weeks (f5 re-applied, stands) | +3.45 (+2.7%) | 0.65 | 4.1% vs 14.7% real | gradient cp3 −1.4% → cp12 +6.9%: players out NOW drawn healthy |
| 5c (reverted) | + conjugate posterior | +13.96 (+10.8%) | 0.56 | — | weight criterion MET (applies 0.71 vs target 0.68); the bias is undrawn absence, not the posterior |
| F4 | initial state: out-now players enter on a measured two-stage clock (0.29 / 0.16) | +1.84 (+1.4%) | 0.63 | 7.9%; week 6: 5.6% vs 5.3% real | initial state verified; level offset → F5 |
| F5 step 1 | onset week is a missed game; 0.35× partial-week mechanic removed | +2.43 (+1.9%) | 0.63 | 11.9% (analytic 11.3%) | engine now delivers its constants: r 0.047 vs 0.050, D 2.90 vs 3.11; bias UP because an onset hole was filled free at replacement level |
| F5 step 2 | locked-lineup onsets (p = 0.21) stay candidates and realise 0 | +1.77 (+1.4%) | 0.66 | — | started-zero rate 0.09 vs 0.20–0.24 real: denominator mismatch → F6 |
| F6 | onset hazard by intended-lineup exposure (1.05 / 0.84, pooled hazard held); locked draw on intended starters only | +1.51 (+1.2%) | 0.64 | — | started-zero rate 0.099 vs 0.236 and starter-onsets 3.24 vs 4.7: the LEVEL of per-position INJURY_RATES (WR 0.068 vs 0.040, TE 0.063 vs 0.035 real vs config) → Phase 7, with F6's factors and 0.21 fixed |

What each stage settled, in one line each: byes are derived, not fetched (5a); history zeros
are not games (5b, stands); the posterior weight was never the problem (5c: 0.71 applied vs
0.68 target — Phase 2 finding 4 is gated on the backtest bias, which is gated on F6); absence
has an initial state (F4) and a forward model (F5), and they are measured separately; the
forward model's constants are right and were being under-delivered by an off-by-one (F5 step
1); an absence is priced by who fills the slot, and a same-week zero is two regimes, 90% known
and bench-covered, 10% locked (F5 step 2); the remaining half of the locked-zero gap is that
onsets are drawn roster-wide from a per-active-player rate (F6). Constants introduced along the
way and their evidence: `ABSENCE_RETURN_HAZARD_FIRST_WEEK` 0.29 (n=101), `_STEADY` 0.16 (n=62/43/29),
`LOCKED_ONSET_PROBABILITY` 0.21 (13/61, Wilson 0.13–0.33) — all one-season 2025, all written
that way. Constants deliberately NOT changed: `INJURY_RATES`, the duration scales (both closed
on their own statistics), and 0.21 (not re-tuned to a different mechanism's gap).
Decisions deliberately made and recorded: on_ir = absent regardless of status; no Doubtful
mechanic (no source, no live case); onset week = missed game (a reversal, not a re-timing);
pooled p_locked with the position split as next season's hypothesis.

**Fixed inputs to Phase 7 (recorded at F6's merge, 2026-08-28).** The exposure factors
(`ONSET_EXPOSURE_STARTER` 1.05 / `ONSET_EXPOSURE_BENCH` 0.84) and `LOCKED_ONSET_PROBABILITY`
(0.21) are FIXED inputs to Phase 7's per-position `INJURY_RATES` recalibration, not free
parameters to compensate with. If the backtest still misses after that recalibration, the miss
belongs to the position-level rates, not to anything F6 touched.

Every time a fix in this chain was verified against real 2025 data rather than trusted on
internal consistency alone, something was found that internal checks alone would have missed —
a fix that looked correct in isolation and worsened real calibration (Phase 2, twice), a
mechanism that measured right on its own statistic while a different, unexamined mechanism
absorbed the consequence (F5's onset rate and duration versus locked-zero pricing), and a
compensating error hiding inside an error already believed fixed (the old n₀=4 form masking the
same absence gap the conjugate form later exposed). No single fix in this arc was accepted on
the strength of its own internal logic; every one was required to move the real-data backtest
in the direction it predicted, and more than half of them did not do so on the first attempt.

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

**In-process repetition (2026-08-28, asked for because of this project's two prior shared-state bugs):**
the full suite run 20 times inside ONE interpreter (modules imported once; class state, mock
patches and GC history carried across iterations; `-X faulthandler`; a result class recording
the most recently started test before any error). 20/20 iterations: 206 ran, 0 failures, 0
TypeErrors, no crash. The only errors were Hypothesis's `differing_executors` health-check on
its 5 property tests from iteration 2 on — an artifact of re-running them in one process, not
R1 (iteration 1: 0 errors). So object identity / GC timing within a process does not reproduce
it either. Total under observation: 0 of 36 (16 fresh processes + 20 in-process iterations).

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

**Step 1 — onset-week semantics (deliberate reversal, 2026-08-28).** The original injury model
let a newly injured player PLAY his onset week at 0.35× mean / 0.5× std and burned the first
unit of his clock at the end of that same week. That partial-week mechanic is REMOVED, not
re-timed: (1) the duration mixture it feeds was calibrated on games MISSED (ProFootballLogic:
64% of injuries ≤ 2 games missed, mean 3.1 games) and the real-2025 spells this phase measured
are runs of exact zeros — in both, an onset week IS a missed game; (2) the 0.35× / 0.5× had no
source, no comment beyond "exactly as before this restructuring", and no test; (3) measured
in-simulation it delivered n − 1 missed games per drawn n (out-on-clock ÷ newly-hurt = 2.08
against the mixture's 3.11) and made 40% of onsets (n = 1) vanish. Now: a player on a clock
scores nothing from the onset week on, the clock covers n full weeks, vacated volume is still
recorded in the onset week. If "hurt mid-game" realism is ever wanted, it must come back as its
own small, SOURCED addition (a measured fraction of onsets that are in-game with a measured
partial share), not as an unsourced multiplier that silently shortens every spell. Consequence
worth knowing: an onset-week hole is filled by the unbid fallback streamer (the convention for
every unfilled slot, since needs are bid before onsets are drawn), which is higher than the
0.35× zombie it replaces — the 30-season golden means moved UP (+2.2% week01, +1.7% week06);
the paired backtest sizes it.

**Step 1 result and the onset-week hole, traced (2026-08-28).** In-simulation after the fix,
measured the same way as the real data: r = 0.047 (real 0.050), D = 2.90 and still ramping at
week 11 (mixture 3.11; real 2.56 censored), absence 11.9% (analytic steady state 11.3%; real
14.7% over weeks 6–11, 11.6% over 1–11). The engine now delivers its constants; judged each on
its own statistic, neither `INJURY_RATES` nor the duration scales is contradicted by the data
— (a) and (c) closed without a change. Yet the paired backtest did not improve: bias +1.84 →
+2.43 pts, cover80 0.63 → 0.63, gradient 8.2 → 8.8 pts; the 30-season goldens moved UP (+2.2%
week01, +1.7% week06). Traced through the real code on the week01 fixture (hooks on the
assignment, the apportion boundary and the FAAB bid; sim 0's audit log for what actually
started): needs are bid BEFORE onsets are drawn (need scan ~line 780, PASS 1 ~line 905), so a
starter hurt this week leaves an "unfilled slot" at the assignment (line 1024), and every
unfilled slot is filled at lines 1028–1051 by one of two paths — (i) a streamer the team had
already won for some other hole, valued at the ladder capped at the position's data-derived
replacement level (line 1045): week 6, Jaylen Waddle (WR, mean 11.06) hurt → two FLEX holes →
`STREAMER_FLEX_0/1` at expected 10.53 = the FLEX replacement level, 95% of his mean; or (ii)
with no won streamer, the unbid fallback at line 1047, `max(0.8 × replacement level, BASE ×
decay^k)`: week 2, Danielle Hunter (DE, mean 9.14) hurt → `STREAMER_DL_0` at 7.50 = BASE, 82%
of his mean. So an absence removes a starter and hands the slot a replacement-level body for
free in the same week, no FAAB, no bench check beyond the assignment — which is why 4 points
more absence cost the backtest nothing. The real-world counterpart is a locked lineup scoring
0 in that slot (or, if ruled out pre-game, the best bench body, which the assignment already
models). The absence RATE is right; the absence PRICE is not. This is the next F5 question
and it is a decision-logic change to how holes are filled, not a constant.

**Step 2 scoping — how an absence is priced (2026-08-28, no code written).** Real target derived
from Sleeper's 2025 matchup payloads (`starters` + `players_points` per roster-week, weeks 1–14,
bye weeks excluded, 1,768 rostered player-weeks). "Same-week zero" is NOT one regime:

| rostered player-week | n | share |
|---|---|---|
| started, > 0 | 1,199 | 67.8% |
| **started, zero** (a zero sat in a locked slot) | **21** | **1.2%** |
| benched, > 0 | 366 | 20.7% |
| benched, zero (manager knew; swapped) | 182 | 10.3% |

Of 203 zero weeks, **90% were benched** — the manager knew before lock and the bench filled the
slot, which is exactly what the engine's assignment already models (bench fills; a streamer only
where no bench player fits). Only **10% (21) were started zeros**: 18 fresh onsets (in-game
injury or inactive after lock), 3 already-out players left in. Price of a started zero = the
starter's own mean-to-date, **11.0 pts** (n = 20); the engine hands that slot a replacement-
level streamer worth 7.5–10.5 instead. Frequency: 0.19 started zeros per team-week, 17 of 112
team-weeks — ≈ 2.1 pts/team-week (≈ 1.5% of a ~140-pt score), the size of the remaining backtest
bias (+1.9%). By position: WR 3.2% of starts, TE 3.1%, K 0.9%, **QB and RB 0 of 468 starts** —
regime B is a pass-catcher phenomenon in this sample (a QB/RB who is hurt is known before lock).

The parameter a mechanism would need — the share of FRESH onsets that land in a locked lineup:
**23%** of 75 (weeks 2–14); **21%** of 61 restricted to players who started the previous week,
Wilson 95% 0.13–0.33; QB 0/8, RB 0/19, WR 11/38 (29%), TE 3/7 (43%). One season; n small; the
position split is suggestive, not established.

**Proposed mechanism (Phase 4 treatment — brute-force cross-check on the fill logic, then the
paired backtest as the baseline-contamination gate):** at onset, with probability p_locked the
player is a "locked zero": he stays in the candidate list at his pre-game expectation (the
lineup is chosen on `expected_pre`, as it must be — the manager did not know) and his realised
score is 0; otherwise (known pre-game) he is excluded from candidates as step 1 does now, and the
bench or a streamer fills the slot. No partial production either way. p_locked enters as a
measured constant with the n above and the one-season caveat, position-specific only if the
QB/RB-vs-WR/TE split survives a second season; a single pooled 0.21 otherwise. Expected effect:
≈ −0.19 × (11.0 − 9) ≈ −0.4 pts/team-week from the price alone plus the removal of the free
replacement-level fill on those onsets — the backtest sizes it. Acceptance: on the paired
real-2025 backtest, started-zero starters per team-week in simulation within ±0.05 of the real
0.19, bias moves toward zero from +1.9%, cover80 not below 0.63, gradient not wider; the streamer
-needs tests keep their onset accounting (a locked zero leaves NO unfilled slot). Not touched:
regime A's timing (a known-out player's waiver pickup the same week) — the engine bids before
onsets, so a bench-uncoverable known absence takes the unbid fallback without FAAB; measured
small (regime A holes are bench-covered in the assignment) and left as a note.

**Step 2 result (2026-08-28) — built as scoped, brute-force verified, backtest gate PARTIAL.**
`LOCKED_ONSET_PROBABILITY = 0.21` (pooled; position split recorded as next season's hypothesis).
Brute force on the week01 fixture, 2,768 onsets: locked share 0.203 (SE 0.008); every locked
starter in sim 0 realised exactly 0 (14/14) at a positive pre-game expectation (562/562); no
excluded onset ever started; re-solving without the locked player never reduced unfilled slots
(0/562 — a locked zero fills, never creates, a hole). Paired 300-sim backtest, same F4 inputs
and seed: bias **+2.43 → +1.77 pts** (+1.9% → +1.4%), mean z −0.111 → −0.089, cover80 **0.63 →
0.66**, gradient cp3→cp12 8.8 → 9.0 pts (unchanged within noise). Started-zero starters per
team-week, measured in simulation EXACTLY as the real figure (assigned rostered players in the
week's onset set — realised 0 by construction — per team-week; streamers excluded as in
reality): **0.093** (weeks 6–14, cp6) and **0.096** (weeks 3–14, cp3) against real **0.236**
(17/72) and **0.198** (19/96) for the same weeks — the ±0.05 criterion is MISSED, at about half
the real rate. Decomposed, not tuned: (i) the 0.21 was measured among onsets by PREVIOUS-WEEK
STARTERS, but the engine applies it to every onset and only ≈ 75% of onset players are then
assigned, so the effective conditional rate is right and the base is what differs — real
onsets skew to starters (4.7 starter-onsets/week vs the engine's 5.4 × 0.75 ≈ 4.0: starters
take more snaps and the engine's per-player rate is uniform across the roster); (ii) 4 of the 17
real locked zeros were bench players promoted into the lineup that week (0.04/team-week);
(iii) 3 were already-out players a manager left in (0.03/team-week) — engine managers are
perfect. (i)–(iii) sum to ≈ 0.09, the size of the gap. None is the locked-zero mechanism
itself; (i) is a snap-exposure question for `INJURY_RATES` (the engine's rate is per rostered
player, the sources it cites are per active player), (ii) and (iii) are manager behaviour.
Recorded; the constant is NOT re-tuned to close the gap (it would mean applying a starter-
conditional probability to a roster-wide denominator). Mechanism stands: every metric moved the
right way and the brute force shows it does exactly what it says.

**Acceptance criterion:** on the paired real-2025 points backtest at 300 sims, realised
bye-excluded absence in weeks 6–11 within 2 points of the real rate (14.7%, or the injury-only
rate once a real 2025 injury list separates scratches from injuries), the cp3→cp12 bias
gradient ≤ 1.5 pts, cover80 ≥ 0.65 — the F4 criterion, moved here where the cause is. Then
re-run Phase 2 finding 4 (conjugate posterior): the weight criterion is already met; the
backtest bias is what it waits on.

**When:** after F4 merges and BEFORE Phase 7, for the reason F4 carried: Phase 7 fits
`EPISTEMIC_ERROR_RATES` on this backtest, and an unmodelled ~7-point absence gap would be
absorbed into it as spurious uncertainty.

### F6 — `INJURY_RATES` is per active player but applied uniformly to every rostered player

**Origin:** F5 step 2 (2026-08-28), the decomposition of the started-zero miss. `INJURY_RATES`
is derived (config.py) from "% of players missing ≥ 1 game per season" studies of ACTIVE NFL
players, converted to a weekly onset hazard, and the engine draws that hazard for every
rostered player every week regardless of role. Real 2025 onsets skew to starters — they take
the snaps: 61 of 75 fresh onsets (81%) were by players who had started the previous week, ≈ 4.7
starter-onsets per week, against the engine's ≈ 5.4 onsets/week × ~75% assigned ≈ 4.0. A bench
player in the engine is as likely to be hurt as a starter, and his onset costs nothing, so the
league-wide onset count is right while the count that matters (starters) is low. The pooled
onset hazard itself (0.047 realised vs 0.050 real) is not the issue; its distribution across the
roster is.

**What is blocked on it:** F5's remaining acceptance — the started-zero rate (0.093 vs 0.236
per team-week) and the cp3→cp12 bias gradient (9.0 pts vs ≤ 1.5) — and, through it, the re-run
of Phase 2 finding 4 (conjugate posterior), which waits on the backtest bias. Phase 7 should not
re-derive `INJURY_RATES` before this is decided, for the same reason F4/F5 preceded it: a
roster-wide rate fit to starter-driven absence would be biased low for starters and high for
the bench.

**Scope (sized, not implemented):**

| piece | what | size |
|---|---|---|
| exposure model | make the weekly onset hazard proportional to expected usage: simplest honest form, hazard = `INJURY_RATES[pos]` × (player is in the optimal lineup this week ? 1 : `BENCH_EXPOSURE`), with `BENCH_EXPOSURE` measured — real 2025 gives 14 of 75 onsets by non-starters against ≈ 27% of rostered player-weeks on the bench, i.e. bench exposure ≈ 0.55 × starter exposure (one season, n = 14) | ~15 lines in PASS 1; the lineup must be known before the onset draw, which reverses the current order (PASS 1 runs before the assignment) — a decision-logic change, Phase 4 treatment |
| source check | re-read the cited studies for their denominator (all rostered vs active vs starters) and re-derive the per-position rate on the right base; if they are per active player, the starter hazard is `INJURY_RATES` as is and the bench hazard is the scaled one | comment + numbers in config.py |
| manager behaviour | the 4 bench-promoted and 3 left-in locked zeros (0.07/team-week) are NOT this item; record them under F2-style manager modelling if ever wanted | none |
| tests | property: per-position onset counts by starters vs bench match the exposure ratio (brute force on the fixture, as F5 step 2); goldens move (RNG order) | small |

**Acceptance criterion:** on the paired real-2025 backtest at 300 sims, starter-onsets per
week within ±0.5 of real (4.7), the started-zero rate within ±0.05 of real (0.19–0.24 by
window) with `LOCKED_ONSET_PROBABILITY` UNCHANGED at 0.21, and the pooled onset hazard still
within ±0.01 of 0.050 (the exposure split must redistribute onsets, not add them). Then F5's
gradient criterion is re-read, and Phase 2 finding 4 is re-run.

**When:** before Phase 7 touches `INJURY_RATES`; otherwise any time. Independent of F1–F3.

**PASS-1 ordering, scoped before implementation (2026-08-28).** The weekly loop today, by line:
streamer-need scan and bids (814–878) → PASS 1: onset draws, clocks, vacated-volume RECORD
(891–925) → apportion (929) → PASS 2: per-player scoring with `expected_pre` that INCLUDES this
week's contingency (1005–1012) → assignment on `expected_pre` (1036) → streamers for unfilled
slots (1040) → clock decrement (1171). "Draw onsets after the assignment" as literally stated
IS circular: the assignment's `expected_pre` carries contingency points, contingency comes from
apportioning vacated volume, and vacated volume is recorded at onset; and candidacy itself
(excluded vs locked) depends on the onset. Drawing onsets after the final assignment would
mean the lineup was chosen before it knew who is out — wrong in the other direction.

Resolution: the exposure model does not need the FINAL lineup, it needs the INTENDED one —
the lineup a manager would set before this week's injuries exist, which is exactly what the
data conditioned on ("started the previous week" ≈ "is a starter"). So the order becomes:
(1) bids as now; (2) NEW: intended lineup per team — `_solve_optimal_assignment` on the
candidates who are healthy and not on bye, valued at `mean × (v_tot / env_norm) × script_mult`
with NO contingency (this week's onsets do not exist yet, so there is nothing to apportion; no
lookahead); (3) PASS 1 onset draws with hazard `INJURY_RATES[pos] × (starter factor if in the
intended lineup else bench factor)`, clocks, vacated-volume record — unchanged otherwise;
(4) apportion; (5) PASS 2 scoring and the FINAL assignment exactly as now, on `expected_pre`
with contingency, candidates = healthy ∪ locked. No cycle: (2) reads only state that precedes
(3); (3) reads (2); (5) reads (3)–(4). The intended and final lineups differ only through this
week's onsets and contingency, which is what they should differ by.

Consequences to decide, then pin: (a) the locked draw should apply only to onsets by players
in the intended lineup — that is the denominator the 0.21 was measured on (previous-week
starters), which directly removes the "starter-conditional rate applied roster-wide" mismatch
F5 step 2 identified; bench onsets are simply excluded (their slot was never theirs). The 4
real bench-promoted locked zeros (0.04/team-week) are then a known, named under-count, not a
hidden one. (b) Cost: one extra Hungarian solve per team-week (≤ 20 × 13; the trade block
already runs several per evaluation) — measure, expect a few percent of runtime. (c) RNG: the
per-player onset draw stays one `rand()` per healthy player in roster order, so the stream is
consumed identically; only the comparison threshold changes — goldens move only where a
player's draw crosses the scaled threshold, and the pooled hazard shifts unless the factors
are set so that the roster-weighted hazard is unchanged (the acceptance criterion says it must
be, within ±0.01). Derive the split from the real data — 61 starter / 14 bench onsets over
≈ 73% / 27% of rostered player-weeks → bench hazard ≈ 0.55 × starter hazard (n = 14, one season,
written that way) — then set the factors so the roster-weighted mean equals the current
`INJURY_RATES[pos]` (starter ≈ 1.14×, bench ≈ 0.63× at a 73/27 split): the per-active-player
sourcing stays honest and onsets are redistributed, not added. (d) The streamer-need scan's own
greedy fill is NOT reused as the intended lineup: it fills positional requirements in roster
order, not by value, and it exists to count holes, not to pick starters. (e) Characterisation
before the change: on the real engine, the onset count among intended-lineup players vs bench
players equals the exposure ratio (brute force on the fixture, as F5 step 2 was verified), and
the pooled hazard is unchanged.

**Correction during implementation (2026-08-28).** The "bench hazard ≈ 0.55 × starter" (and the
1.18 / 0.63 factors sketched from it) mixed two definitions: onsets were classified by
previous-week status, exposure by this-week status, and "benched player-weeks" included the
already-out zeros, inflating the bench denominator. On ONE consistent definition — a player-
week is exposed if the player scored > 0 the previous week, classified by whether he started
that previous week — real 2025 gives starters 61 / 1,060 = 0.0575, bench 14 / 303 = 0.0462,
**ratio 0.80** (n = 14; interval roughly 0.6–1.1), starters 77.8% of exposures. Factors built
in: **starter 1.05, bench 0.84** (0.778 × 1.05 + 0.222 × 0.84 = 1.00). The effect is about a
third of what the scoping sketched; the acceptance criterion (starter-onsets 4.7/wk, started-
zero rate ±0.05) is left as written and will be judged against it honestly. The
characterisation measured the engine at 0.91 on the same proxy.

**Result (2026-08-28) — built as scoped with the corrected factors, fixture-verified, gate
NOT met; prediction missed in a way that is itself the finding.** Fixture cross-check (week01,
2 × 15 seasons, 2,700 onsets): observed ÷ expected-at-base-rate 1.028 for intended starters,
0.862 for the bench, ratio 1.19 against the built-in 1.25 (SE ≈ 0.03); pooled hazard 0.0406 vs
0.0421 roster-weighted (the fixture's starter share is 72.7% against the 77.8% the factors were
normalised at — ≈ 2% fewer onsets, a known level effect); every locked zero an intended starter
(0 of 435 otherwise); engine lineup persistence 0.861 vs real 0.890; wall clock 1.4 s vs a
1.2–1.4 s baseline (the extra Hungarian per team-week is within noise). Paired 300-sim backtest,
same F4 inputs and seed: bias **+1.77 → +1.51 pts** (+1.4% → +1.2%), mean z −0.089 → −0.068,
cover80 0.66 → 0.64, gradient 9.0 → 9.0 pts. Gate quantities, measured exactly as the real ones:
started-zero starters per team-week **0.099** (weeks 6–14) / **0.096** (3–14) against real
0.236 / 0.198 — unchanged from F5 step 2's 0.093 / 0.096; **starter-onsets per week 3.24**
against real 4.7; pooled hazard −2 to −3% (within ±0.01). Stated prediction beforehand: 0.09 →
≈ 0.11. It landed at 0.099 — a null result, not the predicted rise: restricting the locked draw
to intended starters removed roughly as many eligible onsets as the 1.05 factor added.

Why, decomposed rather than tuned: the 2025 league had no IDP players (team-DEF era), so each
real roster's ≈ 9–10 true starters are all offence and their measured hazard is 0.0575/week;
the engine's per-position rates for that same offence mix give ≈ 0.043 (RB 0.070, WR 0.040,
TE 0.035, QB 0.025, × 1.05). 76 starter-slots × 0.043 ≈ 3.3 onsets/week = what the simulation
shows; × 0.0575 ≈ 4.4 = what reality shows. Per position on the real data (weeks 1–11, players
with ≥ 2 recorded weeks): QB 0.039, RB 0.041, **WR 0.068**, **TE 0.063** against config QB 0.025,
RB 0.070, WR 0.040, TE 0.035 — WR and TE roughly 70–80% under, RB over; n = 3 / 11 / 20 / 4
onsets respectively. The remaining starter-onset shortfall is therefore the LEVEL of
`INJURY_RATES` by position, not its distribution across the roster — F6's scope row "source
check" — and that is Phase 7 calibration, to be done on the per-position statistic with the
n above written next to it, not by scaling the exposure factors. F6's exposure split stands as
built (correct on its own claim: onsets redistributed toward starters at the measured ratio,
pooled hazard held, locked draw on the right denominator); its acceptance criterion moves with
the cause to Phase 7's `INJURY_RATES` item, where "starter-onsets 4.7/week ± 0.5 and started-zero
rate ± 0.05 with `LOCKED_ONSET_PROBABILITY` and the exposure factors unchanged" is the test.

### F7 — Store weekly projections at sync so projection error can be measured next season

**Origin:** Phase 7 step 2 (2026-08-29). `EPISTEMIC_ERROR_RATES` is, in production, the error of
a projection-based prior; Sleeper serves only the current week's projections (2025's return 404),
so that error has never been measurable and the rates were tuned instead under the retired-then-
kept n₀ = 4 form with a positional prior. **Scope:** on every sync, append the week's fetched
Sleeper and ESPN projections for rostered players to `data/projection_log.jsonl` (pid, week,
source, projected mean); ~15 lines in `sync.py`, no engine change, no golden movement. **Acceptance:**
after one season of logging, per-position RMS of (projection − realised per-game mean) minus the
sampling term, over the projected mean, with n written beside it — the first direct derivation
of `EPISTEMIC_ERROR_RATES`. **When:** before week 1 of 2026, or the season's data is lost.

**DONE (2026-08-29, branch `audit/f7-projection-log`).** `PROJECTION_LOG_FILE` = `data/projection_log.jsonl`;
`generate_player_baselines` collects one row per ROSTERED player (season, week, synced_at UTC, player_id,
name, pos, team, sleeper_mean, espn_mean or null, fallback_season) and `sync.append_projection_log`
appends them at the end of baseline generation — append-only, a re-sync within a week appends again, a
write failure warns and never breaks the sync. `.gitignore` carries `data/*` + `!data/projection_log.jsonl` so the
one file that cannot be refetched is under version control. CORRECTION: the first commit (94fcdc1) used the
exception under a `data/` directory rule and claimed it verified; git cannot re-include a file beneath an
excluded directory and `git check-ignore -v` showed it still ignored. Fixed to `data/*` and re-verified
(`data/player_baselines.json` still ignored; the log is not). The
analysis is written now so next season is one call: `backtest_player.load_projection_log` (last row per
season/week/pid wins) and `analyze_projection_error(rows, actual_by_pid_week)` — per position, RMS of
(realised per-game mean − projection) with the within-player sampling term removed, over the mean
projection = the epistemic rate; zero weeks excluded as absences; tested on synthetic rows where bias
and noise are separable by hand. No engine change; goldens byte-identical. Realised scores need no
logging (Sleeper matchups persist). What remains is time: the log starts filling at the first 2026 sync
and the derivation needs a season of it.
SMOKE TEST (2026-08-29, first real `run_sync` on main after the merge): **155 rows** read back from the
file — 155 distinct pids of 156 rostered, week 1, season 2026, ESPN matched on 116 (the 118 blend-eligible
offence players less two; K/IDP are never matched by design), `fallback_season` 0. The one rostered
player NOT logged is Jordyn Tyson: Sleeper carries no projection for him, so there is no projection to
log — he enters the engine through `KNOWN_MISSING_ASSETS` imputation, whose prior is positional, not a
projection. Known, expected, and the right behaviour for this file. First rows committed to main.

### F8 — Within-season drift of a player's true mean (the static-mean assumption)

**Origin:** Phase 7 steps 2–3. On the project's own calibration instrument std_z rises from ≈ 1.0
at cp3 to ≈ 1.2 by cp9–cp12 under BOTH the old (n₀ = 4) and the conjugate posterior forms, and
the conjugate form over-predicts late checkpoints (+11% at cp12) with any σe wide enough to fix
cp3. Both forms assume a static true weekly mean; reality drifts (role changes, returns at less
than full strength, the post/pre per-game ratio 0.884). **Scope (sized, not implemented):** a
random-walk component on the true mean — prior variance grows with weeks since the last
observation, i.e. the posterior forgets — one constant (drift variance per week) measured from
the autocorrelation of per-game means across windows on real 2025; touches
`_apply_bayesian_updates` and the epistemic draw; Phase 4 treatment plus the backtest gate.
**Acceptance:** std_z within ±0.1 of 1.0 at every checkpoint on the instrument, and the paired
backtest not worse than the old pair (+0.6%, cover80 0.63). **Blocks:** Phase 2 finding 4 (the
conjugate re-run) — its weight criterion is met; the late-checkpoint bias is this item. **When:**
after F7 has a season of projections, so the prior's own error and its drift are derived from
the same data; independent of F1–F3.

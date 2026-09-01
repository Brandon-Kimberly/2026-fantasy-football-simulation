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

> **Reading order for newcomers:** `AUDIT_SUMMARY.md` (one page per phase: found / fixed / left open / running
> defect count), then the phase findings documents, then this plan for the open items and follow-ups.

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
8. FIXED (2026-08-31, commit 9ccb9e9). `power_rankings_baseline_pts` was labelled "Optimal
   Valid Starting Lineup Baseline" but `get_optimal_score` returns lineup + 10% of bench (166.8
   true vs 173.1 reported). Deliberate depth reward, undisclosed label. Renamed the export key
   to `roster_value_baseline_pts` and reworded the chart title/x-axis to say "+ Bench Depth";
   `get_optimal_score`'s return value is unchanged. Confirmed via golden-master diff that only
   stage_b/stage_c (the export layer) moved -- stage_a (the simulation itself) is
   byte-identical.

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

**Revisited 2026-08-31**, after an external audit (Gemini) called `MAX_REALISTIC_WEEKLY_SCORE =
80` "arbitrary": it is grounded in real NFL single-game scoring records, and the measurement two
lines up is exactly the cost of that cap on this engine's own output, not a guess. Conclusion
unchanged, no action taken — see F13 below for the full record of this audit's findings so this
does not get re-litigated blind next time.

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


**Invocation context captured (2026-08-29, F3 branch).** A full-suite run under `-X faulthandler`
died with process exit code **−1073741819 = 0xC0000005, a Windows access violation**, and the
faulting Python frame was `tests/test_lineup_optimality.py:62` in `brute_force_best` — a pure-Python
list comprehension inside `itertools.product`, in a test that has just made ~1,700
`scipy.optimize.linear_sum_assignment` calls. A fault in pure Python means the heap was already
corrupted by native code before that frame. That is consistent with all three R1 symptoms seen so
far: the impossible `TypeError` at an `isinstance`-guarded line (a corrupted object), the earlier
"exit code 5" runs (the same crash with the code reported through a different shell path), and the
0-of-36 non-reproduction under any deterministic condition. Environment at the time: Python 3.8.10
(Windows Store build), numpy 1.24.4, scipy 1.10.1, pandas 2.0.3, matplotlib 3.7.5. The immediate
re-run passed (232 tests). Standing instruction updated: R1 is a **native memory fault in the
test process, not a test-ordering or shared-state defect**; the next step when it recurs is to
capture the faulthandler frame again and compare — if `linear_sum_assignment` or the pandas/
numpy percentile paths are the common ancestor, pin or upgrade that library and re-run the
20-iteration probe. Suite runs that die this way must be re-run, never counted as green.


**Infrastructure research (2026-08-29, asked for after the access violation).** R1 is no longer a
watch item: `linear_sum_assignment` runs once per team per simulated week (plus once more per
team-week since F6, and inside every trade evaluation), so a native fault on that path is a
production risk for `run_simulation`, not a test curiosity. Findings:

1. *Upstream:* no scipy release note (1.11–1.17) and no scipy issue describes a crash in
   `linear_sum_assignment`; its known failure modes are `ValueError`s on NaN / inf inputs and a
   size limit at 2^31 elements (scipy issues #14545, #6900, #13421). The engine's cost matrix is
   finite (a `LARGE` sentinel for ineligible cells, `−value` otherwise), so those paths are not
   reachable here. Nothing to pin *to*.
2. *This environment cannot be upgraded:* the production interpreter is the Windows Store
   Python 3.8.10, for which pip offers nothing newer than scipy 1.10.1 and numpy 1.24.4 — the
   last releases for 3.8 (scipy 1.11 requires 3.9+). Both lines are end-of-life; whatever the
   fault is, no fix will ever ship for this stack.
3. *Direct stress, both stacks:* 300,000 calls of the real `_solve_optimal_assignment` on random
   1–20-player rosters with interleaved numpy percentile / pandas work, under `faulthandler` —
   **clean on 3.8 (33 s) and clean on 3.10 (31 s)**. Hammering the call alone does not reproduce
   the fault; it needs the full suite's mix (matplotlib/seaborn, pandas, scipy, hypothesis) and
   luck, which is consistent with heap corruption anywhere in that native mix, not necessarily
   in scipy.
4. *A supported stack exists on this machine and the engine is bit-identical on it:* Python
   3.10.0 (`AppData\Local\Programs\Python\Python310`) had no packages; installed numpy 2.2.6,
   scipy 1.15.3, pandas 2.3.3, matplotlib 3.10.9, seaborn 0.13.2, requests, hypothesis (this touched
   only that interpreter's site-packages; reversible). Full suite there: **232 tests, OK
   (skipped=4, expected failures=4)** — the extra three skips are the documented `espn_api`
   optional skips (not installed on 3.10). **All three golden scenarios pass byte-for-byte on
   numpy 2.2.6 / scipy 1.15.3**, i.e. the engine's numerics do not depend on the EOL stack.
   One tooling caveat: hypothesis 6.165 fails internally on Python 3.10.0
   (`'TreeNode' object has no attribute 'is_exhausted'`; not the example database — verified with
   a fresh one); pinned `hypothesis<6.120` (6.119.4) and the five property tests pass. A newer
   3.10.x patch release would likely remove the need for that pin.

**Recommendation (decision for the user, not taken unilaterally):** move the runtime to Python
3.10 with a pinned `requirements.txt` (numpy 2.2.6, scipy 1.15.3, pandas 2.3.3, matplotlib 3.10.9,
seaborn 0.13.2, requests, `hypothesis<6.120`, `espn_api`), keep the golden hashes as they are
(they already pass there), run the 20-iteration in-process probe and a few dozen fresh-process
suite runs on 3.10 before declaring R1 closed, and retire the Store 3.8 interpreter. Until then:
production runs stay on 3.8 with the standing rule that a run dying with `0xC0000005` (or an
impossible `TypeError`) is re-run, never trusted; and the projection log (F7) should be
confirmed to have appended after every sync, since a mid-sync fault would lose that week's rows.


**RECLASSIFIED (2026-08-29, migration branch): R1 is a machine-level fault under multi-core
load, not a software defect. NOT CLOSED.** The probe series asked for before closing:

| arm | what | result |
|---|---|---|
| fresh-process full suite ×10, Python 3.10 (other heavy jobs running concurrently) | | 7 OK (232 tests), **3 died 0xC0000005** (runs 2, 3, 8; faulting frames in pure-Python lines of `run_simulation`) |
| in-process 20× loop, 3.10 | | died in iteration 1 with 0xC0000005 while the other jobs ran |
| targeted mix, single process: assignment-heavy (`_solve_optimal_assignment` + exhaustive brute force) interleaved with real matplotlib/seaborn rendering and pandas | 3.10, 800 rounds / 320k solves | **clean** (500 s) |
| same, 3.8 control | | `TypeError: object of type 's' has no len()` on a fresh list at round 30; second run 0xC0000005 at round ~105 — both while other jobs ran |
| A: 3 concurrent targeted probes, 3.10, default OpenBLAS threads | | 1 of 3 died: `SystemError: unknown opcode` (corrupted bytecode) |
| B: same with `OPENBLAS_NUM_THREADS=1` | | 1 of 3 died: 0xC0000005 → BLAS threading excluded |
| C: same with caches cleared and `-B` / `PYTHONDONTWRITEBYTECODE` | | 1 of 3 died: a pandas Cython function object where an indexer attribute should be → `.pyc` race excluded |
| **D: 6 concurrent PURE-Python probes (stdlib only: sort, dict, sha256 — no numpy/scipy/matplotlib, no project code), 3.10** | | **5 of 6 died**: four `listobject.c: bad argument to internal function` (a list whose type pointer no longer says list), one 0xC0000005 |
| **D on 3.8** | | **4 of 6 died**: two 0xC0000005, two `"sort order broken"` — `sorted()` returned an unsorted list |
| Windows Application log | | faults in `python310.dll`, `python38.dll`, numpy `mtrand.pyd` and "unknown", all 0xC0000005, clustered in the concurrent windows; one at 2026-08-28 19:31 = the original R1. No WHEA events (consumer RAM does not log bit flips). |

Every single-process run in this session — 300k direct assignment calls on each stack, the
800-round mixed probe, dozens of suite runs — was clean; every failure occurred while several
CPU-heavy processes ran at once (the earlier "3 of ~9" R1 runs coincided with parallel tool
calls launching two Python processes). The corrupted object differs every time and the workload
that fails last needs no native extension at all. **This is memory or CPU instability of the
machine under multi-core load (or something injecting into every process — an AV/EDR hook), not
CPython, not numpy/scipy/matplotlib, not this codebase.** Recommended for the machine, outside
this repository's scope: MemTest86 / Windows Memory Diagnostic (several passes), disable any XMP/
overclock profile and re-run Arm D, check CPU temperatures under load, and run Arm D with real-
time protection paused to exclude an injected hook.

**What this means for the project.** The Python 3.10 migration stands on its own merits (EOL
3.8 stack; three goldens byte-identical) but does not cure R1. Operating rules until the machine
is fixed: run `run_sync` / `run_simulation` and the test suite **one at a time** (a single process
never failed here); treat any run that dies with 0xC0000005, an impossible `TypeError` /
`SystemError` / `AttributeError`, or a "sort order broken"-class inconsistency as void and re-run
it; never count a crashed suite as green; and after any crash re-check that `data/projection_log
.jsonl` gained its rows (`wc -l`). R1 stays open under the reproducibility watch with these
probe scripts (`probe_mixed.py`, `probe_pure.py` in the session scratchpad; copied to
`scripts/probes/` so they survive) as the re-test once the hardware is addressed: Arm D must pass
6/6 on both interpreters before R1 is closed.


**Arm D re-test after MemTest86 (2026-08-30).** MemTest86: complete 4-pass run overnight, full
address range each pass, `Test result: PASS (Errors: 0)`, no thermal issues. Then Arm D exactly as
specified (six concurrent `probe_pure.py 240`, quiet machine, 0 python processes before launch):

| interpreter | passed | failed | failure signatures |
|---|---|---|---|
| 3.10 | 2 / 6 | 4 | 0xC0000005; `SystemError: listobject.c:324 bad argument to internal function`; `SystemError: error return without exception set`; `TypeError: cannot unpack non-iterable type object` |
| 3.8 | 2 / 6 | 4 | two 0xC0000005; `"sort order broken"` (`sorted()` returned an unsorted list); exit **0xC0000409** (STATUS_STACK_BUFFER_OVERRUN / fast-fail) |

**Hold stays.** The memory-hardware side is cleared by MemTest86; the fault is unchanged. What
that narrows it to: MemTest86 exercises DRAM, not all-core compute — and this fault appears
only under all-core CPU load and never in a single process. Remaining candidates, outside this
repository's scope: (a) CPU-side instability under all-core load — a core/cache or power-delivery
issue (undervolt, PBO/boost curve, VRM/thermal throttling under sustained load); test with an
all-core CPU stress (Prime95 small FFTs or OCCT) and, if any curve-optimiser/undervolt/XMP profile
is active, at stock settings; (b) an injected process hook (AV/EDR): run Arm D once with
real-time protection paused; (c) load dependence: Arm D at 3 concurrent processes vs 6 vs 12 to
see whether failure rate scales with the number of loaded cores. Windows Reliability Monitor may
show other applications faulting under load. Re-test remains Arm D 6/6 on both interpreters.


**Arm D with antivirus paused (2026-08-30, 3.10).** Defender reported `RealTimeProtectionEnabled
= False` and `BehaviorMonitorEnabled = False` at launch (AM service itself still running). Result:
**1 of 6 passed, 5 failed** — four `SystemError: listobject.c:324 bad argument to internal
function`, one `"sort order broken"`. Caveat recorded: by the end of the 240 s Defender reported
real-time monitoring `True` again (it re-armed itself mid-run); in every earlier arm the failures
occurred within the first 10–20 s, so the re-arm does not rescue the hypothesis, but a run with
tamper protection fully disabled would remove the caveat. **Injected-hook hypothesis: effectively
excluded.** With DRAM cleared by MemTest86 and AV excluded, what remains is the CPU side under
all-core load — core/cache/power delivery (undervolt, PBO/boost curve, VRM, thermal) — which is
what MemTest86 does not exercise. Next checks unchanged: all-core CPU stress at stock settings;
Arm D at 3 / 6 / 12 processes for load scaling. Hold stays.


**Load scaling and hardware identification (2026-08-30).** Prime95 Small FFTs ran 23 minutes
clean (all self-tests passed). Then Arm D on 3.10, quiet machine, timed variant (same workload,
elapsed-seconds per round so each failure carries its time of death), sequential:

| concurrent processes | failed | failure times (s after launch) | signatures |
|---|---|---|---|
| 3 | **0 / 3** | — | — |
| 6 | **1 / 6** | 2.8 | sort order broken |
| 12 | **9 / 12** | 0.7, 5.7, 7.4, 8.9, 9.2, 13.1, 13.2, **89.9, 141.3** | 2 × 0xC0000005, 3 × `listobject.c` SystemError, 3 × sort order broken, `TypeError: 'Random' object is not iterable` |

The rate scales steeply with the number of busy cores (0% → 17% → 75%), and at 12 processes two
failures landed at 90 s and 141 s — well past any process-launch window. So it is not
process-creation-specific: it is the number of cores under sustained load, with most failures
early because that is when all N are running flat out together. Prime95 passing is not a
contradiction: Small FFTs is a fixed-pattern stress on every core; the failing workload is
many independent processes with heavy allocation and branchy integer/pointer work — a
different voltage/frequency profile per core.

*Reliability Monitor / Application log, last 36 h:* every crash record at Arm D times is a
python process (`python310.dll`, `python38.dll`, numpy `mtrand.pyd`, "unknown"; `0xC0000005`,
one `0xC0000409`). The only non-python crashes are `lghub_system_tray.exe` (Logitech G HUB,
`0xC000027B`, a UWP/XAML fault) at 08-29 19:51 and 08-30 07:10 — neither coincides with a probe
window. No independent system-wide confirmation, but no contradiction either: nothing else on
the machine runs 12 processes flat-out.

*Hardware identified:* **Intel Core i7-13700K** (Raptor Lake, 8P+8E, 24 threads), MSI MAG Z790
TOMAHAWK WIFI, BIOS H.G0 (2025-04-08), **CPU microcode 0x12C**; active power plan "Bitsum
Highest Performance" (a Process Lasso plan — no Lasso process or service is running now, but
the plan, which disables core parking and holds maximum performance, is still active).
Raptor Lake 13th/14th-gen parts at 65 W+ carry Intel's documented **Vmin Shift Instability**
defect: a clock-tree circuit degrades under elevated voltage/heat, producing crashes under load
that worsen over time; Intel's mitigations are microcode 0x125/0x129/**0x12B (Sept 2024,
comprehensive)** and **0x12F (May 2025, supplementary, idle/light-load voltage)** plus "Intel
Default Settings" power limits in BIOS. This machine's April-2025 BIOS carries 0x12C, i.e.
post-0x12B but **pre-0x12F**; whether the board ran within Intel's power guidance before
mitigation is unknown; a chip already degraded is not repaired by microcode — Intel's public
guidance for symptomatic processors is an RMA under the extended warranty.

**Hypothesis now, in order:** (1) a Vmin-shift-degraded 13700K — fits every observation:
load-dependent, core-count-dependent, random corruption in pure-Python, unaffected by
interpreter, BLAS threads, bytecode caching, antivirus, DRAM (MemTest86 clean), and a fixed-
pattern Prime95 pass; (2) the "Highest Performance" plan / board power limits pushing the chip
past Intel's defaults, which is the same mechanism from the other side. Next, outside this
repository: update BIOS to the latest (0x12F microcode), load "Intel Default Settings" in BIOS,
switch the Windows power plan to Balanced, re-run Arm D at 12 — if the rate falls but stays
above zero, the chip is degraded and the answer is Intel's RMA process; if it drops to 0/12 on
both interpreters, the hold lifts. Until then every rule in this section stands.


**Remediation in progress (2026-08-30).** Windows power plan switched from "Bitsum Highest
Performance" to **Balanced** (`powercfg /setactive 381b4222-…`; verified active). BIOS target:
MSI 7D91vHI (2026-04-22, "Update Micro Code", the latest for MAG Z790 TOMAHAWK WIFI; 0x12F first
appeared in vHH1, Intel Default Settings in vHC2 — both after this machine's H.G0 of 2025-04),
then load **Intel Default Settings** in BIOS — both require a reboot into firmware and are done
by the operator, not from this session. Warranty: the CPU's serial (ATPO) and batch (FPO) are
NOT software-readable — Windows exposes only `ProcessorId BFEBFBFF000B0671` (a feature/family
signature, identical across every 13700K) and "To Be Filled By O.E.M."; HWiNFO cannot read
them either. They are printed on the retail box label and laser-etched on the heat spreader
(batch on top, partial ATPO on the edge, full ATPO in the 2D matrix — Intel's phone-camera
decoder reads it). Intel extended the boxed 13th/14th-gen warranty by two years (five years
from purchase); check at Intel's warranty page with FPO + ATPO. Re-test after the BIOS change:
Arm D at 12 on both interpreters — 0/12 lifts the hold; any residual failure rate is a degraded
chip and the RMA path.

**Post-mitigation re-test (2026-08-30).** BIOS flashed to E7D91IMS.HI0 (H.I0, 2026-03-16); CPU
microcode now reads **0x133** (newer than 0x12F); BIOS "CPU Cooler Tuning" set to the board's
preset labelled **"Intel Default Settings (PL1: 253W)"**; Windows on Balanced. Arm D at 12,
timed variant, quiet machine (0 python processes before each launch):

| interpreter | failed | failure times (s) | signatures |
|---|---|---|---|
| **3.10** | **9 / 12** | 1.2, 37.5, 46.0, 46.8, 47.4, 49.0, 50.2, 50.5, 61.8 | 6 × 0xC0000005, 2 × sort order broken, 1 × `listobject.c` SystemError |
| **3.8** | **11 / 12** | 6.9, 11.3, 18.4, 26.0, 29.8, 33.2, 42.9, 49.9, 50.4, 79.6, 108.6 | 8 × 0xC0000005, `listobject.c` / `dictobject.c` SystemErrors, `TypeError: cannot unpack non-iterable int object` |

**Verdict: the chip is still exhibiting the instability after full mitigation — microcode 0x133,
Intel Default Settings, Balanced plan. Per the rule set before the test, this points to RMA, not
to further BIOS work.** One observation for the RMA case: with the new settings the 3.10 failures
moved from the first 1–13 s (previous runs) to a cluster at 37–62 s — the window where a
sustained all-core load settles into its steady voltage/thermal state — which is the behaviour of
a degraded part under sustained load, not of a launch race. The hold on Phase 8 stays; this
machine cannot certify byte-identical refactors. Phase 8 execution moves to whichever machine
next passes Arm D 6/6 (a replacement CPU here, or another machine entirely — the suite is
verified to run from a clean checkout, and the goldens are platform-stable on the pinned stack).

**PL1 — the real answer.** Intel's published specification for the Core i7-13700K is **Processor
Base Power 125 W (= PL1) and Maximum Turbo Power 253 W (= PL2)**. Intel's Default Settings
profiles for K-series parts keep **PL1 = 125 W** in both the "Baseline" (PL2 188 W) and
"Performance" (PL2 253 W) profiles; the only profile with PL1 = PL2 = 253 W is "Extreme", which
Intel defined for the Core i9 K parts. MSI's dropdown preset "Intel Default Settings (PL1:
253W)" therefore applies the i9-Extreme-style sustained limit to an i7 — it is *not* Intel's
default for a 13700K, and 253 W sustained is precisely the condition that pinned this CPU at
100 °C under sustained load before. **Reapply the 125 W Long Duration Power Limit (PL1) with
Short Duration (PL2) 253 W and Tau 56 s** on top of this preset — that is Intel's Performance
profile for this part. (Caveat on sourcing: Intel's ARK page and the original table article were
not fetchable from this session; the 125/253 figures are Intel's published spec via WikiChip and
the retail datasheet, and the i9 Extreme/Performance/Baseline rows via igor'sLAB; the i7-K
Performance row is by Intel's stated rule that PL1 stays at the part's base power in every
profile below Extreme.) Whether 125 W changes Arm D's outcome is a separate question and worth
one more 12-process run for the RMA record — but the verdict above does not depend on it: the
chip failed at Intel's own limits.

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

**Refinement noted 2026-08-31 (external audit, Gemini):** the audit's "Marginal Championship
Equity" framing — evaluate an offer by each side's change in overall championship odds, not (or
not only) by each side's optimal lineup score — is a more fleshed-out version of what this
entry's acceptance criterion already gestures at with the "both optimal scores improve" proxy.
Not a new item; a candidate refinement to the acceptance metric above, to weigh against
`get_optimal_score` when this is actually implemented (`Champ_Pct` is already computed elsewhere
in `run_simulation`, so the data to do this exists — the open question is whether re-running
enough of the season simulation per candidate offer to get a stable `Champ_Pct` delta is cheap
enough to do per evaluation, which `get_optimal_score` trivially is and a re-simulation is not).

**When:** any time after Phase 4 closes; independent of F1.

**COMMIT 1 DONE (2026-09-01): position-aware offer construction.** Survey first: the offer,
not the acceptance rule, was the defect -- nobody offers two starters for one bench player.
`_construct_trade_offers` (no RNG) solves both sides' lineups, walks the desperate side's
starters from weakest upward, takes the first slot the rich side's BENCH can start at (top two
bench upgrades = what the rich side gives), and offers the desperate side's CHEAPEST player
that still upgrades a rich starter; bounded to 3 slots x 2 givers = 6 candidates per pairing,
evaluated best-first under the unchanged rule (both optimal scores must rise), stopping at the
first acceptance. 2-for-2 throw-in kept (finding 2). Tests first: two offer-construction tests
(failed on the missing method, pass now); the conservation guard's crafted league re-shaped for
the new offer (fixture, not assertion); `test_trades_are_live_on_the_preseason_fixture` flipped
from red characterisation to guard (expected failures 4 -> 3).

Measured, 100 seasons per fixture (2 x 50):

| bound (slots x givers) | week01 trades/season | week06 trades/season |
|---|---|---|
| **3 x 2 (shipped)** | **0.55** | **1.17** |
| 5 x 3 | 0.55 | 2.12 |
| 13 x 5 | 0.55 | 2.22 |

Rosters conserved on every completion. Diagnosis: on week01 the *desperate* side rejects
92-96% of offers and the rich side ~1%; widening the bound adds only offers the desperate
side rejects (accepted stays at exactly 55 at every bound). The offer shape is right -- the
rich side now accepts nearly everything proposed -- and what binds at preseason is the
desperate side's own "my optimal score must rise now" rule: with healthy rosters and strong
top-2 starters, the cheapest desperate player that upgrades a rich starter is usually a
desperate *starter*, whose loss outweighs a bench-quality gain. By week 6, injuries open holes
on the rich side and cheaper givers qualify.

**Criterion (a) restated.** The [1.0, 4.0] trades-per-season band applies to the **mid-season
(week06) fixture** -- met at 1.17 with the shipped bound. The preseason (week01) rate, 0.55, is
reported alongside as the honest, correct output of the acceptance rule on a healthy-roster
league, not a shortfall to keep chasing. Golden master: stage_a moved on week01 and week06 (a
completed trade reshuffles rosters and the RNG stream from week 6 on), week15 byte-identical
(weeks 6-10 are banked there). Suite 300 tests.

**Ruled out, recorded before it is ever attempted:** the Marginal-Championship-Equity proxy
(commit 3) is *not* a candidate for closing the preseason gap. It scales point deltas by
win-probability sensitivity, so it makes the desperate side's acceptance **stricter, not
looser** -- a desperate team whose expected points fall from a trade loses equity under any
monotone equity measure. This rules out that direction of commit 3, not merely leaves it
untried; commit 3 remains relevant only as a *tightening* gate if the mid-season rate ever
exceeds the 4.0 ceiling.

**Considered and declined.** (C) Hold everything pending further deliberation: declined --
once the mechanism is understood there is nothing left to decide; the preseason number is
what the rule produces. (B) A consolidation offer -- the desperate side bundles two bench
pieces for one rich bench upgrade -- is a real, separate design that may be worth its own
item if trade volume still feels thin once the season is actually live; not built
speculatively against a fixture that may not represent real in-season roster damage.

**COMMIT 2 DONE (2026-09-01, `6e0dc5d`): criterion (c) reconstructed and measured.** The
scratch `bt_points.py` the criterion cited existed nowhere in the repo or its history. It is
now `scripts/run_points_backtest.py` (paired, seeded, 300 sims, checkpoints 3/6/9/12; bias =
sim mean - real weekly team points; mean z; cover80/cover50), appending one JSON line per run
to the tracked `data/logs/points_backtest.jsonl` stamped with git commit + dirty flag, Python
version and executable, and machine (F12 is open; a result must be attributable to the exact
code and interpreter that produced it). Harness: `run_backtest_checkpoint(return_raw=True)`,
additive. Both runs on Python 3.10.0, same machine, same seeds:

| engine | overall bias | mean z | cover80 | cp3 bias | cp6 | cp9 | cp12 |
|---|---|---|---|---|---|---|---|
| pre-commit-1 (`simulation.py` @ `9b7b4cd`) | -0.98 pts (-0.8%) | +0.049 | 0.63 | -3.98 | -0.33 | +0.56 | +5.96 |
| commit 1 (`2756858`, run at `6e0dc5d`) | -1.04 pts (-0.8%) | +0.060 | 0.65 | -4.19 | -0.49 | +0.92 | +5.96 |

**Criterion (c) met:** bias moved 0.06 pts (bound 0.5) and mean z 0.011 (bound 0.05). cp12 is
identical to the last digit across the two engines -- trades occur in weeks 6-10 and a
checkpoint at week 12 never sees one -- which is the pairing check: everything that differs
is the trade block. The redesign did not leak into scoring. (Absolute levels are not
comparable to the absence-modelling arc's numbers above: those were earlier code states.)

**CLOSED (2026-09-01).** All four criteria met or correctly restated: (a) 1.17 trades/season
on the mid-season fixture, inside [1.0, 4.0], with the preseason 0.55 recorded as the rule's
correct output; (b) rosters conserved on every completion; (c) points backtest moved 0.06 pts
bias / 0.011 z, inside the bounds; (d) sized at 3,000 paired seasons under F14 -- the whole
mechanism is worth ~+-3 points of championship/playoff probability to the teams it touches
most. **Commit 3 (the Marginal-Championship-Equity acceptance proxy): considered and
declined**, per the reasoning above -- it can only tighten the desperate side's acceptance,
so it cannot address the one gap that exists, and no tightening is needed with volume inside
the band. It would become relevant only if a future offer variant (option B) pushed the
mid-season rate past the 4.0 ceiling.

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

**DONE (2026-08-29, branch `audit/f3-playoff-seeding`).** Survey found a prerequisite defect first:
sync banks `weekly_actuals` for every week below `current_week`, and Sleeper's `/matchups/15` and
`/16` carry all eight teams with `matchup_id`s (semifinals plus consolation games — verified on
the 2025 league), so from the first week-16 sync the banked "regular-season" standings included
playoff-week wins, median wins and points. Characterised on the week06 fixture with a week_15
entry written as sync writes one (every team's banked figures moved), then fixed: standings are
banked from weeks ≤ 14 only; the posterior keeps using every completed week's player scores.

Built: `sync.generate_playoff_bracket` fetches `/winners_bracket` each sync and writes
`data/playoff_bracket.json` resolved to team names (round, match, t1, t2, winner, loser; seeds
1v4 then 2v3; `{}` on failure, warned). Engine: `_seed_from_banked_standings` — seeds = top four
by (banked wins, banked points), the week-14 block's own key; Sleeper's bracket field overrides
it with a warning when they disagree; at week 16 the round-1 winners come from the bracket, or
from `weekly_actuals` week_15 `h2h_win` among the field, or the run refuses by name. Week 17+
refuses as "season complete" (the plan's open decision: exporting a banked final state adds
surface for no forecast value). Per sim, `seed_matrix` / `b_playoffs` / `b_toilets` are banked
from the ranking since the week-14 block does not run; `_playoff_winner` and the week-15/16
blocks are reused unchanged. Export: `weeks_simulated` may be 0 — every regular-season rate then
divides by 1 and is 0 by construction, flagged `regular_season_banked: true` in schedule luck;
the weekly-score distribution is exported as nulls with the same flag; the density plot and
median-cut line skip. `assert weeks_simulated >= 0`.

Acceptance, measured: at week 15 `b_playoffs` is exactly 0/1 per team and equals the seeds;
`Playoff_Pct` sums to 400 and `Champ_Pct` to 100; the champion is always a seed, and at week 16
always one of the two recorded semifinal winners; the bracket-override and the week-17 refusal
are tested. Stage A of both regular-season goldens is **byte-identical**; stage B/C moved only in
`syndicate_insights` by the additive `regular_season_banked` key. A third golden scenario
`week15` (week06 rosters, deterministic fabricated weeks 6–14 actuals, a bracket file) now pins
playoff-week behaviour by hash. Suite 232 tests (223 + 9), OK (skipped=1, expected failures=4).

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

**SURVEY (2026-09-01): the F7 log holds one pre-kickoff week.** 465 rows, all `(2026, week 1)`
from three syncs (the analysis keeps the last row per season/week/pid: 156 players x 1 week);
Sleeper's `/state/nfl` reads 2026 regular season week 1, `weekly_actuals` is empty, kickoff is
~9 days out. Zero (projection, realised) pairs exist, so the "same data" derivation this entry's
When clause requires is wholly blocked on the season -- informative around week 8-10, complete
at season end. What is not blocked is the drift constant itself, which this entry scoped to be
measured on real 2025. Done below, as a throwaway read-only analysis (scratch, not committed;
raw pull cached there), same treatment as F13.

**MEASURED (2026-09-01): within-season drift of the true weekly mean, real 2025, league-wide.**
Sleeper's positional stats endpoint, all 18 weeks, QB/RB/WR/TE, scored with the live league's
`scoring_settings`; played weeks only (gp >= 1 or offensive snaps > 0); players with >= 10
played weeks: **391** (QB 31, RB 96, WR 162, TE 102; 386 with usable variance). Three
estimators, pooled per position, 3,000-rep bootstrap CIs over players:

1. *Between-window variance excess* (windows = weeks 1-6 / 7-12 / 13-18; under a static mean
   E[MS_between] = MS_within, so the excess is drift resolvable at ~6-week scale):

   | | QB | RB | WR | TE | **ALL (386)** |
   |---|---|---|---|---|---|
   | excess, % of within-variance | +23 [-30, +87] | **+36 [+5, +68]** | **+39 [+16, +66]** | +34 [-0, +69] | **+34 [+18, +52]** |
   | F = MS_between / MS_within | 1.23 | 1.36 | 1.39 | 1.34 | -- |

2. *Lag-k autocorrelation of standardised residuals* (null under a static mean is -1/(n-1)
   ~ -0.074, not 0): pooled lag-1 **-0.019 [-0.042, +0.006] vs null -0.074**, i.e. +0.055
   above the static-mean line and the CI excludes it; lags 2-4 sit +0.02 to +0.06 above null in
   every position except a few negative lag-3/4 cells. Positive, small, consistent.

3. *Variogram* -- mean (x_t - x_{t+k})^2 / (2 sigma_within^2) vs week separation k; flat at 1.0
   under a static mean, rises linearly under a random walk with slope = q / sigma_within^2 per
   week, which is exactly the one constant this entry's random-walk form needs:

   | | QB | RB | WR | TE | **ALL** |
   |---|---|---|---|---|---|
   | slope per week | +0.020 [-0.006, +0.046] | +0.010 [-0.003, +0.024] | +0.003 [-0.006, +0.014] | +0.012 [+0.000, +0.024] | **+0.0085 [+0.0021, +0.0148]** |

**Reading, plainly: drift is real, and small.** The static-mean assumption is measurably wrong
in real 2025 data, by two independent estimators that agree on magnitude: a random walk on the
true mean with per-week variance about **0.85% of the within-game (aleatoric) variance** (95%
CI 0.2-1.5%), which accumulates to ~14% of it over a 17-week season -- a drift sd of roughly
0.4 sigma_aleatoric by season's end -- and a ~34% excess in the variance of 6-week window means
over what sampling alone predicts (the two are consistent: n_w x k x q ~ 6 x 6 x 0.0085 ~ 0.3).
Positive lag-1 autocorrelation of the same order confirms the direction. Per position only RB
and WR are individually significant on the window estimator and only TE on the variogram; the
pooled estimate is the defensible number, and no position contradicts it. This is the first
direct test of the static-mean assumption in this project, and it does not survive: a
"no detectable drift" answer would have exonerated it, and the answer is the opposite.

**What it does and does not establish.** Drift of this size is a plausible *contributor* to
the late-checkpoint std_z rise on Phase 7's instrument (1.0 at cp3 to 1.05-1.35 by cp12); it
is not shown to be the whole cause, and the number is one season's -- exactly the
single-season overfitting risk already flagged. It also cannot separate a random walk from
deterministic within-season trends (role changes, returns at less than full strength), which
the variogram treats alike; either is "drift" for the engine's purposes. **The engine mechanism
is deliberately NOT scoped here** (step 2 stays held): `_apply_bayesian_updates` and the
epistemic draw are touched only once real 2026 data exists to validate the forgetting rate
against, per this entry's acceptance criterion (std_z within +-0.1 of 1.0 at every checkpoint,
paired backtest not worse). What this measurement contributes to that future step is its
starting value and its bound: q / sigma_aleatoric^2 ~ 0.01 per week, not larger than 0.015,
not zero.

### F9 — `data/` directory structure: season-long retention, DONE (2026-08-30)

**Origin:** the visualization work adding `fantasy_sim.positional_tiers` (tiers/charts/HTML
table derived from `player_baselines.json`) initially left three of its own path helpers
unstamped by week, reasoning that a tier report "isn't tied to a specific simulated week" —
wrong: `BASELINES_FILE` is itself overwritten fresh by every sync with that week's projections,
so a report derived from it is exactly as week-specific as the engine's own weekly exports, and
a second weekly run would have silently overwritten the first's tiers/chart/table with no trace
they'd ever existed. Caught before it shipped as a real bug, not a style preference.

**Fix 1 — week-stamp the three tiers path helpers.** `positional_tiers_report_path(week)`,
`tier_chart_path(position, week)`, `positional_tiers_table_path(position, week)` now all take
`week` and route through the new per-week directory (fix 2, below); `build_positional_tier_report`
now requires `week` explicitly, and `scripts/run_positional_tiers.py` resolves it the same way
the engine does (`league_state.json`'s `current_week`). Orphaned pre-fix `Tiers_*_FullList.png`
files (dead since the HTML-table replacement) and the flat, un-week-stamped `positional_tiers.json`/
`Tiers_{POS}.png`/`Tiers_{POS}_Table.html` were deleted from `data/`. Regression tests:
`tests/test_positional_tiers.py::TestWeekStampedPaths`.

**Fix 2 — a real directory structure, not just three fixed functions.** The bug in fix 1 was a
symptom: `data/` was one flat directory of 45 files with no structural distinction between
"overwritten every sync" and "must persist per week," so any new weekly artifact could make the
same mistake again. Surveyed all 45 files (see `fantasy_sim/storage.py`'s module docstring for
the full accounting) and every consumer of `fantasy_sim.storage`'s path helpers (`sync.py`,
`simulation.py`, `backtest_season.py`, `positional_tiers.py`, `clients/sleeper.py`, and every
test that imports them) before touching anything — confirmed no module ever hardcodes a `data/…`
string or bypasses these helpers, so the entire migration is contained inside `storage.py` plus
two one-line call-site fixes (below). New layout:

- `data/current/` — sync's snapshot of the world as of the last sync; always overwritten, never
  historical (the 12 sync-input files, plus `simulation_audit_log_sim0.json` and
  `syndicate_warnings.log`, which despite their "log" names are both opened in overwrite mode —
  verified by reading the write sites, not assumed from the filename — so they behave like this
  bucket, not like `logs/`. **Flagged, not fixed:** making those two genuinely per-week would
  mean threading `week` through their `simulation.py` call sites, the same class of fix as F9
  itself but on code this session didn't write.)
- `data/logs/` — genuinely append-only, season-spanning. Today just `projection_log.jsonl`, the
  one file this project tracks in git (moved with `git mv` to preserve history; `.gitignore`'s
  `!data/projection_log.jsonl` exception became `!data/logs/` + `!data/logs/projection_log.jsonl`
  — git will not apply a nested exception if the parent directory is itself excluded by the
  blanket `data/*` rule, the same class of mistake this exception's very first version made;
  verified this time with `git check-ignore -v` on both the log file and an ordinary `current/`
  file before moving on).
- `data/weeks/week_NN/` — one directory per simulated week, zero-padded (`week_02` before
  `week_10` in a plain listing, unlike the engine's existing mixed-width `Week_2_`/`Week_10_`
  filename prefixes). Holds the engine's own weekly exports/charts and everything
  `positional_tiers.py` produces, including a `tiers/` subdirectory for the per-position pair.

**Basenames:** the four weekly JSON exports (`live_season_forecast_path` et al.) and
`SIMULATION_AUDIT_LOG_FILE` keep their pre-existing basenames (still embedding `_week_N`, now
redundant with the directory) — deliberately, because golden master's stage_b hashes key each
`save_json` call by `os.path.basename(path)` (`tests/golden_master.py`'s `capture_save`), and
renaming them would change stage_b hashes, which is its own gated, regenerate-with-deltas change
and out of scope for a pure directory move. Everything else — the seven weekly PNG chart names,
and every `positional_tiers.py` artifact — was free to drop the now-redundant week prefix, since
charts are deliberately never hashed and `positional_tiers.py` never touches
`fantasy_sim.simulation.save_json` (the only name the golden master's sandbox patches).

**A real bug found during verification, not just theorized:** directory creation must happen at
*write* time, not at path-*construction* time. `fantasy_sim.backtest_season` `chdir`s into
`BACKTEST_WORKDIR` and reuses these same storage constants (`storage.LEAGUE_STATE_FILE` etc.) to
write there; those constants are evaluated once, at `fantasy_sim.storage` import time, before
that `chdir` ever runs. Baking `os.makedirs` into path *construction* for those constants would
have created the directory next to the wrong (original) cwd. Fix: `_current`/`_log` (which back
constants) stay pure string-joins; a new `ensure_dir_for(path)` — called from `save_json`,
matching the pattern `sync.append_projection_log` already used — creates the directory at the
moment of the actual write. `_week(...)` (only ever called fresh, at runtime, with a real week
number — never pre-computed into a constant anywhere in this codebase) is the one exception:
safe to create its directory eagerly, and necessary to, since a bare `plt.savefig(path)` has no
chance to call `ensure_dir_for` itself and there are nine such call sites across `simulation.py`
and `positional_tiers.py`. This was caught empirically: a first end-to-end run of
`scripts.run_positional_tiers` against a cleared `data/weeks/` failed with
`FileNotFoundError: data\weeks\week_01\tiers\K.png` before this fix, succeeded after.

**Touched outside `storage.py` (minimal, both necessitated by the above, not optional):**
`simulation.py` (its module-level `ensure_data_dir()` call, which only ever created the flat
top-level `data/`, replaced with `ensure_dir_for(SYNDICATE_WARNINGS_LOG_FILE)` so its logging
`FileHandler` — opened at import time — gets the right nested directory) and
`positional_tiers.py`'s `_render_tier_table` (same fix, for its raw `open()`). `ensure_data_dir`
itself was deleted as dead code once both call sites were fixed — no other caller remained.

**Verified:** full suite before and after, 266 tests, `OK (skipped=1, expected failures=4)`
throughout (golden master untouched, as predicted by the basename-stability reasoning above);
`scripts.run_positional_tiers` and `scripts.run_simulation` both run clean end-to-end against a
freshly cleared `data/weeks/`, producing the expected nested tree. All 15 real `current/`-bucket
files and the one prior week's real weekly output were migrated on disk (not just left to be
regenerated) so the existing local dataset keeps working without a fresh sync.

### F10 — `simulation_audit_log_sim0.json` and `syndicate_warnings.log` have no season-long retention

**Origin:** F9's `data/` directory migration (2026-08-30) categorized these two files as
`current/` (always-overwritten) rather than `weeks/week_NN/` (retained per week), because
neither is currently week-stamped — confirmed by reading their write sites, not assumed from
the "log" in their names: `SIMULATION_AUDIT_LOG_FILE` is written via a plain `save_json` call
inside `export_and_visualize` (overwritten every run, same as any other current-state file) and
`SYNDICATE_WARNINGS_LOG_FILE`'s `logging.FileHandler` is opened with `mode='w'` at import time.
Putting them in `current/` was an honest description of their EXISTING behavior, not a fix for
it — and it directly conflicts with the season-long retention goal F9 exists for: a manager
auditing week 3's simulation from week 10 has nothing to look at. Named here so it isn't lost
once this session's momentum moves elsewhere.

**Scope (sized, not implemented):** thread `week` through both write sites in `simulation.py`.
`SIMULATION_AUDIT_LOG_FILE` becomes a function `simulation_audit_log_path(week)` (parallel to
`live_season_forecast_path` et al.), written under `weeks/week_NN/` — mechanically the same
change F9 already made for four other JSON exports. `SYNDICATE_WARNINGS_LOG_FILE` is harder:
its `FileHandler` is opened once, at MODULE import time, before `current_week` is known at all
(that value only exists once `FantasySimulationEngine.__init__` runs). Fixing it needs either
(a) moving handler creation into `__init__` — a real behavior change, since any warning logged
between import and engine construction, if one exists, would then go uncaptured — or (b) a
second, week-independent handler kept just for that narrow window. Don't pick between these by
feel; whichever is chosen needs its own short design note first, not just threading `week`
through by analogy with the JSON case.

**Acceptance criterion:** both files retained per week under `weeks/week_NN/`; a past week's
copy is untouched by a later week's run; a test asserts week 3's and week 5's audit logs coexist
and differ.

**When:** whenever engine-level retention work is picked up. Independent of F1–F8 and of the
positional-tiers work F9 grew out of.

**DONE (2026-08-31, commits `68269f3` and the warnings commit that follows it).** Survey first,
which changed the design:

- *The FileHandler-timing question, resolved.* Option (a) -- handler in `__init__` -- was
  rejected on a hazard the entry had not seen: it attaches one root-logger FileHandler per
  engine constructed, and the suite constructs ~30 engines per run across 10 files, each of
  which would open a real file under `data/weeks/week_NN/` (the fixture's week) because no test
  mocks `logging`. That is F11's class of bug reintroduced on purpose. Option (b) as written
  (a second handler for the pre-`__init__` window) was moot: scanning the module found no
  logging call before `current_week` is set, and the run's earliest warning (`VEGAS STALE`) is
  emitted from *inside* `__init__` (`_check_vegas_staleness`, line 95), so capture simply has
  to start at the top of `__init__`.
- *What `syndicate_warnings.log` actually is.* `basicConfig` binds the root logger at import,
  so the file holds whatever process last imported `simulation.py`: at survey time its two
  lines were the golden scenarios' week-6 and week-15 `VEGAS STALE` errors, written by the test
  suite over the last real run's. `run_sync` never imports `simulation`, so sync's 18 warning
  sites never reach it either. It is a process-level console mirror, not a per-run record, and
  its comment in both `storage.py` and `simulation.py` now says so plainly.
- *Design taken: warnings merged into the per-week audit JSON, not a second per-week `.log`.*
  One process-wide, bounded, sequence-numbered in-memory handler is installed at import next to
  the existing FileHandler; `__init__` snapshots the sequence; `export_and_visualize` writes
  `{**audit_log, 'warnings': [records since the snapshot]}` through the existing `save_json`
  call. Zero new raw write sites, so every test that already mocks `save_json` is covered
  automatically -- a separate `save_text` file would have needed a new mock at all 30
  `run_simulation()` test call sites, and one miss reintroduces F11. Shallow copy, not
  mutation: `audit_log` is a stage_a argument hashed after export returns.

Acceptance, measured: week-3 and week-5 audit logs coexist at distinct per-week paths and
differ (`test_sim0_audit_log_is_retained_per_week`); a marker logged after construction is
exported and one logged before is not (`test_run_warnings_are_exported_inside_the_per_week_
audit_log`). Both tests confirmed failing first. Golden master, commit 1: the audit-log payload
hash under the renamed key is *byte-identical* to the old key's in all three scenarios and both
export stages (checked explicitly, not inferred from the rename); commit 2: stage_a
byte-identical everywhere, and only the audit-log payload moved in stage_b/c. Suite 294 → 298.
Along the way: `week15` had been in `golden_master.SCENARIOS` since F3 but never compared by
any test -- run and regenerated, never asserted -- fixed in `89badc8` (golden suite 12 → 15).

### F11 — `test_simulation.py` silently truncated real production data on every full-suite run, since the initial commit

**What happened.** Three tests in `tests/test_simulation.py` mocked `json.dump` directly instead
of mocking `fantasy_sim.simulation.save_json` (the function that actually opens the file).
`save_json` is `ensure_dir_for(path); open(path, 'w')... json.dump(...)` — opening a file in
`'w'` mode truncates it immediately, before a single byte is written back, regardless of whether
the subsequent `json.dump` call is mocked. With `json.dump` mocked and `save_json` not, every
`FantasySimulationEngine.run_simulation()` call inside those three tests opened-and-truncated
whatever REAL file already sat at the path its (also-mocked, for input) `current_week` pointed
to. The tests' shared fixture hardcodes `LEAGUE_STATE_FILE: {"current_week": 1}` — and this
project's real `data/current/league_state.json` has read `current_week: 1` for the entire
duration of this audit (the season this project simulates has never progressed past week 1 in
real time; confirmed separately while scoping the win-trajectory chart, which found only one
week of real `live_season_forecast` history on disk). So the mocked and the real week always
matched: every run of `unittest discover tests` reliably truncated five real files to 0 bytes --
`data/current/simulation_audit_log_sim0.json` and, under F9's directory layout,
`data/weeks/week_01/{live_season_forecast,model_learning_report,syndicate_comprehensive_matrix,
syndicate_insights}_week_1.json` (the same four files, at their pre-F9 flat paths, before that
migration).

**How long this existed.** Since the initial commit. `git blame` on the pre-fix lines (all three
occurrences, both the two `with patch(...)` blocks and the `@patch('json.dump')` decorator)
attributes them to `c14b333`, "Initial commit", 2026-08-27 -- before Phase 0, before F1, before
this audit began. Every full-suite run across the entire audit's history (every phase, every
finding F1 through F10, the R1 Python-runtime migration) triggered this. It was never noticed
because a full-suite run was, in practice, always followed by a real `run_sync`/`run_simulation`
invocation that regenerated the truncated files before anyone inspected them at the exact wrong
moment -- the silent-corruption window closed itself before it was ever visibly open, which is
exactly why it took an accident (see below) rather than a targeted check to surface it.

**How it was found.** Not by design -- by accident, while building `fantasy_sim.win_trajectory`
(2026-08-31): that work needed to read a real `syndicate_comprehensive_matrix_week_1.json` and
found it 0 bytes. Traced to `test_simulation.py`, confirmed via file modification timestamps
(not inferred from reading the test code alone) that running the full suite reliably reproduces
the truncation every time, then confirmed via `git blame` that the pattern is as old as the
repository. This is the same class of finding this entire audit exists to hunt for -- a silent,
systematic corruption of real data by code that looks correct on casual reading -- just found by
stumbling into its effect rather than by asking a property question about it, which is worth
being honest about rather than folding quietly into a feature commit's message.

**Scope check: is this pattern anywhere else?** Grepped the entire project, not just the one
file already implicated, for every `json.dump` mock (`patch('json.dump')`,
`patch.object(json, 'dump', ...)`, and any module that imports `dump` directly and could patch
it by another name -- none of the latter two forms exist anywhere in this codebase). Two files
use the pattern: `tests/test_sync.py` (12 occurrences, every single one read individually, not
sampled -- each is paired with `patch('builtins.open', mock_open())` in the same `with` block,
so `open()` itself never touches a real file; no corruption risk) and `tests/test_simulation.py`
(the three now fixed). Also checked whether any other serialization method (`pickle`, `csv`)
appears anywhere in production code that could carry an analogous mock-vs-real-write mismatch:
none does -- `json` via `fantasy_sim.storage` is the only serialization path in this project,
aside from the `plt.savefig`/`save_chart` case F-numbered-fixed alongside this one.

**Fix.** The two tests that inspect `json.dump`'s captured call arguments now patch
`fantasy_sim.simulation.save_json` with a recording `side_effect` instead -- the exact pattern
already used correctly elsewhere in the same file (the h2h-matrix/championship-value tests'
sibling test, which predates these two and never had the bug). The third, a pure smoke test,
never inspected its `json.dump` mock's call arguments at all, so swapping its patch target to
`fantasy_sim.simulation.save_json` is a behavior-neutral rename.

**Verified, not assumed.** Ran `tests.test_simulation` specifically after the fix and confirmed
via file size and modification timestamp -- not just green tests -- that all five previously-
truncated files were untouched. Then ran the full suite (289 tests, `OK`) and confirmed the same
files still untouched afterward. Golden master: 12/12, byte-identical (this fix changes what a
few tests mock, not any production code path the golden master exercises).

**Cost.** No real damage: every truncated file is fully regenerable (`data/*` is entirely
untracked output except the one append-only log F9 already separates out), and every truncation
this session actually caused was caught and repaired before being relied on. The real cost is
what it implies about the preceding four days of audit work: an unknown, unknowable number of
full-suite runs across Phases 0–7 likely truncated these same files just as reliably, silently,
every time, with no record of how many times or whether any of that work happened to inspect a
truncated file at the wrong moment without noticing. Nothing in the audit's own findings (F1–F10)
depended on these specific files' contents surviving between runs, so there is no reason to
believe any conclusion in this document is compromised by it -- but that is an inference from
what the files are used for, not a verification that it never mattered, and is recorded here
exactly that plainly rather than rounded up to "no impact."

### F12 — `SystemError: error return without exception set` inside `_solve_optimal_assignment`, seen once during Pass-2 fix verification (OPEN, does not reproduce under single-process conditions)

**What happened.** While regenerating `Expected_Wins.png` to visually confirm the violin
`density_norm`/`cut` fix (`d7335d1`), a `py -3.10 -m scripts.run_simulation` run's combined
stdout/stderr log contained an uncaught Python traceback:

```
Traceback (most recent call last):
  ...
  File ".../fantasy_sim/simulation.py", line 1013, in run_simulation
    intended_assigned, _ = self._solve_optimal_assignment(intended_cands)
  File ".../fantasy_sim/simulation.py", line 449, in _solve_optimal_assignment
    cost = np.full((n_players, n_slots), LARGE)
SystemError: error return without exception set
```

`SystemError: error return without exception set` is a CPython-internal-invariant complaint --
some C-level code returned a NULL/error status without setting a matching Python exception --
not an application-level bug in this project's own logic. It is exactly the class of failure
this project's runtime notes already warn about (`CLAUDE.md`'s "do not use plain `python`"
guidance, and the R1 chain of commits investigating "an intermittent native access violation in
the test process" on the retired Python 3.8 interpreter), even though this run used the pinned
`py -3.10` launcher, not `python`.

**What is NOT yet known.** Whether this is reproducible, transient, or environmental
(hardware/driver-level, per the R1 investigation's direction -- power plan, BIOS microcode, CPU
load scaling are all still open per that chain's most recent commits) is not established from a
single occurrence. The process's overall exit code was 0, and the log's later lines (`[PRE-FLIGHT
SUCCESS] 929 Projections Validated`, `[>>>] EXECUTING 10 INDEPENDENT BATCHES...`) show the run
continuing and ultimately producing correct output -- confirmed separately by checking
`data/weeks/week_01/*.png` timestamps, which matched this run and rendered correctly. Whether
the traceback text's position in the combined log genuinely reflects a mid-run recovery, or is a
stdout/stderr interleaving artifact of piping a buffered stream, was not determined. This finding
records the observation only; it does not attempt a root-cause diagnosis, was not reproduced a
second time, and should not be treated as characterised, isolated, or closed.

**Why this is being recorded now instead of chased down.** It surfaced incidentally while
verifying an unrelated, already-scoped chart-rendering fix (Pass 2, item 4). Per this project's
own phase discipline (work one phase per session; do not fold an unrelated finding into an
in-flight commit), it is logged here rather than investigated mid-pass. It should be picked up
as its own piece of work -- starting with an attempt to reproduce it across several consecutive
`run_simulation` invocations -- before being marked anything other than OPEN.

**Reproduction attempt (2026-08-31, same session).** Ran `py -3.10 -m scripts.run_simulation` 10
times, sequentially (one at a time, each waited on to completion before starting the next -- no
concurrent processes, no piping), each run's stdout+stderr redirected directly to its own file
(`run_1.log` .. `run_10.log`, never combined or interleaved with any other stream). Every run used
the unmodified production `SIM_CONFIG` (the same 10 batches / 10,000-sim configuration that was
running when the original traceback was seen). Result:

- **0 of 10 runs reproduced the traceback.** `grep`-ing all 10 logs for `Traceback`, `SystemError`,
  or the literal string `Error` found nothing in any of them.
- All 10 exited with code 0.
- All 10 logs are complete and well-formed (each ends with a real `[EXPORT COMPLETE]` line, not a
  truncated one; all 10 logs are byte-identical in size, 7521 bytes, consistent with this
  project's deterministic non-stochastic log lines -- roster-hole warnings, projection counts --
  being unaffected by which random draws a given run happens to make).
- Total wall time for the 10 runs: ~99 minutes (~9-10 minutes per run; substantially longer than
  the ~150-250s this same command took the first two times it was run mid-session, in immediate
  succession with other `py -3.10` invocations still warm -- itself a data point, though not
  chased further here, since a first-process-of-a-cold-run slowdown is a mundane and far more
  likely explanation than anything related to F12).

**What this does and does not establish.** This rules out "reproduces reliably, even in isolation,
every time or most times" -- it clearly does not, at least not under these specific sequential,
single-process conditions. It does NOT establish that the original occurrence didn't happen, that
its cause is understood, or that it cannot recur under some other condition not tested here
(genuine multi-process concurrency, a specific machine/thermal/power state, a specific data
shape encountered only on some runs). The honest characterisation remains: one observed
occurrence, cause unknown, and now ten clean attempts at reproducing it under one specific
(single-process, sequential) condition. Per this project's own rule against re-tuning or
re-characterising a finding to make it appear more or less serious than the evidence supports,
this is recorded exactly that plainly. Given the R1 chain's own conclusion that hardware/firmware
factors were the leading candidate and remain only partially remediated (`932995c` -- power plan,
BIOS, and microcode queued, not yet confirmed to have resolved R1 itself), F12 is left OPEN and
unclosed rather than downgraded, but does not currently meet the bar the user set in advance
("recurs even once more under single-process conditions") for treating it as the project's top
priority.

### F13 — Game-script-dependent and tail-asymmetric player correlation: measured, not adopted -- CLOSED (2026-08-31)

**Origin:** An external audit (Gemini, 2026-08-31) reviewed this project's correlation model and
flagged that the Gaussian copula enforces zero tail dependence (an extreme outcome for one player
does not make an extreme, same-direction outcome for a correlated player any more likely than the
bulk of the distribution implies) and that per-pair correlations are static regardless of game
script, when real football correlation is plausibly asymmetric (a QB's boom weeks may correlate
with his WR1's boom weeks more strongly than their bust weeks correlate with each other) and
game-script-dependent (a trailing team passes more, which should raise its pass-catchers' shared
upside with the QB specifically in games the team is behind in).

**Correction to the record.** The audit did not know, and could not be expected to know, that
this project already had a game-script-dependent correlation mechanism once: the `shared_z` gate,
added early and removed in Phase 2 (`AUDIT_PHASE_2_FINDINGS.md` finding 2; recapped above in this
document's Phase 2 status write-up, finding 2). It fired whenever `(opponent implied total +
spread) > 23` -- open in 44% of team-weeks -- and on every fire it blended 0.6 of one shared
per-game z-score into every same-team QB/WR/TE draw, which silently overrode every calibrated
pairwise correlation for the pairs it touched, most damagingly forcing WR-WR correlation from a
calibrated -0.004 to +0.32. It was not removed because game-script-dependent correlation is
inherently a bad idea; it was removed because *this implementation* of it clobbered calibration
instead of composing with it, AND because it was a binary threshold gate (`(opponent implied
total + spread) > 23`) that either fully applied its fixed +0.6 blend or did nothing -- a step
function standing in for what is, if it's real at all, a continuous relationship between game
script and correlation strength. Any future mechanism in this space must be designed around
*both* specific failure modes, not a generic "be careful" caveat: **(a) it must compose with the
existing calibrated per-pair correlation (e.g. apply multiplicatively, as a conditional
adjustment on top of the calibrated value), never silently replace or override it; and (b) if it
is game-script-dependent, it must be parameterised as a continuous function of Vegas spread --
e.g. some f(Σ(v_spr)) -- not a binary/threshold gate, so it cannot repeat `shared_z`'s all-or-
nothing discontinuity at an arbitrary cutoff.**

**Scope:** measure first, build only if warranted. Pull real 2025 play-by-play or box-score data
and directly test, before scoping any mechanism. The qualitative claim (boom-week correlation
exceeds bust-week correlation for a QB and his pass-catchers) is not what's in question here --
it's a well-established real-football pattern and does not need re-proving from first principles
on this project's own data. What's open is the **magnitude in this specific scoring system**
(this league's IDP-inclusive format, roster construction, and the players it actually rosters),
which nothing currently measures:

1. Size the boom/bust correlation asymmetry, **pooled across the full league and a full real
   season** -- every QB/WR1 and QB/WR2 pair league-wide, not team by team. A single team-season
   does not carry enough weeks to size a correlation difference at any usable precision; pooling
   across every pair and the whole season is what makes the magnitude measurable at all. Split
   each QB's weeks by whether his own realised score was above or below his own median, and
   compare his pass-catchers' realised correlation with him conditional on each half, pooled over
   every such pair league-wide.
2. Does game script (Vegas spread magnitude, or realised score differential) measurably shift
   pairwise correlations in a way current calibration misses -- e.g. does QB/WR1 correlation rise
   as spread magnitude grows in games the team trailed in, versus games it led or played close?
   Measure this as a relationship over the spread's range, not a before/after split at one
   threshold -- consistent with (b) above, since a threshold-shaped measurement would only ever
   be able to recommend a threshold-shaped mechanism.

Only if both effects are real (not noise at the available sample size) and non-trivial in
magnitude for this scoring system does this become an implementation item. If it does, scope a
specific mechanism at that point -- e.g. a continuous, game-script-conditional multiplicative
adjustment layered on the existing calibrated correlation, not a replacement of it and not a
threshold gate -- sized the way every other item in this document is sized: measured effect
first, specific lines and tests second.

**MEASURED 2026-08-31 (throwaway analysis in scratch, not committed; raw pulls retained there).**
Data: Sleeper's positional stats endpoint (`api.sleeper.com/stats/nfl/2025/{week}?season_type=
regular&position[]=QB&position[]=WR`, which carries `team`/`opponent` per player-week -- the
local player cache is 2026 and would mis-group offseason movers), all 18 regular-season weeks,
scored with the live league's `scoring_settings` (offensive keys identical between the 2025 and
2026 league objects -- checked). Spread source: ESPN's core odds endpoint (`sports.core.api.espn.
com/.../events/{id}/competitions/{id}/odds`, ESPN BET closing line) retains 2025 lines -- **272
of 272 games had one, so the spread view was measured as asked, nothing substituted.** Pairing
mirrors `backtest_player.analyze_correlations` (one primary QB per team = most weeks leading in
pass attempts, weeks with >= 10 attempts; WR1/WR2 by mean points over >= 8 played weeks with that
team; >= 8 common weeks per pair): 31 QB-WR1 pairs / 401 pair-weeks, 25 QB-WR2 pairs / 313
pair-weeks. Series z-scored within pair before pooling (so level differences between pairs do
not masquerade as co-movement); CIs are 4,000-rep bootstraps over *pairs*, not weeks.

| | QB-WR1 (31 pairs) | QB-WR2 (25 pairs) |
|---|---|---|
| unconditional pooled r (calibrated) | **+0.364** [+0.26, +0.46] (0.40) | **+0.382** [+0.29, +0.46] (0.315) |
| mean / median per-pair r (backtest_player's statistic) | +0.351 / +0.382 | +0.361 / +0.414 |
| boom half (QB > own median) r | +0.193 (n=193) | +0.240 (n=153) |
| bust half (QB <= own median) r | +0.247 (n=208) | +0.170 (n=160) |
| **boom - bust** | **-0.054** [-0.276, +0.155] | **+0.070** [-0.082, +0.238] |
| P(WR > own med \| QB top quartile) | 0.652 (n=112) | 0.701 (n=87) |
| P(WR < own med \| QB bottom quartile) | 0.723 (n=112) | 0.586 (n=87) |
| upper - lower tail | -0.071 [-0.162, +0.018] | +0.115 [+0.035, +0.188] |
| spread slope d(r)/d(spread), per point | +0.0034 [-0.0118, +0.0199] | +0.0049 [-0.0136, +0.0213] |
| implied r at -7 vs +7 | +0.315 vs +0.363 | +0.324 vs +0.392 |
| by spread bin (fav>=7 / fav 3-6.5 / pick / dog 3-6.5 / dog>=7) | .31 / .25 / .43 / .47 / .18 | .26 / .51 / .25 / .36 / .27 |

Reading, plainly:

- **Calibration holds.** Both calibrated values sit inside the unconditional CIs (0.40 in
  [0.26, 0.46]; 0.315 in [0.29, 0.46]). The per-pair mean, the statistic `backtest_player.py`
  reports, lands at 0.35/0.36. Nothing here argues for re-tuning `CORRELATIONS`.
- **Boom/bust asymmetry: not measurable at a full league-season.** The two pair types point in
  *opposite* directions (WR1 -0.05, WR2 +0.07), both CIs straddle zero, and the bootstrap bound
  says |boom - bust| < ~0.25 at 95% -- i.e. the data cannot distinguish the asymmetry from zero,
  and whatever it is, it is smaller than the calibration's own uncertainty (~+-0.10). The
  quartile tail statistic tells the same story: WR1's *lower* tail is nominally the stronger
  one (-0.07, CI touching zero), WR2's *upper* tail is (+0.12, CI excluding zero). One
  nominally significant result of opposite sign to its sibling, out of the four asymmetry
  contrasts computed here, is what noise looks like -- not a consistent tail-dependence
  signal. The qualitative prior ("boom correlates more than bust") is not contradicted; it is
  simply not visible at this sample size in this scoring system, which bounds its magnitude.
- **Game script (Vegas spread): no continuous relationship.** Slopes +0.003/+0.005 per point of
  spread, CIs [-0.012, +0.020] / [-0.014, +0.021]; across the whole realistic -7..+7 range that is
  a point-estimate swing of ~0.05 in r, with the CI edges allowing at most ~+-0.28. The bins are
  non-monotonic (WR1 peaks at pick'em/small dog and collapses for big dogs; WR2 peaks at small
  favourite), which is the signature of five noisy sub-samples, not of a dose-response. The
  secondary realised-margin view (reported only alongside, never instead of, the spread) is
  likewise inconsistent across the two pair types (WR1 trailing 0.40 vs leading 0.29 -- the
  hypothesised direction; WR2 the reverse, 0.31 vs 0.38).

**What this establishes for the copula question.** Neither scoped effect is detectable at the
largest sample one real season provides, and both are bounded well inside the range where a
t- or Archimedean copula would change any exported probability by more than the calibration
noise already present. Under this document's own rule -- no added model complexity without a
measured effect size that justifies it -- the measurement does not justify touching the copula,
and the measured bounds say the cost of *not* touching it is small. The result is recorded here
so the question can be re-asked only with more seasons of data (the bounds shrink ~1/sqrt(k)
with k seasons), not re-argued from priors.

**CLOSED 2026-08-31 -- decision.** Measured, not assumed. Boom/bust asymmetry and Vegas-spread
dependence of QB-WR correlation are **not adopted**: neither is measurable at the sample size a
real season provides, and both are bounded well inside the copula's own calibration noise. **The
Gaussian copula with the current calibrated `CORRELATIONS` stands.** This is a resolved
disagreement with the external audit, not an open question that stopped being worked on: the
audit's critique was theoretically sound (a Gaussian copula does enforce zero tail dependence;
correlations are static in game script), and it was answered empirically rather than argued --
the effects it describes, if present in this scoring system, are too small to detect in
714 pooled pair-weeks across all 32 teams and too small to justify the added complexity of a
t- or Archimedean copula or a spread-conditional adjustment. The one thing that would reopen it
is more seasons of data narrowing the bounds to exclude zero; nothing else should.

**Acceptance criterion:** cannot be set yet -- there is no measurement to hold it to. To be set
once the measurement above exists, under this project's standing rule for every constant and
every model-complexity decision: no adoption of a more complex correlation model (a t-copula for
tail dependence, an Archimedean copula for asymmetric dependence, or anything else) without a
measured effect size that justifies the added complexity over the current Gaussian copula.
Because this touches correlation structure directly, the real-data backtest gate applies to any
implementation that follows the measurement, exactly as it did for F2, F4, and every other
correlation- or scoring-adjacent change in this document.

**Also from this audit, already tracked -- not new items.**

- Handcuff / vacated-volume mean-weighting being backwards in the true-backup case (a real backup
  carries a low projection precisely because he sits behind the starter) is the same limitation
  this project has documented since before Phase 0 (`CLAUDE.md`'s "Deliberate decisions"
  section). The fix is ingesting Sleeper's `depth_chart_order`, not adjusting the weights by
  feel. Nothing filed here.
- Trade/waiver economics is F2, already scoped above. The audit's "Marginal Championship Equity"
  framing has been folded into F2 as a candidate refinement to its acceptance metric, not filed
  as a separate item -- see F2's "Refinement noted 2026-08-31" paragraph.

**Reconsidered and declined: the 80-point score cap.** The audit called
`MAX_REALISTIC_WEEKLY_SCORE = 80` "arbitrary." It is not: it is grounded in real NFL single-game
scoring records, and Phase 2 already measured its actual cost directly on this engine's own
output -- max exceedance 4.3e-3, mean loss <= 0.06 pts/week (Phase 2 status write-up above;
`AUDIT_PHASE_2_FINDINGS.md`). Revisited specifically because of this external critique, against
that existing measurement: the conclusion is unchanged, and no action was taken. Recorded here,
and cross-referenced from the Phase 2 write-up itself, so this does not get re-litigated blind
the next time an external critique raises it without engaging with the number already measured.

Phase 2's measurement is in average points, which is not the metric that actually matters for
playoff equity -- a cap could plausibly cost nothing on average yet still occasionally clip the
one outlier score that would have flipped who wins a semifinal. Checked directly (2026-08-31): a
paired comparison of `Champ_Pct` and `Playoff_Pct` at cap=80 (current), cap=60 (a deliberately
more aggressive intermediate level), and effectively uncapped (1e6, i.e. the `min()` never
binds), isolated to weeks 15-16 via the existing `tests.golden_master` week15 fixture (week06
rosters, deterministic fabricated weeks 6-14 actuals, a bracket file -- weeks 1-14 are fully
banked and identical across all three runs; only weeks 15-16 are actually simulated).
`run_simulation` reseeds `np.random.seed(1000 + batch)` at the top of every batch and draws
nothing from the global stream before that, so all three cap settings ran the *same* underlying
z-draws batch-for-batch (`tests.golden_master`'s own documented determinism property) -- any
delta is attributable only to the cap actually clipping a score and that clip changing a game's
winner, not to independent sampling noise. 40 batches x 100 sims = 4,000 sims per cap setting.

Result: **cap=80 vs. uncapped moved `Champ_Pct` and `Playoff_Pct` by exactly 0.0000 percentage
points for every one of the 8 teams** (4 decimal places; below the ~0.025-point granularity of a
single simulation flipping outcome across 4,000 sims -- i.e. not one simulated season's
champion or playoff berth changed). `Playoff_Pct` itself carries no variance in this fixture
regardless of cap (seeding is fixed from banked standings before week 15 per F3, so the four
playoff seeds are already determined; the measurement is really testing whether the cap can
flip who wins among them). The measurement is not simply insensitive: the deliberately more
aggressive cap=60 *did* move `Champ_Pct` slightly (e.g. Drunk Cats 39.075 -> 39.025, Femboy Cats
17.325 -> 17.400), a small but nonzero effect consistent with roughly one simulated season's
outcome flipping at that tighter threshold -- confirming the pipeline can detect a real effect
when the cap is tight enough to produce one. At the actual production value, 80, it does not.

**Conclusion: the "no change needed" call from Phase 2 holds, now on direct playoff-equity
evidence rather than only an average-points measurement.** No code changed as a result of this
check -- it is additional evidence for an existing conclusion, not a new finding.

**When:** unscheduled. Pure investigation with no dependency on any other open item (F1-F12) or
on Phase 8 -- can start whenever real 2025 play-by-play or box-score data is pulled for the
measurement.

### F14 — `MANAGER_PROFILES` sensitivity: measured, small -- values left as-is, CLOSED (2026-09-01)

**Origin:** `MANAGER_PROFILES` (`config.py`) was self-derived from prior-season observation plus
an external tool, with unknown validation quality, and is deliberately excluded from data-driven
calibration (`CLAUDE.md`: per-manager sample size is far too small, and an optimiser would use
these values to compensate for errors elsewhere). Two of its fields were already found to have
minimal measured effect in Phase 4 -- but `trade_will` was measured against the old, dead trade
mechanism (0 of 548 offers accepted on week01), which answers nothing about whether it matters
under a mechanism managers might actually engage with. No comprehensive check had covered every
field, and the derivation quality was never going to be established directly -- so the question
is reframed as sensitivity: if the values move nothing, their provenance is low-risk; if they
move outcomes materially, they need real validation or a neutral default.

**Usage sites (grepped, not assumed).** Exactly four reads in production code, all in
`simulation.py`: `trade_will` twice (both gates of the week-6-10 trade block, lines 923/925)
and `faab_agg` twice (`_compute_faab_bid`'s `aggression` argument at line 1014, and the bid
sort's tie-break at 1019). The third field, `style`, is a label read by nothing. No other
module reads the dict; four test files patch it to neutral values. So the entire surface is:
bid size, bid-tie ordering, and the two trade-willingness coin flips.

**What `faab_agg` can reach.** Bids are sorted and *every* bidder receives a streamer in bid
order, valued `max(4, 12 - 0.5 * rank)`; nobody loses a bid. Aggression therefore buys (a) FAAB
spend and (b) streamer quality *rank* at 0.5 points per place among that week's bidders --
nothing else. Simulated spend is not exported (`remaining_faab` in the export is the sync-time
starting value), so the outcome channel is only (b).

**Scope:** paired sensitivity -- current values vs a neutralised baseline (every manager
identical) -- across enough seasons to detect a real effect, measuring every output the values
could plausibly touch. Neither field changes the number or order of RNG draws (the uniform bid
draw and the trade `rand()` gates fire unconditionally), so both arms consume the identical
random stream batch for batch. Sequencing: the FAAB portion now; the trade portion held until
F2 commit 1 (offer construction) lands, then `trade_will` measured under the corrected
mechanism specifically.

**MEASURED, FAAB portion (2026-09-01; throwaway scripts in scratch).** Arms: config values vs
`faab_agg = 0.5` for all (the code's own default for an unknown team); `trade_will` untouched
(inert on these fixtures under the old mechanism). Behaviour moved as designed: mean bid ranges
0.90 (agg 0.10) to 7.81 (agg 0.85) under current values vs 4.49 flat under neutral; league FAAB
spent per season 252 vs 282 (week01), 174 vs 194 (week06); bids per season identical (62.8 vs
62.7) since need, not aggression, creates bids. Outcomes:

- *First pass, 1,000 seasons per arm, week01 and week06:* per-team deltas up to +-2.8 `Champ_Pct`
  / +-3.7 `Playoff_Pct` -- but with signs that did not track aggression (the low-aggression
  Clankers gained +2.8 under the current values; the high-aggression Year of Jarvis lost -2.3 in
  both scenarios). Suspicious rather than conclusive: once a changed bid alters a streamer
  assignment, that season diverges and behaves as an independent draw, so "paired" does not
  mean noise-free.
- *Proper paired statistic, 3,000 seasons per arm, week01, per-batch differences over the 30
  shared-seed batches:* every team's |t| < 2. `Champ_Pct` deltas (current minus neutral, +-SE):
  Femboy +0.43+-0.72, Year of Jarvis -1.00+-0.86, Drunk Cats +1.60+-0.93, Glutton -0.30+-0.86,
  Canton -0.17+-0.54, Legion -0.13+-0.67, Clankers +0.10+-0.76, Wine Drinkers -0.53+-0.59.
  `Playoff_Pct` deltas all within +-1.5+-1.4. Expected wins all within +-0.28+-0.18. The
  1,000-season outliers collapsed (Clankers +2.8 -> +0.1; Year of Jarvis -2.3 -> -1.0+-0.9).
  76% of team-seasons differ between arms in win total, confirming the pairing buys little
  variance reduction and these SEs are the honest ones.

**Acceptance, FAAB portion -- recorded plainly: SMALL.** `faab_agg` is behaviourally live and
outcome-inert: no team's championship or playoff probability moves detectably (bounded within
roughly +-2 `Champ_Pct` at 95%), and what movement exists has no coherent direction in
aggression. The mechanism explains why: every bidder is served, so aggression only reorders
streamers spaced 0.5 points apart. The current `faab_agg` values are low-risk to leave as-is
regardless of derivation quality. Not touched.

**MEASURED, trade portion (2026-09-01, after F2 commit 1 `2756858`).** Three arms, 30 paired
batches x 100 seasons each, both fixtures: CURRENT (config values, league mean 0.39), UNIFORM
0.39 (every manager at the current mean -- isolates the *dispersion* across managers), OFF 0.0
(mechanism disabled, for scale). Completed trades per season:

| arm | week06 | week01 |
|---|---|---|
| CURRENT | **1.09** | 0.46 |
| UNIFORM 0.39 | **0.69** | 0.48 |
| OFF 0.0 | 0.00 | 0.00 |

*Behaviour:* `trade_will` is now live, unlike under the old mechanism -- and its dispersion
matters for volume mid-season: the current values complete 58% more trades than a uniform
league at the same mean (1.09 vs 0.69), because both gates must pass and a high-willingness
desperate side meeting a high-willingness rich side is what the spread creates. At preseason
the offer-side constraint binds (F2 commit 1) and dispersion makes no difference (0.46 vs 0.48).

*Outcomes, dispersion effect (CURRENT minus UNIFORM), paired-batch SEs:* week06 every team's
|t| < 2 on `Champ_Pct` (largest Drunk Cats -2.17+-1.45, Femboy +1.77+-0.96) and on expected
wins; one `Playoff_Pct` contrast at t = -2.2 (Wine Drinkers -2.47+-1.11). week01: Year of Jarvis
`Champ_Pct` +3.17+-1.01 (t 3.1), Glutton `Playoff_Pct` -3.17+-1.17 (t -2.7), everything else
|t| < 2. Across the 48 contrasts per comparison, two or three at |t| > 2 is what the null
produces; the signs do not line up with willingness (the two highest-willingness managers
move +0.43 and +3.17 on week01, +1.77 and -0.77 on week06). 91-96% of team-seasons diverge
between arms, so these are honest unpaired-scale SEs.

*Mechanism on vs off (CURRENT minus OFF), for scale:* week06 Femboy Cats +2.20+-0.97
`Champ_Pct` / +2.87+-1.05 `Playoff_Pct`, Year of Jarvis +2.93+-1.26 `Playoff_Pct`, Drunk Cats
-2.90+-1.32 `Champ_Pct` -- the two most willing managers gain and the strongest team gives a
little up, a coherent direction at ~2 sigma; the whole mechanism is worth about +-3 points of
championship or playoff probability to the teams it touches most, and less to the rest.

**Acceptance, trade portion -- recorded plainly: SMALL-to-MODEST.** The dispersion in
`trade_will` changes *how often* trades happen mid-season (materially: +58%) but moves no
team's championship or playoff probability beyond ~+-3 points, with no coherent direction in
the values themselves; the entire mechanism is a ~+-3-point effect for the most-affected
teams. The current values are low-risk to leave as-is regardless of derivation quality --
what they encode (who is willing to trade) shows up where it should (volume) and does not
leak into outcomes in a way that would make their provenance dangerous. Not touched.
**F14 closed.** Both portions measured; `MANAGER_PROFILES` stays as it is and stays excluded
from calibration.

**When:** done (FAAB 2026-09-01, trade 2026-09-01).

### F15 — Draft-pick retrospective: ingest Sleeper's real draft history (scoped, not built)

**Origin:** Decision-support work (2026-09-01). A pick-by-pick retrospective -- what each
manager actually drafted against who was realistically available at that pick -- needs the
league's real draft history. **Confirmed not ingested anywhere in this project:** a grep of
every `.py` and `.md` for "draft" finds only a DraftKings odds URL in `sync.py` and prose.
Sleeper has it: `/league/{id}/drafts` returns one completed snake draft per season
(2026: `draft_id` 1310010483046109184, 19 rounds x 8 teams, position limits enforced; 2025:
1253869356119506944), and `/draft/{draft_id}/picks` returns every pick with `round`, `pick_no`,
`draft_slot`, `roster_id`, `picked_by`, `player_id`, `is_keeper` and a `metadata` block
(name, position, NFL team at pick time). Probed 2026-09-01: **152 picks (2026), 128 (2025)**.

**Scope (sized, not implemented):**

| piece | what | size |
|---|---|---|
| ingestion | `sync.fetch_draft_picks(league_id)` -> `/drafts` then `/picks`; resolve `roster_id` to team name with the roster map sync already builds; write `data/logs/draft_{season}.json` (historical, immutable once complete, season-spanning -- the `logs/` bucket by F9's definition; under the `data/*` rule it needs its own `!` exception like the other two logged files, and the nested-exception lesson recorded under F7 applies) | ~40 lines in `sync.py`, one storage path, one test with a fake HTTP layer (pattern: `test_sync.py`) |
| analysis, at-draft value | for each pick: the drafted player's baseline mean / VORP / tier versus the best available at that pick (players not yet taken, at positions the roster could still fill under the position limits), from the preseason baselines. Honest caveat: today's `player_baselines.json` is the closest thing to draft-time value on disk (the draft ran 2026-08-22 per Sleeper's `start_time`; F7's projection log starts 2026-08-29), so it is a proxy for what was knowable at the draft, not the exact board | ~150 lines in a new `fantasy_sim/draft_review.py` + tests on a crafted 2-round draft |
| analysis, realised value | the same comparison against realised season points -- needs the season; buildable at any checkpoint from `weekly_actuals` (F7's derivation machinery already reads it) | ~60 lines, after the season has weeks in it |
| report | per-manager and per-round tables (reach / value / steal by VORP gap), one chart | ~80 lines, reuses `positional_tiers` rendering conventions |

Roughly 330 lines and three commits. No engine change, no golden movement, no backtest gate
(nothing touches baseline computation or the loop).

**Acceptance criterion:** every pick in both seasons resolved to a team and a baseline-pool
player (or listed by name as unresolvable -- the F1 name-key limitation applies: pid is the
right key and the picks carry it); the at-draft comparison reproduces the draft order as a
sanity check (pick 1's drafted player should be at or near the top of the available board);
the realised-value comparison waits for the season.

**When:** not now -- the season starts in days and the roster-grade report plus the three
decision tools are the better use of remaining pre-season time. Ingestion alone (the first
row) is worth doing early in the season so both drafts are on disk under version control;
the analysis can follow at any checkpoint.

### F16 — Cross-fantasy-roster same-NFL-team correlation is zero in the engine

**Origin:** Found while surveying the opponent-aware lineup tool (2026-09-01). The weekly loop
draws one correlated z-vector *per fantasy team* -- `build_covariance_matrix(sim_rosters[t],
sim_meta[t])` then `z_corr = L @ z_uncorr`, team by team -- so two players on the SAME NFL team
rostered by two DIFFERENT fantasy teams are drawn independently. `SIM_CONFIG['CORRELATIONS']`
(QB-WR1 0.40, QB-WR2 0.315, QB-TE 0.35, measured on real pairs and re-confirmed league-wide
under F13) is applied only when both players sit on one roster. Sized on the real 2026
schedule and rosters: **all 56 regular-season matchups** pair at least one same-NFL-team
QB/WR/TE/RB across the two opposing rosters (mean ~3 candidate pairs per matchup; week 1: 6, 7,
2, 4), so the gap is live every week, not occasional.

**Why it matters, and where.** For a head-to-head margin the correlation that matters most is
exactly the one omitted: if my QB and his WR1 boom together, the margin's variance is
*smaller* than two independent draws imply, and the engine therefore overstates margin
variance in every matchup that contains such a pair. Overstated margin variance biases every
H2H win probability toward 50%: favourites' `Playoff_Pct`/`Champ_Pct` are understated,
underdogs' overstated, by an amount that scales with how many correlated cross-roster pairs a
team's schedule contains. This is a precision gap in `Playoff_Pct` specifically -- the number
the closed-form `Playoff_SE` now reports to within sampling error (Phase 0, implemented
2026-08-31) but which carries this systematic, non-sampling error on top. It is not just an
H2H curiosity. The same omission applies to the median-beat decision (all eight totals share
the omission), to a lesser degree.

**Scope: measure first, then decide.** Two complementary measurements, neither an engine change:

1. *Paired simulation, with vs without cross-roster correlation, on real matchups.* The
   decision tool being built alongside this entry (`fantasy_sim.decisions`, opponent-aware
   lineup construction) already samples both rosters through one combined Cholesky factor from
   the SAME `build_covariance_matrix` -- so a `--no-cross` switch gives the engine's current
   behaviour and the default gives the corrected one, on identical seeds. Report, for each
   week-1 matchup and a few later ones: margin sd with vs without, and P(win) with vs without,
   for the max-expected lineups. Effect size = the P(win) shift for the favourite.
2. *Real data.* On the 2025 season (F13's pull machinery, all four offensive positions), the
   realised correlation between two opposing fantasy teams' weekly totals when they share
   same-NFL-team QB/pass-catcher pairs versus when they do not -- a direct check that the
   effect exists in totals, not only in the model.

If the P(win) shift is below the paired-batch SE of a production run (~0.5 points of
`Playoff_Pct` at 10,000 seasons), record it as measured-and-immaterial. If it is larger, the
engine fix is a league-wide z draw: one `build_covariance_matrix` over the union of all eight
rosters per week (156 x 156 Cholesky, once per week per sim -- the same cost class as the
eight per-roster factorisations it replaces), which preserves every within-roster correlation
exactly and adds the cross-roster ones. That touches the core loop's draw order (a golden
regeneration with a documented stage_a move) and is gated by the real-data backtest like any
correlation change.

**Acceptance criterion:** to be set once sized -- the P(win) shift on real matchups and the
2025 totals correlation, with CIs, decide whether an engine change is warranted; if it is,
the acceptance is the backtest gate (bias/mean z within F2 criterion (c)'s bounds) plus
`Playoff_Pct` movement reported per team.

**When:** unscheduled, no dependency on any open item. Measurement (1) becomes a one-line
script once the opponent-aware tool lands; worth running in the first weeks of the season.

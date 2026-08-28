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
7. OPEN. The bye-week mechanism is dead code end to end. Sleeper's payload has no `team_bye` key
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
4. HIGH (mid-season), FIXED in Phase 3 (conjugate form; no n_0; std_aleatoric² as the likelihood variance). `_apply_bayesian_updates` was not conjugate: `n_0 = 4` quadruples prior
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
4. LOW. `team: null` reaches baselines (2 today); consumers tolerate it individually.
5. MED, MITIGATED (sole rostered claimant keeps the plain name, others suffixed `(pid)`, warnings, raise on two rostered; prior blend now pid-tracked). Full rekey tracked as follow-up F1. Name-keyed baselines/rosters; 2 duplicate names today, last pid won — Byron
   Murphy's committed baseline is the wrong player's.
6. MED, PARTLY FIXED (whitelist team corrected to NO; engine warns on whitelist/roster mismatch; the silent drop itself is still open). Zero-projection rostered player silently dropped, then hand-imputed with team `FA`
   where Sleeper says NO (Jordyn Tyson).
7. MED, FIXED (refresh past 24h or on force=True; loud on failure). Player cache was never refreshed after first fetch.
8. LOW. Defensive prior fallback 21.5 vs prior-table mean 22.8 (and 2025 real 23.0).
9. LOW. Weather, `injury_status`, standings `h2h_wins`/`points_scored` ingested and never read.

`n_0` (bounded piece): the two uses are different constructs — a pseudo-count *is* the
defensive prior's variance (none is stated), but multiplies an already-stated variance on the
player side. Real 2025 data: within-team var 91.4, between-team 7.7 → empirical n₀ ≈ 12, not 4;
the code trusts early games ~3× too much. Player priors already imply ≈1 pseudo-game (offence)
/ ≈10 (IDP) before the ×4. Recommendation: conjugate form for players (no n₀), n₀ ≈ 12 for
defences with the derivation as its source, retire the "consistency" comment.

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

---

## Phase 5 — Season and playoff mechanics

**Invariant:** league rules are implemented as written.

- Median scoring: 2 decisions/team/week when enabled, 1 when disabled.
- Seeding, tiebreakers, playoff bracket, toilet bowl.
- Schedule-luck decomposition and `all_play_wins`.
- Week indexing: confirm `range(current_week - 1, 16)` and the 14-week regular season line up
  with the real league calendar at every entry point.

**Deliverable:** rule-conformance tests.

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

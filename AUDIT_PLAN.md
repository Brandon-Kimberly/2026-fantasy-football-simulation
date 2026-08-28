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

# Audit Phase 2 — Statistical Core

**Invariant under test:** the sampler draws from the distribution it claims to.

**Deliverable:** `tests/test_distributions.py` — 14 tests. 7 pass and lock verified properties;
7 fail and characterise the defects below. Plus the variance-budget finding the plan asked for
(§ Variance budget).

**Suite:** 110 → 124 tests. No pre-existing test changed behaviour. Runtime 34s → 72s: six of
the new tests run the real `run_simulation` on a controlled league (300–600 seasons each) so
that moments are asserted against the engine rather than against a transcription of its formulas.

**Status:** characterisation only. Nothing is fixed. Triage before remediation, as in Phase 1.

---

## Method

The weekly draw is inline in `run_simulation` with no seam of its own: lognormal base,
correlated `z` via the Cholesky factor, the `shared_z` game-script mix, `env_var`,
`script_mult`, the cap. Copying those formulas into a test would verify the copy, not the
engine. So every property that *can* be reached through production code is:

- **`controlled_season`** runs the real `run_simulation` on a league built to be tractable —
  identical players, all on team `FA` (identity covariance), injuries off, manager profiles
  zeroed (no trades, no FAAB), no byes, no completed weeks. The weekly team score is then a sum
  of 13 iid draws with closed-form mean and variance, and epistemic structure shows up as
  within-season correlation of weekly scores.
- Covariance tests call the real `build_covariance_matrix`; Bayesian tests call the real
  `_apply_bayesian_updates` through the mock-filesystem pattern `test_simulation.py` uses;
  environment tests call the real `_compute_future_week_matchup_environment` on the committed
  week01 fixture's real ratings and schedule.
- Where only a formula-level Monte Carlo was possible (per-player cap exceedance, realised
  pairwise score correlations by pair type), it is labelled as such and its result is reported,
  not asserted.

Every probe was run on the committed Phase 0 fixtures, so the numbers below are reproducible.

---

## Verified — holds, now locked with tests

| Plan item | Result | Test |
|---|---|---|
| Lognormal `E[X] == mean_val` after the `μ = log(mean) − σ²/2` correction | Engine-level team mean 152.5 vs analytic 152.5 (SE 0.10); formula-level rel. error 4e-5 at realistic σ | `TestWeeklyDrawMoments` |
| Variance of the draw equals `std_aleatoric²` × environment factors | Engine-level team sd 18.18 vs analytic 18.23 — this also validates the `env_var` variance model | `TestWeeklyDrawMoments` |
| Epistemic drawn **once per season and held**, aleatoric redrawn weekly | Within-season week correlation 0.247 (predicted 0.252); season-level excess variance 1408 (predicted 1453); both → 0 with epistemic off | `TestEpistemicStructure` (3 tests) |
| Covariance matrix PSD in all cases | 3000 random rosters (every position, same-team clusters to 25, `FA`/`None` teams): 0 failures. No committed fixture roster triggers the repair branch (min eigenvalue ≥ 0.996 on all 8) | `TestCovarianceMatrix` |
| Cap `MAX_REALISTIC_WEEKLY_SCORE = 80` vs `env_var` tails | Rostered players, neutral environment: max P(X > 80) = 2.7e-4 (10 of 156 above 1e-4). Best case (Gibbs, RB script +10%, total 28.5): 4.3e-3, mean loss 0.05 pts/week. The cap clips only the tail it was meant to. Already bounded by two tests in `test_simulation.py`; nothing added | — |
| Near-zero regime `mean_val → 0` | `std_aleatoric = k·√max(0.5, mean)` floors the std, so CV → ∞ (127 at mean 0.01) and σ reaches 3.1. `E[X] = mean` still holds exactly; the tail is thin in absolute terms (P(X > 5) ≤ 8e-4). 195 of 964 baselines have σ > 1, **none rostered**. Benign | reported |

The `shared_z` gate's semantics are also pinned by a passing test
(`test_the_gate_is_the_opponents_implied_total`), because Finding 2 depends on them.

---

## Variance budget (the plan's written deliverable)

`VOLATILITY_CONSTANTS` are calibrated so that `k·√mean` matches the **total** empirical
week-to-week std of real players (`backtest_player.analyze_aleatoric_variance`). The engine then
uses that as the lognormal σ **and** stacks three more sources on top. Measured on the 156
rostered fixture players, per-player weekly variance relative to `std_aleatoric²`:

| layer | Var / std_a² (median) | note |
|---|---|---|
| lognormal alone, neutral total 21.5 | 0.955 | the (21.5/22)² factor |
| + `env_var` ~ N(v/22, 0.10) | 1.001 | `env_var` itself adds only ~1–4% |
| + `shared_z` mix (0.8 z + 0.6 s) | 1.000 | marginal variance preserved by design (0.64 + 0.36) |
| **realistic schedule** (v drawn from weeks 2–14) | **1.173** (range 1.09–1.29) | **sd +8.3%**, mean **+2.8%** |

Decomposition of the realistic figure: `(E[v]/22)² = 1.057` from the normaliser mismatch
(Finding 1), `Var[v/22] = 0.016` from schedule spread (a real matchup signal, but one the
calibration target already contains), `0.010` from `env_var`, and the `mean²·0.01` term from
`env_var` acting on the mean (≈ +3% at the median CV of 0.55).

**Conclusion:** `env_var` is *not* a material double-count — the plan's suspicion does not hold.
The material excess is the normaliser. The realised per-player sd is 8% above calibrated, the
mean 2.8% above, and the largest single cause is one hardcoded constant.

---

## Findings

### 1. The environment multiplier is not mean-preserving — `v_tot / 22.0` vs a schedule mean of 22.6 — **FIXED**

Every expected and realised score is multiplied by `v_tot / 22.0` (inline, twice). For that to
leave calibrated means intact it must average 1 over the games actually played. On the week01
fixture's real ratings and schedule:

| week | mean `v_tot` | mean multiplier |
|---|---|---|
| 1 (Vegas) | 22.61 | 1.028 |
| 2–14 (model, `(off + opp_def)/2`) | 22.62 | **1.028** |
| `FA` / bye fallback | 21.50 | 0.977 |

Three constants that should agree don't: the `22.0` normaliser, `LEAGUE_AVG_PPG = 21.5`, and
the ratings themselves (`off_rating` mean 22.61, `points_allowed_estimate` mean 22.82).

**Blast radius:** every player, every week. Uniform across teams, so rankings and playoff odds
barely move — but every absolute output (`Expected_Points`, weekly percentiles, the median-cut
line, and the backtest CRPS against real points) carries a +2.8% mean and +17% variance shift
against the calibration it claims to rest on. Fixing it moves `stage_a` hashes in both
scenarios. **Severity: high** (silent, systematic, contradicts the calibration).

**Fixed.** The normaliser is now `_compute_environment_normaliser()`: the mean implied total
over every (NFL team, week) the run simulates, built from the same `_compute_week_environment`
the weekly loop uses (extracted so there is one code path, not a mirror). The multiplier
therefore averages exactly 1 by construction; the regression test asserts it to 9 places.
Deterministic, so no RNG order changed.

Observed on regeneration (fixed together with finding 2, so the RNG stream also moved): week01
weekly team mean 179.9 → 171.7 (−4.6%) at the golden size of 30 seasons. **That figure is
mostly noise.** Isolated at 400 seasons: pinning the new normaliser back to 22.0 on the same RNG
stream gives −2.65% (prediction 22.0/22.63 = −2.78%) for finding 1 alone; finding 2.s removal
moves the mean +0.27% ± 0.34 (nil, as a unit-variance mix should); total old → new is −2.39%
(180.05 → 175.75, SE 0.43 each). An earlier draft attributed the gap to rostered players sitting
on high-total offences; that cannot be right — the normaliser ratio is uniform across players —
and is withdrawn. The rostered-weighted mean `v_tot` is 23.09 (vs 22.63 league-wide), but that
affects the level under both normalisers equally, not the change. The Phase 1 conserved quantities — win sums, all-play and h2h
totals, playoff/champ/toilet shares, seed counts — are **sum-identical, not bit-identical**: the
arrays themselves (and their `stage_a` hashes) all moved, because different simulations now win.
Their sums are invariant to any draw by construction (each week awards exactly one decision
per team, every pair plays every week, every season seats exactly 4/1/1), which is precisely
what Phase 1 locked; the sums held is a check that those invariants survived the change, not
a claim that the outcomes did.

### 2. `shared_z` injects +0.32 correlation into every pass-catcher pair for 44% of team-weeks — **FIXED**

```python
eff_z = (z_corr[idx] * 0.8) + (shared_z * 0.6) if (p_pos in ['WR','TE','QB'] and (v_tot + v_spr) > 23.0) else z_corr[idx]
```

Two things, measured:

- **The gate is not "high-scoring game".** `total + spread` is identically the *opponent's*
  implied total (32/32 Vegas pairs, 384/384 model pairs), so the condition is "opponent projected
  above 23". It is open for **169 of 384 team-weeks (44%)** on the real schedule.
- **What it does when open.** `0.8 z + 0.6 s` keeps each marginal at unit variance but adds
  0.36 correlation on `z` between *every* pair of qualifying pass-catchers on the team — on top
  of the copula. Realised on scores (formula-level, real Cholesky factor):

| pair | calibrated target | gate closed | gate open |
|---|---|---|---|
| QB–WR1 | 0.400 | 0.349 | **0.573** |
| QB–WR2 | 0.315 | 0.270 | **0.512** |
| QB–TE | 0.350 | 0.296 | **0.531** |
| WR1–WR2 | **−0.004** (measured on real pairs) | −0.002 | **0.320** |
| WR1–TE | untargeted (0) | −0.004 | **0.319** |
| QB–RB, WR–RB | 0 | 0.00 | 0.00 (RBs are excluded even in shootouts) |

Confirmed through the real engine: a two-receiver DET team's week-1 variance is **657.7 with the
gate open vs 600.4 closed** (paired draws, SE 6.0), a +57 shift where the calibrated WR–WR
target predicts ~0.

Season-averaged, WR–WR realised correlation is ≈ 0.44 × 0.32 ≈ **0.14 against a calibrated
−0.004**. `CORRELATIONS` was measured on real data by `backtest_player.analyze_correlations`;
the engine then overrides it for nearly half the season. **Severity: high.** Moves `stage_a`.

**Fixed** by removing the mix and its per-game draw. The copula is the one place correlation
is set, and `CORRELATIONS` — measured on real scores — already contains whatever shared
game-script effect exists in reality. The engine-level test now passes (2·ΔCov within noise of
0). The gate's semantics are kept pinned by a passing test so any future "high-scoring game"
condition is built on the right field. Removing the draw shifts the RNG stream, so this
commit's hash movement is not separable from finding 1's; both are one cause pair by design.

### 3. Copula targets are applied on `z` but were calibrated on scores — 12–14% attenuation

Even with the gate closed (table above), realised score correlations sit 12–14% below target:
Pearson on lognormals is smaller than on the underlying normals, and independent `env_var`
attenuates further. `analyze_correlations` measures Pearson on real *scores*, so the targets
are already score-scale numbers being fed into a `z`-scale copula. **Severity: low–medium**
(consistent undershoot; direction is opposite to Finding 2 and partially masks it).

### 4. `_apply_bayesian_updates` is not the conjugate normal it stands in for

```python
post_var  = 1 / (n_0 / prior_var + n / actual_var)      # n_0 = 4.0
actual_var = max(np.var(scores), 0.5 * prior_var)       # sample variance of n scores, ddof=0
```

Conjugate normal with known observation variance is `1/prior_var + n/σ²`, and the model *has* σ:
`std_aleatoric` is calibrated to exactly the week-to-week variance this likelihood needs. Two
departures, both measured on the week06 fixture (156 players, n = 5 each):

- **`n_0 = 4` multiplies the prior precision by 4** on top of the prior's own stated variance —
  equivalent to halving `std_epistemic` before updating. `DEF_RATING_SHRINKAGE_N0` in
  `config.py` describes this as "trust N games of prior", but 4 games of trust would be
  `prior_var = σ²/4`, not `4 × (1/prior_var)`. The prior already carries a variance; this
  double-counts it.
- **The likelihood variance is the sample variance of 5 scores, floored at half the prior
  variance.** That ties the update strength to the *prior's* width rather than to aleatoric
  noise, and the direction flips by position:

| | weight on data (closed-form) | weight on data (engine) |
|---|---|---|
| offense (WR/RB/QB/TE, sd_e ≈ 0.3–0.6 × mean) | 0.75–0.89 | **0.29–0.67** (under-updated) |
| IDP (LB/DL/DB, sd_e = 0.15 × mean) | 0.32–0.38 | **0.07–0.71** (driven by 5-sample variance noise) |
| median, all players | 0.802 | 0.602 |

- **Posterior std is over-confident everywhere:** engine / closed-form = **0.69** (min 0.32).
  Posterior sd falls to 31.5% of prior after 5 games, vs 44.5% closed-form. That posterior
  `std_epistemic` is what the once-per-season draw uses, so the season-level spread of every
  updated player is narrowed to ~70% of what the stated model implies.
- Largest mean discrepancy: Drake Maye, engine 19.80 vs conjugate 22.63 (2.83 pts/week).

The existing `test_bayesian_shrinkage_math` asserts the code's own arithmetic back at itself and
calls it "James-Stein"; it is neither James-Stein nor conjugate. **Severity: high** for
mid-season runs (no effect at week 1, when there are no completed weeks). Moves week06 `stage_a`.

### 5. Zero-score weeks are scored as observed performance — **FIXED**

`_apply_bayesian_updates` ingests every `player_scores` entry. **20 of the 780** player-weeks in
the week06 fixture are exactly 0.0 — byes and DNPs, which `backtest_player.
collect_real_player_weekly_scores` explicitly excludes for that reason. In the engine a 0 is a
real game: two above-prior games of 12 and 13 plus one 0.0 pull a prior of 10 down to 9.34.
Related to Phase 1 finding 7 (byes are unmodelled): the zeros are the byes the engine cannot
see. **Severity: medium.** Moves week06 `stage_a`.

**Fixed:** `_apply_bayesian_updates` now skips a week whose score is exactly 0.0, the same rule
`backtest_player` applies. Negative scores (IDP can go negative) are real games and are kept.
Moved week06 only, as predicted — week01 has no completed weeks. Weekly team mean 110.26 →
111.55 (+1.2%): dropping the zeros lifts the posteriors they had been dragging down.

### 6. PSD repair is not renormalised to a correlation matrix — **FIXED**

```python
cov += (abs(min_eig) + 1e-4) * np.eye(n)
return np.linalg.cholesky(cov)
```

Diagonal loading makes every marginal variance `1 + δ` and leaves the off-diagonals in absolute
terms. When the branch fires, `z_corr = L z` has sd `√(1+δ)` for every player on the roster —
inflating every lognormal σ on that team — and every effective correlation is shrunk to
`corr / (1+δ)`. In the existing 7-WR test scenario δ = 0.08 (sd × 1.039); in the fuzz, 93 of
3000 rosters fired it, max δ = 0.51 (sd × 1.23, correlations ÷ 1.5). **Never fires on a committed
fixture roster.** **Severity: low** — a correct-when-it-matters defect in a branch that
currently doesn't fire. Moves no hash today.

**Fixed:** after loading the diagonal, the matrix is rescaled by `1/√(d_i d_j)` back to a
correlation matrix — same eigenvalue shift, unit marginals. Confirmed to leave all 16 fixture
roster matrices bit-identical (max |Δ| = 0), so it moved no hash.

### 7. QB correlation is non-monotone in receiver rank — **FIXED**

`rank 0 → QB_WR1 (0.40)`, `rank 1 → QB_WR2 (0.315)`, everything else — WR3, WR4 *and* the TE —
falls through to `QB_TE (0.35)`. A team's third and fourth receivers are modelled as more
correlated with their QB than the second. **Severity: low.** Moves `stage_a` if fixed (any
roster with ≥ 3 same-team pass-catchers).

**Fixed:** a TE always takes `QB_TE`; WRs are ranked among the team's WRs only, rank 0 → `QB_WR1`,
rank ≥ 1 → `QB_WR2`. The WR3+ value is **unverified** — `backtest_player` calibrates only WR1 and
WR2, so WR2's value is carried down as a ceiling rather than inventing a smaller one; the
property enforced is monotonicity, and the exact WR3+ value is a Phase 7 calibration item. No
fixture roster has a same-team QB with ≥ 3 pass-catchers or a TE ranked above a WR, so this
moved no hash (all 16 matrices bit-identical).

### 8. Minor, reported only

- `std_aleatoric` is not re-derived after the posterior moves `mean` (up to 5.27 pts on the
  fixture): it stays at `k·√(prior mean)`. Low.
- `expected_pre = mean·(v/22)·s + c` vs `E[final] = (mean + c)·(v/22)·s`: contingency points are
  not environment-scaled in the lineup criterion. Difference is `c·(1 − v/22)` ≈ 2% of contingency
  points. Negligible for lineup ranking.

---

## Triage table

| # | Finding | Severity | Blast radius | Moves hashes |
|---|---|---|---|---|
| 1 | Environment normaliser not mean-preserving (+2.8% mean, +17% var) — **fixed** | High | every player, every week, both scenarios | `stage_a`, both |
| 2 | `shared_z` overrides calibrated correlations for 44% of team-weeks — **fixed** | High | every QB/WR/TE pair on the same NFL team | `stage_a`, both |
| 3 | Copula targets calibrated on scores, applied on `z` (−12–14%) | Low–Med | all correlated pairs | `stage_a`, both |
| 4 | Bayesian update not conjugate; over-confident posterior | High (mid-season) | every player with completed weeks | week06 `stage_a` |
| 5 | Zero-score weeks treated as observed — **fixed** | Medium | players with a bye/DNP in history | week06 `stage_a` |
| 6 | PSD repair not renormalised — **fixed** | Low | rosters with dense same-team clusters | none (verified) |
| 7 | Receiver-rank correlation non-monotone — **fixed** | Low | teams with ≥ 3 same-team pass-catchers | none on fixtures (verified) |
| 8 | Two minor consistency notes | Negligible | — | — |

Findings 1 and 2 are both "the engine quietly overrides its own calibration" — the same class as
Phase 1's hardcoded-14 family, and worth fixing together since both touch the same inline block.
Finding 4 should be decided jointly with `DEF_RATING_SHRINKAGE_N0` (Phase 3), which uses the same
construct on the same reasoning.

## Coverage gaps (stated, not papered over)

1. The +17% realistic-schedule variance budget is reported, not asserted; its root cause
   (Finding 1) is asserted.
2. Findings 2 and 3's pairwise correlation numbers are formula-level with the real Cholesky
   factor. The engine-level test asserts the *variance* consequence on a two-player team, which
   is the observable that production code exposes.
3. Batch seed independence (`np.random.seed(1000 + batch)`) remains Phase 0's open item.

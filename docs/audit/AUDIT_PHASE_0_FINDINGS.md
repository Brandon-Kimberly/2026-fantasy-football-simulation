# Phase 0 — Reproducibility harness: findings

Session date: 2026-08-27. Branch: `audit/phase-0-reproducibility`.

Environment for every number below: Python 3.8.10, numpy 1.24.4, pandas 2.0.3, scipy 1.10.1,
matplotlib 3.7.5, seaborn 0.13.2, Windows 11 (10.0.26200), single-threaded.

Test suite: **72 tests passing before, 84 passing after** (12 added, 0 removed, 0 skipped —
`espn_api` is installed in this environment, so the 3 documented optional skips do not occur).
Suite wall-clock rose from **10.0s to 31.1s**; the golden master accounts for the difference.

---

## 1. The seeding question

### 1.1 What was asked

`simulation.py:490` calls `np.random.seed(1000 + batch)` at the top of each of 10 batches.
`AUDIT_PLAN.md` flags this: sequential Mersenne Twister seeds are not guaranteed to yield
independent streams, and if the batch streams were correlated, the cross-batch standard error

```python
p_se = (np.std(b_playoffs[t], ddof=1) / np.sqrt(SIM_CONFIG["NUM_BATCHES"])) * 100
```

(`simulation.py:920`) would understate true uncertainty.

### 1.2 Answer

**No correlation, at any power this investigation could bring to bear — including an in-model
test with 99% power against a factor-2 deflation of the batch variance. `Playoff_SE` is not
understated by cross-batch dependence.** Seven lines of evidence, (a)–(g) below.

**But the concern pointed at a real defect next door.** `Playoff_SE` is a *batch-means*
estimator, and batch-means is the right tool for *correlated* streams. These streams are
independent and the simulations are i.i.d., so the batch-means estimator is solving a problem
this code does not have — at a real cost in precision. See §1.4.

### 1.3 Evidence

**(a) Mechanism — which seeding routine, and how far apart do adjacent seeds land?**

numpy's legacy `RandomState.seed(int)` for a 32-bit integer uses `mt19937_seed`, the Knuth
`init_genrand` recurrence, not `init_by_array`. Confirmed by reproducing the recurrence by
hand and comparing to numpy's post-seed state — exact match on all 624 words. So the concern
is the well-formed one: seeds 1000 and 1001 differ by 1 in `mt[0]`.

That difference does not survive the recurrence:

| seeds 1000 vs 1001 | value |
|---|---|
| identical 32-bit words in the 624-word state | **0 / 624** |
| Hamming distance across the full state | **9,911 / 19,968 bits (49.6%)** |

49.6% is the value for two unrelated bit strings. The initial states are already fully
decorrelated before a single number is drawn.

**(b) Direct stream correlation, the 10 seeds actually used.**
200,000 draws from each of seeds 1000–1009; all 45 matched-offset pairwise Pearson
correlations. Max |r| = 0.0036, i.e. **max |z| = 1.62** against a Bonferroni threshold of 3.26.
Nothing.

**(c) The hardest case — the first draws only.**
Simulation #1 of every batch starts at stream position 0, so if adjacent-seed structure
survives anywhere, it survives there. Across 20,000 sequential seeds, lag-1 serial correlation
of the first draw: **z = −1.14**; KS vs Uniform(0,1): **D = 0.0084, p = 0.115**.

One value in this scan looked real and was chased down rather than waved away: the *third*
draw showed lag-1 **z = +3.27** at M = 20,000, nominally Bonferroni-significant over 10 tests.
A real effect grows as √M and repeats on a disjoint seed family. It did neither:

| seed family | M | z for draw k=2 |
|---|---|---|
| 1000..20999 | 20,000 | **+3.27** |
| 1000..200999 | 200,000 | +0.87 |
| 500000..699999 | 200,000 | −0.71 |
| 7000000..7199999 | 200,000 | −0.13 |

At 10× the sample size a genuine effect would have reached z ≈ 10. It fell to 0.87. Across all
40 (family × draw-index) tests the largest |z| was 2.84, which is what the null predicts for 40
tests. Fluke.

**(d) Does the estimator recover the right variance? — surrogate, high power.**
Batch-means reconstructed exactly as `export_and_visualize` computes it (B=10, n=1000,
`np.random.seed(1000+b)`), replicated over 400 disjoint sequential-seed families.
Under independence `(B−1)s²/[p(1−p)/n] ~ χ²₉`.

| draws consumed per sim | p | Σχ² (df=3600) | z | mean s_obs/s_iid |
|---|---|---|---|---|
| 1 (adversarial) | 0.10 | 3454.7 | −1.71 | 0.9555 |
| 1 (adversarial) | 0.50 | 3656.7 | +0.67 | 0.9778 |
| 64 | 0.10 | 3624.4 | +0.29 | 0.9754 |
| 64 | 0.50 | 3637.1 | +0.44 | 0.9763 |

The expected value of `mean(s_obs/s_iid)` under independence is **0.9727** (E[s]/σ at 9 df — s
with Bessel's correction is a biased estimator of σ). Observed values bracket it.

`D=1` is deliberately adversarial: every sim's indicator is taken from the earliest stream
positions, where any adjacent-seed structure would be least diluted. The real engine consumes
**≈ 8,721 variates per simulated season** (measured: 6,112 normal + 2,497 rand + 97 exponential
+ 16 uniform), so ≈ 8.7M per batch and ≈ 87M across the production run — 2²⁶·⁴ draws against a
period of 2¹⁹⁹³⁷. Stream overlap is not a possibility that needs testing.

Positive control, to show the test can detect what it is looking for — inject a known shared
component across batches:

| injected shared-variance fraction ρ | z | mean s_obs/s_iid |
|---|---|---|
| 0.00 | +0.67 | 0.9778 |
| 0.02 | −0.19 | 0.9679 |
| 0.05 | −1.49 | 0.9530 |
| 0.15 | **−5.80** | 0.9015 |

**(e) In-model, on the real production run (10 × 1,000 = 10,000 sims, 420.3s).**

Per team, `(B−1)s²/[p(1−p)/n] ~ χ²₉`, comparing the exported `Playoff_SE` against the i.i.d.
standard error √(p̂(1−p̂)/N):

| team | p̂ | exported SE% | i.i.d. SE% | ratio | χ²₉ | p |
|---|---|---|---|---|---|---|
| Crimson Marmots | 0.4189 | 0.4639 | 0.4934 | 0.940 | 7.96 | 0.923 |
| Turbo Llamas | 0.5793 | 0.4435 | 0.4937 | 0.898 | 7.26 | 0.781 |
| Polar Yetis | 0.5943 | 0.5331 | 0.4910 | 1.086 | 10.61 | 0.607 |
| Neon Walruses | 0.5067 | 0.6337 | 0.5000 | 1.267 | 14.46 | 0.214 |
| Quantum Ferrets | 0.5313 | 0.5710 | 0.4990 | 1.144 | 11.78 | 0.452 |
| Cosmic Badgers | 0.5038 | 0.5613 | 0.5000 | 1.123 | 11.34 | 0.506 |
| Iron Wombats | 0.3897 | 0.5226 | 0.4877 | 1.072 | 10.34 | 0.648 |
| Rocket Pandas | 0.4760 | 0.4837 | 0.4994 | 0.969 | 8.44 | 0.980 |

Ratios sit on both sides of 1. Over all 24 team × metric tests (playoff, championship, toilet
bowl) the smallest p-value was 0.071 — unremarkable for 24 tests.

A far more powerful in-model test is available, because the per-sim *win total* is continuous
where the playoff indicator is binary. One-way ANOVA over the 10 batches, F(9, 9990):

| metric | mean F across teams | min p |
|---|---|---|
| per-sim wins | **1.008** | 0.444 |
| per-sim points | **1.140** | 0.104 |

Under independent streams F = 1. Nothing moves.

Prerequisite for all of the above, verified by inspection: `run_simulation` performs **no**
assignment, item-assignment, or in-place mutation of engine state anywhere in the simulation
loop (lines 462–906). `sim_rosters`, `sim_meta` and `faab` are deep-copied per simulation.
Simulations within a batch are therefore genuinely i.i.d., which is what makes p(1−p)/n the
correct null variance.

**(f) On the limits of evidence (e), and why (d) carries the weight.**

Evidence (e) is a single realisation: 9 degrees of freedom per team. That is weak, and the
power table makes the weakness explicit — an F(9, 9990) test has only **20% power** against a
true variance ratio of 0.5:

| true variance ratio r | power of F(9, 9990) at α=0.05 |
|---|---|
| 0.3 | 0.56 |
| 0.5 | 0.20 |
| 0.7 | 0.08 |
| 1.5 | 0.18 |
| 3.0 | 0.71 |

So a single production run **cannot on its own** rule out a factor-√2 understatement. Evidence
(d) supplies the power the single run lacks — the surrogate replicates the estimator's precise
structure 400 times, reaching df=3600 rather than 9.

**(g) In-model replication of the seeding procedure — 8 independent seed families.**

To close the gap inside the real engine rather than a surrogate, the seeding procedure was
replicated across 8 disjoint families of 10 sequential seeds (1000.., 2000.., … 8000..),
each a full engine run (10 batches × 300 sims; χ² is scale-free in n, so the reduced sim count
purely buys wall-clock). The 8 families are mutually independent, so per team the 8 χ²₉ values
sum to χ²₇₂ — 72 df per team instead of 9. Total: 24,000 simulated seasons, 1,031s.

| team | playoffs z | champs z | toilets z | mean s/σ (playoffs) |
|---|---|---|---|---|
| Crimson Marmots | −0.20 | −0.64 | +0.24 | 0.939 |
| Turbo Llamas | −0.13 | −0.18 | +0.43 | 0.957 |
| Polar Yetis | −0.47 | +0.15 | −1.89 | 0.932 |
| Neon Walruses | −0.82 | +0.15 | −0.37 | 0.908 |
| Quantum Ferrets | −0.11 | −0.48 | +0.61 | 0.936 |
| Cosmic Badgers | +0.08 | +0.15 | +0.59 | 0.979 |
| Iron Wombats | +1.44 | −0.50 | +1.12 | 1.097 |
| Rocket Pandas | −0.83 | −0.10 | −1.29 | 0.906 |

Across all 24 tests exactly one falls below α=0.05 (Polar Yetis, toilet bowl, p=0.038). The null
predicts 1.2 such results in 24 tests. The `s/σ` column clusters on 0.9727, its expected value
under independence.

Power of this pooled 72-df test:

| true variance ratio r | power |
|---|---|
| 0.5 | **0.99** |
| 0.7 | 0.52 |
| 0.8 | 0.23 |
| 1.5 | 0.71 |

A factor-√2 understatement of `Playoff_SE` (r = 0.5) would have been caught with 99%
probability. It was not caught. Combined with (a)–(f), the sequential-seed hypothesis is
closed.

*Process note:* the first attempt at this experiment appeared to have died — `Get-Process`
showed no live interpreter and the log had stalled — and this document briefly said so. That
was wrong; the run was alive with buffered output and completed normally at 1,031s. The
numbers above are from that run. Recorded here because "I concluded X and X was wrong" is the
kind of thing this audit is supposed to write down.

### 1.4 The real defect the question surfaced

`Playoff_SE` is estimated from the standard deviation of **10 numbers**. That has 9 degrees of
freedom, so the *reported standard error is itself* imprecise:

| B (batches) | relative SE of the reported SE | 95% interval for reported/true |
|---|---|---|
| **10 (current)** | **23.6%** | **[0.55, 1.45]** |
| 20 | 16.2% | [0.68, 1.31] |
| 50 | 10.1% | [0.80, 1.20] |
| 100 | 7.1% | [0.86, 1.14] |

A reported `Playoff_SE` can legitimately land 45% low or 45% high with nothing wrong at all.
That is visible in the real run: Neon Walruses' exported SE is 27% above the i.i.d. value,
Turbo Llamas' 10% below — both pure estimator noise.

This is not a Mersenne Twister problem. It is an estimator-choice problem. Because the
simulations are i.i.d. Bernoulli draws, the Monte Carlo standard error of a proportion has a
closed form:

```
SE = sqrt(p_hat * (1 - p_hat) / N)      # N = 10,000
```

which is exact and carries no estimation noise at all. The batch-means estimator exists for
*correlated* sample paths (MCMC), where no closed form is available. Applying it to i.i.d.
Monte Carlo throws away precision for nothing.

Note the same 9 df is what limits the diagnostics in §1.3(e): the estimator's imprecision and
the test's low power are the same fact seen twice.

---

## 2. Proposals (not implemented — reporting first, as instructed)

Listed in order of value. None of these has been applied; the working tree contains only the
test harness.

1. **Replace the batch-means SE with the closed form.** One line in
   `export_and_visualize`. Removes 23.6% estimator noise from every reported SE at zero
   runtime cost. Requires threading a global playoff/champ/toilet count out of
   `run_simulation` alongside the per-batch rates, or simply computing p̂ from the batch rates
   (already available) and applying the formula. Under CLAUDE.md rule 1 this needs a failing
   test first — the natural one asserts the reported SE equals √(p̂(1−p̂)/N) to within float
   tolerance, which fails today.

2. **Do not migrate to `default_rng(SeedSequence)` on the strength of the independence
   concern** — the concern is not supported by the evidence, and the migration would change
   every number in the model (different bit generator, different variate algorithms) while
   invalidating the golden master, in exchange for a property the current code already has.
   There are *other* good reasons to prefer the modern API (no global state; explicit
   generator objects; reproducibility that cannot be perturbed by a third-party library
   drawing from `np.random`), and if the migration happens it should be argued and costed on
   those, in its own commit, with the golden master regenerated deliberately.

3. **`--seed` CLI flag** (the outstanding `AUDIT_PLAN.md` Phase 0 item). The seed base is
   currently the literal `1000`, so every production run is the same fixed realisation and
   run-to-run stability cannot be assessed without editing source. A `--seed` flag threaded to
   the seed base makes that a command-line matter. Deferred to keep this session's diff to the
   two requested deliverables.

4. **Raise `NUM_BATCHES` only if the batch structure is kept.** If proposal 1 is taken, batch
   count stops mattering for SE precision and 10 is fine.

---

## 3. Deliverable 2 — the golden master

### 3.1 What was built

| file | role |
|---|---|
| `tests/golden_master.py` | harness: fixture sandbox, canonicalisation, hashing, `--regenerate` |
| `tests/test_golden_master.py` | 12 tests |
| `tests/fixtures/golden/week01/` | 11 input files — verbatim snapshot of a real `run_sync` at `current_week = 1` |
| `tests/fixtures/golden/week06/` | 11 input files — mid-season state, 5 completed weeks |
| `tests/fixtures/golden/expected/*.json` | the recorded hashes (8.1 KB each) |
| `tests/fixtures/make_week06_fixture.py` | regenerates week06 deterministically from week01 |

Fixtures total 207 KB for week01 and 237 KB for week06 — small enough to commit. `data/` is
gitignored, so the fixtures had to be copied in; the harness reads only from
`tests/fixtures/`, never from `data/`, and writes nothing (`save_json` and `plt.savefig` are
both intercepted).

### 3.2 Hashing is split into three stages so failures localise

- **stage_a** — the 17 arguments `run_simulation` passes to `export_and_visualize`. This is
  the complete output of `run_simulation`. A stage_a break means the engine's numbers moved.
- **stage_b** — the 5 payloads `export_and_visualize` passes to `save_json`. A stage_b break
  with stage_a intact means the export layer moved, not the engine.
- **stage_c** — the export re-run on the same arguments with championship appearances scaled
  past `MIN_CHAMP_APPEARANCES_FOR_RANKING`. See §3.4.

27 hashed outputs per scenario, two scenarios.

Floats are canonicalised through `float.hex()`, which is exact, so the hashes catch bit-level
drift. Every hash is stored with a `summary` (n, sum, mean, std, min, max) that is never
asserted on and exists solely so a failure prints stored-vs-current moments — the difference
between "one ulp on a different platform" and "the distribution moved" is then visible without
re-running anything.

### 3.3 The harness is proved to be load-bearing

Per CLAUDE.md rule 2 — a golden master that cannot fail is worthless, and there are several
ways this one could silently become vacuous. Three tests exist to prevent that:

- `test_golden_master_detects_a_change_in_the_model` triples every `INJURY_RATES` entry and
  **requires** the hashes to move, including `stage_a/wins` specifically. Verified to fail if
  the harness stops observing the model.
- `test_stage_c_actually_exercises_the_champion_ranking_block` requires stage_c's insights
  payload to differ from stage_b's, so the branch coverage stage_c buys cannot silently lapse.
- `test_canonicalisation_distinguishes_values_a_naive_encoder_would_not` pins the cases a
  weaker encoder would collide: `0.1+0.2` vs `0.3`, `1` vs `1.0`, `-0.0` vs `0.0`, key order
  vs key content, `zeros((2,3))` vs `zeros((3,2))`.

Separately, `test_output_does_not_depend_on_ambient_rng_state` perturbs the global numpy
stream before two runs and requires identical hashes. This is the property everything else
rests on: `run_simulation` seeds the global stream itself before any draw, so results are not
hostage to test execution order. Verified to hold.

### 3.4 Coverage gaps — stated, not papered over

1. `export_and_visualize` gates its championship-share ranking behind
   `MIN_CHAMP_APPEARANCES_FOR_RANKING = 50`. A player accrues at most one appearance per
   simulation his team wins, so reaching 50 needs thousands of sims — far more than a fast
   test can afford. **Measured**: at 2 × 15 the stage_b ranking holds 0 entries. stage_c
   closes this for the export path (20 entries) by scaling appearances by 40 and re-running.
   It does **not** close it for `run_simulation`; the accumulation side is covered only
   inside stage_a's hash of `champ_players`.
2. **Charts are not hashed.** PNG bytes vary with matplotlib/freetype versions and platform
   font rasterisation, so hashing them would generate failures carrying no information about
   the model. The data behind every chart *is* covered (trajectories, weekly scores, H2H
   matrix, seed matrix in stage_a; exported JSON in stage_b), and the plotting calls still
   execute for real under the golden run, so an exception in the plotting path is caught. A
   refactor that broke only chart *appearance* would pass. Charts remain uncovered.
3. Fixtures cover two league states. They do not cover a `current_week` past the end of the
   14-week regular season, a league size other than 8, or `MEDIAN_SCORING_ENABLED = False`.
4. These are **characterisation** hashes. They pin what the engine does. They assert nothing
   about whether it is right — that is Phases 1–7.

### 3.5 Cost

Golden master adds **21.1s** to the suite (10.0s → 31.1s). Most of that is matplotlib
rendering 7 figures per export call, not the simulation itself. The rendering is deliberately
left live rather than stubbed, so that an exception in the plotting path is still caught.

---

## 4. Perf baseline (Phase 0 deliverable)

| configuration | wall-clock | per sim |
|---|---|---|
| production, 10 × 1,000 = 10,000 sims (`run_simulation` only, export excluded) | **420.3s** | 42.0 ms |
| including engine init and full export | ~430s | — |
| RNG consumption | 8,721 variates per simulated season | ≈ 87M per production run |

Single-threaded, no vectorisation. This is the number Phase 8's performance work should be
measured against.

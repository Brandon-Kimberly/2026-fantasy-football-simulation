# Audit Phase 7 — calibration

Branch `audit/phase-7-calibration` from `main` at `d4286e4` (2026-08-29). Order fixed before the
phase started: (1) per-position `INJURY_RATES`, (2) `EPISTEMIC_ERROR_RATES`, (3) the Phase 2
finding-4 (conjugate posterior) re-run. Fixed inputs throughout, not free parameters:
`ONSET_EXPOSURE_STARTER` / `_BENCH` 1.05 / 0.84 and `LOCKED_ONSET_PROBABILITY` 0.21 (F6),
`ABSENCE_RETURN_HAZARD_*` 0.29 / 0.16 (F4). Every number below is the paired, seeded, points-level
backtest on real 2025 (300 sims, checkpoints 3/6/9/12, `bt_inputs_f4`) unless stated.

## 1. `INJURY_RATES` — redefined as all-cause weekly absence-onset hazard; WR and QB re-derived

**Redefinition.** `INJURY_RATES[pos]` now means P(rostered player scores exactly 0 this week |
scored > 0 last week), any cause. That is what the engine must reproduce against a backtest
that scores real weekly team points; the previous injury-only conversion from "% missing ≥ 1
game per season" (all active NFL players, not an 8-team league's rostered players) is kept in
`config.py` under "superseded derivation" and no longer defines the constant.

**Measured, real 2025, weeks 2–14, Wilson 95%** (rostered player-weeks with > 0 the week before):

| pos | k / n | hazard | interval | config | verdict |
|---|---|---|---|---|---|
| QB | 8 / 149 | 0.054 | 0.027–0.102 | 0.025 | outside → **0.054** (n = 8; interval spans ×4) |
| RB | 19 / 414 | 0.046 | 0.030–0.071 | 0.070 | inside, at the edge → unchanged, flagged |
| WR | 38 / 472 | 0.081 | 0.059–0.109 | 0.040 | outside → **0.081** |
| TE | 7 / 142 | 0.049 | 0.024–0.098 | 0.035 | inside → unchanged (n = 7: not derivable) |
| K | 0 / 94 | 0.000 | 0.000–0.039 | 0.005 | inside → unchanged |
| DL/LB/DB | — | — | — | — | no 2025 data (no IDP rostered) → unchanged, unmeasurable here |

Decision rule, applied without exception: a rate moves only where the config lies outside the
interval, and then to the point estimate with the interval written beside it. One season of an
8-team league; the intervals are the caveat in numbers.

**Characterisation** (`tests/test_injury_status.py::TestInjuryRateLevel`, week01 fixture,
2 × 15 seasons, per-position realised hazard vs the interval): QB 0.022 and WR 0.038 outside
(red under `expectedFailure`); RB 0.070 (at the edge), TE 0.036, K 0.006 inside (guard).
Flipped to guards with the change; the RB/TE/K guard survived.

**Sensitivity, measured on the engine before the change** (cp6 gate measurement, overrides
applied in-process): starter-onsets/week and started-zero rate — baseline 3.24 / 0.099;
WR only **4.23 / 0.131**; QB only 3.34 / 0.100; both **4.31 / 0.136**. WR does ≈ 90% of the
work; QB adds ≈ 0.08 onsets/week — real, small, one slot.

**Prediction stated before the change:** starter-onsets 3.24 → ≈ 4.6 (real 4.7); started-zero
rate 0.099 → ≈ 0.14 (real 0.236 / 0.198), residual ≈ 0.07 attributable to the bench-promoted
and left-in manager cases; weeks 6–11 absence 11.9% → toward 14.7%.

**Result against the prediction:**

| quantity | before | **after** | real | predicted | gate |
|---|---|---|---|---|---|
| starter-onsets / week (cp6 / cp3) | 3.24 | **4.31 / 4.46** | 4.7 | ≈ 4.6 | ±0.5 ✓ (below the prediction: the previous-week-lineup proxy dilutes ≈ 7%) |
| started-zero starters / team-week (wks 6–14 / 3–14) | 0.099 / 0.096 | **0.136 / 0.135** | 0.236 / 0.198 | ≈ 0.14 | ±0.05 ✗ — as predicted; residual 0.10 / 0.06 vs the ≈ 0.07 named |
| absence, weeks 6–11, bye-excluded | 11.9% | **14.6%** | 14.7% | toward 14.7% | — (matches) |
| realised onset hazard r | 0.047 | **0.065** | — | rises (level change) | — |
| bias, all | +1.51 (+1.2%) | **+0.72 (+0.6%)** | | toward zero | ✓ |
| mean z | −0.068 | −0.021 | | | |
| cover80 | 0.64 | 0.63 | | ≥ 0.63 | ✓ (at the floor) |
| cp3 / cp6 / cp9 / cp12 bias | −2.1 / +0.8 / +1.2 / +4.9% | **−3.0 / −0.1 / +0.6 / +4.7%** | | | gradient 9.0 → **10.0 pts** ✗ |

The prediction held on every quantity it named: starter-onsets landed at 4.3–4.5 against ≈ 4.6,
the started-zero rate at 0.136 against ≈ 0.14, and absence at 14.6% against 14.7% real. The
residual started-zero gap (0.06–0.10 per team-week) is the manager behaviour F5 named — bench-
promoted and left-in locked zeros — which no rate can supply and which is recorded, not
compensated. No constant beyond WR and QB was touched.

**What the miss on the gradient now says.** With absence at the real level, the checkpoint
profile is early-negative / late-positive: cp3 −3.0% (blank-slate priors on 2 completed weeks
under-predict), cp12 +4.7% (late-season over-prediction). That is no longer an absence
question — it is the prior / posterior question the queue already has next: (2)
`EPISTEMIC_ERROR_RATES` and then (3) the conjugate posterior, whose weight criterion is already
met and which waits on this bias. The gradient criterion (≤ 1.5 pts) therefore passes to those
two items unchanged.

**Goldens** (direction only, 30 seasons): `global_weekly_scores` week01 +0.2%, week06 −0.8% —
more onsets, but onset holes take replacement-level fills, so the mean barely moves.
Regenerated. **Suite:** 215 tests, OK (skipped=1, expected failures=3).

**Also fixed on the way:** the F6 guard was measured on the previous-week-lineup proxy, which
dilutes as onset churn rises (1.19 at the old rates, 1.03 at these); it now measures on
intended-lineup membership — the quantity the engine actually scales on — where the ratio is
1.14 (fixture cross-check `f6_bruteforce.py`: starters 1.017 × expected, bench 0.889 ×; 0 of 587
locked zeros outside the intended lineup).

**On the F6 guard change, for the record (asked 2026-08-29).** Order of events: the guard failed
at 1.03 → proxy dilution was hypothesised → the intended-membership cross-check read 1.14 → the
guard was switched. The decision was made with the result visible. It holds on grounds that do
not depend on the outcome: (1) intended-lineup membership was the pre-specified check — F6's
scoping paragraph (e), written before any F6 code, names "onset count among intended-lineup
players vs bench" as the characterisation; the proxy was an implementation shortcut for
comparability with the real data's forced proxy, and the intended-membership cross-check has
existed since the F6 fix (1.19 then); (2) the proxy's reading moved 1.19 → 1.03 when an
unrelated constant (`INJURY_RATES` level) changed while the mechanism it pins was untouched —
a guard that fails on an unrelated change is measuring the wrong thing; (3) the replacement
measures the engine's actual branch condition with the same bounds, and had it read < 1.05
the rate change would have been the suspect. Not claimable: that the switch would have been
made had intended membership also failed.

## 2 + 3. `EPISTEMIC_ERROR_RATES` and the posterior form — a matched pair; joint change built, gated, REVERTED

**Semantics.** `std_epistemic = EPISTEMIC_ERROR_RATES[pos] × mean` is the prior sd on a player's
true weekly mean; it feeds the once-per-season epistemic draw and `prior_var` in
`_apply_bayesian_updates`. The values (QB 0.30, RB 0.63, WR 0.55, TE 0.50, K 0.40) were tuned
with `backtest_player` on 2025 under the n₀ = 4 form. Sleeper no longer serves 2025 weekly
projections (404 on both URL forms), so production *projection error* cannot be measured.

**Survey measurement 1 — the project's own instrument by checkpoint** (leave-one-out peer prior,
current pair): std_z 0.94–0.98 at cp3 for QB/RB/WR/TE, drifting to 1.05–1.35 by cp12; mean_z
≈ 0. Calibrated where it was tuned (week 4), over-confident later.

**Survey measurement 2 — variance components of a positional prior**, real 2025, per game played:
between-player variance of season means minus the within-player sampling term → sd_true / rostered
mean: QB 0.07 (n = 20), RB 0.28 (46), WR 0.22 (60), TE 0.20 (19), K 0.25 (12). The config rates
are ≈ 2× these — not an error in isolation: the n₀ = 4 form quadruples prior precision, so the
pair is calibrated together.

**The 2 × 2, demonstrated before acting** (asked for explicitly; std_z at cp3 → cp12):

| cell | QB | RB | WR | TE |
|---|---|---|---|---|
| old rates, n₀ = 4 (current) | 0.94 → 1.10 | 0.94 → 1.05 | 0.98 → 1.17 | 0.98 → 1.32 |
| **new rates, n₀ = 4** (requested) | **1.34 → 1.29** | **1.43 → 1.18** | **1.43 → 1.44** | **1.30 → 1.45** |
| old rates, conjugate (5c's cell) | 0.75 → 0.95 | 0.76 → 1.11 | 0.82 → 1.12 | 0.73 → 1.12 |
| new rates, conjugate (joint) | 1.10 → 1.06 | 1.00 → 1.08 | 1.09 → 1.24 | 1.07 → 1.22 |

**A wrong prediction, recorded.** The survey said moving the rates alone under the old form would
"collapse std_z to ≈ 0.5". It went the other way: 1.3–1.5. A narrower prior under a form that
already quadruples prior precision makes the posterior *more* confident, so real surprises look
larger. The coupling claim held; the sign attached to it was backwards. Same class as F6's
wrong-direction miss, and the reason "these must move together" is demonstrated, not derived.

**The joint change** (rates 0.07 / 0.28 / 0.22 / 0.20 / 0.25; conjugate form with
`std_aleatoric` as the known observation sd; mirrored in `backtest_player.compute_bayesian_posterior`;
the two Phase 2 finding-4 tests and the new rate test flipped) was built and run through the
gate. Suite green apart from the week06 goldens (which move: `global_weekly_scores` +1.4%). Then:

| backtest (paired, 300 sims) | cp3 | cp6 | cp9 | cp12 | ALL | cover80 |
|---|---|---|---|---|---|---|
| before (c246492, old pair) | −3.0% | −0.1% | +0.6% | +4.7% | **+0.6%** | 0.63 |
| joint pair on inputs prepped with the old σe (= "old rates, conjugate") | +2.5% | +7.1% | +9.7% | +14.4% | +8.4% | 0.62 |
| **joint pair, inputs re-prepped with the new σe** | **−7.4%** | **−3.7%** | −0.9% | +3.6% | **−2.1%** | **0.61** |
| diagnostic: joint pair, harness σe widened by the prior's own centring error (rate@BASE) | −0.9% | +3.6% | +6.6% | +11.3% | +5.2% | 0.61 |

(The first joint row is a lesson in itself: the engine reads σe from the baselines file, which the
harness writes at prep time, so re-prepping was required — recorded so the next person does not
re-learn it.)

**Verdict: gate missed; reverted; the old pair stands.** The joint pair is neutral-to-slightly-better
on the instrument (a leave-one-out prior centred on the real positional mean) and worse on the
backtest in *both* configurations: with σe as the true spread, the harness's `BASE`-centred prior
(RB 9.0 vs rostered 12.0) is now believed tightly and the early checkpoints under-predict (−7.4%
at cp3, −3.7% at cp6 — caveat 2, but larger than "cp3 only"); with σe widened to absorb that
centring error, the conjugate form trusts the early weeks and the late checkpoints over-predict
(+11.3% at cp12 — 5c's signature). No single σe makes the conjugate form fit both ends, and the
old n₀ = 4 form's slow, floored trust happens to track reality better across the season. The
constants were NOT tuned between those two cells; the pair was reverted.

**Caveat 1 handled — the empirical weight against a correctly centred prior** (leave-one-out
positional mean, look-ahead-safe; players with ≥ 4 non-zero weeks each side of week 6): RB 1.06
(n = 22), QB 1.16 (n = 6), **WR 0.00 (n = 25)**, pooled 0.55; against the `BASE`-centred prior the
same players give 0.99 / 0.90 / 0.42. The earlier 0.68 / 0.80 targets were an artefact of the
low-centred prior and are withdrawn as targets. With WR at zero and QB/RB at one on n = 6–25,
no single weight — hence no single (rate, form) pair — is identifiable from one season.

**What is actually open, named (not "calibration fixed"):**
1. **Within-season drift.** std_z rises to ≈ 1.2 by cp9–cp12 under BOTH pairs. Both forms assume a
   static true mean; a player's true level moves within a season (role changes, returns at less
   than full strength, the post/pre 0.884 effect). This is a model-structure limitation neither
   pair could close and it is the reason the conjugate form over-predicts late. Not fixed here;
   not fixable by any σe.
2. **No production projection-error data.** The engine's real prior is a projection; its error is
   the quantity `EPISTEMIC_ERROR_RATES` should be, and it cannot be measured until projections are
   stored week by week (follow-up F7). Until then the rates stay as tuned under the old form.
3. **Phase 2 finding 4 stays open**, now blocked on (1) and (2) rather than on absence: its weight
   criterion is met but the backtest bias under the conjugate form is the drift in (1).

The characterisation (`tests/test_calibration.py`, red under `expectedFailure`) stays as the
record of the rate/form mismatch; the two finding-4 tests stay red. Engine unchanged.

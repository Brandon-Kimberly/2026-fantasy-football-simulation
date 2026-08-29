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

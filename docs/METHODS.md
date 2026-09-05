# Methods

The statistical specification of the simulation in one document: the distributional
assumptions, how each parameter was estimated, the calibration results as measured, and
the known limitations. Every number here either cites its derivation or says
"unverified" — the same rule the code follows (`fantasy_sim/config.py` carries the
per-constant sourcing comments; `docs/AUDIT_PLAN.md` carries the full measurement
records by finding number, cited as F-numbers below).

## 1. The player-week model

Each player's weekly fantasy score is modeled as lognormal around a projected mean,
with variance split into two components that are handled differently on purpose:

- **Aleatoric variance** (week-to-week performance noise) is redrawn every simulated
  week. Position-level volatility constants scale it: the offensive constants were
  calibrated on real player-week data scored under this league's settings
  (`backtest_player`, F13); the IDP constants (DL 2.16, LB 1.67, DB 1.58) and the
  kicker constant (1.45) were derived from full-NFL 2025 weekly stats scored under
  this league's rules (F28), replacing placeholder values.
- **Epistemic variance** (projection error — how wrong the mean itself may be) is drawn
  **once per simulated season and held fixed**. This is the mechanism that carries
  parameter uncertainty through to season-level outcomes; redrawing it weekly would
  average it away and understate the spread of season results. Its position-level
  rates await a full season of logged projections-vs-outcomes to derive directly
  (F7/F22 — the projection log has captured every sync's projections since 2026-08
  for exactly this purpose).

The projected mean blends two independent sources: Sleeper's weekly projection and
ESPN's, averaged when both exist, with the **disagreement between them driving the
epistemic term** — two sources disputing a projection is a measured, per-player signal
of uncertainty rather than an assumed one. Kickers and IDP are excluded from the mean
blend (the sources' scoring systems cannot be reconciled at the points level) but since
F29 contribute an epistemic signal through a shared-category **stat-line** comparison:
both sources' projected stat lines scored under this league's own settings.

## 2. Dependence

Same-NFL-team players move together through a **Gaussian copula** with measured
correlation coefficients, re-confirmed league-wide on 391 players / 714 pooled
pair-weeks of real data. Tail dependence was measured and deliberately **not** adopted
(the measurement did not support it — recorded in the audit trail rather than assumed
either way). Game environment (Vegas implied totals and spreads, live via
the-odds-api) shifts means through position-level game-script multipliers; the
multiplier magnitudes are documented as under-derived (F33).

## 3. Injuries and availability

Injury onset is a per-week hazard for active players; duration is a two-component
mixture (short/long); players entering with absence statuses (IR, PUP, Out, Sus, DNR,
NA) start inside the duration model rather than healthy. Rates for RB/WR were sourced
from published injury data; TE/QB/DL/LB/DB rates are documented as less rigorously
sourced (`config.py`). When a lead player is absent, his **vacated volume** is
apportioned to same-position teammates weighted by projected means — a mechanism
suspected backwards for months and then **measured as correct** on the 8 real 2025
lead-RB absence events (F24). The capture rate (0.65 of vacated volume realized by
inheritors) was measured at +1.53 points per event, CI [0.87, 2.19], on those 8 events
and **held** rather than re-tuned on a sample that thin (F30).

## 4. Lineups and the lookahead rule

Lineups are chosen by the **Hungarian algorithm** (rectangular linear assignment) on
pre-game expectation, never on realized scores. Any path by which a realized outcome
could influence selection is treated as lookahead leakage and a serious bug; the test
suite pins this. Roster holes are filled by replacement-level streamers whose value is
capped at the position's replacement level computed from real free-agent-pool players —
a cap that exists because uncapped streamer values measurably made roster holes
*profitable* (Phase 4).

## 5. Season mechanics

The season is simulated 10,000 times (10 independent batches × 1,000), with standard
errors computed across batches. Waivers and FAAB bidding run stochastically: bid sizes
follow a lognormal fitted to the real league's 99 attributed 2025 claims (μ = 1.423,
σ = 1.120 in log-points; the real median bid is 4, mean 7.4, max 39), with a
per-manager two-parameter behavior model (aggression, activity) derived from 2025 and
updated in-season from the live decision log under a decaying prior (F31). Simulated
league-wide spending lands inside the pre-declared acceptance band of [650, 800] FAAB
points per season (real 2025: 728). An automatic trade mechanism runs but almost never
completes a trade — a tracked limitation (F2), reported honestly by the behavioral
harness rather than patched cosmetically. The hybrid H2H + weekly-median format is
simulated as such; the 2025 backtests run with median scoring off because that season
was pure H2H.

Defensive strength is estimated by **empirical-Bayes shrinkage**: a conjugate normal
update from a preseason prior toward observed points allowed, with the prior's weight
expressed as a pseudo-count of games derived from the 2025 season's within- vs
between-team variance decomposition.

## 6. Calibration results (as measured, not as hoped)

The points-level backtest reconstructs as-of-week inputs from the real 2025 season at
checkpoints 3/6/9/12 and runs them through the actual, unmodified engine (240 team-week
forecasts). Logged per commit in `data/logs/points_backtest.jsonl`:

- **Mean accuracy**: overall bias **−1.13 points (−0.9%)**, mean z **+0.054** — the
  central forecast is essentially unbiased.
- **Interval calibration**: cover80 **0.65** against a nominal 0.80, cover50 **0.36**
  against 0.50 — the simulated intervals are **too narrow against started lineups**.
  This is owned, not hidden: part of the gap is real manager start/sit error the model
  never claimed to predict (measured at roughly 144 points² of variance), which is why
  calibration is *also* scored against hindsight-optimal lineups with the selection
  premium recentred (cover80 ≈ 0.67, sd(z) 1.25 there). The remaining
  under-dispersion is a standing caveat carried in every release's "what this tag does
  not claim" section, with the 2026 season's pre-registered evaluation
  (`SEASON_2026_EVALUATION.md`, sha256-locked before kickoff) as the decisive test.
- **Against a naive baseline**: the same backtest scores a projections-only static
  forecast (each team's Hungarian-optimal lineup total on checkpoint means, byes
  excluded, nothing else modeled) on identical rows. Measured (2026-09-05, 240
  team-weeks): naive MAE **26.55** vs engine MAE **22.24** — a 16% error reduction —
  and the naive is heavily biased (**−17.8** points: it cannot see injuries or
  absences) where the engine's bias is −1.13. The machinery earns its complexity, and
  the comparison is re-logged with every backtest run
  (`data/logs/points_backtest.jsonl`).

Reproducibility: a byte-exact golden master (15 tests, three scenarios) pins the entire
engine's outputs, platform-locked to Windows; a sync-stage golden pins baseline
generation from pinned inputs; a behavioral harness compares simulated mechanic rates
(spending, claims, bids, lineup churn) against the real 2025 league with a
deterministic drift baseline.

## 7. Known limitations

Stated here in one place; each is tracked with an F-number and an unlock condition:

1. Interval under-dispersion against started lineups (above) — the bracket narrows
   with 2026 data (F25 first measurable ~week 5).
2. IDP epistemic rates are not yet derived from logged projection error (F7/F22;
   unlock: season end).
3. The trade mechanism is inert (~0 completions vs 11 real in 2025) — tracked (F2),
   with its calibration target recorded.
4. The zero-cost free-agent churn channel is measured (122 real 2025 adds, committed
   derivation) but not yet simulated; scheduled as one arc with the claim-premium
   measurement (F34/F32, unlock ~January 2027).
5. Several in-engine constants remain explicitly under-derived and labeled as such
   (game-script multipliers, streamer decay, replacement depth indices — F33).
6. The 2025 backtest crosses a format change (that league year used a team-defense
   slot, not IDP), documented in the backtest's own docstring.

## References

- H. W. Kuhn (1955), "The Hungarian method for the assignment problem," *Naval
  Research Logistics Quarterly* 2:83–97 — the lineup assignment.
- B. Efron & C. Morris (1975), "Data analysis using Stein's estimator and its
  generalizations," *JASA* 70:311–319 — the shrinkage estimator family used for
  defensive ratings.
- R. B. Nelsen (2006), *An Introduction to Copulas*, 2nd ed., Springer — the Gaussian
  copula construction.
- T. Gneiting, F. Balabdaoui & A. E. Raftery (2007), "Probabilistic forecasts,
  calibration and sharpness," *JRSS B* 69:243–268 — the calibration framing the
  backtest gate and the pre-registered evaluation use.

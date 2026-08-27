# Fantasy Football Monte Carlo Simulation

A quant-grade fantasy football simulation engine for an 8-team IDP Sleeper league, with a
statistically rigorous variance/correlation model, an empirical backtesting framework, and a
data pipeline that pulls real projections and market data from Sleeper, ESPN, and Vegas odds
rather than hand-typed guesses.

## What this actually does

Most fantasy projection tools give you a single number per player per week. This project
instead models each player's score as a probability distribution and runs 10,000+ simulated
seasons forward from the current week, producing calibrated distributions over final wins,
playoff odds, and championship odds for every team -- with real, tested statistical machinery
behind every step, not defaults picked because they "felt right."

**Data pipeline** (`fantasy_sim.sync`): pulls real per-player weekly projections from Sleeper,
blends in a second independent projection source from a dedicated ESPN league (configured to
match this league's scoring as closely as ESPN's UI allows), pulls real Vegas point totals and
spreads, and derives NFL team defensive strength from actual completed-game results rather than
a static hand-typed list.

**Simulation engine** (`fantasy_sim.simulation`): draws each player's weekly score from a
lognormal distribution with separately-modeled aleatoric (game-to-game) and epistemic
(projection uncertainty) variance, correlates same-game players through a Gaussian copula
(QB-WR stacking, WR-WR negative correlation), solves the true optimal lineup via the Hungarian
algorithm rather than greedy slot-filling, and models injuries, waiver streaming, trades, and
FAAB bidding stochastically across the season.

**Backtesting** (`fantasy_sim.backtest_season`, `fantasy_sim.backtest_player`): validates the
model in two complementary ways -- a season-level backtest against this league's real 2025
season (reusing the actual, unmodified simulation engine, not a reimplementation), and a
player-level statistical backtest that checks the model's variance, correlation, and epistemic
uncertainty constants directly against real historical player performance. Both are honest
about their own limitations -- see the docstrings in each module for what they do and don't
validate, including a documented confound (a defensive scoring-format mismatch between the
2025 and current seasons) that the season-level backtest cannot fully control for.

## Project structure

```
fantasy_sim/
├── config.py              # every tunable constant: scoring model parameters, league IDs,
│                           # NFL reference data, simulation config
├── storage.py              # every file path this project reads or writes, named once, plus
│                           # load_json/save_json helpers used consistently everywhere
├── clients/
│   ├── sleeper.py          # Sleeper player database client
│   └── espn.py             # ESPN league client (second projection source)
├── sync.py                 # data ingestion pipeline
├── simulation.py           # FantasySimulationEngine -- the Monte Carlo core
├── backtest_season.py      # season-level backtest against real historical outcomes
└── backtest_player.py      # player-level statistical calibration checks

scripts/                    # thin CLI entrypoints, one per pipeline stage
tests/                      # full test suite (59 tests), one module per fantasy_sim module
data/                        # gitignored -- all runtime output lands here
```

## Setup

```bash
pip install -r requirements.txt
```

Two credentials are read from environment variables, never hardcoded:

| Variable | Required for |
|---|---|
| `ODDS_API_KEY` | Live Vegas odds polling after Week 1 (falls back to a verified static Week 1 dataset otherwise) |
| `ESPN_S2`, `ESPN_SWID` | Only if the dedicated ESPN league (see `fantasy_sim/config.py`) is made private |

## Usage

```bash
python -m scripts.run_sync                # pull real data into data/
python -m scripts.run_simulation           # run the Monte Carlo simulation
python -m scripts.run_season_backtest      # backtest against the real 2025 season
python -m scripts.run_player_backtest      # validate variance/correlation constants directly
```

## Testing

```bash
python -m unittest discover tests
```

59 tests across all four modules, including hand-verified numeric checks (e.g. CRPS computed
against a brute-force reference implementation), cross-checks that standalone statistical
replicas produce bit-for-bit identical output to the real production methods they mirror, and
adversarial verification on every regression test added during development (each was confirmed
to actually fail when the bug it guards against was deliberately reintroduced).

## Notable engineering details

- **Two-variance model**: aleatoric variance is redrawn every simulated week; epistemic
  variance is drawn *once* per simulated season and held fixed, correctly propagating
  parameter uncertainty to season-level outcomes instead of averaging it away.
- **Empirical, not hand-typed, defensive ratings**: derived from real completed-game scores
  with empirical-Bayes shrinkage toward a preseason prior.
- **Real backtesting infrastructure**: the season-level backtest writes reconstructed
  historical inputs to an isolated working directory and runs the actual, unmodified
  simulation engine against them -- not a parallel reimplementation that could silently drift
  from what's really deployed.
- **Statistically validated, not guessed, magic numbers**: `VOLATILITY_CONSTANTS`,
  `EPISTEMIC_ERROR_RATES`, and the copula's correlation coefficients were calibrated against
  real historical player-week data via `fantasy_sim.backtest_player`, not set by intuition.

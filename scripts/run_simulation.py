#!/usr/bin/env python3
"""
Runs the Monte Carlo simulation engine against the data produced by run_sync.py, and writes
forecasts, diagnostics, and charts into data/.

Also builds the boom/bust and floor/ceiling report (fantasy_sim.player_variance) right after
the engine call returns, reading sim.player_weekly_scores directly -- that accumulator only
exists after a real run_simulation() call, so this can't be a standalone script the way
positional_tiers/strength_of_schedule are (see player_variance.py's module docstring).

Usage:
    python -m scripts.run_simulation
"""
from fantasy_sim.player_variance import build_player_variance_report
from fantasy_sim.simulation import FantasySimulationEngine


def main():
    sim = FantasySimulationEngine()
    sim.run_simulation()
    build_player_variance_report(sim)


if __name__ == "__main__":
    main()

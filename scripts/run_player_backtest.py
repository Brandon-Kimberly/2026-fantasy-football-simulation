#!/usr/bin/env python3
"""
Validates the model's core statistical constants (VOLATILITY_CONSTANTS, EPISTEMIC_ERROR_RATES,
SIM_CONFIG['CORRELATIONS']) directly against real historical player data, bypassing the
roster/scoring-format confounds that limit the season-level backtest. See
fantasy_sim/backtest_player.py's module docstring for the full methodology.

Usage:
    python -m scripts.run_player_backtest
"""
from fantasy_sim.backtest_player import run_full_player_level_backtest


def main():
    run_full_player_level_backtest()


if __name__ == "__main__":
    main()

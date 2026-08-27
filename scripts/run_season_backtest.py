#!/usr/bin/env python3
"""
Backtests the simulation engine against a real historical season (2025), reusing the actual
production sync/simulation code rather than a reimplementation. See
fantasy_sim/backtest_season.py's module docstring for the full methodology and its documented
v1 scope and limitations before interpreting results.

Usage:
    python -m scripts.run_season_backtest
"""
from fantasy_sim.backtest_season import run_full_backtest


def main():
    run_full_backtest()


if __name__ == "__main__":
    main()

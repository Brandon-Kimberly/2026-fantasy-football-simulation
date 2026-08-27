#!/usr/bin/env python3
"""
Runs the Monte Carlo simulation engine against the data produced by run_sync.py, and writes
forecasts, diagnostics, and charts into data/.

Usage:
    python -m scripts.run_simulation
"""
from fantasy_sim.simulation import FantasySimulationEngine


def main():
    sim = FantasySimulationEngine()
    sim.run_simulation()


if __name__ == "__main__":
    main()

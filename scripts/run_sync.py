#!/usr/bin/env python3
"""
Fetches real data from Sleeper, ESPN, the-odds-api, and Open-Meteo, and writes everything the
simulation engine needs into data/. Run this before run_simulation.py.

Usage:
    python -m scripts.run_sync [--sharp]

    --sharp   Poll for sharper (closer-to-kickoff) Vegas lines instead of the default timing.
"""
import sys

from fantasy_sim.sync import sync_all


def main():
    sharp = "--sharp" in sys.argv
    sync_all(sharp_polling=sharp)


if __name__ == "__main__":
    main()

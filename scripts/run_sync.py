#!/usr/bin/env python3
"""
Fetches real data from Sleeper, ESPN, the-odds-api, and Open-Meteo, and writes everything the
simulation engine needs into data/. Run this before run_simulation.py.

Usage:
    python -m scripts.run_sync [--sharp] [--allow-fallback]

    --sharp           Poll for sharper (closer-to-kickoff) Vegas lines instead of the
                      default timing.
    --allow-fallback  Sync even if ODDS_API_KEY is REJECTED, accepting the flat 21.5
                      fallback. Without it a rejected key stops the run before anything
                      is written.

H5: the odds key is checked BEFORE the sync touches anything. A dead key answers 401,
which reads exactly like the API being down, and on Windows a shell can hold a stale
pre-rotation value long after `setx` updated it. Only a rejected key stops -- a 5xx or a
timeout is a transient and must never block the scheduled runner, and an absent key is the
documented no-key path.
"""
import sys

from fantasy_sim.config import ODDS_API_KEY
from fantasy_sim.sync import should_stop_for_odds_key, sync_all, verify_odds_key


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    sharp = "--sharp" in argv
    allow_fallback = "--allow-fallback" in argv

    verdict, detail = verify_odds_key(ODDS_API_KEY)
    if verdict != "ok":
        print(f"[ODDS KEY: {verdict.upper()}] {detail}", file=sys.stderr)
    if should_stop_for_odds_key(verdict, allow_fallback=allow_fallback):
        print("\nSTOPPED before writing anything. Nothing on disk was changed.",
              file=sys.stderr)
        return 2

    sync_all(sharp_polling=sharp)
    return 0


if __name__ == "__main__":
    sys.exit(main())

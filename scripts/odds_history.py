#!/usr/bin/env python3
"""
How have my championship odds moved, and what landed in between? (R1)

  py -3.10 -m scripts.odds_history [--team "Quantum Ferrets"] [--json]

One row per CANONICAL prediction run -- champ%, playoff%, expected wins, the change since
the previous canonical run, and the transactions that landed in that window.

CANONICAL ONLY, and it matters. The predictions log holds scheduled runs and ad-hoc ones
alike; an ad-hoc evaluation is a what-if, not a prediction (F56/B5's provenance split), and
splicing those into a time series would invent movement the model's published view never
made. A row with no `canonical` flag is not canonical either -- the earliest rows predate
the flag.

THE MOVES ARE EVIDENCE, NOT EXPLANATION. They are what happened in the window, not what
caused the change: odds also move because rival rosters changed, because Vegas lines moved,
and because the Bayesian blend saw another week of real scores.

Reads data/logs/ only. Writes nothing.
"""
import argparse
import json

from fantasy_sim.config import MY_TEAM as DEFAULT_TEAM
from fantasy_sim.odds_history import odds_history, render_lines
from fantasy_sim.storage import DECISION_LOG_FILE
from fantasy_sim.weekly_report import real_name_overlay

PREDICTIONS = "data/logs/predictions_2026.jsonl"


def _jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--predictions", default=PREDICTIONS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    hist = odds_history(_jsonl(a.predictions), a.team, _jsonl(DECISION_LOG_FILE))
    if a.json:
        print(json.dumps(hist, indent=1, sort_keys=True))
    else:
        print()
        for line in render_lines(hist, name_of=real_name_overlay()):
            print(line)
    return hist


if __name__ == "__main__":
    main()

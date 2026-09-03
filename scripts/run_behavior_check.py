"""Behavioral-plausibility check: simulated mechanic rates vs the real 2025 league, plus
a drift check against the committed behavioral baseline.

  py -3.10 -m scripts.run_behavior_check                 # report + drift check (exit 1 on drift)
  py -3.10 -m scripts.run_behavior_check --regenerate    # accept current rates as the baseline
  py -3.10 -m scripts.run_behavior_check --scenario week06

Standalone by design, run at milestones (before a MAJOR, at release tags), not in the
suite: the real-2025 comparison intentionally reports known filed gaps (trades, churn)
every time, and a test that fails on a known gap becomes wallpaper. Only DRIFT vs the
committed baseline exits nonzero -- the rates are deterministic on the seeded golden
fixtures, so drift means engine behavior moved. Regenerate deliberately, in its own
commit, with the deltas explained (the golden-master discipline applied to rates).
"""
import argparse
import json
import os
import sys

from fantasy_sim.behavior_check import (
    measure, compare_to_baseline, baseline_path, load_baseline, render_report,
)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="week01")
    ap.add_argument("--regenerate", action="store_true",
                    help="accept the current rates as the committed baseline "
                         "(runs twice and requires identical results first)")
    args = ap.parse_args(argv)

    metrics = measure(args.scenario)

    if args.regenerate:
        second = measure(args.scenario)
        if second != metrics:
            diff = {k: (metrics[k], second[k]) for k in metrics if metrics[k] != second[k]}
            raise SystemExit(f"NOT DETERMINISTIC across two runs -- refusing to write a "
                             f"baseline on unstable rates: {diff}")
        path = baseline_path(args.scenario)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(metrics, f, indent=2, sort_keys=True)
        print(render_report(metrics, [], True))
        print(f"\nbaseline regenerated (double-run identical) -> {path}")
        return 0

    exists = os.path.exists(baseline_path(args.scenario))
    drifted = compare_to_baseline(metrics, load_baseline(args.scenario)) if exists else []
    print(render_report(metrics, drifted, exists))
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Tier-1 window watcher for GitHub Actions (2026-09-04) -- READ-ONLY, no credentials.

  py -3.10 -m scripts.windows_watch [--hours N]

Computes canonical-window state on a bare checkout: kickoffs live-fetched when no synced
schedule exists, the current week from Sleeper, and coverage from the COMMITTED
predictions log's canonical rows (data/decisions is untracked and absent on a runner;
weekly_report's logs_push step pushes the log at canonical time, so the committed log is
current exactly when coverage matters). Prints a JSON verdict and always exits 0 -- the
workflow turns actionable windows into GitHub issues (email + mobile push), never a
failing job: a red X three times a week trains itself to be ignored.

Pleasant side effect: if a local canonical run's push failed, the runner keeps nagging.
That is correct -- an unpushed record is not durable yet.
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

from fantasy_sim.freshness import read_nfl_week
from fantasy_sim.run_windows import (
    compute_windows, load_kickoffs, stamps_from_predictions_rows, watch_verdict,
)


def read_predictions_rows():
    """All rows from the newest committed predictions log (predictions_<season>.jsonl);
    empty when none exists (preseason of a first season, or a bare pre-first-push clone)."""
    paths = sorted(glob.glob(os.path.join("data", "logs", "predictions_*.jsonl")))
    if not paths:
        return []
    rows = []
    with open(paths[-1], encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=24.0,
                    help="notify horizon: an OPEN uncovered window inside this many hours is actionable")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    kicks, kick_source = load_kickoffs()
    if not kicks:
        # No schedule and ESPN unreachable: report that, quietly -- a reminder system
        # must not cry wolf about its own plumbing every two hours.
        print(json.dumps({"target_week": None, "actionable": [], "missed": [],
                          "note": "no kickoff data (schedule absent, ESPN unreachable)"}))
        return 0

    state_week = read_nfl_week()
    rows = read_predictions_rows()
    target = compute_windows(now, kicks, [], state_week=state_week)["target_week"]
    stamps = stamps_from_predictions_rows(rows, target) if target else []
    result = compute_windows(now, kicks, stamps, state_week=state_week)
    verdict = watch_verdict(result, now, horizon_hours=args.hours)
    verdict["kickoff_source"] = kick_source
    verdict["now"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    print(json.dumps(verdict, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

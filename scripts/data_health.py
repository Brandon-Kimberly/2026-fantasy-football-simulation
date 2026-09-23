#!/usr/bin/env python3
"""
End-to-end data health: every source the model consumes, checked independently against
what is on disk (fantasy_sim.data_health).

  py -3.10 -m scripts.data_health [--season 2026] [--week N]

Exits 0 on PASS, 2 on DEGRADED, 1 on FAIL -- the same convention as
scripts.check_freshness.

WHICH TOOL TO RUN. `check_freshness` answers "did the last sync succeed, and what did
each source deliver during it" (B6/F57's sources block). This answers "is the data I am
about to make decisions on healthy", which needs different evidence: ESPN coverage per
week ACROSS the season rather than in one sync, F54's posterior reach, and Vegas
fallbacks detected by value rather than by an error being reported. Neither replaces the
other, and this one does not restate sync status.

Reads data/current/ and data/logs/ only; writes nothing.
"""
import argparse
import json
import os
import sys

from fantasy_sim.data_health import DEGRADED, FAIL, PASS, report
from fantasy_sim.storage import (
    BASELINES_FILE, LEAGUE_STATE_FILE, NFL_SCHEDULE_FILE, PROJECTION_LOG_FILE,
    VEGAS_FILE, WEEKLY_ACTUALS_FILE, load_json,
)

EXIT = {PASS: 0, DEGRADED: 2, FAIL: 1}


def _rows(path=PROJECTION_LOG_FILE):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", default="2026")
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    week = args.week or (load_json(LEAGUE_STATE_FILE) or {}).get("current_week", 1)
    r = report(
        baselines=load_json(BASELINES_FILE) or {},
        projection_rows=_rows(),
        weekly_actuals=load_json(WEEKLY_ACTUALS_FILE) or {},
        vegas=load_json(VEGAS_FILE) or {},
        nfl_schedule=load_json(NFL_SCHEDULE_FILE) or {},
        season=args.season, week=week,
    )
    if args.json:
        print(json.dumps(r, indent=1, sort_keys=True, default=str))
        return EXIT[r["verdict"]]

    print("=" * 78)
    print(f"DATA HEALTH -- season {args.season}, week {week}     OVERALL: {r['verdict']}")
    print("=" * 78)
    for c in r["checks"]:
        print(f"  [{c['verdict']:8s}] {c['name']:34s} {c['detail']}")
    print("\n  Sync status is a different question: py -3.10 -m scripts.check_freshness")
    sys.exit(EXIT[r["verdict"]])


if __name__ == "__main__":
    main()

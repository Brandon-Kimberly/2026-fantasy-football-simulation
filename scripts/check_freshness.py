#!/usr/bin/env python3
"""
One glance: has sync run this week, and did it succeed? (fantasy_sim.freshness)

  py -3.10 -m scripts.check_freshness [--offline]

Prints OK / DEGRADED (sync tolerated failures -- listed) / STALE (reasons listed) and exits
0 / 2 / 1. Reads data/current/sync_manifest.json (written last by sync_all, so it exists only
for a sync that completed), the sync outputs' mtimes, vegas_totals._meta.week, the current
week's simulation export, and -- unless --offline -- Sleeper's current NFL week.
"""
import argparse
import sys

from fantasy_sim.freshness import check, EXIT_CODES, OK, DEGRADED


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="skip the Sleeper week check")
    args = ap.parse_args(argv)
    status, reasons, details = check(offline=args.offline)
    m = details["manifest"] or {}
    head = f"{status}"
    if m:
        head += (f" -- sync for week {m.get('current_week')} completed {m.get('finished_at')} UTC"
                 f" ({len(m.get('degraded') or [])} tolerated failure(s), {m.get('notices_count', 0)} routine notices)")
    if details["nfl_week"] is not None:
        head += f"; Sleeper week {details['nfl_week']}"
    print(head)
    label = "degraded:" if status == DEGRADED else ("notes:" if status == OK else "reasons:")
    for r in reasons:
        print(f"  {label} {r}" if r is reasons[0] else f"  {' ' * len(label)} {r}")
    sys.exit(EXIT_CODES[status])


if __name__ == "__main__":
    main()

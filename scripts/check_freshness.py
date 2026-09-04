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

from fantasy_sim.freshness import check, read_logs_git_state, EXIT_CODES, OK, DEGRADED


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

    # The log-push discipline, made mechanical (2026-09-04): the logs under data/logs are
    # the only unrecoverable season data, and R1 makes "appended locally, never pushed" a
    # real loss mode. Canonical runs push automatically (weekly_report); this line is the
    # backstop that catches everything else. Deliberately not part of the exit code: log
    # durability is orthogonal to data freshness.
    uncommitted, ahead = read_logs_git_state()
    if uncommitted is None:
        print("  logs: push state unknown (git unavailable)")
    elif uncommitted:
        names = ", ".join(u.rsplit("/", 1)[-1] for u in uncommitted)
        print(f"  logs: ACTION -- {len(uncommitted)} uncommitted log file(s): {names}")
        print('        run: git add -- data/logs && git commit -m "Logs: manual capture" -- data/logs && git push')
    elif ahead is None:
        print("  logs: committed; push state unknown (no upstream)")
    elif ahead:
        print(f"  logs: ACTION -- {ahead} unpushed commit(s) touching data/logs -- run: git push")
    else:
        print("  logs: committed and pushed")
    sys.exit(EXIT_CODES[status])


if __name__ == "__main__":
    main()

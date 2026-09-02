#!/usr/bin/env python3
"""
Weekly orchestrator: sync -> simulation -> roster grades -> lineup -> matchup -> waivers, one
consolidated Markdown digest (fantasy_sim.weekly_report). Team from config.MY_TEAM, week from
the sync -- no arguments needed for the common case.

  py -3.10 -m scripts.weekly_report [--full] [--skip-sync] [--sims 5000] [--evaluate N] [--team T]

  --full       also run the trade-target finder (--evaluate N passes through to tool 2)
  --skip-sync  use the data on disk; the digest opens with the freshness verdict, and STALE
               data stops the run rather than being reported as current

FAILS LOUD: the first step that raises -- or a gate that finds the previous step did not leave
its data -- stops the chain, the digest carries a FAILED banner, and the exit code is 1.
Nothing downstream ever runs on stale or partial data.
"""
import argparse
import sys

from fantasy_sim.config import MY_TEAM
from fantasy_sim.weekly_report import run_weekly_report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--skip-sync", action="store_true")
    ap.add_argument("--sims", type=int, default=5000, help="matchup joint-sample size")
    ap.add_argument("--evaluate", type=int, default=0, help="with --full: tool-2 evaluations of the top N trade packages")
    ap.add_argument("--team", default=MY_TEAM)
    ap.add_argument("--embed", action="store_true", help="inline the charts as data URIs (portable, ~15-20 MB)")
    ap.add_argument("--canonical", action="store_true", help="a scheduled run (Tue post-waivers / Sun pre-lock): artifacts to week_NN/ instead of week_NN/archive/")
    args = ap.parse_args(argv)

    report, md, path, html_path = run_weekly_report(args.team, full=args.full, skip_sync=args.skip_sync,
                                                    sims=args.sims, evaluate=args.evaluate, embed=args.embed, canonical=args.canonical)
    print("\n" + md)
    print(f"\n[{report['status']}] digest -> {path}" + (f"\n[{report['status']}] html   -> {html_path}" if html_path else ""))
    if report["status"] != "OK":
        print(f"[FAILED] step `{report['failed_step']}`: {report['error']}", file=sys.stderr)
        sys.exit(1)
    return report


if __name__ == "__main__":
    main()

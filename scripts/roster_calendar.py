#!/usr/bin/env python3
"""
Which weeks am I short, and who do I cut when the IR'd man comes back?

  py -3.10 -m scripts.roster_calendar [--team "Quantum Ferrets"] [--through 14]

Two sections (fantasy_sim.roster_calendar), both pure roster arithmetic:

  CALENDAR  per remaining week: who is on bye, which required slots are still fillable,
            and which bench piece steps into each bye-week starter's slot. A slot no one
            on the roster can fill is a HOLE -- plan a claim BEFORE that week.
  CRUNCH    for each IR'd player: the active count when he returns, whether that breaks
            the 19-man limit, and which bench pieces are droppable. A bench piece is
            droppable only when it covers NO starter's bye in any remaining week; one
            that does is load-bearing even though it never starts today.

WHO COUNTS AS OUT is `SIM_CONFIG['INITIAL_ABSENCE_STATUSES']`, the engine's own set.
**Questionable is deliberately not in it (F51)**: the Sleeper projection a baseline derives
from already reflects expected usage, so a Questionable starter is not a hole and treating
him as one would send you to spend FAAB on a gap that is not there.

The return WEEK is unknown -- Sleeper publishes a designation, not a date -- so the crunch
is reported conditional on a return rather than dated.

Reads data/current/ only; writes one JSON record under data/decisions/.
"""
import argparse
import datetime as _dt

from fantasy_sim.config import MY_TEAM as DEFAULT_TEAM
from fantasy_sim.roster_calendar import calendar, crunch, render_lines
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_week_path, save_json
from fantasy_sim.weekly_report import real_name_overlay


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--through", type=int, default=None,
                    help="last week to plan through (default: the regular season)")
    ap.add_argument("--canonical", action="store_true",
                    help="a scheduled/deliberate run: write to week_NN/ instead of week_NN/archive/")
    a = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    cal = calendar(engine, a.team, through=a.through)
    cr = crunch(engine, a.team, through=a.through)
    print()
    for line in render_lines(cal, cr, name_of=real_name_overlay()):
        print(line)

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    week = engine.current_week
    out = decisions_week_path(week, f"roster_calendar_{stamp}_week{week}.json",
                              canonical=a.canonical)
    save_json(out, {"timestamp_utc": stamp, "tool": "roster_calendar",
                    "calendar": cal, "crunch": cr})
    print(f"\n  logged -> {out}")
    return {"calendar": cal, "crunch": cr}


if __name__ == "__main__":
    main()

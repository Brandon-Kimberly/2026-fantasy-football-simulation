#!/usr/bin/env python3
"""
What should I be watching this week? -- the brief that used to be assembled by hand.

  py -3.10 -m scripts.matchup_watch [--team "Quantum Ferrets"] [--week N] [--opponent T]

Five blocks, all mechanical (fantasy_sim.matchup_watch):

  games                both lineups grouped by the real NFL game they play in, with that
                       game's implied total, each side's spread, wind and precipitation
  stacks               a game holding three or more starters from one side -- that side's
                       week rides on one football game
  designations         every flagged starter on EITHER roster (B4: checking only your own
                       is how you get surprised at 12:58)
  shared games         where the two fantasy rosters meet: the same NFL team (correlated --
                       you rise and fall together) or opposite sides (hedged)
  their losing script  the single game carrying the largest share of the opponent's total

NOTHING HERE IS A NEW NUMBER. Expectations come from `decisions.week_expectation` and lines
from the engine's own environment; this groups and counts. Weather is CONTEXT and is not in
any projection (F55 open) -- the Vegas total already prices the forecast, so reading the
wind as a further discount double-counts it.

Lineups: the engine's deterministic max-expectation assignment for both sides, no sampling.
`scripts.matchup_lineup` prints the same brief from ITS solved lineups, which is the one to
read when you want the brief for the lineup that tool recommends.

Reads data/current/ only; writes one JSON record under data/decisions/.
"""
import argparse
import datetime as _dt

from fantasy_sim.config import MY_TEAM as DEFAULT_TEAM
from fantasy_sim.matchup_watch import render_lines, starters_by_expectation, watch
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_week_path, save_json
from fantasy_sim.weekly_report import real_name_overlay


def resolve_opponent(engine, team, week):
    pairs = engine.league_schedule[week - 1] if week - 1 < len(engine.league_schedule) else []
    for a, b in pairs:
        if team in (a, b):
            return b if a == team else a
    raise SystemExit(f"{team} has no scheduled opponent in week {week}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--opponent", default=None)
    ap.add_argument("--stack-min", type=int, default=3,
                    help="starters from one side in one game before it counts as a stack")
    ap.add_argument("--canonical", action="store_true",
                    help="a scheduled/deliberate run: write to week_NN/ instead of week_NN/archive/")
    a = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    week = a.week or engine.current_week
    opponent = a.opponent or resolve_opponent(engine, a.team, week)
    w = watch(engine, a.team, opponent, week,
              starters_by_expectation(engine, a.team, week),
              starters_by_expectation(engine, opponent, week),
              stack_min=a.stack_min)
    print()
    for line in render_lines(w, name_of=real_name_overlay()):
        print(line)

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = decisions_week_path(week, f"matchup_watch_{stamp}_week{week}.json",
                              canonical=a.canonical)
    save_json(out, {"timestamp_utc": stamp, "tool": "matchup_watch", **w})
    print(f"\n  logged -> {out}")
    return w


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Opponent-aware lineup construction (fantasy_sim.decisions.matchup_lineups): this week's real
opponent from the schedule, both rosters (and the other six, for the median-beat decision)
sampled JOINTLY through the engine's copula -- including same-NFL-team correlation across the
two rosters, which the engine itself omits (AUDIT_PLAN.md F16; --no-cross reproduces the
engine) -- and four lineup constructions evaluated on that one sample:

  max_mean  the engine's own rule            safe   expectation - k*sd
  stack     expectation + k*sd + QB-stack bonus   p_max  local search maximising P(beat opponent)

  py -3.10 -m scripts.matchup_lineup [--team "Legion of Coom"] [--week N] [--opponent T]
        [--sims 5000] [--seed S] [--k 0.5] [--no-cross] [--opponent-lineup "A, B, ..."]

Reads data/current/ only; writes one JSON record under data/decisions/.
"""
import argparse
import datetime as _dt

from fantasy_sim.decisions import matchup_lineups
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_path, save_json

from fantasy_sim.config import MY_TEAM as DEFAULT_TEAM


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--opponent", default=None)
    ap.add_argument("--sims", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--k", type=float, default=0.5)
    ap.add_argument("--no-cross", action="store_true")
    ap.add_argument("--opponent-lineup", default=None, help="comma-separated names; default = his max-expectation lineup")
    args = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    week = args.week or engine.current_week
    opp_lineup = [x.strip() for x in args.opponent_lineup.split(",")] if args.opponent_lineup else None
    r = matchup_lineups(engine, args.team, week, opponent=args.opponent, sims=args.sims, seed=args.seed,
                        cross=not args.no_cross, k=args.k, opponent_lineup=opp_lineup)

    print(f"\n{args.team} vs {r['opponent']} -- week {week}   n={r['n']}   "
          f"{'favoured' if r['favoured_by_max_mean'] else 'underdog'} on the engine's lineup   "
          f"[{'cross-roster copula' if r['cross'] else 'per-roster copula (engine)'}]")
    print(f"  {'construction':12s} {'mean':>6s} {'sd':>5s} {'P(beat opp)':>11s} {'+-':>4s} {'P(>=median)':>11s} {'margin':>7s} {'m.sd':>5s}")
    for key in r["ranking_by_p_beat_opponent"]:
        c = r["constructions"][key]
        print(f"  {key:12s} {c['mean']:6.1f} {c['sd']:5.1f} {100 * c['p_beat_opponent']:10.1f}% {100 * c['se']:4.1f} "
              f"{100 * c['p_beat_median']:10.1f}% {c['margin_mean']:+7.1f} {c['margin_sd']:5.1f}")
    lineups = {tuple(sorted(x["name"] for x in c["lineup"])) for c in r["constructions"].values()}
    if len(lineups) == 1:
        print("  NOTE: all four constructions pick the same lineup -- this roster offers no variance lever this "
              "week (every bench alternative is dominated at its slot under expectation - k*sd, + k*sd, and "
              "P(beat opponent) alike). The asymmetry only matters when a roster has a real choice.")
    best = r["ranking_by_p_beat_opponent"][0]
    print(f"\n  best by P(beat opponent): {best}")
    for row in r["constructions"][best]["lineup"]:
        print(f"    {row['slot']:5s} {row['name'][:26]:26s} {row['nfl_team'] or '-':4s} exp {row['expected']:5.1f}  sd {row['sd']:4.1f}")
    diff = [(a, b) for a, b in zip(r["constructions"]["max_mean"]["lineup"], r["constructions"][best]["lineup"]) if a["name"] != b["name"]]
    if best != "max_mean" and diff:
        print("  changes vs max_mean: " + "; ".join(f"{a['slot']}: {a['name']} -> {b['name']}" for a, b in diff))
    print(f"  opponent lineup ({'assumed' if r['opponent_lineup_assumed'] else 'supplied'}): "
          + ", ".join(f"{x['name']} ({x['expected']:.1f})" for x in r["opponent_lineup"]))
    print(f"  {r['note']}")

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = decisions_path(f"matchup_{stamp}_week{week}.json")
    save_json(out, {"timestamp_utc": stamp, "tool": "matchup_lineup", **r})
    print(f"  logged -> {out}")
    return r


if __name__ == "__main__":
    main()

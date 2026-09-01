#!/usr/bin/env python3
"""
Waiver / FAAB targets: cross-references a team's real roster gaps (a starting slot no healthy,
non-bye rostered player can fill, plus the weakest incumbent per slot) against the free-agent
pool in data/current/player_baselines.json, ranked by value over replacement
(fantasy_sim.decisions.rank_waiver_targets).

  py -3.10 -m scripts.waiver_targets [--team "Legion of Coom"] [--week N] [--top 15]
                                     [--positions RB,WR] [--sims 2000] [--seed S]

Each target: tier (positional_tiers), VORP, a light-sampled week distribution, a SUGGESTED BID
(an unverified value heuristic -- no real waiver outcomes exist yet to calibrate it; see
suggest_bid) beside the engine's behavioural bid model, and for upgrades P(beats the
incumbent) -- an independent-draw comparison, caveated. Reads data/current/ only; writes one
JSON record under data/decisions/.
"""
import argparse
import datetime as _dt

from fantasy_sim.decisions import rank_waiver_targets
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_path, save_json

from fantasy_sim.config import MY_TEAM as DEFAULT_TEAM


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--positions", default=None, help="comma-separated, e.g. RB,WR")
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    week = args.week or engine.current_week
    positions = {p.strip().upper() for p in args.positions.split(",")} if args.positions else None
    r = rank_waiver_targets(engine, args.team, week, top_n=args.top, sims=args.sims, seed=args.seed,
                            positions=positions)

    print(f"\n{args.team} -- week {week}   FAAB remaining {r['remaining_faab']:.0f} (league avg {r['league_avg_faab']:.0f})")
    print(f"  holes this week: {r['holes'] or 'none'}   next week: {r['holes_next_week'] or 'none'}")
    print(f"  {'#':>2s} {'player':26s} {'pos':4s} {'tier':>4s} {'season':>6s} {'VORP':>5s} {'wk mean':>7s} {'p10':>5s} {'p50':>5s} {'p90':>5s} "
          f"{'zero':>5s} {'fills':8s} {'bid':>4s} {'model':>5s}  incumbent / P(beats)")
    for i, t in enumerate(r["targets"], 1):
        w = t["week"]
        inc = ""
        if t["p_beats_incumbent"]:
            inc = f"{t['incumbent']} / {100 * t['p_beats_incumbent']['p']:.0f}%*"
        print(f"  {i:2d} {t['name'][:26]:26s} {t['pos']:4s} {str(t['tier'] or '-'):>4s} {t['mean']:6.1f} {t['vorp']:+5.1f} {w['mean']:7.1f} "
              f"{w['p10']:5.1f} {w['p50']:5.1f} {w['p90']:5.1f} {100 * w['p_zero']:4.0f}% {t['fills']:8s} "
              f"{t['bid']['suggested']:4d} {t['bid']['typical_manager_model']:5.1f}  {inc}")
    print("  season = baseline (season-level) mean, the basis of VORP; wk mean/p10/p50/p90 = this week's environment-adjusted draws.")
    print("  bid = UNVERIFIED value heuristic (suggest_bid); model = what the engine simulates a typical manager paying.")
    print(f"  * P(beats incumbent): {r['caveat']}")

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = decisions_path(f"waivers_{stamp}_week{week}.json")
    save_json(out, {"timestamp_utc": stamp, "tool": "waiver_targets", **r})
    print(f"  logged -> {out}")
    return r


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Market sweep: every starting slot against the best free agent available for it, on the
ENGINE's own corrected values (fantasy_sim.market.market_sweep).

  py -3.10 -m scripts.market_sweep [--team "Quantum Ferrets"] [--week N] [--min-gain 0.5]

Three columns that are easy to conflate and are deliberately separate:
  * the SLOT-LOSING STARTER -- the weakest man the engine's optimal assignment actually
    starts at that position, FLEX included (a third WR starting at FLEX is a WR starter);
  * the BEST AVAILABLE free agent at that position, and the gain over that man;
  * the DROP -- the WORST man you own there, who is usually someone else entirely.

Everything is SEASON mean points per week, the unit replacement_levels is expressed in.
A week-specific start/sit question is scripts.optimize_lineup, which works in week
expectations; the two units must not be compared.

No hand-blending: engine.baselines[name]['mean'] is the corrected post-F54 number for
every player. Reads data/current/ only and writes nothing.
"""
import argparse

from fantasy_sim.config import MY_TEAM as DEFAULT_TEAM
from fantasy_sim.market import market_sweep
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.weekly_report import real_name_overlay


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--min-gain", type=float, default=0.5,
                    help="only list upgrades worth at least this much per week")
    args = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    week = args.week or engine.current_week
    r = market_sweep(engine, args.team, week)

    ov = real_name_overlay()
    show = ov.get(args.team, args.team)
    rep = r["replacement_levels"]
    print(f"\n{show} -- week {week} market sweep   (season mean pts/wk)")
    print("  replacement: " + "  ".join(f"{k} {v:.2f}" for k, v in sorted(rep.items())))
    print(f"\n  {'pos':4s} {'slot-losing starter':24s} {'mean':>6s} | "
          f"{'best available':24s} {'mean':>6s} {'gain':>6s} | {'drop (worst owned)':22s} {'mean':>6s}")
    for row in r["rows"]:
        loser = (f"{row['slot_loser'][:18]} ({row['n_starting']}/{row['n_owned']})"
                 if row["slot_loser"] else f"-- EMPTY ({row['n_owned']} owned) --")
        best = row["best_available"] or "-- none available --"
        flag = "  <-- UPGRADE" if row["best_available"] and row["gain"] >= args.min_gain else ""
        print(f"  {row['pos']:4s} {loser:24s} {row['slot_loser_mean']:6.2f} | "
              f"{best[:24]:24s} {row['best_available_mean']:6.2f} {row['gain']:+6.2f} | "
              f"{(row['drop'] or '-')[:22]:22s} {row['drop_mean']:6.2f}{flag}")

    ups = [u for u in r["upgrades"] if u["gain"] >= args.min_gain]
    print(f"\n  actions worth >= {args.min_gain:.2f}/wk:")
    for u in ups:
        same = " (the same man -- this position is one deep)" if u["drop"] == u["slot_loser"] else ""
        print(f"    {u['pos']:3s} +{u['gain']:5.2f}   add {(u['best_available'] or '')[:22]:22s} "
              f"drop {(u['drop'] or '(none)')[:22]:22s}{same}")
    if not ups:
        print("    -- none --")

    print("\n  dead weight (below replacement):")
    for d in r["dead_weight"]:
        print(f"    {d['pos']:3s} {d['name'][:24]:24s} {d['mean']:6.2f}  vorp {d['vorp']:+6.2f}"
              + ("   STARTING" if d["starting"] else ""))
    if not r["dead_weight"]:
        print("    -- none --")
    print(f"\n  {r['note']}")
    return r


if __name__ == "__main__":
    main()

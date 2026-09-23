#!/usr/bin/env python3
"""
Weekly lineup optimizer: the engine's own lineup rule applied to the real roster for one NFL
week (fantasy_sim.decisions.optimize_lineup) -- optimal assignment on this week's pre-game
expectations (baseline mean x environment x game script; bye / out-now = 0), with each starter's
sampled p10/p50/p90 and the margin over the best bench alternative eligible for the slot.

  py -3.10 -m scripts.optimize_lineup [--team "Quantum Ferrets"] [--week N] [--sims 1000] [--seed S]

No draw enters the choice (lineups are chosen on expectation, never on realised scores). Reads
data/current/ only; writes one JSON record under data/decisions/.
"""
import argparse
import datetime as _dt

from fantasy_sim.decisions import optimize_lineup
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_week_path, save_json

from fantasy_sim.config import MY_TEAM as DEFAULT_TEAM


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--canonical", action="store_true", help="a scheduled/deliberate run: write to week_NN/ instead of week_NN/archive/")
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    week = args.week or engine.current_week
    r = optimize_lineup(engine, args.team, week, sims=args.sims, seed=args.seed)

    print(f"\n{args.team} -- week {week} optimal lineup   expected total {r['expected_total']:.1f}"
          + (f"   UNFILLED: {r['unfilled']}" if r['unfilled'] else ""))
    print(f"  {'slot':5s} {'player':26s} {'pos':4s} {'flag':12s} {'exp':>5s} {'p10':>5s} {'p50':>5s} {'p90':>5s} {'zero':>5s} {'margin':>7s}  alternative")
    for row in r["lineup"]:
        print(f"  {row['slot']:5s} {row['name'][:26]:26s} {row['pos']:4s} {row.get('flag', '')[:12]:12s} "
              f"{row['expected']:5.1f} {row['p10']:5.1f} "
              f"{row['p50']:5.1f} {row['p90']:5.1f} {100 * row['p_zero']:4.0f}% "
              f"{(format(row['margin'], '+7.1f') if row['alternative'] else '      -')}  {row['alternative'] or '(no bench alternative)'}")
    print("  bench:")
    for b in r["bench"]:
        print(f"        {b['name'][:26]:26s} {b['pos']:4s} {b.get('flag', '')[:12]:12s} {b['expected']:5.1f}  {b['reason']}")

    # B4: the starters carrying in-week risk the model does NOT price, each with the best
    # available fallback. Printed after the table because it is a decision, not a number.
    q = r.get("questionable_starters") or []
    if q:
        print("")
        print(f"  QUESTIONABLE STARTERS ({len(q)}) -- designation is NOT priced into any "
              f"number above:")
        for x in q:
            if x["fallback"]:
                print(f"        {x['name'][:26]:26s} {x['slot']:5s} {x['expected']:5.1f}   "
                      f"fallback {x['fallback'][:24]:24s} {x['fallback_expected']:5.1f} "
                      f"(give up {x['give_up']:.1f})")
            else:
                print(f"        {x['name'][:26]:26s} {x['slot']:5s} {x['expected']:5.1f}   "
                      f"NO eligible bench fallback")
        print("        The projection already reflects expected usage, so this is not a "
              "second discount to apply --")
        print("        it is the risk you carry by starting him. Check the Saturday "
              "designations before kickoff.")
    print(f"  {r['note']}")

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = decisions_week_path(week, f"lineup_{stamp}_week{week}.json", canonical=args.canonical)
    save_json(out, {"timestamp_utc": stamp, "tool": "optimize_lineup", **r})
    print(f"  logged -> {out}")
    return r


if __name__ == "__main__":
    main()

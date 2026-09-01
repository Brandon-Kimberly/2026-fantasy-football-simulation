#!/usr/bin/env python3
"""
Trade-target finder (fantasy_sim.decisions.find_trade_targets): scans the other seven rosters
with F2's offer constructor -- BUY: their buried bench players who would start at my weakest
fillable slot, with my cheapest give-back that still upgrades one of their starters; SELL: what
each opponent would want from my bench. Gains are the engine's own acceptance rule; the seller
flag comes from the latest season export's Playoff_Pct (never from MANAGER_PROFILES, which is
shown as "modelled willingness" only).

  py -3.10 -m scripts.find_trades [--team "Legion of Coom"] [--week N] [--top 10]
        [--seller-threshold 35] [--evaluate N] [--batches 3] [--sims 1000]

--evaluate N runs tool 2 (paired simulations, real Champ/Playoff deltas) on the top N buy
packages, ~2 min each at the defaults. Reads data/current/ and the latest week's season export;
writes one JSON record under data/decisions/.
"""
import argparse
import datetime as _dt
import os

from fantasy_sim.decisions import find_trade_targets
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_path, save_json, load_json, syndicate_comprehensive_matrix_path

DEFAULT_TEAM = "Legion of Coom"


def _outcomes(week):
    path = syndicate_comprehensive_matrix_path(week)
    if not os.path.exists(path):
        return None
    rows = load_json(path).get("season_outcomes", [])
    return {r["Team"]: r for r in rows} or None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--seller-threshold", type=float, default=35.0)
    ap.add_argument("--evaluate", type=int, default=0)
    ap.add_argument("--batches", type=int, default=3)
    ap.add_argument("--sims", type=int, default=1000)
    args = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    week = args.week or engine.current_week
    outcomes = _outcomes(week)
    r = find_trade_targets(engine, args.team, outcomes=outcomes, week=week, seller_threshold=args.seller_threshold,
                           top_n=args.top, evaluate_top=args.evaluate, batches=args.batches, sims=args.sims)

    print(f"\n{args.team} -- week {week} trade targets   ({r['contention_note']})")
    if outcomes and all(v["Playoff_Pct"] >= args.seller_threshold for v in outcomes.values()):
        print(f"  pre-season note: every team is above {args.seller_threshold:.0f}% playoff odds -- the 'far from "
              "contention' seller signal is weak until the season sorts the league.")
    print("\n  BUY -- their buried player who starts for me:")
    print(f"  {'#':>2s} {'from':16s} {'target':24s} {'mean':>5s} {'behind':22s} {'slot':5s} {'I give':34s} {'my +':>6s} {'their +':>7s} {'ok':>3s} {'PO%':>5s} {'sell?':>5s} {'will':>4s}")
    for i, b in enumerate(r["buy"], 1):
        print(f"  {i:2d} {b['with'][:16]:16s} {b['target'][:24]:24s} {b['target_mean']:5.1f} {str(b['buried_behind'] or '-')[:22]:22s} "
              f"{str(b['fills_my_slot'] or '-'):5s} {', '.join(b['i_give'])[:34]:34s} {b['my_gain']:+6.1f} {b['their_gain']:+7.1f} "
              f"{'yes' if b['acceptable'] else 'no':>3s} {(format(b['their_playoff_pct'], '5.1f') if b['their_playoff_pct'] is not None else '    -')} "
              f"{('yes' if b['seller'] else 'no') if b['seller'] is not None else '-':>5s} {b['willingness'] if b['willingness'] is not None else '-':>4}")
        if "evaluation" in b:
            ev = b["evaluation"]["teams"]
            me, them = ev.get(args.team, {}), ev.get(b["with"], {})
            if me:
                print(f"       tool 2 ({b['evaluation']['n_sims']} paired seasons): me Champ {me['champ_pct']['delta']:+.2f}+-{me['champ_pct']['se']:.2f} "
                      f"Playoff {me['playoff_pct']['delta']:+.2f}+-{me['playoff_pct']['se']:.2f} | them Champ {them['champ_pct']['delta']:+.2f} "
                      f"Playoff {them['playoff_pct']['delta']:+.2f}")
    print("\n  SELL -- my surplus an opponent would want:")
    print(f"  {'#':>2s} {'buyer':16s} {'they want':34s} {'they give':34s} {'my +':>6s} {'their +':>7s} {'ok':>3s} {'PO%':>5s} {'will':>4s}")
    for i, s_ in enumerate(r["sell"], 1):
        print(f"  {i:2d} {s_['buyer'][:16]:16s} {', '.join(s_['they_want'])[:34]:34s} {', '.join(s_['they_give'])[:34]:34s} "
              f"{s_['my_gain']:+6.1f} {s_['their_gain']:+7.1f} {'yes' if s_['acceptable'] else 'no':>3s} "
              f"{(format(s_['their_playoff_pct'], '5.1f') if s_['their_playoff_pct'] is not None else '    -')} {s_['willingness'] if s_['willingness'] is not None else '-':>4}")
    print(f"  {r['note']}")

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = decisions_path(f"trade_targets_{stamp}_week{week}.json")
    save_json(out, {"timestamp_utc": stamp, "tool": "find_trades", **r})
    print(f"  logged -> {out}")
    return r


if __name__ == "__main__":
    main()

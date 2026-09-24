#!/usr/bin/env python3
"""
Trade-target finder (fantasy_sim.decisions.find_trade_targets): scans the other seven rosters
with F2's offer constructor -- BUY: their buried bench players who would start at my weakest
fillable slot, with my cheapest give-back that still upgrades one of their starters; SELL: what
each opponent would want from my bench. Gains are the engine's own acceptance rule; the seller
flag comes from the latest season export's Playoff_Pct (never from MANAGER_PROFILES, which is
shown as "modelled willingness" only).

  py -3.10 -m scripts.find_trades [--team "Quantum Ferrets"] [--week N] [--top 10]
        [--seller-threshold 35] [--evaluate N] [--batches 3] [--sims 1000]

--evaluate N runs tool 2 (paired simulations, real Champ/Playoff deltas) on the top N buy
packages, ~2 min each at the defaults. Reads data/current/ and the latest week's season export;
writes one JSON record under data/decisions/.
"""
import argparse
import datetime as _dt
import os

from fantasy_sim.decisions import find_trade_targets, screen_sim_disagreement, evaluate_trade
from fantasy_sim.swaps import describe, exhaustive_swaps
from fantasy_sim.pending import committed_players, note as pending_note
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_week_path, save_json, load_json, syndicate_comprehensive_matrix_path

from fantasy_sim.config import MY_TEAM as DEFAULT_TEAM


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
    ap.add_argument("--canonical", action="store_true", help="a scheduled/deliberate run: write to week_NN/ instead of week_NN/archive/")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--seller-threshold", type=float, default=35.0)
    ap.add_argument("--evaluate", type=int, default=0)
    ap.add_argument("--batches", type=int, default=3)
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--exhaustive", action="store_true",
                    help="B15: scan EVERY 1-for-1 and (bounded) 2-for-2 across the league, "
                         "not just their bench player who fills my weakest slot")
    ap.add_argument("--include-pending", action="store_true",
                    help="consider players already committed to a PENDING trade. Off by "
                         "default: the finder ranked one first on 2026-09-23 (T3). Pending "
                         "is not certain, so this flag exists -- a vetoed or withdrawn "
                         "trade must not leave the finder blind to a player")
    ap.add_argument("--require-mutual", action="store_true",
                    help="with --exhaustive: keep only swaps the SCREEN thinks they also "
                         "gain from. Off by default -- B2: that number is the least "
                         "reliable one here and can discard a deal the sim would like")
    args = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    week = args.week or engine.current_week
    outcomes = _outcomes(week)
    # T3: advisory only. Nothing about the engine, the rosters or any simulation changes.
    skip = set() if args.include_pending else committed_players(engine)
    if skip:
        print("  " + pending_note(skip))
    if args.exhaustive:
        # B15: the need-driven finder cannot see "their starter I could displace with a
        # piece they need more", which is where three of week 3's real deals lived.
        swaps = exhaustive_swaps(engine, args.team, week=week, top_n=args.top,
                                 require_mutual=args.require_mutual, exclude=skip)
        print(f"\n{args.team} -- week {week} EXHAUSTIVE swap scan")
        print(f"  {'#':>2s} {'with':18s} {'I give':34s} {'I get':34s} {'my scrn':>8s} {'thr scrn':>9s}")
        for i, sw in enumerate(swaps, 1):
            print(f"  {i:2d} {sw['with'][:18]:18s} {', '.join(sw['i_give'])[:34]:34s} "
                  f"{', '.join(sw['i_get'])[:34]:34s} {sw['my_screen_gain']:+8.2f} "
                  f"{sw['their_screen_gain']:+9.2f}")
        # B2: spend the sims on MY best, and say what is unmeasured.
        for sw in swaps[:args.evaluate]:
            ev = evaluate_trade(engine, args.team, sw["i_give"], sw["with"], sw["i_get"],
                                batches=args.batches, sims=args.sims)
            mine = (ev.get("teams") or {}).get(args.team) or {}
            delta = (mine.get("champ_pct") or {}).get("delta")
            sw["simulated"], sw["sim_champ_delta"] = True, (float(delta) if delta is not None else None)
            sw["disagreement"] = screen_sim_disagreement(sw["my_screen_gain"], sw["sim_champ_delta"])
            print(f"\n  SIM  {', '.join(sw['i_give'])} -> {', '.join(sw['i_get'])} with {sw['with']}")
            print(f"       me Champ {mine['champ_pct']['delta']:+.2f}+-{mine['champ_pct']['se']:.2f} "
                  f"Playoff {mine['playoff_pct']['delta']:+.2f}+-{mine['playoff_pct']['se']:.2f}")
            if sw["disagreement"]["disagree"]:
                print(f"       {sw['disagreement']['note']}")
        if not args.evaluate:
            print("  ALL UNSIMULATED -- screen numbers only; add --evaluate N")
        if not args.require_mutual and swaps and swaps[0]["their_screen_gain"] < 0:
            # Ranked by MY gain alone (B2), the top of the list is "give me your two best
            # players" -- correct as an answer to "what would help me most", useless as a
            # list of proposals. Say so instead of letting it read as advice.
            print("  NOTE: ranked by MY gain only, so the leaders here are offers nobody "
                  "would accept.")
            print("        --require-mutual for a proposable list (its cost is above).")
        print(f"\n  {describe(swaps)}")
        return {"exhaustive": swaps}

    r = find_trade_targets(engine, args.team, outcomes=outcomes, week=week, seller_threshold=args.seller_threshold,
                           top_n=args.top, evaluate_top=args.evaluate, batches=args.batches,
                           sims=args.sims, exclude=skip)

    print(f"\n{args.team} -- week {week} trade targets   ({r['contention_note']})")
    if outcomes and all(v["Playoff_Pct"] >= args.seller_threshold for v in outcomes.values()):
        print(f"  pre-season note: every team is above {args.seller_threshold:.0f}% playoff odds -- the 'far from "
              "contention' seller signal is weak until the season sorts the league.")
    print("\n  BUY -- their buried player who starts for me:")
    print(f"  {'#':>2s} {'from':16s} {'target':24s} {'mean':>5s} {'behind':22s} {'slot':5s} {'I give':34s} {'my scrn':>7s} {'thr scrn':>8s} {'prop':>4s} {'PO%':>5s} {'sell?':>5s} {'will':>4s}")
    for i, b in enumerate(r["buy"], 1):
        print(f"  {i:2d} {b['with'][:16]:16s} {b['target'][:24]:24s} {b['target_mean']:5.1f} {str(b['buried_behind'] or '-')[:22]:22s} "
              f"{str(b['fills_my_slot'] or '-'):5s} {', '.join(b['i_give'])[:34]:34s} {b['my_gain']:+6.1f} {b['their_gain']:+7.1f} "
              f"{'yes' if b['worth_proposing'] else 'no':>3s} {(format(b['their_playoff_pct'], '5.1f') if b['their_playoff_pct'] is not None else '    -')} "
              f"{('yes' if b['seller'] else 'no') if b['seller'] is not None else '-':>5s} {b['willingness'] if b['willingness'] is not None else '-':>4}")
        # B2: a screen number is not a finding. Anything without a paired sim says so.
        if not b.get("simulated"):
            print("       UNSIMULATED -- screen numbers only; run tool 2 before acting")
        if "evaluation" in b:
            ev = b["evaluation"]["teams"]
            me, them = ev.get(args.team, {}), ev.get(b["with"], {})
            if me:
                print(f"       tool 2 ({b['evaluation']['n_sims']} paired seasons): me Champ {me['champ_pct']['delta']:+.2f}+-{me['champ_pct']['se']:.2f} "
                      f"Playoff {me['playoff_pct']['delta']:+.2f}+-{me['playoff_pct']['se']:.2f} | them Champ {them['champ_pct']['delta']:+.2f} "
                      f"Playoff {them['playoff_pct']['delta']:+.2f}")
            d = b.get("disagreement") or {}
            if d.get("disagree"):
                print(f"       {d['note']}")
    print("\n  SELL -- my surplus an opponent would want:")
    print(f"  {'#':>2s} {'buyer':16s} {'they want':34s} {'they give':34s} {'my scrn':>7s} {'thr scrn':>8s} {'prop':>4s} {'PO%':>5s} {'will':>4s}")
    for i, s_ in enumerate(r["sell"], 1):
        print(f"  {i:2d} {s_['buyer'][:16]:16s} {', '.join(s_['they_want'])[:34]:34s} {', '.join(s_['they_give'])[:34]:34s} "
              f"{s_['my_gain']:+6.1f} {s_['their_gain']:+7.1f} {'yes' if s_['worth_proposing'] else 'no':>3s} "
              f"{(format(s_['their_playoff_pct'], '5.1f') if s_['their_playoff_pct'] is not None else '    -')} {s_['willingness'] if s_['willingness'] is not None else '-':>4}")
    print(f"  {r['note']}")

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = decisions_week_path(week, f"trade_targets_{stamp}_week{week}.json", canonical=args.canonical)
    save_json(out, {"timestamp_utc": stamp, "tool": "find_trades", **r})
    print(f"  logged -> {out}")
    return r


if __name__ == "__main__":
    main()

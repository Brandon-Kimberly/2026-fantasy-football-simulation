#!/usr/bin/env python3
"""
Roster-grade report: every rostered player's tier (standing in the whole pool, free agents
included) and VORP (mean minus replacement level at his position), rolled up per position and
overall, for one team or as a league table (fantasy_sim.decisions.grade_roster / roster_grades).

  py -3.10 -m scripts.roster_grades                 # league table, ranked by lineup VORP
  py -3.10 -m scripts.roster_grades --team "Quantum Ferrets"   # per-player detail

No letter grades: rank is relative to the league. lineup_vorp = starters' expectation over the
replacement level of the slot each fills (unfilled slots count 0); depth_vorp = positive bench
VORP; optimal_score = the engine's roster valuation, which includes its deliberate 0.1 x bench
term. Reads data/current/ only; writes one JSON record under data/decisions/.
"""
import argparse
import datetime as _dt

from fantasy_sim.decisions import grade_roster, roster_grades
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_week_path, save_json


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=None)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--canonical", action="store_true", help="a scheduled/deliberate run: write to week_NN/ instead of week_NN/archive/")
    args = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    week = args.week or engine.current_week
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    table = roster_grades(engine, week)
    print(f"\nLeague roster grades -- week {week}  (rank by lineup VORP; no letter grades)")
    print(f"  {'#':>2s} {'team':18s} {'lineupVORP':>10s} {'depthVORP':>9s} {'optScore':>8s} {'holes':>5s} {'T1 st':>5s} {'st<rep':>6s}")
    for r in table["teams"]:
        print(f"  {r['rank']:2d} {r['team']:18s} {r['lineup_vorp']:10.1f} {r['depth_vorp']:9.1f} {r['optimal_score']:8.1f} "
              f"{r['holes']:5d} {r['tier1_starters']:5d} {r['starters_below_replacement']:6d}")
    record = {"timestamp_utc": stamp, "tool": "roster_grades", "league": table}

    if args.team:
        g = grade_roster(engine, args.team, week)
        print(f"\n{args.team} -- lineup VORP {g['lineup_vorp']:+.1f}, depth VORP {g['depth_vorp']:+.1f}, "
              f"optimal score {g['optimal_score']:.1f} (incl. 0.1 x bench), holes: {g['holes'] or 'none'}")
        print(f"  {'player':26s} {'pos':4s} {'role':8s} {'slot':5s} {'tier':>4s} {'mean':>6s} {'rep':>5s} {'VORP':>6s}  status")
        for p in g["players"]:
            st = []
            if p["injury_status"]: st.append(p["injury_status"])
            if p["on_ir"]: st.append("IR")
            if p["bye"] == week: st.append("BYE")
            print(f"  {p['name'][:26]:26s} {p['pos']:4s} {p['role']:8s} {str(p['slot'] or '-'):5s} {str(p['tier'] or '-'):>4s} "
                  f"{p['mean']:6.1f} {g['replacement_levels'].get(p['pos'], 4.0):5.1f} {p['vorp']:+6.1f}  {' '.join(st)}")
        print(f"\n  {'pos':4s} {'st':>3s} {'bn':>3s} {'startVORP':>9s} {'depthVORP':>9s} {'tiers':14s}  best free agent")
        for pos, b in sorted(g["by_position"].items()):
            fa = b["best_free_agent"]
            fa_txt = f"{fa['name']} ({fa['vorp']:+.1f})" if fa else "-"
            print(f"  {pos:4s} {b['n_starters']:3d} {b['n_bench']:3d} {b['starters_vorp']:9.1f} {b['depth_vorp']:9.1f} "
                  f"{str(sorted(t for t in b['tiers'] if t is not None)):14s}  {fa_txt}")
        print(f"  {g['note']}")
        record["team_detail"] = g

    out = decisions_week_path(week, f"roster_grades_{stamp}_week{week}.json", canonical=args.canonical)
    save_json(out, record)
    print(f"  logged -> {out}")
    return record


if __name__ == "__main__":
    main()

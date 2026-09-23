#!/usr/bin/env python3
"""
Price a proposed scoring change before the league votes on it (fantasy_sim.reprice).

  py -3.10 -m scripts.reprice --scoring '{"idp_sack": 2.0, "idp_qb_hit": 0.5}'
  py -3.10 -m scripts.reprice --scoring '{"idp_sack": 2.0}' --rostered-only --top 20

Recomputes every projected player under the proposed settings, using Sleeper's own
projected stat lines and the same arithmetic `sync.generate_player_baselines` applies, and
prints per-player and per-team deltas.

The override is PARTIAL: categories you do not name keep their current value. A key that
is not a real setting RAISES rather than quietly reporting "no impact" -- a misspelling is
the one failure a warning tool must never swallow.

`sync.py` reads scoring live from the league object, so once a change is actually adopted
this tool becomes a no-op. Its value is entirely in the window before the vote: F49's
repricing found the owner's starting DL was the league's biggest loser at -19.5% and that
he should refuse any trade bringing him a pass rusher.

Reads the live projections endpoint and data/current/; writes nothing.
"""
import argparse
import json

import requests

from fantasy_sim.config import BASE_URL, LEAGUE_ID
from fantasy_sim.reprice import reprice_players, team_impact
from fantasy_sim.storage import LEAGUE_STATE_FILE, LIVE_ROSTERS_FILE, PLAYER_CACHE_FILE, load_json


def _live_scoring():
    if not LEAGUE_ID:
        raise SystemExit("SLEEPER_LEAGUE_ID is not set (F37: league ids are env-only).")
    lg = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}", timeout=30).json() or {}
    return lg.get("scoring_settings") or {}


def _projections(season, week):
    r = requests.get(f"{BASE_URL}/projections/nfl/regular/{season}/{week}", timeout=30)
    if r.status_code == 200 and r.json():
        return r.json(), False
    r = requests.get(f"{BASE_URL}/projections/nfl/regular/{season}", timeout=30)
    if r.status_code == 200 and r.json():
        return r.json(), True
    raise SystemExit("Sleeper served no usable projections; try again shortly.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scoring", required=True,
                    help='JSON of the PROPOSED changes, e.g. \'{"idp_sack": 2.0}\'')
    ap.add_argument("--season", default="2026")
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--rostered-only", action="store_true",
                    help="only players on a league roster (the ones anyone votes about)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        override = json.loads(args.scoring)
    except ValueError as ex:
        raise SystemExit(f"--scoring is not valid JSON ({ex}). Quote it: '{{\"idp_sack\": 2.0}}'")
    if not isinstance(override, dict) or not override:
        raise SystemExit("--scoring must be a non-empty JSON object of category -> value.")

    week = args.week or (load_json(LEAGUE_STATE_FILE) or {}).get("current_week", 1)
    scoring = _live_scoring()
    proj, season_long = _projections(args.season, week)
    db = load_json(PLAYER_CACHE_FILE) or {}

    rows = reprice_players(proj, db, scoring, override, per_game=season_long)

    lr = load_json(LIVE_ROSTERS_FILE) or {}
    rosters = {t: [p["name"] for p in pl] for t, pl in lr.items()}
    rostered = {n for names in rosters.values() for n in names}
    if args.rostered_only:
        rows = [r for r in rows if r["name"] in rostered]

    moved = [r for r in rows if abs(r["delta"]) > 1e-9]
    if args.json:
        print(json.dumps({"override": override, "n_moved": len(moved),
                          "players": moved[:args.top],
                          "teams": team_impact(rows, rosters)}, indent=1, sort_keys=True))
        return rows

    changed = ", ".join(f"{k}: {scoring.get(k)} -> {v}" for k, v in override.items())
    print(f"\nREPRICE -- {changed}")
    print(f"  {len(moved)} of {len(rows)} projected players move"
          + ("  (rostered only)" if args.rostered_only else "")
          + ("  [season-long projections, per game]" if season_long else "  [week projections]"))
    if not moved:
        print("  Nothing moves. If that is a surprise, check the category spelling -- "
              "though an unknown key would have raised rather than reaching here.")
        return rows

    print(f"\n  BIGGEST LOSERS\n  {'player':26s} {'pos':4s} {'before':>7s} {'after':>7s} "
          f"{'delta':>7s} {'pct':>7s}  rostered")
    for r in moved[:args.top]:
        pct = "-" if r["pct"] is None else f"{r['pct']:+6.1f}%"
        print(f"  {r['name'][:26]:26s} {str(r['pos'] or '-'):4s} {r['before']:7.2f} "
              f"{r['after']:7.2f} {r['delta']:+7.2f} {pct:>7s}  "
              f"{'yes' if r['name'] in rostered else ''}")

    gainers = [r for r in reversed(moved) if r["delta"] > 0][:5]
    if gainers:
        print("\n  BIGGEST GAINERS")
        for r in gainers:
            pct = "-" if r["pct"] is None else f"{r['pct']:+6.1f}%"
            print(f"  {r['name'][:26]:26s} {str(r['pos'] or '-'):4s} {r['delta']:+7.2f} {pct:>7s}")

    print("\n  PER TEAM (rostered players only)")
    for t in team_impact(rows, rosters):
        print(f"  {t['team'][:24]:24s} {t['delta']:+8.2f}  {t['n_affected']:3d} affected"
              + (f"   worst: {t['worst']}" if t["worst"] else ""))
    print("\n  Arithmetic is sync's own (sum of stat x multiplier). Once a change is "
          "adopted, sync reads it live and this tool becomes a no-op.")
    return rows


if __name__ == "__main__":
    main()

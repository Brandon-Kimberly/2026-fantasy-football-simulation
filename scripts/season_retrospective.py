#!/usr/bin/env python3
"""
Season retrospective: four separate measurements of one season's record -- schedule luck
(real all-play vs actual wins), lineup efficiency (actual vs Hungarian-optimal on realized
scores), absence rate (0.0-point DNP proxy), and losses to the weekly high scorer. No
combined verdict: the numbers are reported side by side with the scoring-format context.

  py -3.10 -m scripts.season_retrospective                  # 2025
  py -3.10 -m scripts.season_retrospective --season 2026    # after that season completes

Ingests the season bundle on first use (sync.ingest_season -> data/logs/season_{season}.json,
immutable); positions for the optimal-lineup solve come from the player cache. Writes one
JSON record under data/decisions/.
"""
import argparse
import datetime as _dt
import json
import os

import requests

from fantasy_sim.config import BASE_URL, LEAGUE_ID, MY_TEAM
from fantasy_sim.season_retrospective import season_retrospective
from fantasy_sim.storage import PLAYER_CACHE_FILE, decisions_season_path, load_json, save_json, season_log_file
from fantasy_sim.sync import ingest_season


def _league_id_for_season(season):
    """Walks the previous_league_id chain from the current league to the requested season."""
    lid, seen = LEAGUE_ID, set()
    while lid and lid not in seen:
        seen.add(lid)
        info = requests.get(f"{BASE_URL}/league/{lid}", timeout=10).json() or {}
        if str(info.get("season")) == str(season):
            return lid
        lid = info.get("previous_league_id")
    raise SystemExit(f"no league found for season {season} in the renewal chain")


def _positions(players_db):
    out = {}
    for pid, e in players_db.items():
        pos = e.get("fantasy_positions") or ([e.get("position")] if e.get("position") else None)
        if pos:
            out[str(pid)] = [p for p in pos if p]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", default="2025")
    ap.add_argument("--team", default=MY_TEAM)
    args = ap.parse_args(argv)

    path = season_log_file(args.season)
    if not os.path.exists(path):
        n = ingest_season(_league_id_for_season(args.season))
        if not n or not os.path.exists(path):
            raise SystemExit(f"season {args.season} could not be ingested; see warnings above")
        print(f"[SEASON LOG] season {args.season} ingested -> {path}")
    with open(path, encoding="utf-8") as f:
        bundle = json.load(f)

    players_db = load_json(PLAYER_CACHE_FILE)
    r = season_retrospective(bundle, _positions(players_db))

    print(f"\nSeason {r['season']} retrospective -- {len(r['regular_season_weeks'])} regular-season "
          f"weeks, slots {r['slots']}")
    print(f"  CONTEXT: {r['context_note']}")
    if r["position_unknown"]:
        print(f"  caveat: {len(r['position_unknown'])} player ids had no position in today's cache "
              f"and could not be seated in optimal lineups")

    print("\n(1) Schedule luck -- all-play expected wins vs actual (negative luck = unlucky):")
    print(f"  {'team':18s} {'expW(all-play)':>13s} {'actualW':>7s} {'luck':>7s} {'points':>8s} {'ptsRank':>7s}")
    for t, d in sorted(r["schedule_luck"].items(), key=lambda kv: kv[1]["luck"]):
        print(f"  {t:18s} {d['expected_wins_all_play']:13.2f} {d['actual_wins']:7} "
              f"{d['luck']:+7.2f} {d['points']:8.2f} {d['points_rank']:7d}")

    print("\n(2) Lineup efficiency -- actual started points vs Hungarian-optimal on realized scores:")
    print(f"  {'team':18s} {'actual':>8s} {'optimal':>8s} {'lost':>7s} {'pct':>6s}")
    for t, d in sorted(r["lineup_efficiency"].items(), key=lambda kv: -(kv[1]["pct"] or 0)):
        print(f"  {t:18s} {d['actual']:8.2f} {d['optimal']:8.2f} {d['points_lost']:7.2f} {d['pct']:6.2f}")
    mine = r["lineup_efficiency"].get(args.team)
    if mine:
        worst = sorted(mine["weeks"], key=lambda w: -w["lost"])[:3]
        print(f"  {args.team}'s worst weeks: " + "; ".join(
            f"wk{w['week']} left {w['lost']:.1f} on the bench" for w in worst))

    print(f"\n(3) Absences -- {r['absence_note']}")
    print(f"  {'team':18s} {'zeros':>5s} {'of':>5s} {'rate':>7s} {'startedZeros':>12s}")
    rates = []
    for t, d in sorted(r["absences"].items(), key=lambda kv: -(kv[1]["rate"] or 0)):
        rates.append(d["rate"] or 0)
        print(f"  {t:18s} {d['zero_point_player_weeks']:5d} {d['player_weeks']:5d} "
              f"{(d['rate'] or 0) * 100:6.1f}% {d['starter_zeros']:12d}")
    print(f"  league mean rate: {100 * sum(rates) / len(rates):.1f}%")

    print("\n(4) Losses against the week's high scorer:")
    print(f"  {'team':18s} {'losses':>6s} {'vsWeekHigh':>10s}")
    for t, d in sorted(r["high_scorer_losses"].items(), key=lambda kv: -kv[1]["vs_week_high_scorer"]):
        print(f"  {t:18s} {d['losses']:6d} {d['vs_week_high_scorer']:10d}")

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = decisions_season_path(f"season_retrospective_{args.season}_{stamp}.json")
    save_json(out, {"timestamp_utc": stamp, "tool": "season_retrospective", "retrospective":
                    {k: v for k, v in r.items()}})
    print(f"\n  logged -> {out}")
    return r


if __name__ == "__main__":
    main()

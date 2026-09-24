#!/usr/bin/env python3
"""
Trade bait, two ways (fantasy_sim.leverage).

  py -3.10 -m scripts.trade_leverage [--team "Quantum Ferrets"] [--week N] [--no-draft]

1. SELL HIGH -- whose public price sits above the model's. The proxies are where these
   eight managers actually drafted him (a revealed preference by this league, not a
   national ADP) and his PRESEASON Sleeper projection, read from the F7 projection log's
   earliest row. A player taken early, or still projected high, whom the corrected blend
   now marks down, is a name worth more than his production.

2. LEVERAGE -- which rival's own optimal lineup starts someone BELOW replacement, and do
   I have surplus there? That is the position they need badly enough to overpay in kind.
   Only players I do NOT start are offered: sending a starter is a downgrade.

Both external maps are keyed by player_id, never by name. The raw player cache carries
220 colliding names, seven involving a player rostered here, so a name-keyed join hands a
cornerback's draft pick to the wide receiver who shares it.

Season mean points per week throughout. Reads data/current/ and data/logs/, and the
league draft unless --no-draft; writes nothing.
"""
import argparse
import json
import os

import requests

from fantasy_sim.config import BASE_URL, LEAGUE_ID, MY_TEAM as DEFAULT_TEAM
from fantasy_sim.leverage import leverage, sell_high
from fantasy_sim.pending import committed_players, note as pending_note
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import PROJECTION_LOG_FILE
from fantasy_sim.weekly_report import real_name_overlay


def draft_picks_by_pid(season=None, fetch=None):
    """{player_id: pick_no} for the league's most recent draft.

    Sleeper's pick payload already carries `player_id`; the scratchpad threw it away and
    rebuilt a name from the cache, which is where the collisions are.
    """
    get = fetch or (lambda u: requests.get(u, timeout=45).json())
    if not LEAGUE_ID:
        raise SystemExit("SLEEPER_LEAGUE_ID is not set (F37: league ids are env-only).")
    drafts = get(f"{BASE_URL}/league/{LEAGUE_ID}/drafts") or []
    if season:
        drafts = [d for d in drafts if str(d.get("season")) == str(season)] or drafts
    if not drafts:
        return {}
    picks = get(f"{BASE_URL}/draft/{drafts[0]['draft_id']}/picks") or []
    return {str(p["player_id"]): p.get("pick_no")
            for p in picks if p.get("player_id") is not None}


def preseason_by_pid(season, path=PROJECTION_LOG_FILE):
    """{player_id: the FIRST logged sleeper_mean this season} -- the preseason number,
    before any in-season correction. Keyed by the log's own player_id field."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if str(row.get("season")) != str(season):
                continue
            pid = row.get("player_id")
            mu = row.get("sleeper_mean")
            if pid is not None and mu and str(pid) not in out:
                out[str(pid)] = float(mu)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--season", default="2026")
    ap.add_argument("--include-pending", action="store_true",
                    help="count players already committed to a PENDING trade as surplus "
                         "I could send (T3). Off by default; pending is not certain, so "
                         "the flag exists")
    ap.add_argument("--no-draft", action="store_true",
                    help="skip the draft fetch (offline); picks show as '-'")
    args = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    week = args.week or engine.current_week
    ov = real_name_overlay()
    show = lambda t: ov.get(t, t)          # noqa: E731

    picks = {} if args.no_draft else draft_picks_by_pid(args.season)
    pre = preseason_by_pid(args.season)

    rows = sell_high(engine, args.team, picks, pre)
    print(f"\n{show(args.team)} -- week {week} trade leverage   (season mean pts/wk)")
    print(f"\n  1. SELL HIGH -- the market rates these above the model")
    print(f"  {'player':24s} {'pos':4s} {'pick':>5s} {'preseason':>10s} {'now':>7s} "
          f"{'markdown':>9s} {'vs rep':>7s}")
    for r in rows:
        if r["markdown"] is None:
            continue
        flag = ("  <-- SELL HIGH" if r["sell_high"]
                else ("  <-- name > production" if r["name_over_production"] else ""))
        print(f"  {r['name'][:24]:24s} {r['pos']:4s} {str(r['pick'] or '-'):>5s} "
              f"{r['preseason']:10.2f} {r['now']:7.2f} {r['markdown']:+9.2f} "
              f"{r['vs_replacement']:+7.2f}{flag}")
    missing = [r["name"] for r in rows if r["markdown"] is None]
    if missing:
        print(f"  (no preseason row for {len(missing)}: {', '.join(m[:18] for m in missing)})")

    print(f"\n  2. LEVERAGE -- rivals starting someone below replacement")
    skip = set() if args.include_pending else committed_players(engine)
    if skip:
        print("  " + pending_note(skip))
    for g in leverage(engine, args.team, week, exclude=skip):
        if not g["holes"]:
            print(f"  {show(g['team'])[:26]:26s} -- no starting slot below replacement --")
            continue
        print(f"  {show(g['team'])[:26]:26s}")
        for h in g["holes"][:3]:
            send = (f"   <- send {h['i_could_send'][:20]} ({h['my_mean']:.1f}) "
                    f"= +{h['upgrade_for_them']:.1f} for them"
                    if h["i_could_send"] else "   <- I have no surplus there")
            print(f"      {h['pos']:3s} {h['their_starter'][:20]:20s} {h['their_mean']:6.2f}"
                  f"  {h['deficit']:5.2f} below rep{send}")
    return {"sell_high": rows}


if __name__ == "__main__":
    main()

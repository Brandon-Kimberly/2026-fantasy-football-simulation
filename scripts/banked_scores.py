"""
scripts.banked_scores -- is every past week's score the league's? (fantasy_sim.banked_scores)

The sync writes data/current/banked_scores.json on every run. This checks the same thing on
demand, without a sync: each completed week's matchups and stat lines, scored under the
league's logged settings (data/logs/scoring_settings.jsonl), against every team's banked points,
points against and win-loss string -- and prints each week's scores beside Sleeper's recomputed
box scores, so a mismatch names itself.

    py -3.10 -m scripts.banked_scores            # check and print; writes nothing
    py -3.10 -m scripts.banked_scores --write    # and write data/current/banked_scores.json

When it says NOT verified: the league's settings changed in a way the log does not hold (a sync
logs every change it sees, but not one made and reverted between syncs), or the commissioner
adjusted a total with no per-week override. Add the missing settings row to the log, or leave
it: the site keeps showing Sleeper's box scores, with their caveat, until it verifies.

Reads Sleeper's public API (SLEEPER_LEAGUE_ID, refused loudly when unset).
"""
import argparse
import sys

import requests

from fantasy_sim.banked_scores import eras
from fantasy_sim.config import BASE_URL, LEAGUE_ID, TEAM_NAME_MAP
from fantasy_sim.storage import BANKED_SCORES_FILE, SCORING_SETTINGS_FILE, save_json
from fantasy_sim.sync import build_banked_scores


def _get(url):
    r = requests.get(url, timeout=45)
    r.raise_for_status()
    return r.json()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="write data/current/banked_scores.json")
    a = ap.parse_args(argv)
    if not LEAGUE_ID:
        sys.exit("SLEEPER_LEAGUE_ID is not set. Locally: setx SLEEPER_LEAGUE_ID <id> and open a NEW terminal.")
    league = _get(f"{BASE_URL}/league/{LEAGUE_ID}")
    state = _get(f"{BASE_URL}/state/nfl")
    season = str(league.get("season") or state.get("season"))
    week = int(state.get("week") or 1)
    rosters = _get(f"{BASE_URL}/league/{LEAGUE_ID}/rosters")
    roster_map = {r["roster_id"]: TEAM_NAME_MAP.get(str(r["roster_id"]), f"roster {r['roster_id']}") for r in rosters}
    weeks = {}
    for w in range(1, week):
        ms = _get(f"{BASE_URL}/league/{LEAGUE_ID}/matchups/{w}")
        if ms:
            weeks[w] = ms
    known = eras(SCORING_SETTINGS_FILE, season)
    doc = build_banked_scores(season, weeks, rosters, roster_map, league.get("settings"), known, fetch=_get)
    if doc is None:
        print("The stats feed failed; nothing checked.")
        return 1
    meta = doc["_meta"]
    print(f"{season}: {len(known)} scoring setting(s) logged; weeks {', '.join(k[5:] for k in doc if k.startswith('week_')) or 'none'}")
    for w, era in sorted(meta["eras"].items(), key=lambda kv: int(kv[0])):
        print(f"  week {w} banked under the settings logged {era}")
    for key in sorted((k for k in doc if k.startswith("week_")), key=lambda k: int(k[5:])):
        print(f"\n{key.replace('_', ' ')}")
        for t, r in sorted(doc[key].items(), key=lambda kv: -kv[1]["points"]):
            note = " (commissioner override)" if r["override"] else ""
            api = r.get("api_points")
            diff = f"   Sleeper's box score now {api:.2f}" if api is not None and abs(float(api) - r["points"]) >= 0.005 else ""
            print(f"  {t:18s} {r['points']:8.2f}{note}{diff}")
    print("\nchecks:", ", ".join(f"{k} {'ok' if v else ('--' if v is None else 'FAILED')}" for k, v in meta["checks"].items()))
    print("VERIFIED: every past week reproduces the league's own numbers to the cent." if meta["verified"]
          else f"NOT verified: {meta['why']}")
    if a.write:
        save_json(BANKED_SCORES_FILE, doc)
        print(f"wrote {BANKED_SCORES_FILE}")
    return 0 if meta["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

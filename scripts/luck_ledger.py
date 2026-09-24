#!/usr/bin/env python3
"""
Am I actually unlucky, or does it just feel that way? -- five pre-registered measurements.

Deliberately NOT part of the weekly report. This is a thing you pull when you want it,
not a number in your face every Sunday (owner's call, 2026-09-21).

  py -3.10 -m scripts.luck_ledger                      # current season
  py -3.10 -m scripts.luck_ledger --season 2025
  py -3.10 -m scripts.luck_ledger --all                # every season in the renewal chain
  py -3.10 -m scripts.luck_ledger --json

The definitions are FIXED in fantasy_sim/luck_ledger.py and documented in
docs/LUCK_LEDGER.md. They were pre-registered before the 2026 data existed; the git
history is the timestamp. Changing one after seeing what it says would make this a story
instead of a test, so don't.

Every metric is differenced against the LEAGUE, never against zero -- the engine carries
known bias (bias -2.12, cover80 0.654) and scoring a team against an absolute would
re-measure the model's error and call it luck.

Read the standard errors before the deltas. At two or three weeks nothing here is
significant and the tool will say so.
"""
import argparse
import json
import textwrap as _textwrap
from collections import defaultdict

import requests

from fantasy_sim.config import BASE_URL, LEAGUE_ID, MY_TEAM, TEAM_NAME_MAP, KNOWN_LEAGUE_IDS
from fantasy_sim.league_chain import resolve_chain
from fantasy_sim.luck_ledger import direction, ledger, two_sided_p
from fantasy_sim.weekly_report import real_name_overlay


def _get(url):
    r = requests.get(url, timeout=45)
    r.raise_for_status()
    return r.json()


def _league_chain():
    """(season, league_id, info) for every season, newest first.

    B20: walks previous_league_id AND falls back to config.KNOWN_LEAGUE_IDS, because this
    league's chain is broken at 2025 -- without the map, --all silently stopped one season
    short. The shared walker lives in fantasy_sim.league_chain so this and
    scripts.season_retrospective cannot drift apart.
    """
    seen = {}

    def fetch(lid):
        seen[str(lid)] = info = _get(f"{BASE_URL}/league/{lid}") or {}
        return info

    pairs = resolve_chain(LEAGUE_ID, fetch=fetch, known=KNOWN_LEAGUE_IDS)
    # Seasons supplied by the map were never fetched by the walk; fetch them now.
    return [(season, lid, seen.get(str(lid)) or fetch(lid)) for season, lid in pairs]


def _team_names(lid, info):
    """roster_id -> display name. The current league uses the pseudonym map; older
    seasons fall back to that season's own team names."""
    rosters = _get(f"{BASE_URL}/league/{lid}/rosters")
    if str(lid) == str(LEAGUE_ID):
        return {r["roster_id"]: TEAM_NAME_MAP.get(str(r["roster_id"]), f"roster {r['roster_id']}")
                for r in rosters}
    users = {u["user_id"]: (u.get("metadata", {}).get("team_name") or u.get("display_name"))
             for u in _get(f"{BASE_URL}/league/{lid}/users")}
    return {r["roster_id"]: users.get(r.get("owner_id"), f"roster {r['roster_id']}")
            for r in rosters}


def _season_data(lid, info, names, through_week=None):
    """weekly_scores, pairs, starter_points for a season -- completed weeks only."""
    last = int(info.get("settings", {}).get("playoff_week_start") or 15) - 1
    if through_week:
        last = min(last, int(through_week))
    scores, pairs, starters = {}, {}, {}
    for wk in range(1, last + 1):
        try:
            ms = _get(f"{BASE_URL}/league/{lid}/matchups/{wk}") or []
        except Exception:
            continue
        row, by_mid, srow = {}, defaultdict(list), {}
        live = False
        for m in ms:
            rid = m["roster_id"]
            t = names.get(rid, f"roster {rid}")
            pts = float(m.get("points") or 0.0)
            row[t] = pts
            if pts > 0:
                live = True
            pp = m.get("players_points") or {}
            srow[t] = [float(pp.get(str(s)) or 0.0) for s in (m.get("starters") or [])
                       if s and str(s) != "0"]
            if m.get("matchup_id") is not None:
                by_mid[m["matchup_id"]].append(t)
        if not live:
            continue                     # week hasn't been played
        scores[wk] = row
        starters[wk] = srow
        pairs[wk] = [tuple(v) for v in by_mid.values() if len(v) == 2]
    return scores, pairs, starters


def _banked_wins(lid, names):
    """{team: Sleeper's own settings.wins} -- the record the league BANKED (C4/F70).

    Every `points` value this tool reads is what Sleeper serves TODAY, re-scored under the
    league's CURRENT settings, so a mid-season scoring change rewrites finished weeks
    (F49's IDP cut flipped one on 2026-09-23). This number is not re-scored: it was
    written when the week closed. The two disagreeing is the only available signal that
    the scores are rewritten history.

    NOTE the field is TOTAL wins -- both legs of a median-scoring week -- which is why
    luck_ledger.banked_disagreement compares it against h2h wins PLUS median wins.
    """
    try:
        rosters = _get(f"{BASE_URL}/league/{lid}/rosters")
    except Exception:
        return {}
    out = {}
    for r in rosters or []:
        t = names.get(r.get("roster_id"))
        w = (r.get("settings") or {}).get("wins")
        if t is not None and w is not None:
            out[t] = int(w)
    return out


def _projections(season):
    """{week: {team: (expected_total, sd)}} from the committed predictions log.

    Only exists from 2026 -- the log started with this season, so earlier years get an
    honest None for scoring_luck rather than a fabricated one.
    """
    try:
        rows = [json.loads(l) for l in open("data/logs/predictions_2026.jsonl") if l.strip()]
    except OSError:
        return None
    if str(season) != "2026":
        return None
    out = {}
    for r in rows:
        med = r.get("median") or {}
        wk = r.get("week")
        if not med or wk is None:
            continue
        # last canonical row for a week wins; fall back to any row for that week
        if wk in out and not r.get("canonical"):
            continue
        out[wk] = {t: (float(v.get("expected_total") or 0.0), float(v.get("sd_total") or 0.0))
                   for t, v in med.items() if isinstance(v, dict)}
    return out or None


def _fmt(metric, keys, label, name, weeks,
         absent="-- not measurable for this season --"):
    if metric is None:
        return f"  {label:16s} {absent}"
    delta, se, z = metric.get("delta"), metric.get("se"), metric.get("z")
    detail = "  ".join(f"{k}={metric[k]}" for k in keys if k in metric)
    way = direction(name, delta)
    if z is None:
        return f"  {label:16s} {delta:+8.2f}  {way:8s} (no spread to test)  {detail}"
    p = two_sided_p(z)
    # Below six weeks nothing gets a significance word. The arithmetic is honest but the
    # sample is not, and "SIGNIFICANT" next to n=2 is how a tool like this starts lying.
    if weeks < 6:
        verdict = "too early"
    else:
        verdict = "SIGNIFICANT" if p < 0.05 else ("suggestive" if p < 0.20 else "noise")
    return (f"  {label:16s} {delta:+8.2f}  {way:8s} +-{se:6.2f}  z {z:+5.2f}  p {p:5.3f}  "
            f"{verdict:11s} {detail}")


def render(res, season, team_label, n_weeks):
    print(f"\n=== LUCK LEDGER -- {team_label}, {season} ({n_weeks} completed weeks) ===")
    print("  pre-registered definitions; every metric differenced against the league")
    d = res.get("banked_disagreement")
    absent = ("-- WITHHELD, the record was rewritten (see below) --" if d
              else "-- not measurable for this season --")
    print(_fmt(res["schedule_luck"], ["actual_wins", "expected_wins", "all_play_pct"],
               "schedule luck", "schedule_luck", n_weeks, absent))
    print(_fmt(res["opponent_luck"], ["my_pa_per_game", "league_avg_pa_per_game"],
               "opponent luck", "opponent_luck", n_weeks))
    print(_fmt(res["close_games"], ["wins", "losses", "n"], "close games",
               "close_games", n_weeks, absent))
    print(_fmt(res["dnp_luck"], ["my_dnps_per_game", "league_avg"], "DNP luck",
               "dnp_luck", n_weeks))
    print(_fmt(res["scoring_luck"], ["my_mean_z", "league_mean_z", "n_weeks"],
               "scoring luck", "scoring_luck", n_weeks))
    if d:
        print(f"\n  !! These {d['weeks']} weeks recompute to {d['recomputed']} wins "
              f"({d['h2h_wins']} head-to-head + {d['median_wins']} median), but the league "
              f"banked {d['banked']}.")
        print("     " + "\n     ".join(_textwrap.wrap(d["note"], 86)))
    if n_weeks < 6:
        print(f"\n  n = {n_weeks} weeks. Nothing here can be significant yet; the standard "
              "errors are the point.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", default=None)
    ap.add_argument("--all", action="store_true", help="every season in the renewal chain")
    ap.add_argument("--league-id", default=None, dest="league_id",
                    help="score a league the renewal chain cannot reach. 2025 carries a "
                         "null previous_league_id, so 2024 is orphaned. Normally you do "
                         "not need this: set SLEEPER_LEAGUE_ID_2024 and --all picks it up "
                         "via config.KNOWN_LEAGUE_IDS (B20). The flag remains for a league "
                         "that is in no map at all.")
    ap.add_argument("--team", default=None)
    ap.add_argument("--week", type=int, default=None, help="only count through this week")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.league_id:
        info = _get(f"{BASE_URL}/league/{a.league_id}") or {}
        chain = [(str(info.get("season")), a.league_id, info)]
    else:
        chain = _league_chain()
    if not a.all and not a.league_id:
        want = str(a.season) if a.season else chain[0][0]
        chain = [c for c in chain if c[0] == want] or chain[:1]

    ov = real_name_overlay()
    payload = []
    for season, lid, info in chain:
        names = _team_names(lid, info)
        scores, pairs, starters = _season_data(lid, info, names, a.week)
        if not scores:
            print(f"\n=== {season}: no completed weeks ===")
            continue
        team = a.team or (MY_TEAM if str(lid) == str(LEAGUE_ID) else None)
        if team is None or team not in next(iter(scores.values())):
            # older seasons: find my roster by owner id via the current league
            cur_rosters = _get(f"{BASE_URL}/league/{LEAGUE_ID}/rosters")
            uid = next((r.get("owner_id") for r in cur_rosters
                        if TEAM_NAME_MAP.get(str(r["roster_id"])) == MY_TEAM), None)
            old = _get(f"{BASE_URL}/league/{lid}/rosters")
            rid = next((r["roster_id"] for r in old if r.get("owner_id") == uid), None)
            team = names.get(rid)
        if team is None:
            print(f"\n=== {season}: could not identify your roster ===")
            continue
        # C4: only cross-check a season whose counted weeks are all CLOSED. A week still
        # in progress, or a --week cutoff, would differ from the banked total for an
        # innocent reason, and an alarm that cries wolf teaches the reader to ignore it.
        leg = (info.get("settings") or {}).get("leg")
        closed = a.week is None and (leg is None or max(scores) < int(leg))
        banked = _banked_wins(lid, names).get(team) if closed else None
        res = ledger(scores, pairs, team, starter_points=starters,
                     projections=_projections(season), banked_wins=banked)
        res["season"] = season
        payload.append(res)
        if not a.json:
            render(res, season, ov.get(team, team), len(scores))

    if a.json:
        print(json.dumps(payload, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

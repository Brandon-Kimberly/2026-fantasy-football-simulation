"""
scripts.as_played_record -- the league's week results AS PLAYED, for the weeks Sleeper's API
no longer reports that way (F83; docs/EVALUATION_BOUNDARIES.md boundary 1).

Weeks 1-2 of 2026 were played -- and are banked in the league's standings -- under the OLD
IDP scoring. Sleeper's /matchups endpoint recomputes a completed week against the CURRENT
settings, so it reports those two weeks on the NEW scale, permanently, and on the new scale
one week-2 game has the other winner. The owner's ruling (2026-09-28): the league's record,
as played, decides a result.

This rebuilds each team's as-played score from the frozen pre-change snapshot
(data/logs/first_recorded_scores.jsonl, captured before the change -- B19) and each week's
starters, derives the head-to-head and median results, and REFUSES to write unless every
team's reconstructed wins equal the wins the league banked (league_standings.json) and no
starter is missing from the snapshot. What it writes is results, not points: the
reconstructed points match the banked totals only to within a few points (the league's
totals moved again after F83's to-the-cent check), and nothing here claims them.

    py -3.10 -m scripts.as_played_record            # reconstruct, verify, print; writes nothing
    py -3.10 -m scripts.as_played_record --write    # and write data/logs/as_played_results_2026.json

Reads Sleeper's public /matchups for the starters (SLEEPER_LEAGUE_ID, refused loudly when
unset). The web UI reads the file it writes and never runs this.
"""
import argparse
import datetime as _dt
import json
import os
import sys

# EVALUATION_BOUNDARIES.md boundary 1 (resolution, 2026-09-24): weeks 1-2 were played and
# are banked under the OLD IDP scoring; /matchups recomputes them on the NEW.
OLD_SCALE_WEEKS = (1, 2)
OUT = os.path.join("data", "logs", "as_played_results_2026.json")
SNAPSHOT = os.path.join("data", "logs", "first_recorded_scores.jsonl")


def reconstruct(week, matchups, frozen, frozen_by_name, names, team_map):
    """({team: {points, h2h_win, median_win, matchup_id}}, [missing (team, pid, name)]) for one
    week: each starter's frozen as-played points (by player id, else by name), summed; the
    head-to-head result against the matchup partner (a tie is half each); and the median result
    against the average of the two middle as-played scores."""
    out, missing = {}, []
    for m in matchups:
        team = team_map.get(str(m.get("roster_id")))
        if not team:
            continue
        total = 0.0
        for pid in [str(p) for p in (m.get("starters") or []) if p and str(p) != "0"]:
            v = frozen.get((week, pid))
            if v is None:
                v = frozen_by_name.get((week, names.get(pid)))
            if v is None:
                missing.append((team, pid, names.get(pid)))
                continue
            total += float(v)
        out[team] = {"points": round(total, 2), "matchup_id": m.get("matchup_id")}
    pts = sorted(r["points"] for r in out.values())
    n = len(pts)
    cut = (pts[n // 2 - 1] + pts[n // 2]) / 2 if n and n % 2 == 0 else (pts[n // 2] if n else 0.0)
    for team, r in out.items():
        opp = next((t for t, o in out.items() if t != team and o["matchup_id"] == r["matchup_id"]), None)
        mine, theirs = r["points"], (out[opp]["points"] if opp else None)
        r["h2h_win"] = None if theirs is None else (1.0 if mine > theirs else (0.0 if mine < theirs else 0.5))
        r["median_win"] = 1 if mine > cut else 0
    return out, missing


def verify(by_week, standings):
    """[(team, reconstructed wins, banked wins)] for every team whose head-to-head plus median
    wins over the reconstructed weeks differ from the league's banked `h2h_wins` -- which
    counts both kinds. Empty means every team reconciles."""
    wins = {}
    for results in by_week.values():
        for team, r in results.items():
            wins[team] = wins.get(team, 0.0) + float(r.get("h2h_win") or 0) + float(r.get("median_win") or 0)
    bad = []
    for team, w in sorted(wins.items()):
        banked = (standings.get(team) or {}).get("h2h_wins")
        if banked is None or float(banked) != w:
            bad.append((team, w, banked))
    return bad


def _frozen(path):
    by_pid, by_name = {}, {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("player_id"):
                by_pid[(int(r["week"]), str(r["player_id"]))] = r["points"]
            by_name[(int(r["week"]), r.get("name"))] = r["points"]
    return by_pid, by_name


def _names(cache):
    out = {}
    for pid, c in (cache or {}).items():
        if isinstance(c, dict):
            out[str(pid)] = c.get("full_name") or f"{c.get('first_name') or ''} {c.get('last_name') or ''}".strip()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help=f"write {OUT} when every team reconciles")
    a = ap.parse_args(argv)
    from fantasy_sim.config import BASE_URL, LEAGUE_ID, TEAM_NAME_MAP
    if not LEAGUE_ID:
        print("as_played_record: SLEEPER_LEAGUE_ID is not set; it is needed for the weeks' starters.", file=sys.stderr)
        return 2
    import requests
    with open(os.path.join("data", "current", "league_standings.json"), encoding="utf-8") as fh:
        standings = json.load(fh)
    with open(os.path.join("data", "current", "sleeper_players_cache.json"), encoding="utf-8") as fh:
        names = _names(json.load(fh))
    by_pid, by_name = _frozen(SNAPSHOT)
    by_week, missing = {}, []
    for wk in OLD_SCALE_WEEKS:
        ms = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/matchups/{wk}", timeout=20).json()
        by_week[wk], miss = reconstruct(wk, ms, by_pid, by_name, names, TEAM_NAME_MAP)
        missing += [(wk,) + m for m in miss]
    for wk, res in by_week.items():
        print(f"week {wk}")
        for team, r in sorted(res.items(), key=lambda kv: kv[1]["matchup_id"] or 0):
            print(f"   {team:18s} {r['points']:7.2f}  h2h {r['h2h_win']}  median {r['median_win']}")
    bad = verify(by_week, standings)
    if missing:
        print(f"REFUSED: {len(missing)} starter(s) missing from the snapshot: {missing}", file=sys.stderr)
        return 1
    if bad:
        print(f"REFUSED: the reconstruction does not reproduce the league's banked wins: {bad}", file=sys.stderr)
        return 1
    print(f"verified: every team's reconstructed wins equal the league's banked wins ({len(standings)} teams)")
    if a.write:
        doc = {"_meta": {"source": "scripts.as_played_record", "basis": "as played (OLD IDP scoring), F83",
                         "weeks": list(OLD_SCALE_WEEKS), "snapshot": SNAPSHOT.replace(os.sep, "/"),
                         "verified_against": "league_standings.json h2h_wins, every team exactly",
                         "written_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "note": "results only; reconstructed points are not claimed"},
               **{f"week_{wk}": {t: {"h2h_win": r["h2h_win"], "median_win": r["median_win"]} for t, r in res.items()}
                  for wk, res in by_week.items()}}
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

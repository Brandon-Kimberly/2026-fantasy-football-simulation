"""
webui.history -- the league's history: every regular-season game, the record book, the
rivalries, the draft board (docs/WEB_UI_ROADMAP.md UI-H1, H2, H3).

Two rules the open-source league almanacs learned the hard way, kept here:
  - a median game is never a head-to-head result (2025 was pure head-to-head; 2026's median
    results live in webui.results and are not games between two teams);
  - only the championship path counts as playoffs. The 2025 archive does not record which
    post-season games were the championship path and which were consolation, so the record
    book and the rivalries count the REGULAR SEASON only, and the pages say so.

2025 comes from its archive (data/logs/season_<year>.json) as recorded. 2026 comes from the
schedule and webui.results -- the winner as the league counted it (F83) -- with the box score's
points, and `rescored` marks a game whose box score now names the other winner. Reads only.
"""
import re

from webui.results import rescaled_weeks, week_results


def _archives(root):
    for e in root.logs():
        m = re.match(r"^season_(\d{4})\.json$", e.get("name") or "")
        if m:
            yield m.group(1), root.read_json(e["rel"], {}) or {}


def games(root):
    """[{season, week, a, b, pa, pb, winner (None on a tie), margin, rescored}], regular season."""
    out = []
    for season, arc in sorted(_archives(root)):
        start = int((arc.get("settings") or {}).get("playoff_week_start") or 15)
        rmap = {str(k): v for k, v in (arc.get("roster_map") or {}).items()}
        for wk, rows in (arc.get("matchups") or {}).items():
            try:
                week = int(wk)
            except (TypeError, ValueError):
                continue
            if week >= start:
                continue
            by_id = {}
            for r in rows or []:
                if r.get("matchup_id") is not None:
                    by_id.setdefault(r["matchup_id"], []).append(r)
            for pair in by_id.values():
                if len(pair) != 2:
                    continue
                x, y = pair
                a, b = rmap.get(str(x.get("roster_id"))), rmap.get(str(y.get("roster_id")))
                pa, pb = float(x.get("points") or 0.0), float(y.get("points") or 0.0)
                if not a or not b:
                    continue
                out.append({"season": season, "week": week, "a": a, "b": b, "pa": pa, "pb": pb,
                            "winner": a if pa > pb else (b if pb > pa else None), "margin": round(abs(pa - pb), 2),
                            "rescored": False, "rescaled": False})
    seasons = {g["season"] for g in out}
    state = root.read_json("current/league_state.json", {}) or {}
    cur_season = str(state.get("season") or (root.read_json("current/sync_manifest.json", {}) or {}).get("season") or "")
    if cur_season and cur_season not in seasons:
        sched = root.read_json("current/league_schedule.json", []) or []
        scaled = rescaled_weeks(root)
        for week, res in sorted(week_results(root).items()):
            pairs = sched[week - 1] if isinstance(sched, list) and 0 < week <= len(sched) else []
            for p in pairs or []:
                if not isinstance(p, (list, tuple)) or len(p) != 2:
                    continue
                a, b = p
                ra, rb = res.get(a), res.get(b)
                if not ra or not rb or ra.get("h2h_win") is None:
                    continue
                pa, pb = float(ra.get("points_scored") or 0.0), float(rb.get("points_scored") or 0.0)
                w = float(ra["h2h_win"])
                out.append({"season": cur_season, "week": week, "a": a, "b": b, "pa": pa, "pb": pb,
                            "winner": a if w >= 1 else (b if w <= 0 else None), "margin": round(abs(pa - pb), 2),
                            "rescored": bool(ra.get("rescored") or rb.get("rescored")), "rescaled": week in scaled})
    return out


def _side(g, team):
    return (g["pa"], g["pb"], g["b"]) if team == g["a"] else (g["pb"], g["pa"], g["a"])


def _contradicted(g):
    """True when the winner the league counted scored less in today's box score."""
    if not g.get("winner"):
        return False
    mine, theirs = (g["pa"], g["pb"]) if g["winner"] == g["a"] else (g["pb"], g["pa"])
    return mine < theirs


def record_book(gs):
    """The game records: [{key, label, team, value, season, week, opponent, rescored}]."""
    scores = [(t, *_side(g, t), g) for g in gs for t in (g["a"], g["b"])]      # (team, mine, theirs, opp, game)
    if not scores:
        return []

    def row(key, label, t, value, g, opp):
        return {"key": key, "label": label, "team": t, "value": round(value, 2), "season": g["season"],
                "week": g["week"], "opponent": opp, "rescored": g["rescored"], "rescaled": g.get("rescaled", False)}
    hi = max(scores, key=lambda s: s[1])
    lo = min(scores, key=lambda s: s[1])
    # a game whose box score now names the other winner (Sleeper re-scores finished weeks) keeps
    # its points for the points records and sits out the result records (audit 2026-09-29)
    agree = [g for g in gs if not _contradicted(g)]
    decided = [g for g in agree if g["winner"]]
    out = [row("high", "Highest score", hi[0], hi[1], hi[4], hi[3]),
           row("low", "Lowest score", lo[0], lo[1], lo[4], lo[3])]
    if decided:
        big = max(decided, key=lambda g: g["margin"])
        out.append(row("margin", "Biggest win", big["winner"], big["margin"], big, big["b"] if big["winner"] == big["a"] else big["a"]))
        hl = max(decided, key=lambda g: min(g["pa"], g["pb"]))
        loser = hl["b"] if hl["winner"] == hl["a"] else hl["a"]
        out.append(row("high_loss", "Highest score in a loss", loser, min(hl["pa"], hl["pb"]), hl, hl["winner"]))
        lw = min(decided, key=lambda g: max(g["pa"], g["pb"]))
        out.append(row("low_win", "Lowest score in a win", lw["winner"], max(lw["pa"], lw["pb"]), lw,
                       lw["b"] if lw["winner"] == lw["a"] else lw["a"]))
    close = min(agree or gs, key=lambda g: g["margin"])
    out.append(row("close", "Closest game", close["winner"] or close["a"], close["margin"], close,
                   close["b"] if (close["winner"] or close["a"]) == close["a"] else close["a"]))
    return out


def rivalries(gs):
    """{(team, team) sorted: {teams, wins {team: n}, ties, games, avg_margin {team: points}, last}}."""
    out = {}
    for g in sorted(gs, key=lambda g: (g["season"], g["week"])):
        key = tuple(sorted((g["a"], g["b"])))
        r = out.setdefault(key, {"teams": key, "wins": {key[0]: 0, key[1]: 0}, "ties": 0, "games": 0,
                                 "points": {key[0]: 0.0, key[1]: 0.0}, "last": None})
        r["games"] += 1
        if g["winner"]:
            r["wins"][g["winner"]] += 1
        else:
            r["ties"] += 1
        r["points"][g["a"]] += g["pa"]
        r["points"][g["b"]] += g["pb"]
        r["last"] = g
    for r in out.values():
        a, b = r["teams"]
        r["avg_margin"] = {a: round((r["points"][a] - r["points"][b]) / r["games"], 1),
                           b: round((r["points"][b] - r["points"][a]) / r["games"], 1)}
    return out


def draft_board(root, season):
    """{season, rounds, slots, grid[round][slot] -> pick or None, teams by slot}: each pick with
    the player's current season average (player_baselines) and whether the drafting team still
    has the player."""
    d = root.read_json(f"logs/draft_{season}.json", {}) or {}
    picks = [p for p in d.get("picks") or [] if isinstance(p, dict)]
    if not picks:
        return None
    base = root.read_json("current/player_baselines.json", {}) or {}
    rosters = root.read_json("current/live_rosters.json", {}) or {}
    owner = {e.get("name"): t for t, es in rosters.items() for e in es or []}
    rounds = max(int(p.get("round") or 0) for p in picks)
    slots = max(int(p.get("draft_slot") or 0) for p in picks)
    grid = [[None] * slots for _ in range(rounds)]
    by_slot = {}
    for p in picks:
        r, s = int(p.get("round") or 0), int(p.get("draft_slot") or 0)
        if not (0 < r <= rounds and 0 < s <= slots):
            continue
        b = base.get(p.get("name")) or {}
        cell = {"pick": p.get("pick_no"), "name": p.get("name"), "pid": p.get("player_id"), "pos": p.get("pos"),
                "nfl": p.get("nfl_team"), "team": p.get("team"), "mean": b.get("mean"),
                "kept": owner.get(p.get("name")) == p.get("team"), "now": owner.get(p.get("name"))}
        grid[r - 1][s - 1] = cell
        if r == 1:
            by_slot[s] = p.get("team")
    return {"season": str(season), "rounds": rounds, "slots": slots, "grid": grid,
            "teams": [by_slot.get(s) for s in range(1, slots + 1)]}


def seasons_available(root):
    return sorted({m.group(1) for e in root.logs() for m in [re.match(r"^draft_(\d{4})\.json$", e.get("name") or "")] if m})


def model_records(root):
    """UI-H4: records only a model can keep -- this season so far, from the quoted forecasts
    and the weekly exports.
      least_likely_win  the lowest quoted pre-game chance that won (the league's result, F83)
      comeback          the lowest playoff odds in an earlier forecast for a team now in a playoff
                        place, whose odds have risen since
      collapse          the highest playoff odds in an earlier forecast for a team now out of one,
                        whose odds have fallen since
      champion          the title winner's odds in the season's first forecast, once there is one
    Each is None until the season supplies it."""
    from webui.accuracy import chances_in, quoted_week
    from webui.glance import odds_at
    from webui.standings import table
    out = {"least_likely_win": None, "comeback": None, "collapse": None, "champion": None}
    for w, teams in sorted(week_results(root).items()):
        q = quoted_week(root, w)
        for team, r in teams.items():
            if r.get("h2h_win") is None or float(r["h2h_win"]) < 1:
                continue
            p = (chances_in(q, team) or {}).get("h2h")
            if p is not None and (out["least_likely_win"] is None or float(p) < out["least_likely_win"]["p"]):
                out["least_likely_win"] = {"team": team, "p": float(p), "week": w}
    forecasts = [(w, odds_at(root, w)) for w in root.weeks()]
    forecasts = [(w, f) for w, f in forecasts if f]
    places = table(root)
    inside = {r["team"] for r in places if r["in_places"]}
    for team in {r["team"] for r in places}:
        seen = [(w, f[team]["playoff"]) for w, f in forecasts if (f.get(team) or {}).get("playoff") is not None]
        if not seen:
            continue
        # a comeback or a collapse is a MOVE: the low (or peak) comes before the latest forecast
        # and the odds have since risen (or fallen) -- a team at its highest odds ever, now,
        # is no collapse (2026-09-30)
        now = float(seen[-1][1])
        earlier = seen[:-1]
        low = min(earlier, key=lambda x: x[1]) if earlier else None
        high = max(earlier, key=lambda x: x[1]) if earlier else None
        if team in inside and low and low[1] < now and (out["comeback"] is None or low[1] < out["comeback"]["low"]):
            out["comeback"] = {"team": team, "low": float(low[1]), "week": low[0], "now": now}
        if team not in inside and high and high[1] > now and (out["collapse"] is None or high[1] > out["collapse"]["high"]):
            out["collapse"] = {"team": team, "high": float(high[1]), "week": high[0], "now": now}
    bracket = root.read_json("current/playoff_bracket.json", {}) or {}
    rounds = [r for r in bracket.get("rounds") or [] if isinstance(r, dict) and r.get("winner")]
    final = max(rounds, key=lambda r: r.get("round") or 0) if rounds else None
    if final and final.get("round") == max((r.get("round") or 0) for r in bracket.get("rounds") or []) and forecasts:
        first_w, first = forecasts[0]
        champ = (first.get(final["winner"]) or {}).get("champ")
        out["champion"] = {"team": final["winner"], "p": champ, "week": first_w}
    return out


def season_page(root, season):
    """UI-H6: one archived season -- final standings (the league's own record for the season)
    and every regular-season week's scores with their head-to-head results, paired by
    matchup_id. The archives record no bracket, so no champion is claimed. None when the
    season is not archived."""
    bundle = next((b for s, b in _archives(root) if s == str(season)), None)
    if not bundle:
        return None
    names = {str(k): v for k, v in (bundle.get("roster_map") or {}).items()}
    last = int((bundle.get("settings") or {}).get("playoff_week_start") or 15) - 1
    fs = bundle.get("final_standings") or {}
    standings = sorted(({"team": t, "w": int(v.get("wins") or 0), "l": int(v.get("losses") or 0), "t": int(v.get("ties") or 0),
                         "pts": float(v.get("points_scored") or 0.0)} for t, v in fs.items()),
                       key=lambda r: (-r["w"], -r["pts"]))
    for i, r in enumerate(standings):
        r["rank"] = i + 1
    weeks = []
    for wk in sorted((int(k) for k in (bundle.get("matchups") or {})), key=int):
        if wk > last:
            continue
        rows = bundle["matchups"][str(wk)] or []
        empty = {str(x) for x in bundle.get("unowned_rosters") or []}
        pts = {names.get(str(e.get("roster_id")), "Empty slot" if str(e.get("roster_id")) in empty else f"roster {e.get('roster_id')}"):
               e.get("points") for e in rows}
        by_mid = {}
        for e in rows:
            if e.get("matchup_id") is not None:
                by_mid.setdefault(e["matchup_id"], []).append(names.get(str(e.get("roster_id")), f"roster {e.get('roster_id')}"))
        res = {}
        for pair in by_mid.values():
            if len(pair) == 2 and pts.get(pair[0]) is not None and pts.get(pair[1]) is not None:
                a, b = pair
                res[a] = "W" if pts[a] > pts[b] else ("L" if pts[a] < pts[b] else "T")
                res[b] = {"W": "L", "L": "W", "T": "T"}[res[a]]
        weeks.append({"week": wk, "points": pts, "result": res})
    return {"season": str(season), "standings": standings, "weeks": weeks, "status": bundle.get("status")}

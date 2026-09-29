"""
webui.objects -- the pages a manager thinks in: a team, a player, a week's games
(docs/WEB_UI_ROADMAP.md UI-A1, A2, A3).

Read-only views over what is already on disk, built on the same helpers the other pages
use, so a number here can never disagree with the same number elsewhere: odds through
odds_now / odds_at (UI-E4), results through webui.results (as the league played them,
F83), the model's pre-game quote through accuracy.quoted_week (UI-Q2), and moves through
decisions_report. Nothing here reaches the network or writes anything.

The Sleeper players cache is ~20 MB; it is read once per file modification, trimmed to the
fields a player page shows, and held in memory -- the pattern PlayerIndex already uses.
"""
import datetime as _dt
import json
import math

from webui import render
from webui.accuracy import _first_kickoff, chances_in, quoted_week
from webui.glance import (decisions_report, freshness_report, h2h_report, odds_moves, odds_now, records,
                          roster_vorp, team_hue)
from webui.live import expectations
from webui.results import rescaled_weeks, week_results

CACHE_FIELDS = ("full_name", "position", "team", "number", "age", "college", "height", "weight", "years_exp",
                "depth_chart_position", "depth_chart_order", "injury_status", "injury_body_part", "injury_notes",
                "practice_participation", "practice_description", "news_updated", "status")
_memo = {}


def _cached(root, rel, build):
    """build(root) once per (root, file, modification time)."""
    key = (str(root.data), rel)
    stamp = root.mtime(rel)
    hit = _memo.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    value = build(root)
    _memo[key] = (stamp, value)
    return value


def _players_cache(root):
    def build(r):
        raw = r.read_json("current/sleeper_players_cache.json", {}) or {}
        return {str(pid): {k: c.get(k) for k in CACHE_FIELDS} for pid, c in raw.items() if isinstance(c, dict)}
    return _cached(root, "current/sleeper_players_cache.json", build)


def _jsonl_by_pid(root, rel):
    def build(r):
        out = {}
        try:
            text = r.read_text(rel)
        except FileNotFoundError:
            return out
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("player_id"):
                out.setdefault(str(row["player_id"]), []).append(row)
        return out
    return _cached(root, rel, build)


def _schedule(root):
    s = root.read_json("current/league_schedule.json", []) or []
    return s if isinstance(s, list) else []


def _opponent(pairs, team):
    for p in pairs or []:
        if isinstance(p, (list, tuple)) and team in p:
            return next((t for t in p if t != team), None)
    return None


def _matrix(root, week):
    if not week:
        return {}
    m = root.read_json(f"weeks/week_{int(week):02d}/syndicate_comprehensive_matrix_week_{int(week)}.json", {}) or {}
    return m.get("h2h_win_probability_matrix") or {}


def _chance(matrix, a, b):
    """P(a beats b) from the head-to-head matrix (stored in percent); None when absent."""
    v = (matrix.get(a) or {}).get(b)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else round(v / 100.0, 4)


def _epoch_iso(ms):
    """Sleeper's news_updated (epoch milliseconds) -> ISO UTC; None when absent or unreadable."""
    try:
        return _dt.datetime.fromtimestamp(float(ms) / 1000.0, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _wl(v):
    if v is None:
        return None
    v = float(v)
    return "W" if v >= 1 else ("L" if v <= 0 else "T")


def _current_week(root):
    wk = freshness_report(root)["week"]
    return int(wk) if wk else None


def _teams(root):
    st = root.read_json("current/league_standings.json", {}) or {}
    ro = root.read_json("current/live_rosters.json", {}) or {}
    return sorted(set(st) | set(ro))


def team_for_slug(root, slug):
    return next((t for t in _teams(root) if render.slug(t) == slug), None)


def _ranges(root, week, team):
    """{player name: {p10, p50, p90}} for `team` from that week's player_variance export."""
    if not week:
        return {}
    pv = root.read_json(f"weeks/week_{int(week):02d}/player_variance.json", {}) or {}
    return {p.get("name"): p for p in (pv.get(team) or []) if isinstance(p, dict)}


SEVERITY = {"IR": 0, "Out": 1, "Sus": 1, "PUP": 1, "DNR": 1, "Doubtful": 2, "Questionable": 3, "NA": 4}


def injury_report(root, team):
    """UI-P7: every player on `team`'s roster with an injury designation, most serious first:
    status (IR where on injured reserve), body part, practice participation and its note,
    Sleeper's notes, and when Sleeper last updated the player -- read from the current roster,
    the baselines and Sleeper's players cache, not from a tool record that may be stale."""
    base = root.read_json("current/player_baselines.json", {}) or {}
    cache = _players_cache(root)
    rows = []
    for e in (root.read_json("current/live_rosters.json", {}) or {}).get(team) or []:
        name = e.get("name")
        b = base.get(name) or {}
        pid = str(b.get("player_id")) if b.get("player_id") is not None else None
        c = cache.get(pid) or {} if pid else {}
        status = "IR" if (b.get("on_ir") or e.get("on_ir")) else (b.get("injury_status") or e.get("injury_status") or c.get("injury_status"))
        if not status:
            continue
        rows.append({"name": name, "pid": pid, "pos": b.get("pos") or e.get("pos"), "nfl": b.get("team") or e.get("team"),
                     "status": status, "body_part": c.get("injury_body_part"), "practice": c.get("practice_participation"),
                     "practice_note": c.get("practice_description"), "notes": c.get("injury_notes"),
                     "updated": _epoch_iso(c.get("news_updated")), "mean": b.get("mean")})
    rows.sort(key=lambda r: (SEVERITY.get(r["status"], 5), r["name"] or ""))
    return rows


def _standings_row(root, team):
    """UI-O1: the team's row from the one standings helper League and Home use."""
    from webui.standings import by_team
    return by_team(root).get(team)


def team_report(root, team, my_team):
    """UI-A1: one team -- where it stands, its season week by week, its roster, its moves."""
    now = odds_now(root)
    cur = _current_week(root)
    standings = root.read_json("current/league_standings.json", {}) or {}
    order = sorted(standings.items(), key=lambda kv: (-float(kv[1].get("h2h_wins") or 0), -float(kv[1].get("points_scored") or 0)))
    rank = next((i + 1 for i, (t, _s) in enumerate(order) if t == team), None)
    results = week_results(root)
    scaled = rescaled_weeks(root)
    matrix = _matrix(root, now["week"])
    quotes = {}
    schedule = []
    for i, pairs in enumerate(_schedule(root)):
        wk = i + 1
        opp = _opponent(pairs, team)
        if not opp:
            continue
        mine, theirs = (results.get(wk) or {}).get(team), (results.get(wk) or {}).get(opp)
        row = {"week": wk, "opponent": opp, "current": wk == cur, "result": None, "mine": None, "theirs": None,
               "median": None, "rescored": False, "rescaled": wk in scaled, "quote": None, "p_win": None}
        if mine and theirs and mine.get("h2h_win") is not None:
            if wk not in quotes:
                quotes[wk] = quoted_week(root, wk)
            row.update(result=_wl(mine["h2h_win"]), mine=mine.get("points_scored"), theirs=theirs.get("points_scored"),
                       median=_wl(mine.get("median_win")), rescored=bool(mine.get("rescored")),
                       quote=(chances_in(quotes[wk], team) or {}).get("h2h"))
        else:
            row["p_win"] = _chance(matrix, team, opp)
            if wk == cur:
                quotes[wk] = quotes.get(wk) or quoted_week(root, wk)
                row["quote"] = (chances_in(quotes[wk], team) or {}).get("h2h")
        schedule.append(row)
    base = root.read_json("current/player_baselines.json", {}) or {}
    rv = roster_vorp(root)
    vorp = (rv["players"].get(team) or {}) if rv else {}
    ranges = _ranges(root, now["week"], team)
    roster = []
    for e in (root.read_json("current/live_rosters.json", {}) or {}).get(team) or []:
        b = base.get(e.get("name")) or {}
        rg = ranges.get(e.get("name")) or {}
        roster.append({"name": e.get("name"), "pid": b.get("player_id"), "pos": b.get("pos") or e.get("pos"),
                       "nfl": b.get("team") or e.get("team"), "mean": b.get("mean"), "bye": b.get("bye"),
                       "status": "IR" if (b.get("on_ir") or e.get("on_ir")) else (b.get("injury_status") or e.get("injury_status") or ""),
                       "p10": rg.get("p10"), "p50": rg.get("p50"), "p90": rg.get("p90"),
                       "vorp": (vorp.get(e.get("name")) or {}).get("vorp")})
    roster.sort(key=lambda r: -(float(r["mean"]) if r["mean"] is not None else -1))
    moves = [dict(d, fx=d["effect"].get(team)) for d in decisions_report(root, my_team)["decisions"] if team in d["teams"]]
    return {"team": team, "hue": team_hue(team), "is_mine": team == my_team, "rank": rank, "of": len(order),
            "standing": standings.get(team) or {}, "record": records(root).get(team), "odds": now["teams"].get(team) or {},
            "row": _standings_row(root, team),
            "odds_week": now["week"], "move": odds_moves(root)["teams"].get(team), "prev_week": odds_moves(root)["prev"],
            "schedule": schedule, "roster": roster, "moves": moves, "week": cur, "injuries": injury_report(root, team),
            "h2h": h2h_report(root, my_team, team) if team != my_team else None}


def player_report(root, pid, my_team):
    """UI-A2: one player -- who, available or not, whose, what the model expects this week and
    over the season, and how the projections have fared week by week. None when unknown."""
    pid = str(pid)
    base = root.read_json("current/player_baselines.json", {}) or {}
    name, b = next(((n, e) for n, e in base.items() if isinstance(e, dict) and str(e.get("player_id")) == pid), (None, {}))
    c = _players_cache(root).get(pid) or {}
    name = name or c.get("full_name")
    if not name:
        return None
    owner = next((t for t, es in (root.read_json("current/live_rosters.json", {}) or {}).items()
                  if any(x.get("name") == name for x in es or [])), None)
    now = odds_now(root)
    cur = _current_week(root)
    wk_price = (expectations(root, cur).get(pid) or {}) if cur else {}
    rg = _ranges(root, now["week"], owner).get(name) if owner else None
    kicks = _first_kickoff(root)
    proj = {}
    for r in _jsonl_by_pid(root, "logs/projection_log.jsonl").get(pid, []):
        try:
            wk = int(r.get("week"))
        except (TypeError, ValueError):
            continue
        at, ko = r.get("synced_at") or "", kicks.get(wk)
        if ko is not None and at and at >= ko.strftime("%Y-%m-%dT%H:%M:%SZ"):
            continue                                        # logged after kickoff: knows too much
        if wk not in proj or at >= (proj[wk].get("synced_at") or ""):
            proj[wk] = r
    points = {int(r["week"]): r.get("points") for r in _jsonl_by_pid(root, "logs/first_recorded_scores.jsonl").get(pid, [])
              if str(r.get("week", "")).isdigit()}
    history = [{"week": wk, "projected": (proj.get(wk) or {}).get("sleeper_mean"), "espn": (proj.get(wk) or {}).get("espn_mean"),
                "points": points.get(wk)} for wk in sorted(set(proj) | set(points))]
    moves = [d for d in decisions_report(root, my_team)["decisions"]
             if any(p.get("name") == name for p in d["adds"] + d["drops"])]
    depth = (f"{c.get('depth_chart_position')}{c.get('depth_chart_order')}"
             if c.get("depth_chart_position") and c.get("depth_chart_order") is not None else None)
    return {"pid": pid, "name": name, "pos": b.get("pos") or c.get("position"), "nfl": b.get("team") or c.get("team"),
            "owner": owner, "is_mine": owner == my_team, "hue": team_hue(owner) if owner else None,
            "number": c.get("number"), "age": c.get("age"), "college": c.get("college"), "height": c.get("height"),
            "weight": c.get("weight"), "years_exp": c.get("years_exp"), "depth": depth,
            "status": b.get("injury_status") or c.get("injury_status"), "on_ir": bool(b.get("on_ir")),
            "body_part": c.get("injury_body_part"), "notes": c.get("injury_notes"),
            "practice": c.get("practice_participation"), "practice_note": c.get("practice_description"),
            "news_updated": _epoch_iso(c.get("news_updated")), "season_mean": b.get("mean"), "bye": b.get("bye"),
            "week": cur, "week_mean": wk_price.get("mean") if wk_price.get("source") not in (None, "baseline") else None,
            "week_source": wk_price.get("source") if wk_price.get("source") not in (None, "baseline") else None,
            "range": rg, "odds_week": now["week"], "history": history, "moves": moves}


def league_extras(root, my_team):
    """League, second pass (UI-O2, O4, O11, F5):
      power    each team's rating -- the mean of its row in the head-to-head matrix, its chance
               to beat an average league opponent -- beside its record rank, and the mean chance
               to win the games still to play (the schedule left). The matrix has no standard
               errors, so no rank interval is claimed.
      bracket  the playoff bracket as the sync wrote it ("if it ended today" before the
               playoffs), each seed with the forecast's chance of finishing in that seed
      pending  each pending trade as its sides: what each team would receive"""
    now = odds_now(root)
    matrix = _matrix(root, now["week"])
    cur = _current_week(root) or 1
    sched = _schedule(root)
    standings = root.read_json("current/league_standings.json", {}) or {}
    record_rank = {t: i + 1 for i, (t, _s) in enumerate(sorted(
        standings.items(), key=lambda kv: (-float(kv[1].get("h2h_wins") or 0), -float(kv[1].get("points_scored") or 0))))}
    power = []
    for t in _teams(root):
        vals = [v for v in (_chance(matrix, t, o) for o in (matrix.get(t) or {}) if o != t) if v is not None]
        left = []
        for i, pairs in enumerate(sched):
            if i + 1 < cur:
                continue
            opp = _opponent(pairs, t)
            c = _chance(matrix, t, opp) if opp else None
            if c is not None:
                left.append(c)
        power.append({"team": t, "rating": round(sum(vals) / len(vals), 4) if vals else None,
                      "remaining": round(sum(left) / len(left), 4) if left else None, "games_left": len(left),
                      "record_rank": record_rank.get(t)})
    power.sort(key=lambda r: -(r["rating"] if r["rating"] is not None else -1))
    for i, r in enumerate(power):
        r["rank"] = i + 1 if r["rating"] is not None else None
    b = root.read_json("current/playoff_bracket.json", {}) or {}
    mx = root.read_json(f"weeks/week_{int(now['week']):02d}/syndicate_comprehensive_matrix_week_{int(now['week'])}.json", {}) if now["week"] else {}
    seeds_p = (mx or {}).get("finishing_seed_probabilities") or {}
    bracket = None
    if b.get("seeds"):
        start = int(b.get("playoff_week_start") or 15)
        bracket = {"projected": cur < start, "start": start,
                   "seeds": [{"seed": i + 1, "team": t, "p_seed": (seeds_p.get(t) or {}).get(f"Seed {i + 1}"),
                              "playoff": (now["teams"].get(t) or {}).get("playoff")} for i, t in enumerate(b["seeds"])],
                   "rounds": [r for r in b.get("rounds") or [] if isinstance(r, dict)]}
    pending = []
    for tr in (root.read_json("current/pending_trades.json", {}) or {}).get("trades") or []:
        if not isinstance(tr, dict):
            continue
        sides = [{"team": t, "gets": [p for p in tr.get("players") or [] if p.get("to_team") == t]} for t in tr.get("teams") or []]
        pending.append({"week": tr.get("week"), "sides": sides, "mine": my_team in (tr.get("teams") or [])})
    return {"power": power, "bracket": bracket, "pending": pending, "odds_week": now["week"], "current": cur}


def week_games(root, week, my_team):
    """UI-A3: every game of `week` -- played ones as the league counted them, with the model's
    pre-game quote and an upset flag; the rest with the chance each side wins."""
    sched = _schedule(root)
    week = int(week)
    cur = _current_week(root)
    results = (week_results(root).get(week) or {})
    quote = quoted_week(root, week)
    matrix = _matrix(root, odds_now(root)["week"])
    games = []
    for p in (sched[week - 1] if 0 < week <= len(sched) else []) or []:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            continue
        a, b = p
        ra, rb = results.get(a), results.get(b)
        qa = (chances_in(quote, a) or {}).get("h2h")
        g = {"a": a, "b": b, "mine": my_team in (a, b), "hue_a": team_hue(a), "hue_b": team_hue(b),
             "pts_a": None, "pts_b": None, "winner": None, "rescored": False, "rescaled": week in rescaled_weeks(root),
             "med_a": None, "med_b": None,
             "quote_a": qa, "p_a": None, "p_b": None, "upset": False}
        if ra and rb and ra.get("h2h_win") is not None:
            wa = float(ra["h2h_win"])
            g.update(pts_a=ra.get("points_scored"), pts_b=rb.get("points_scored"),
                     winner=a if wa >= 1 else (b if wa <= 0 else None),
                     rescored=bool(ra.get("rescored") or rb.get("rescored")),
                     med_a=_wl(ra.get("median_win")), med_b=_wl(rb.get("median_win")))
            if qa is not None and g["winner"]:
                g["upset"] = (qa < 0.5) if g["winner"] == a else (qa > 0.5)
        else:
            pa = qa if qa is not None else _chance(matrix, a, b)
            if pa is not None:
                g.update(p_a=round(pa, 4), p_b=round(1 - pa, 4))
        games.append(g)
    games.sort(key=lambda g: not g["mine"])
    return {"week": week, "current": cur, "weeks": list(range(1, len(sched) + 1)), "games": games,
            "played": bool(games) and all(g["winner"] or g["pts_a"] is not None for g in games),
            "quoted_at": (quote or {}).get("logged_at")}

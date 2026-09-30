"""webui.trade_results -- process against result for completed trades (docs/WEB_UI_ROADMAP.md
UI-T4).

The decision log grades every trade before the fact: the paired simulation's change in each
side's playoff odds. Beside it, what happened: what each side's incoming players scored while
STARTED for their new team, against what its outgoing players scored while started for theirs
(TheDadHut's two nets). The two are shown side by side and never merged into one grade -- a
right trade can lose points and a wrong one can win them, which is the whole point.

Started-ness comes from data/current/weekly_lineups.json (the lineups as played, kept by the
sync since UI-L4) and points from logs/first_recorded_scores.jsonl. A week counts from the
trade's own week on, whenever the player was in his new team's starters. Not built: the
replay of each week with the trade undone, which needs counterfactual lineups; the page says
so. Reads only.
"""
import json

from webui.glance import decisions_report


def _scores(root):
    """({(week, id): points}, {(week, name): points}): a player with no baseline is recorded under a
    null id, so the name is the fallback (audit 2026-09-29). First row wins: the log is union-merged."""
    by_id, by_name = {}, {}
    try:
        text = root.read_text("logs/first_recorded_scores.jsonl")
    except (FileNotFoundError, ValueError):
        return by_id, by_name
    for line in text.splitlines():
        try:
            r = json.loads(line)
            wk = int(r["week"])
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(r, dict):
            continue
        if r.get("player_id"):
            by_id.setdefault((wk, str(r["player_id"])), r.get("points"))
        if r.get("name"):
            by_name.setdefault((wk, r["name"]), r.get("points"))
    return by_id, by_name


def _started(lineups):
    """{(week, team): set of starter ids}."""
    out = {}
    for key, teams in (lineups or {}).items():
        wk = str(key).rsplit("_", 1)[-1]
        if not wk.isdigit():
            continue
        for team, lu in (teams or {}).items():
            out[(int(wk), team)] = {str(p) for p in (lu or {}).get("starters") or []}
    return out


def trade_results(root):
    """[{id, week, created, sides: {team: {got, gave, got_pts, gave_pts, net, decision,
    decision_se}}}], newest first, for every trade in the decision log."""
    started = _started(root.read_json("current/weekly_lineups.json", {}) or {})
    scores, by_name = _scores(root)
    weeks = sorted({w for w, _t in started})

    def while_started(pid, team, from_week, name=None):
        pts, n = 0.0, 0
        for wk in weeks:
            if wk >= from_week and pid in started.get((wk, team), set()):
                got = scores.get((wk, pid))
                pts += float(got if got is not None else (by_name.get((wk, name)) or 0.0))
                n += 1
        return round(pts, 2), n

    out = []
    for d in decisions_report(root, None)["decisions"]:
        if d.get("type") != "trade" or d.get("week") is None:
            continue
        wk = int(d["week"])
        teams = [t for t in d.get("teams") or []]
        moves = [(p, p.get("to")) for p in d.get("adds") or [] if p.get("to")]
        sides = {}
        for team in teams:
            got, gave = [], []
            for p, to in moves:
                if not p.get("pid"):
                    continue
                if to == team:
                    pts, n = while_started(p["pid"], team, wk, p.get("name"))
                    got.append({"name": p.get("name"), "pos": p.get("pos"), "pts": pts, "weeks_started": n})
                elif len(teams) == 2:                              # two sides: what one received, the other gave
                    pts, n = while_started(p["pid"], to, wk, p.get("name"))
                    gave.append({"name": p.get("name"), "pos": p.get("pos"), "to": to, "pts": pts, "weeks_started": n})
            fx = (d.get("effect") or {}).get(team) or {}
            got_pts = round(sum(g["pts"] for g in got), 2)
            gave_pts = round(sum(g["pts"] for g in gave), 2)
            sides[team] = {"got": got, "gave": gave, "got_pts": got_pts, "gave_pts": gave_pts,
                           "net": round(got_pts - gave_pts, 2), "decision": fx.get("playoff"), "decision_se": fx.get("playoff_se")}
        out.append({"id": d.get("id"), "week": wk, "created": d.get("created"), "sides": sides,
                    "weeks_since": [w for w in weeks if w >= wk]})
    out.sort(key=lambda t: t.get("created") or "", reverse=True)
    return out

"""webui.decision_quality -- lineup calls judged before the games (docs/WEB_UI_ROADMAP.md UI-L4).

Every open-source "coaching efficiency" is hindsight: points scored over the best lineup with
the week's results in hand. It punishes a right call that did not work out. This project keeps
the pre-game projections, so it can separate the decision from the dice. For each completed
week:

  before the games   expected points of the lineup started, over the expected points of the
                     best lineup that roster could have started -- both priced on the last
                     projection logged before the week's first kickoff
  in hindsight       the same with the points actually scored, labelled hindsight
  not a decision     the hindsight gap minus the expected gap: what the dice did

The lineups come from data/current/weekly_lineups.json (the sync keeps each week's lineups as
the league played them), the projections from logs/projection_log.jsonl, and the points from
logs/first_recorded_scores.jsonl. Limits, said on the page:
- A player counts as available if the week's pre-game projection priced him, so a player
  already ruled out is usually out of the pool, but not always.
- A starter with no pre-game projection leaves the week unjudged rather than guessed.
Nothing here re-solves anything with the results in hand except the hindsight line, which says
so. Reads only.
"""
import json

from fantasy_sim.config import REQUIRED_STARTING_SLOTS

from webui.accuracy import _first_kickoff
from webui.trade import _assign


def _total(pool, slots):
    return round(sum(mean for _i, _n, mean in _assign(pool, None, slots)), 2)


def judge(started, roster, projected, scored, slots=REQUIRED_STARTING_SLOTS):
    """One week: `started` ids, `roster` {id: entry with pos/slots}, `projected` and `scored`
    {id: points}. See the module docstring for the three numbers."""
    unpriced = [s for s in started if projected.get(s) is None]
    out = {"unpriced": unpriced, "started_exp": None, "best_exp": None, "before": None, "expected_left": None,
           "started_act": None, "best_act": None, "hindsight_left": None, "not_a_decision": None}
    if unpriced or not started:
        return out
    priced = {pid: dict(e, mean=projected[pid]) for pid, e in roster.items() if projected.get(pid) is not None}
    actual = {pid: dict(e, mean=float(scored.get(pid) or 0.0)) for pid, e in roster.items()}
    started_exp = round(sum(projected[s] for s in started), 2)
    best_exp = max(_total(priced, slots), started_exp)       # the lineup started is itself one the roster could start
    started_act = round(sum(float(scored.get(s) or 0.0) for s in started), 2)
    best_act = max(_total(actual, slots), started_act)
    expected_left = round(best_exp - started_exp, 2)
    hindsight_left = round(best_act - started_act, 2)
    out.update({"started_exp": started_exp, "best_exp": best_exp,
                "before": round(started_exp / best_exp, 4) if best_exp else None,
                "expected_left": expected_left, "started_act": started_act, "best_act": best_act,
                "hindsight_left": hindsight_left, "not_a_decision": round(hindsight_left - expected_left, 2)})
    return out


def _rows(root, rel):
    try:
        text = root.read_text(rel)
    except (FileNotFoundError, ValueError):
        return []
    out = []
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def season(root, team, slots=REQUIRED_STARTING_SLOTS):
    """Every completed week with a lineup on file for `team`, judged -- oldest first."""
    lineups = root.read_json("current/weekly_lineups.json", {}) or {}
    kicks = _first_kickoff(root)
    proj, first = {}, {}
    for r in _rows(root, "logs/projection_log.jsonl"):
        try:
            wk, pid = int(r["week"]), str(r["player_id"])
        except (KeyError, TypeError, ValueError):
            continue
        at, ko = r.get("synced_at") or "", kicks.get(wk)
        if ko is not None and at and at >= ko.strftime("%Y-%m-%dT%H:%M:%SZ"):
            continue                                            # logged after kickoff: knows too much
        if (wk, pid) not in first or at >= first[(wk, pid)]:
            first[(wk, pid)] = at
            proj[(wk, pid)] = r.get("sleeper_mean")
    scores = {}
    for r in _rows(root, "logs/first_recorded_scores.jsonl"):
        try:
            key = (int(r["week"]), str(r["player_id"]))
        except (KeyError, TypeError, ValueError):
            continue
        scores.setdefault(key, r.get("points"))                 # first row wins: the log is union-merged
    cache = root.read_json("current/sleeper_players_cache.json", {}) or {}
    base = {str(e.get("player_id")): e for e in (root.read_json("current/player_baselines.json", {}) or {}).values()
            if isinstance(e, dict) and e.get("player_id")}

    def entry(pid):
        c = cache.get(pid) or {}
        if c.get("fantasy_positions"):
            return {"pos": c.get("position"), "slots": list(c["fantasy_positions"])}
        b = base.get(pid) or {}
        return {"pos": b.get("pos") or c.get("position"), "slots": list(b.get("slots") or [])}

    out = []
    for key in sorted(lineups, key=lambda k: int(str(k).rsplit("_", 1)[-1]) if str(k).rsplit("_", 1)[-1].isdigit() else 0):
        lu = (lineups.get(key) or {}).get(team)
        if not lu or not str(key).rsplit("_", 1)[-1].isdigit():
            continue
        wk = int(str(key).rsplit("_", 1)[-1])
        players = [str(p) for p in lu.get("players") or []]
        roster = {pid: entry(pid) for pid in players}
        projected = {pid: proj.get((wk, pid)) for pid in players if proj.get((wk, pid)) is not None}
        scored = {pid: scores.get((wk, pid)) for pid in players if scores.get((wk, pid)) is not None}
        row = judge([str(p) for p in lu.get("starters") or []], roster, projected, scored, slots)
        row["week"] = wk
        out.append(row)
    return out

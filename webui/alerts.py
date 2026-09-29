"""webui.alerts -- the alerts beside the kickoff one (docs/WEB_UI_ROADMAP.md UI-R5).

Each is opt-in on the page, says why and how much, and -- like the kickoff alert -- fires only
while a page is open (there is no server push). Every alert carries a stable `key`: the same
change gives the same key on every read, so the page fires it once and never on reload.

  designation  one of the owner's players has a new status: the sync's designations log
               holds his two newest statuses and they differ, the newer recorded within
               RECENT_DAYS (older changes are history, not news)
  odds         the owner's playoff odds moved by more than two standard errors between the
               current forecast and the one before it (their standard errors combined)
  waiver       the next daily waiver run; the page fires it thirty minutes before `at`

The fourth trigger -- the model's lineup would move the chance of winning by at least two
points -- needs the live read, so the page decides it from the snapshot's plan (UI-L1).
"""
import datetime as _dt
import json
import math

from webui.glance import odds_at, odds_now
from webui.players_page import next_waiver_run
from webui.render import fse

RECENT_DAYS = 3           # a display choice: a designation older than this is not news


def _parse(t):
    try:
        return _dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _designations(root, my_team, now):
    try:
        with open(root.resolve_file("logs/designations.jsonl"), encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    except (FileNotFoundError, ValueError):
        return []
    by_pid = {}
    for r in rows:
        if isinstance(r, dict) and r.get("team") == my_team and r.get("player_id") and _parse(r.get("recorded_at")):
            by_pid.setdefault(str(r["player_id"]), []).append(r)
    out = []
    for pid, rs in by_pid.items():
        rs.sort(key=lambda r: r["recorded_at"])
        if len(rs) < 2:
            continue
        was, new = rs[-2].get("injury_status"), rs[-1].get("injury_status")
        at = _parse(rs[-1]["recorded_at"])
        if was == new or (now - at).total_seconds() > RECENT_DAYS * 86400:
            continue
        name = rs[-1].get("name") or pid
        out.append({"kind": "designation", "key": f"designation:{pid}:{new}:{rs[-1]['recorded_at']}",
                    "title": f"{name}: {new or 'no longer designated'}",
                    "body": f"was {was or 'not designated'} · recorded {rs[-1]['recorded_at'][:16].replace('T', ' ')} UTC"})
    return out


def _odds(root, my_team):
    now = odds_now(root)
    w = now["week"]
    prev = max((x for x in root.weeks() if w and x < w and odds_at(root, x).get(my_team)), default=None)
    if not w or prev is None:
        return []
    a, b = odds_at(root, prev).get(my_team) or {}, now["teams"].get(my_team) or {}
    if a.get("playoff") is None or b.get("playoff") is None or a.get("playoff_se") is None or b.get("playoff_se") is None:
        return []
    d = float(b["playoff"]) - float(a["playoff"])
    se = math.sqrt(float(a["playoff_se"]) ** 2 + float(b["playoff_se"]) ** 2)
    if not se or abs(d) <= 2 * se:
        return []
    return [{"kind": "odds", "key": f"odds:{prev}:{w}",
             "title": f"Playoff odds {'up' if d > 0 else 'down'} {abs(d):.1f} points",
             "body": f"{float(a['playoff']):.1f}% in the week-{prev} forecast, {float(b['playoff']):.1f}% in week {w}'s ({fse(se)} points)"}]


def alerts(root, my_team, now=None):
    now = now or _dt.datetime.now(_dt.timezone.utc)
    at = next_waiver_run(now)
    waiver = [{"kind": "waiver", "key": f"waiver:{at}", "at": at, "title": "Waiver run in 30 minutes",
               "body": "claims process at 9:00 am Pacific"}] if at else []
    return _designations(root, my_team, now) + _odds(root, my_team) + waiver

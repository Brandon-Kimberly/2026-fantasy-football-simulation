"""webui.picks -- the owner's picks against the model's (docs/WEB_UI_ROADMAP.md UI-Q3; Decision 8).

Before a week's first kickoff the owner gives each game a chance for its first-listed team;
after the week both the owner's chances and the model's quoted ones (webui.accuracy's
canonical pre-kickoff row, the one Accuracy scores) are scored with Brier -- the mean squared
distance between the chance and what happened (1 won, 0 lost, 0.5 a tie), lower is better.

Stored ONLY at data/local/webui/picks.json (the owner's ruling of 2026-09-29): never data/logs,
never tracked, beside the site's own settings. A week locks at its first kickoff, so a pick
can never be made knowing the result. Keys are "<team a>|<team b>" in the schedule's order.
"""
import datetime as _dt
import json
import os


def _path(root):
    return os.path.join(root.local, "webui", "picks.json")


def load(root):
    try:
        with open(_path(root), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {"weeks": {}}
    except (FileNotFoundError, ValueError):
        return {"weeks": {}}


def locked(root, week):
    from webui.glance import kickoff_report
    return bool(kickoff_report(root, int(week)).get("started"))


def save(root, week, chances, now_ok=False):
    """Records `chances` ({"a|b": 0..1}) for `week`. False, and nothing written, once the week
    has kicked off (`now_ok` is for tests scoring a planted past week)."""
    if not now_ok and locked(root, week):
        return False
    d = load(root)
    wk = d.setdefault("weeks", {}).setdefault(str(int(week)), {})
    for k, v in chances.items():
        if v is None:
            wk.pop(k, None)
        else:
            wk[k] = max(0.0, min(1.0, float(v)))
    d["saved_at"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(_path(root)), exist_ok=True)
    tmp = _path(root) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)
    os.replace(tmp, _path(root))
    return True


def scores(root):
    """Each picked, played week: the owner's Brier and the model's over the same games, and the
    season's totals."""
    from webui.accuracy import quoted_week
    from webui.results import week_results
    results = week_results(root)
    weeks, all_mine, all_model = [], [], []
    for w, games in sorted(((int(k), v) for k, v in (load(root).get("weeks") or {}).items()), key=lambda x: x[0]):
        res = results.get(w) or {}
        q = quoted_week(root, w) or {}
        quotes = {}
        for m in q.get("matchups") or []:
            quotes[(m.get("a"), m.get("b"))] = m.get("p_a")
            if m.get("p_b") is not None:
                quotes[(m.get("b"), m.get("a"))] = m.get("p_b")
        mine, model = [], []
        for key, p in games.items():
            a, _, b = key.partition("|")
            y = (res.get(a) or {}).get("h2h_win")
            qa = quotes.get((a, b))
            if y is None or qa is None:
                continue
            y = float(y)
            mine.append((float(p) - y) ** 2)
            model.append((float(qa) - y) ** 2)
        if mine:
            weeks.append({"week": w, "n": len(mine), "mine": sum(mine) / len(mine), "model": sum(model) / len(model)})
            all_mine += mine
            all_model += model
    total = {"n": len(all_mine), "mine": sum(all_mine) / len(all_mine) if all_mine else None,
             "model": sum(all_model) / len(all_model) if all_model else None}
    return {"weeks": weeks, "total": total}

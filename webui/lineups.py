"""webui.lineups -- the matchup record's lineups side by side (docs/WEB_UI_ROADMAP.md UI-L2).

scripts.matchup_lineup builds several lineups (most expected points, safe, stacked, best
chance against the opponent) and prices each against the opponent AND against the median
(p_beat_opponent, p_beat_median). Three objectives matter in a median league:

  points    the lineup with the most expected points (max_mean)
  opponent  the lineup with the best chance to beat this week's opponent
  median    of the lineups the tool built, the best chance to beat the median -- a choice
            among its candidates, not a separately optimised lineup; the page says so

A tie goes to the points lineup, so "all three agree" is not broken by a coin-flip between
identical candidates. Rows follow the points lineup's slot order; inside a slot the names
are sorted, so the same players in a different order agree.
"""
from webui.glance import _newest


def _pick(cons, key, first):
    return max(cons, key=lambda k: ((cons[k].get(key) if cons[k].get(key) is not None else -1.0), k == first))


def objectives(record):
    cons = {k: v for k, v in ((record or {}).get("constructions") or {}).items() if isinstance(v, dict) and v.get("lineup")}
    if not cons:
        return {"columns": [], "rows": [], "agree": None}
    points = "max_mean" if "max_mean" in cons else max(cons, key=lambda k: cons[k].get("mean") or 0.0)
    picks = (("points", points), ("opponent", _pick(cons, "p_beat_opponent", points)),
             ("median", _pick(cons, "p_beat_median", points)))
    columns = []
    for objective, key in picks:
        c = cons[key]
        columns.append({"objective": objective, "key": key, "mean": c.get("mean"), "p_opponent": c.get("p_beat_opponent"),
                        "p_median": c.get("p_beat_median"), "se": c.get("se"),
                        "by_slot": _by_slot(c["lineup"])})
    order = []
    for e in cons[points]["lineup"]:
        if e.get("slot") not in order:
            order.append(e.get("slot"))
    for col in columns:
        for s in col["by_slot"]:
            if s not in order:
                order.append(s)
    rows = []
    for slot in order:
        depth = max(len(col["by_slot"].get(slot, [])) for col in columns)
        for i in range(depth):
            names = [(col["by_slot"].get(slot) or [None] * depth)[i] if i < len(col["by_slot"].get(slot, [])) else None
                     for col in columns]
            rows.append({"slot": slot, "names": names, "differs": len(set(names)) > 1})
    return {"columns": columns, "rows": rows, "agree": not any(r["differs"] for r in rows)}


def _by_slot(lineup):
    out = {}
    for e in lineup:
        out.setdefault(e.get("slot"), []).append(e.get("name"))
    return {s: sorted(n for n in names if n) for s, names in out.items()}


def latest(root, week, my_team):
    """The newest matchup_lineup record for `week` about the owner's team, or None."""
    if not week or int(week) not in root.decision_weeks():
        return None
    dec = root.decisions(int(week))
    newest = dec["canonical"] + dec["archive"]
    newest.sort(key=lambda e: (e["stamp"] or "", e["name"]), reverse=True)
    e = _newest(newest, "matchup")
    rec = root.read_json(e["rel"], {}) if e else {}
    if not rec or (rec.get("team") and rec.get("team") != my_team):
        return None
    return dict(objectives(rec), opponent=rec.get("opponent"), stamp=e["stamp"])

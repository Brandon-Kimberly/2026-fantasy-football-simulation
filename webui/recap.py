"""
webui.recap -- the week in review (docs/WEB_UI_ROADMAP.md UI-R1, R2), shown on a played
week's Matchups page.

  awards     upset of the week (the winner the model gave the least chance before kickoff),
             the lowest winning score, the highest losing score, and the closest miss against
             the median -- results as the league counted them (webui.results)
  movers     the teams whose playoff odds moved most in the next forecast (odds_moves)
  best_move  the week's best-graded move by its paired evaluation
  surprises  the players who most beat their own last pre-kickoff projection, DIFFERENCED
             AGAINST THE LEAGUE: the week's mean surprise across every projected player is
             subtracted, because the model has a known bias and an absolute surprise would
             mostly measure it. Points are as first recorded.

Nothing here is called luck and nothing is combined into one score (docs/LUCK_LEDGER.md).
A week with no results has no review. Reads only.
"""
from webui.accuracy import _first_kickoff
from webui.glance import decisions_report, odds_moves
from webui.objects import _jsonl_by_pid, week_games
from webui.results import rescaled_weeks, week_results

EMPTY = {"awards": [], "surprises": [], "movers": [], "best_move": None, "league_surprise": None}


def _surprises(root, week, top=5):
    ko = _first_kickoff(root).get(week)
    cutoff = ko.strftime("%Y-%m-%dT%H:%M:%SZ") if ko else None
    proj = {}
    for pid, rows in _jsonl_by_pid(root, "logs/projection_log.jsonl").items():
        best = None
        for r in rows:
            if str(r.get("week")) != str(week) or r.get("sleeper_mean") is None:
                continue
            at = r.get("synced_at") or ""
            if cutoff and at >= cutoff:
                continue
            if best is None or at >= (best.get("synced_at") or ""):
                best = r
        if best:
            proj[pid] = best
    out = []
    for pid, rows in _jsonl_by_pid(root, "logs/first_recorded_scores.jsonl").items():
        pts = next((r.get("points") for r in rows if str(r.get("week")) == str(week)), None)
        if pid in proj and pts is not None:
            p = proj[pid]
            out.append({"pid": pid, "name": p.get("name"), "pos": p.get("pos"), "projected": float(p["sleeper_mean"]),
                        "points": float(pts), "raw": float(pts) - float(p["sleeper_mean"])})
    if not out:
        return [], None
    mean = sum(r["raw"] for r in out) / len(out)
    for r in out:
        r["over_league"] = round(r["raw"] - mean, 2)
    out.sort(key=lambda r: -r["over_league"])
    return out[:top], round(mean, 2)


def week_recap(root, week, my_team):
    week = int(week)
    res = week_results(root).get(week) or {}
    if not any((r or {}).get("h2h_win") is not None for r in res.values()):
        return dict(EMPTY)
    games = [g for g in week_games(root, week, my_team)["games"] if g["winner"] or g["pts_a"] is not None]
    awards = []
    quoted = []
    for g in games:
        if g["winner"] and g["quote_a"] is not None:
            q = g["quote_a"] if g["winner"] == g["a"] else round(1 - g["quote_a"], 4)
            quoted.append((q, g))
    if quoted:
        q, g = min(quoted, key=lambda x: x[0])
        if q < 0.5:
            awards.append({"key": "upset", "label": "Upset of the week", "team": g["winner"], "quote": q,
                           "opponent": g["b"] if g["winner"] == g["a"] else g["a"]})
    wins, losses = [], []
    for g in games:
        if not g["winner"]:
            continue
        loser = g["b"] if g["winner"] == g["a"] else g["a"]
        wp = g["pts_a"] if g["winner"] == g["a"] else g["pts_b"]
        lp = g["pts_b"] if g["winner"] == g["a"] else g["pts_a"]
        if wp is None or lp is None or wp < lp:
            # the box score (re-scored since) names the other winner: "lost with 148.5 to a team
            # that scored 144.2" is the contradiction a points award must not print (audit 2026-09-29)
            continue
        wins.append((wp, g["winner"], loser))
        losses.append((lp, loser, g["winner"]))
    if wins:
        p, t, o = min(wins)
        awards.append({"key": "low_win", "label": "Won with the week's lowest winning score", "team": t, "points": p, "opponent": o})
    if losses:
        p, t, o = max(losses)
        awards.append({"key": "high_loss", "label": "Lost with the week's best losing score", "team": t, "points": p, "opponent": o})
    actuals = root.read_json("current/weekly_actuals.json", {}) or {}
    cut = (actuals.get(f"week_{week}") or {}).get("median_cutoff")
    missed = [(float(r.get("points_scored") or 0.0), t) for t, r in res.items() if r and r.get("median_win") is not None
              and float(r["median_win"]) <= 0
              and (cut is None or float(r.get("points_scored") or 0.0) < float(cut))]   # not a re-scored contradiction
    if missed:
        p, t = max(missed)
        awards.append({"key": "median_miss", "label": "Closest miss against the median", "team": t, "points": p,
                       "cut": cut, "short": round(float(cut) - p, 2) if cut is not None else None})
    moves = odds_moves(root, week + 1) if (week + 1) in root.weeks() else {"rows": []}
    movers = sorted([r for r in moves.get("rows") or [] if r.get("d_playoff") is not None],
                    key=lambda r: -abs(r["d_playoff"]))[:3]
    best = None
    for d in decisions_report(root, my_team)["decisions"]:
        fx = d.get("actor_effect")
        if d.get("week") == week and fx and fx.get("playoff") is not None and (best is None or fx["playoff"] > best["actor_effect"]["playoff"]):
            best = d
    surprises, mean = _surprises(root, week)
    return {"awards": awards, "surprises": surprises, "movers": movers, "best_move": best, "league_surprise": mean,
            "rescaled": week in rescaled_weeks(root)}


def chat_text(rv, week):
    """UI-R3: the week in review as plain text for the league chat -- the awards, the movers,
    the players who most beat their projection. Pseudonymous here; the page applies the
    owner's overlay before copying, and nothing is written."""
    lines = [f"Week {week} in review"]
    for a in rv.get("awards") or []:
        if a["key"] == "upset":
            tail = f"beat {a['opponent']}, given {round(100 * a['quote'])}% before kickoff"
        elif a["key"] == "median_miss":
            tail = f"{a['points']:.1f} points" + (f", {a['short']:.{2 if a['short'] < 1 else 1}f} short of the {a['cut']:.1f} median" if a.get("short") is not None else "")
        elif a["key"] == "low_win":
            tail = f"{a['points']:.1f} points, enough to beat {a['opponent']}"
        else:
            tail = f"{a['points']:.1f} points, still a loss to {a['opponent']}"
        lines.append(f"{a['label']}: {a['team']} -- {tail}")
    movers = [m for m in rv.get("movers") or [] if m.get("d_playoff") is not None]
    if movers:
        lines.append("Playoff odds moved most: " + ", ".join(f"{m['team']} {m['d_playoff']:+.1f}" for m in movers))
    for p in (rv.get("surprises") or [])[:3]:
        lines.append(f"{p['name']} scored {p['points']:.1f} against a projection of {p['projected']:.1f}")
    return "\n".join(lines)

"""webui.standings -- one standings row per team, for every page (docs/WEB_UI_ROADMAP.md UI-O1).

League, Home and the team page render from `table`, so a number means the same thing
wherever it appears. In this league a week is two games -- one opponent and the median -- and
the league's own standings fold them into one total. A row carries:

  wins, points_for   the league's standings, the authority on both (F84)
  h2h, median,       the two halves and their sum, from the results as the league counted
  combined           them (F83); None unless they add up to the league's total (UI-F4)
  all_play           each played week against every other team's score, ties counted
  points_against     the league's own total (league_standings points_against, Sleeper's
                     fpts_against, audit 2026-09-29); a standings file written before the sync
                     kept it falls back to each opponent's box score, from the schedule pairs
  streak             the run as the league counts it ("W2", "L1"): both games a week, head-to-head
                     then median, the order of Sleeper's own record string -- so it reads what
                     Sleeper's streak reads (a head-to-head-only run disagreed; audit 2026-09-29)
  gb / cushion       games back of fourth for a team outside the places; for one inside, wins
                     over the first team out. Level on wins still loses on points (the
                     league's tiebreak), so 0 back is not "in"
  playoff, champ     THE current forecast's odds (odds_now), in percent, with the move since
  move               the forecast before (odds_moves)
  mark               a clinch or elimination proven from the remaining schedule, or a
                     simulated near-certainty (webui.outcomes.markers, UI-O10), when that
                     forecast wrote its per-season record
  rescored           any counted week is one Sleeper now re-scores: all-play and points
                     against use its box scores, which the as-played record does not replace
                     (it carries results only)
"""
from webui.glance import _wlt, odds_moves, odds_now, records
from webui.outcomes import PLAYOFF_SPOTS
from webui.results import rescaled_weeks, week_results


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _streak(results):
    if not results:
        return None
    word = {1.0: "W", 0.0: "L", 0.5: "T"}
    last, n = results[-1], 0
    for r in reversed(results):
        if r != last:
            break
        n += 1
    return f"{word.get(last, '?')}{n}"


def _marks(root, week, teams):
    from webui import outcomes
    o = outcomes.load(root, at_most=week)
    if o is None or o.week != week or set(o.teams) != set(teams):
        return {}
    return outcomes.markers(o, {t: v.get("banked") for t, v in teams.items()})


def table(root):
    """Every team's row, in the league's order (wins, then points for)."""
    st = root.read_json("current/league_standings.json", {}) or {}
    order = sorted(st.items(), key=lambda kv: (-(_num(kv[1].get("h2h_wins")) or 0), -(_num(kv[1].get("points_scored")) or 0)))
    rec = records(root)
    results = week_results(root)
    sched = root.read_json("current/league_schedule.json", []) or []
    scaled = rescaled_weeks(root)
    now = odds_now(root)
    moves = odds_moves(root)["teams"] if now["week"] else {}
    marks = _marks(root, now["week"], now["teams"]) if now["week"] else {}

    all_play = {t: [0, 0, 0] for t, _s in order}
    against = {t: None for t, _s in order}
    h2h_seq = {t: [] for t, _s in order}
    rescored = False
    for n, teams in sorted(results.items()):
        pts = {t: _num(r.get("points_scored")) for t, r in teams.items()}
        pts = {t: p for t, p in pts.items() if p is not None}
        if pts and n in scaled:
            rescored = True
        for t, p in pts.items():
            if t not in all_play:
                continue
            for u, q in pts.items():
                if u != t:
                    all_play[t][0 if p > q else (1 if p < q else 2)] += 1
        for pair in (sched[n - 1] if 0 < n <= len(sched) else []):
            if len(pair) == 2 and pair[0] in pts and pair[1] in pts:
                for a, b in (pair, pair[::-1]):
                    if a in against:
                        against[a] = (against[a] or 0.0) + pts[b]
        for t, r in teams.items():
            h = _num(r.get("h2h_win"))
            if h is not None and t in h2h_seq:
                h2h_seq[t].append(h)
                m = _num(r.get("median_win"))
                if m is not None:
                    h2h_seq[t].append(m)

    # the league's own points against when the sync kept it for every team: a sum of box scores
    # re-scored under later settings disagrees with what the league counted (7 of 8 teams did)
    pa_counted = bool(order) and all(_num(s.get("points_against")) is not None for _t, s in order)
    if pa_counted:
        against = {t: _num(s.get("points_against")) for t, s in order}
    wins = [(_num(s.get("h2h_wins")) or 0.0) for _t, s in order]
    fourth = wins[PLAYOFF_SPOTS - 1] if len(wins) >= PLAYOFF_SPOTS else None
    fifth = wins[PLAYOFF_SPOTS] if len(wins) > PLAYOFF_SPOTS else None
    rows = []
    for i, (team, s) in enumerate(order):
        r = rec.get(team) or {}
        agrees = bool(r) and r.get("agrees", False)
        fc = now["teams"].get(team) or {}
        w = wins[i]
        inside = i < PLAYOFF_SPOTS
        ap = all_play[team]
        rows.append({
            "team": team, "rank": i + 1, "wins": _num(s.get("h2h_wins")), "points_for": _num(s.get("points_scored")),
            "faab": _num(s.get("remaining_faab")),
            "h2h": r.get("h2h") if agrees else None, "median": r.get("median") if agrees else None,
            "combined": r.get("combined") if agrees else None,
            "all_play": _wlt(*ap) if sum(ap) else None,
            "points_against": round(against[team], 2) if against[team] is not None else None,
            "pa_counted": pa_counted,                               # the league's own figure, not box scores
            "streak": _streak(h2h_seq[team]),
            "in_places": inside,
            "gb": None if inside or fourth is None else fourth - w,
            "cushion": (w - fifth) if inside and fifth is not None else None,
            "playoff": fc.get("playoff"), "playoff_se": fc.get("playoff_se"), "champ": fc.get("champ"),
            "move": (moves.get(team) or {}).get("d_playoff"),
            "mark": marks.get(team) if (marks.get(team) or {}).get("label") or (marks.get(team) or {}).get("destiny") else None,
            "rescored": rescored,
        })
    return rows


def by_team(root):
    return {r["team"]: r for r in table(root)}

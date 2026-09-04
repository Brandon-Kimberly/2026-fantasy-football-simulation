"""
Season retrospective: why did a season's record come out the way it did? Four measurements
on a persisted season bundle (storage.season_log_file, sync.ingest_season), reported
SEPARATELY and never collapsed into a verdict:

  1. schedule_luck -- real all-play expected wins (each week: opponents outscored / (n-1),
     ties half) versus actual H2H wins. This is the historical-all-play computation the
     in-engine schedule_luck_index's KNOWN LIMITATION comment says it lacks: the engine's
     version compares spans of SIMULATED seasons; this one is computed from realized scores.
  2. lineup_efficiency -- actual started points versus the optimal lineup on that week's
     real roster and realized per-player points, via the engine's Hungarian solver with the
     slot list read FROM THE BUNDLE (never hardcoded, so the same code runs on any season).
  3. absences -- a DNP PROXY: rostered player-weeks scoring exactly 0.0. A player who
     genuinely played to a 0.0 counts as absent under this proxy (rare in this scoring but
     not impossible); stated in absence_note. starter_zeros counts the subset that was
     actually STARTED -- absences that directly cost lineup points.
  4. high_scorer_losses -- losses where the opponent posted that week's league-high score:
     games that were probably unwinnable regardless of lineup choices.

Scoring-format context is carried in context_note: a pure-H2H season (Sleeper
league_average_match == 0, this league's real 2025 rules) has ONE binary decision per week,
so records carry structurally higher variance than the current hybrid format's two
decisions -- any luck finding must be read against that.

Positions for the optimal-lineup solve come from a caller-supplied {player_id: [positions]}
map (the script builds it from the player cache); today's position labels stand in for the
season's, and a player with no known position cannot be seated (counted in
position_unknown, which overstates nobody's optimal).
"""
from fantasy_sim.simulation import FantasySimulationEngine


def _regular_season_weeks(bundle):
    cutoff = (bundle.get("settings") or {}).get("playoff_week_start")
    weeks = sorted(int(w) for w in (bundle.get("matchups") or {}))
    return [w for w in weeks if cutoff is None or w < int(cutoff)]


def season_retrospective(bundle, player_positions):
    """The four measurements, per team, plus the format-context and proxy notes."""
    roster_map = {str(k): v for k, v in (bundle.get("roster_map") or {}).items()}
    teams = list(dict.fromkeys(roster_map.values()))
    weeks = _regular_season_weeks(bundle)
    slots = [sl for sl in (bundle.get("roster_positions") or []) if sl != "BN"]

    exp_wins = {t: 0.0 for t in teams}
    act_wins = {t: 0.0 for t in teams}
    losses = {t: 0 for t in teams}
    hs_losses = {t: 0 for t in teams}
    season_pts = {t: 0.0 for t in teams}
    lineup = {t: {"actual": 0.0, "optimal": 0.0, "weeks": []} for t in teams}
    absent = {t: {"player_weeks": 0, "zeros": 0, "starter_zeros": 0} for t in teams}
    unknown_pos = set()

    for wk in weeks:
        entries = bundle["matchups"][str(wk)]
        by_team = {}
        for e in entries:
            t = roster_map.get(str(e.get("roster_id")))
            if t is None:
                continue
            by_team[t] = e
            season_pts[t] += float(e.get("points") or 0.0)

        scores = {t: float(e.get("points") or 0.0) for t, e in by_team.items()}
        week_high = max(scores.values()) if scores else 0.0
        # all-play: opponents outscored this week, ties half
        for t, sc in scores.items():
            others = [v for u, v in scores.items() if u != t]
            if others:
                exp_wins[t] += (sum(sc > v for v in others) + 0.5 * sum(sc == v for v in others)) / len(others)
        # H2H via matchup_id pairing
        by_mid = {}
        for t, e in by_team.items():
            by_mid.setdefault(e.get("matchup_id"), []).append(t)
        for mid, pair in by_mid.items():
            if len(pair) != 2:
                continue
            a, b = pair
            if scores[a] == scores[b]:
                act_wins[a] += 0.5; act_wins[b] += 0.5
            else:
                w, l = (a, b) if scores[a] > scores[b] else (b, a)
                act_wins[w] += 1; losses[l] += 1
                if scores[w] == week_high:
                    hs_losses[l] += 1

        for t, e in by_team.items():
            pp = e.get("players_points") or {}
            candidates = []
            for pid, pts in pp.items():
                absent[t]["player_weeks"] += 1
                if float(pts) == 0.0:
                    absent[t]["zeros"] += 1
                    if pid in (e.get("starters") or []):
                        absent[t]["starter_zeros"] += 1
                pos = player_positions.get(str(pid))
                if not pos:
                    unknown_pos.add(str(pid))
                    continue
                candidates.append((str(pid), list(pos), float(pts)))
            assigned, _ = FantasySimulationEngine._solve_optimal_assignment(candidates, slots=slots)
            optimal = sum(v for _, v, _ in assigned)
            actual = scores[t]
            lineup[t]["actual"] += actual
            lineup[t]["optimal"] += optimal
            lineup[t]["weeks"].append({"week": wk, "actual": round(actual, 2),
                                       "optimal": round(optimal, 2),
                                       "lost": round(optimal - actual, 2)})

    pts_rank = {t: r for r, t in enumerate(
        sorted(teams, key=lambda t: -season_pts[t]), start=1)}

    schedule_luck = {t: {"expected_wins_all_play": round(exp_wins[t], 4),
                         "actual_wins": act_wins[t] if act_wins[t] % 1 else int(act_wins[t]),
                         "luck": round(act_wins[t] - exp_wins[t], 4),
                         "points": round(season_pts[t], 2), "points_rank": pts_rank[t]}
                     for t in teams}
    lineup_eff = {}
    for t in teams:
        a, o = lineup[t]["actual"], lineup[t]["optimal"]
        lineup_eff[t] = {"actual": round(a, 2), "optimal": round(o, 2),
                         "points_lost": round(o - a, 2),
                         "pct": round(100.0 * a / o, 2) if o else None,
                         "weeks": lineup[t]["weeks"]}
    absences = {t: {"player_weeks": d["player_weeks"], "zero_point_player_weeks": d["zeros"],
                    "rate": round(d["zeros"] / d["player_weeks"], 4) if d["player_weeks"] else None,
                    "starter_zeros": d["starter_zeros"]} for t, d in absent.items()}
    high_scorer = {t: {"losses": losses[t], "vs_week_high_scorer": hs_losses[t]} for t in teams}

    pure_h2h = (bundle.get("settings") or {}).get("league_average_match") == 0
    context_note = (("This season ran PURE H2H (league_average_match=0): one binary decision per "
                     "week, so the record carries structurally higher variance than the current "
                     "hybrid format's two decisions per week. Read the luck figure against that.")
                    if pure_h2h else
                    ("This season used median scoring alongside H2H (two decisions per week), "
                     "which lowers record variance relative to pure H2H."))
    return {"season": bundle.get("season"), "regular_season_weeks": weeks, "slots": slots,
            "context_note": context_note,
            "absence_note": ("absence is a DNP PROXY: a rostered player-week scoring exactly 0.0. "
                             "A genuine 0.0 performance counts as absent under this proxy."),
            "position_unknown": sorted(unknown_pos),
            "schedule_luck": schedule_luck, "lineup_efficiency": lineup_eff,
            "absences": absences, "high_scorer_losses": high_scorer}

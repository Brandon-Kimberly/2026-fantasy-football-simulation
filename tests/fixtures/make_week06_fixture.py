#!/usr/bin/env python3
"""
Regenerates tests/fixtures/golden/week06/ from tests/fixtures/golden/week01/.

The week01 fixture is a verbatim snapshot of a real `python -m scripts.run_sync` output at
current_week=1, so it has an EMPTY weekly_actuals.json. That leaves three substantial paths in
the engine completely unexercised by a golden master built on it alone:

  * `_apply_bayesian_updates` -- the conjugate-normal posterior over player means, the MAE
    calibration report, and the accumulation of banked h2h/median wins. With no completed
    weeks it returns the preseason stub on its second line and does nothing else.
  * The banked-state entry into run_simulation (`sim_wins` seeded from actual wins rather
    than zero, `sim_points` from actual points).
  * The shortened week loop, `range(current_week - 1, 16)`, at any value other than 0.

So week06 exists purely to give the golden master coverage of those paths. Its weeks 1-5
results are SYNTHETIC, not real league history -- generated here from a fixed seed. That is
fine and deliberate: a golden master asserts "this input still produces that output", which
requires only that the input be fixed and realistic in SHAPE, not that it be true. Nothing in
this fixture should ever be cited as evidence about the real 2025/2026 season.

Deterministic: uses np.random.default_rng(20260101), independent of the engine's own legacy
np.random stream, so regenerating this fixture cannot perturb simulation results.

Usage:  python -m tests.fixtures.make_week06_fixture
"""
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "golden", "week01")
DST = os.path.join(HERE, "golden", "week06")

CURRENT_WEEK = 6
COMPLETED_WEEKS = 5
FIXTURE_SEED = 20260101
STARTERS = 13


def _load(name):
    with open(os.path.join(SRC, name)) as f:
        return json.load(f)


def _save(name, obj):
    with open(os.path.join(DST, name), "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def main():
    os.makedirs(DST, exist_ok=True)
    rosters = _load("live_rosters.json")
    baselines = _load("player_baselines.json")
    schedule = _load("league_schedule.json")
    standings = _load("league_standings.json")

    rng = np.random.default_rng(FIXTURE_SEED)
    teams = list(rosters.keys())

    cumulative_h2h = {t: 0.0 for t in teams}
    cumulative_points = {t: 0.0 for t in teams}
    actuals = {}

    for wk in range(1, COMPLETED_WEEKS + 1):
        player_scores = {}
        team_scores = {}
        for t in teams:
            per_player = []
            for p in rosters[t]:
                name = p["name"]
                base = baselines.get(name, {})
                mean = float(base.get("mean", 8.0))
                sd = float(base.get("std_aleatoric", 3.0))
                pts = round(float(max(0.0, rng.normal(mean, sd))), 2)
                player_scores[name] = pts
                per_player.append(pts)
            # Team score = the 13 best individual scores. A deliberate simplification: the real
            # league total comes from a positionally-constrained optimal lineup, but fixture
            # realism only needs the right order of magnitude and spread.
            per_player.sort(reverse=True)
            team_scores[t] = round(float(sum(per_player[:STARTERS])), 2)

        median_cut = float(np.median(list(team_scores.values())))

        h2h = {t: 0.0 for t in teams}
        week_matchups = schedule[wk - 1] if wk - 1 < len(schedule) else []
        for pair in week_matchups:
            a, b = pair[0], pair[1]
            if a not in team_scores or b not in team_scores:
                continue
            if team_scores[a] > team_scores[b]:
                h2h[a] = 1.0
            elif team_scores[b] > team_scores[a]:
                h2h[b] = 1.0
            else:
                h2h[a] = h2h[b] = 0.5

        team_results = {}
        for t in teams:
            team_results[t] = {
                "points_scored": team_scores[t],
                "h2h_win": h2h[t],
                "median_win": 1 if team_scores[t] >= median_cut else 0,
                "remaining_faab": float(standings.get(t, {}).get("remaining_faab", 100.0)),
            }
            cumulative_h2h[t] += h2h[t]
            cumulative_points[t] += team_scores[t]

        actuals[f"week_{wk}"] = {
            "median_cutoff": round(median_cut, 2),
            "team_results": team_results,
            "player_scores": player_scores,
        }

    new_standings = {}
    for t in teams:
        prior = standings.get(t, {})
        # Spend FAAB deterministically so the fixture exercises a non-uniform budget state.
        spent = (teams.index(t) * 7) % 40
        new_standings[t] = {
            "h2h_wins": int(cumulative_h2h[t]),
            "points_scored": round(cumulative_points[t], 2),
            "remaining_faab": float(max(0.0, prior.get("remaining_faab", 100.0) - spent)),
        }

    for name in ["vegas_totals.json", "live_rosters.json", "player_baselines.json",
                 "nfl_team_power_ratings.json", "nfl_defensive_ratings.json",
                 "nfl_defensive_tiers.json", "league_schedule.json", "nfl_schedule.json"]:
        _save(name, _load(name))

    _save("league_state.json", {"current_week": CURRENT_WEEK})
    _save("league_standings.json", new_standings)
    _save("weekly_actuals.json", actuals)

    print(f"[OK] wrote week06 fixture to {DST}")
    print(f"     {COMPLETED_WEEKS} completed weeks, {len(teams)} teams, "
          f"{sum(len(v['player_scores']) for v in actuals.values())} player-week scores")


if __name__ == "__main__":
    main()

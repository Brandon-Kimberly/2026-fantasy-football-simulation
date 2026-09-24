"""Engine fixture for the banked-record tests: a league whose BANKED record and whose
RECOMPUTED record deliberately disagree.

That disagreement is the live condition F83 leaves behind. Sleeper's `/matchups` derives a
completed week's points from stat lines against CURRENT scoring settings, so weeks played
under the old IDP rules come back re-priced, while the standings keep what was banked.
This fixture reproduces it at the smallest size that still exercises the credibility
criterion: eight teams, two completed weeks.

Not a test module itself -- it is imported by `tests/test_banked_record_source.py`.
"""
import logging
from unittest.mock import patch

from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)

TEAMS = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis",
         "Turbo Llamas", "Cosmic Badgers", "Iron Wombats", "Crimson Marmots"]
ME = TEAMS[0]
SLOTS = [("QB", 18.0), ("RB", 17.0), ("RB", 15.0), ("WR", 16.0), ("WR", 13.0),
         ("TE", 12.0), ("K", 9.0), ("DL", 9.0), ("LB", 9.0), ("DB", 9.0),
         ("RB", 11.0), ("WR", 10.5), ("WR", 10.0)]


def _fs(banked_wins, recomputed_wins, banked_points, credible):
    base, rosters, pid = {}, {}, 4000
    for t in TEAMS:
        entries = []
        for i, (pos, mu) in enumerate(SLOTS):
            pid += 1
            nm = f"{t[:2]}_{pos}_{i}"
            base[nm] = {"mean": mu, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
                        "team": "DET", "bye": 0, "player_id": str(pid)}
            entries.append({"name": nm, "pos": pos, "team": "DET"})
        rosters[t] = entries

    # Two completed weeks. ME is given `recomputed_wins` decisions across them; every other
    # team gets exactly 2, so the league-wide RECOMPUTED total is whatever ME's makes it.
    actuals = {}
    for wk in (1, 2):
        rows = {}
        for t in TEAMS:
            if t == ME:
                h2h = 1 if (wk == 1 or recomputed_wins > 2) else 0
                med = 1 if recomputed_wins >= 2 else 0
            else:
                h2h, med = 1, 1
            rows[t] = {"points_scored": 150.0 + TEAMS.index(t),
                       "h2h_win": h2h, "median_win": med,
                       "remaining_faab": 100.0}
        actuals[f"week_{wk}"] = {"team_results": rows, "player_scores": {}}

    # The BANKED record. When `credible`, the league-wide total equals teams x weeks, which
    # is what two decisions per team per week produces; otherwise it is deliberately stale.
    others = 2
    total_needed = len(TEAMS) * 2
    standings = {}
    for t in TEAMS:
        w = banked_wins if t == ME else others
        standings[t] = {"h2h_wins": w,
                        "points_scored": (banked_points if t == ME else 300.0),
                        "remaining_faab": 100.0}
    if credible:
        # Make the league-wide sum land exactly on teams x weeks by absorbing ME's offset.
        gap = total_needed - sum(v["h2h_wins"] for v in standings.values())
        standings[TEAMS[1]]["h2h_wins"] += gap
    return {
        LEAGUE_STATE_FILE: {"current_week": 3},
        LEAGUE_STANDINGS_FILE: standings,
        VEGAS_FILE: {"_meta": {"week": 3, "source": "odds_api", "fetched_at": "x"},
                     "DET": {"total": 22.0, "spread": 0.0, "opponent": "CHI"},
                     "CHI": {"total": 22.0, "spread": 0.0, "opponent": "DET"}},
        LIVE_ROSTERS_FILE: rosters,
        BASELINES_FILE: base,
        TEAM_RATINGS_FILE: {"DET": {"off_rating": 22}, "CHI": {"off_rating": 22}},
        DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                 "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[TEAMS[i], TEAMS[i + 1]] for i in range(0, 8, 2)]] * 14,
        NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: actuals,
    }


def engine_with(banked_wins, recomputed_wins, banked_points=336.38, credible=True):
    from fantasy_sim.simulation import FantasySimulationEngine
    fs = _fs(banked_wins, recomputed_wins, banked_points, credible)
    pre = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.ERROR)
    with patch("os.path.exists", side_effect=lambda p: p in fs), \
         patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]):
        e = FantasySimulationEngine()
    logging.getLogger().setLevel(pre)
    return e

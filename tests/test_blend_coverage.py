"""F54 (2026-09-22): the Bayesian blend reached 157 of ~1,140 players, and the
replacement line was computed three lines before it ran.

Two defects, one root cause -- the engine's posterior refinement was fed from a source
that structurally cannot see most of the league:

  1. sync._extract_weekly_player_scores reads `players_points` out of the matchup
     payload, which by Sleeper's design contains ONLY ROSTERED players. So
     weekly_actuals.json carried 157 names; _apply_bayesian_updates updated exactly
     those, and ~750 projected players kept a preseason prior forever. That is
     precisely the population every waiver decision is drawn from, so free agents were
     ranked on frozen numbers while your own roster was ranked on corrected ones.
     Measured live: Mahomes, Shough, Devin Lloyd and Lukas Van Ness all read as
     mediocre against priors none of their actual games had ever touched.

  2. simulation.__init__ called _calc_replacement_levels() at line 270 and
     _apply_bayesian_updates() at line 273, so VORP compared BLENDED rostered means
     against an UNBLENDED replacement line -- two different quantities.

Written before the fix and confirmed failing (rule 1).
"""
import logging
import unittest
from unittest.mock import patch

from fantasy_sim.storage import (
    BASELINES_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    LEAGUE_STANDINGS_FILE, LEAGUE_STATE_FILE, LIVE_ROSTERS_FILE, NFL_SCHEDULE_FILE,
    TEAM_RATINGS_FILE, VEGAS_FILE, WEEKLY_ACTUALS_FILE,
)
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.sync import _extract_weekly_player_scores

PLAYERS_DB = {
    "1": {"first_name": "Rostered", "last_name": "Guy", "position": "WR", "team": "DET"},
    "2": {"first_name": "Unrostered", "last_name": "Breakout", "position": "WR", "team": "CHI"},
}


class TestLeagueWideScoreExtraction(unittest.TestCase):
    """Matchup payloads carry only rostered players, so a player nobody owns can never
    appear -- which is exactly the player a waiver claim is about."""

    MATCHUPS = [{"roster_id": 1, "players_points": {"1": 18.0}}]

    def test_matchup_scores_are_still_extracted(self):
        out = _extract_weekly_player_scores(self.MATCHUPS, PLAYERS_DB, {"1"})
        self.assertAlmostEqual(out["Rostered Guy"], 18.0)

    def test_an_unrostered_player_is_included_when_league_wide_scores_are_supplied(self):
        out = _extract_weekly_player_scores(self.MATCHUPS, PLAYERS_DB, {"1"},
                                            league_wide={"2": 27.5})
        self.assertIn("Unrostered Breakout", out,
                      "F54: a player nobody rosters must still get his observed score, "
                      "or his baseline is a preseason guess forever")
        self.assertAlmostEqual(out["Unrostered Breakout"], 27.5)

    def test_the_matchup_value_wins_when_both_sources_have_a_player(self):
        """Sleeper's own credited total is authoritative -- it is what the league
        actually scored. The stats feed is only there to fill the gaps."""
        out = _extract_weekly_player_scores(self.MATCHUPS, PLAYERS_DB, {"1"},
                                            league_wide={"1": 99.0, "2": 27.5})
        self.assertAlmostEqual(out["Rostered Guy"], 18.0)


def _fs(weekly_actuals):
    teams = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis"]
    return {
        LEAGUE_STATE_FILE: {"current_week": 2},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in teams},
        VEGAS_FILE: {"_meta": {"week": 2, "source": "odds_api", "fetched_at": "x"},
                     "DET": {"total": 24.0, "spread": -4.0, "opponent": "CHI"},
                     "CHI": {"total": 20.0, "spread": 4.0, "opponent": "DET"}},
        LIVE_ROSTERS_FILE: {t: [{"name": f"QB_{i+1}", "pos": "QB", "team": "DET"}]
                            for i, t in enumerate(teams)},
        BASELINES_FILE: dict(
            {f"QB_{i+1}": {"mean": 20.0, "std_aleatoric": 2.0, "std_epistemic": 1.5,
                           "pos": "QB", "team": "DET", "bye": 0} for i in range(4)},
            **{f"FA_WR_{i}": {"mean": 10.0, "std_aleatoric": 4.0, "std_epistemic": 3.0,
                              "pos": "WR", "team": "DET", "bye": 9} for i in range(30)}),
        TEAM_RATINGS_FILE: {"DET": {"off_rating": 25}, "CHI": {"off_rating": 20}},
        DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                 "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[teams[0], teams[1]], [teams[2], teams[3]]]] * 14,
        NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: weekly_actuals,
    }


class TestReplacementIsComputedAfterTheBlend(unittest.TestCase):
    """Every WR in the pool posts 30.0 twice. The blend must lift them, and the
    replacement line -- which is the 24th-best WR mean -- must lift with them. If
    replacement is computed first it stays pinned at the untouched prior of 10.0."""

    ACTUALS = {
        f"week_{w}": {
            "median_cutoff": 100.0,
            "team_results": {t: {"points_scored": 100.0, "h2h_win": 0.0, "median_win": 0}
                             for t in ("Quantum Ferrets", "Neon Walruses",
                                       "Rocket Pandas", "Polar Yetis")},
            "player_scores": {f"FA_WR_{i}": 30.0 for i in range(30)},
        } for w in (1, 2)
    }

    def setUp(self):
        self.fs = _fs(self.ACTUALS)
        self.prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        self.p1 = patch("os.path.exists", side_effect=lambda p: p in self.fs)
        self.p2 = patch("fantasy_sim.simulation.load_json", side_effect=lambda p: self.fs[p])
        self.p1.start(); self.p2.start()
        self.engine = FantasySimulationEngine()

    def tearDown(self):
        self.p1.stop(); self.p2.stop()
        logging.getLogger().setLevel(self.prev)

    def test_the_blend_actually_moved_the_means(self):
        """Guard on the fixture. The engine blends by PRECISION, not by a flat 4:1 count
        (simulation.py:546 weights each side by 1/variance), so the posterior is asserted
        as "moved well above the prior" rather than against a hand formula that would
        quietly diverge from the implementation."""
        self.assertGreater(self.engine.baselines["FA_WR_0"]["mean"], 15.0)

    def test_replacement_reflects_the_blended_pool_not_the_prior(self):
        """All thirty WRs are identical, so the 24th-best WR mean IS the blended mean.
        Formula-agnostic: whatever the posterior works out to, replacement must equal it."""
        blended = self.engine.baselines["FA_WR_0"]["mean"]
        self.assertAlmostEqual(
            self.engine.replacement_levels["WR"], blended, places=6,
            msg="F54: replacement was computed before _apply_bayesian_updates, so it "
                "read the untouched 10.0 prior while rostered players were blended -- "
                "VORP compared two different quantities")


if __name__ == "__main__":
    unittest.main()

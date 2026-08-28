"""
tests.test_backtest_season

Test suite for fantasy_sim.backtest_season. Extracted from what was originally a
unittest.TestCase embedded directly in backtest_harness.py.
"""
import math
import os
import unittest
from unittest.mock import patch, MagicMock

from fantasy_sim import sync
from fantasy_sim import simulation as simmod
from fantasy_sim.backtest_season import (
    compute_crps, build_blank_slate_baselines, build_asof_standings,
    build_full_season_league_schedule, run_backtest_checkpoint, BACKTEST_WORKDIR,
    REGULAR_SEASON_WEEKS,
)


class TestBacktestHarness(unittest.TestCase):
    def test_compute_crps_matches_hand_computed_example(self):
        """Hand-verified against the brute-force O(N^2) CRPS definition:
        samples=[10,20,30], actual=15 -> term1=25/3, term2=40/9, CRPS=3.8889."""
        result = compute_crps([10, 20, 30], 15)
        self.assertAlmostEqual(result, 3.888888888888889, places=9)

    def test_compute_crps_reduces_to_absolute_error_for_point_mass(self):
        """If every sample is identical, CRPS must reduce exactly to the plain absolute error
        against the realized outcome -- a standard sanity property of the metric."""
        result = compute_crps([20, 20, 20, 20], 15)
        self.assertAlmostEqual(result, 5.0)

    def test_compute_crps_is_zero_for_perfect_forecast(self):
        """If every sample exactly equals the realized outcome, CRPS must be exactly 0."""
        result = compute_crps([12, 12, 12], 12)
        self.assertAlmostEqual(result, 0.0)

    def test_build_blank_slate_baselines_uses_positional_prior_only(self):
        """Verifies the reconstruction starts from pure positional assumptions, matching
        production's own replacement-level constants exactly -- not some independently
        invented set of numbers."""
        live_rosters = {"Team A": [{"name": "Test WR", "pos": "WR", "team": "DET"}]}
        result = build_blank_slate_baselines(live_rosters)

        expected_mean = simmod.BASE_STREAMER_MEANS["WR"]
        expected_aleatoric = round(sync.VOLATILITY_CONSTANTS["WR"] * math.sqrt(max(0.5, expected_mean)), 2)
        expected_epistemic = round(sync.EPISTEMIC_ERROR_RATES["WR"] * expected_mean, 2)

        entry = result["Test WR"]
        self.assertAlmostEqual(entry["mean"], expected_mean)
        self.assertAlmostEqual(entry["std_aleatoric"], expected_aleatoric)
        self.assertAlmostEqual(entry["std_epistemic"], expected_epistemic)

    def test_flat_environment_with_pairings_adds_byes_and_nothing_else(self):
        """Bye modelling, step 4. Supplying real pairings must add exactly one piece of
        information -- who is absent which week -- and leave the environment neutral: with
        every rating flat, a real opponent's implied total is 21.5 and the spread 0, the same
        as the 'FA' fallback the empty schedule produced."""
        from fantasy_sim.backtest_season import build_flat_nfl_environment_files
        from fantasy_sim.config import NFL_TEAMS
        pairings = {}
        for wk in range(1, 19):
            playing = [t for t in NFL_TEAMS if not (t == "DET" and wk == 6)]
            pairings[str(wk)] = {t: playing[(i + 1) % len(playing)] for i, t in enumerate(playing)}
        pr, dr, dt, sched = build_flat_nfl_environment_files(pairings=pairings, failed_weeks=[])
        self.assertEqual(sched["_meta"]["byes"], {"DET": 6})
        self.assertEqual(sched["6"].get("DET"), None)
        # neutral environment through the REAL engine formula
        engine = simmod.FantasySimulationEngine.__new__(simmod.FantasySimulationEngine)
        engine.power_ratings, engine.defensive_ratings = pr, dr
        env = engine._compute_future_week_matchup_environment("DET", sched["7"]["DET"])
        self.assertEqual((env["total"], env["spread"]), (21.5, 0.0))
        self.assertEqual(env["opponent"], sched["7"]["DET"])
        # without pairings: unchanged v1 behaviour
        _, _, _, empty = build_flat_nfl_environment_files()
        self.assertNotIn("_meta", empty)
        self.assertEqual(empty["6"], {})

    def test_blank_slate_baselines_carry_the_supplied_bye(self):
        live_rosters = {"Team A": [{"name": "Test WR", "pos": "WR", "team": "DET"},
                                   {"name": "Free WR", "pos": "WR", "team": "FA"}]}
        result = build_blank_slate_baselines(live_rosters, byes={"DET": 6})
        self.assertEqual(result["Test WR"]["bye"], 6)
        self.assertEqual(result["Free WR"]["bye"], 0)
        self.assertEqual(build_blank_slate_baselines(live_rosters)["Test WR"]["bye"], 0)

    def test_build_asof_standings_only_uses_weeks_before_checkpoint(self):
        """Regression test for look-ahead bias: a real result from week >= through_week must
        never be counted in the as-of-checkpoint standings, even if that week's data exists in
        season_matchups (which holds the FULL season for schedule-construction purposes)."""
        roster_map = {1: "Team A", 2: "Team B"}
        season_matchups = {
            1: [{"roster_id": 1, "matchup_id": 100, "points": 100.0},
                {"roster_id": 2, "matchup_id": 100, "points": 90.0}],
            2: [{"roster_id": 1, "matchup_id": 100, "points": 50.0},   # should NOT count if through_week=2
                {"roster_id": 2, "matchup_id": 100, "points": 200.0}],
        }

        standings = build_asof_standings(season_matchups, roster_map, through_week=2)

        self.assertEqual(standings["Team A"]["h2h_wins"], 1)  # only week 1 counted
        self.assertAlmostEqual(standings["Team A"]["points_scored"], 100.0)  # week 2 excluded

    def test_build_full_season_league_schedule_matches_production_format(self):
        """Verifies the reconstructed schedule is a list of REGULAR_SEASON_WEEKS entries, each
        a list of [team1, team2] pairs -- exactly generate_league_schedule()'s output shape."""
        roster_map = {1: "Team A", 2: "Team B"}
        season_matchups = {1: [{"roster_id": 1, "matchup_id": 100, "points": 100.0},
                                {"roster_id": 2, "matchup_id": 100, "points": 90.0}]}

        schedule = build_full_season_league_schedule(season_matchups, roster_map, regular_season_weeks=3)

        self.assertEqual(len(schedule), 3)
        self.assertEqual(schedule[0], [["Team A", "Team B"]])
        self.assertEqual(schedule[1], [])  # no data for week 2 -> empty, not an error
        self.assertEqual(schedule[2], [])

    def test_run_backtest_checkpoint_end_to_end_wiring(self):
        """Fully-mocked end-to-end run of run_backtest_checkpoint against a small synthetic
        4-team league -- this is the highest-risk part of the file (dict shapes and types
        passed between functions, file construction, the export-capturing mechanism) and
        can't be verified by the pure-function tests alone. Exercises the REAL
        FantasySimulationEngine and the real chdir-isolation mechanism, just against
        fabricated instead of live network data. Real network calls against Sleeper's actual
        API still need a live run to confirm -- this only proves the wiring itself is sound."""
        fake_players_db = {
            "101": {"first_name": "QB", "last_name": "One", "position": "QB", "team": "DET"},
            "102": {"first_name": "QB", "last_name": "Two", "position": "QB", "team": "BUF"},
            "103": {"first_name": "QB", "last_name": "Three", "position": "QB", "team": "SF"},
            "104": {"first_name": "QB", "last_name": "Four", "position": "QB", "team": "KC"},
        }
        fake_users = [{"user_id": f"u{i}", "display_name": f"user{i}"} for i in range(1, 5)]
        fake_rosters = [
            {"roster_id": i, "owner_id": f"u{i}", "players": [str(100 + i)],
             "settings": {"wins": 5, "losses": 3, "fpts": 900, "fpts_decimal": 0, "waiver_budget_used": 0}}
            for i in range(1, 5)
        ]
        fake_matchup_week = [
            {"roster_id": 1, "matchup_id": 1, "points": 110.0, "players_points": {"101": 20.0}},
            {"roster_id": 2, "matchup_id": 1, "points": 95.0, "players_points": {"102": 15.0}},
            {"roster_id": 3, "matchup_id": 2, "points": 105.0, "players_points": {"103": 18.0}},
            {"roster_id": 4, "matchup_id": 2, "points": 100.0, "players_points": {"104": 17.0}},
        ]

        # Bypass sync.TEAM_NAME_MAP entirely -- these synthetic usernames aren't in it, so every
        # roster would otherwise map to "Unknown_N" for all 4 teams, colliding into fewer than
        # 4 distinct team names. Patch it so each fake user maps to a distinct team name instead.
        fake_team_name_map = {f"user{i}": f"Team{i}" for i in range(1, 5)}

        def fake_get(url, timeout=None):
            if url.endswith("/users"):
                return _FakeResp(fake_users)
            if url.endswith("/rosters"):
                return _FakeResp(fake_rosters)
            if "/matchups/" in url:
                wk = int(url.rsplit("/", 1)[-1])
                if wk <= 14:
                    return _FakeResp(fake_matchup_week)
                return _FakeResp([], status_code=404)
            if url.endswith("/winners_bracket"):
                return _FakeResp([{"t1": 1, "t2": 2}])
            return _FakeResp({}, status_code=404)

        with patch.object(sync, 'update_player_cache', return_value=fake_players_db), \
             patch.object(sync, 'TEAM_NAME_MAP', fake_team_name_map), \
             patch('requests.get', side_effect=fake_get), \
             patch('matplotlib.pyplot.savefig'), \
             self.assertLogs(level="WARNING") as logs:   # the fake HTTP layer 404s ESPN: byes must degrade loudly
            simmod.SIM_CONFIG['NUM_BATCHES'] = 1
            simmod.SIM_CONFIG['SIMS_PER_BATCH'] = 20
            result = run_backtest_checkpoint(checkpoint_week=3, num_batches=1, sims_per_batch=20)

        self.assertIsNotNone(result, "run_backtest_checkpoint returned None -- check wiring.")
        self.assertEqual(len(result), 4)
        for team, r in result.items():
            self.assertIn("crps", r)
            self.assertIn("sim_expected_wins", r)
            self.assertGreaterEqual(r["crps"], 0.0)
        # Confirm the workdir was actually cleaned up (default keep_workdir=False).
        self.assertFalse(os.path.exists(BACKTEST_WORKDIR))


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload



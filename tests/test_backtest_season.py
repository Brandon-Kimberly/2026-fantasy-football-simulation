"""
tests.test_backtest_season

Test suite for fantasy_sim.backtest_season. Extracted from what was originally a
unittest.TestCase embedded directly in backtest_harness.py.
"""
import math
import os
import unittest

import numpy as np
from unittest.mock import patch

from fantasy_sim import sync
from fantasy_sim import simulation as simmod
from fantasy_sim.backtest_season import (
    compute_crps, build_blank_slate_baselines, build_asof_standings,
    build_full_season_league_schedule, run_backtest_checkpoint, BACKTEST_WORKDIR,
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
             patch('fantasy_sim.simulation.save_chart'), \
             self.assertLogs(level="WARNING"):   # the fake HTTP layer 404s ESPN: byes must degrade loudly
            simmod.SIM_CONFIG['NUM_BATCHES'] = 1
            simmod.SIM_CONFIG['SIMS_PER_BATCH'] = 20
            # F2 commit 2 (criterion c): return_raw=True also hands back the simulated weekly
            # team scores and the real post-checkpoint weekly points, which the points-level
            # backtest (scripts.run_points_backtest) scores. The default return is unchanged.
            result, raw = run_backtest_checkpoint(checkpoint_week=3, num_batches=1, sims_per_batch=20,
                                                  return_raw=True)

        self.assertIsNotNone(result, "run_backtest_checkpoint returned None -- check wiring.")
        self.assertEqual(len(result), 4)
        for team, r in result.items():
            self.assertIn("crps", r)
            self.assertIn("sim_expected_wins", r)
            self.assertGreaterEqual(r["crps"], 0.0)
        # Confirm the workdir was actually cleaned up (default keep_workdir=False).
        self.assertFalse(os.path.exists(BACKTEST_WORKDIR))

        self.assertEqual(raw["checkpoint_week"], 3)
        self.assertEqual(set(raw["weekly_scores"]), set(result))
        for team, arr in raw["weekly_scores"].items():
            self.assertEqual(arr.shape, (20, 14), "one row per simulated season, one column per regular-season week")
        # Real points exist for every week from the checkpoint through week 14 (the fake API
        # serves weeks 1-14), and only those -- weeks before the checkpoint are inputs, not targets.
        for team, by_week in raw["real_weekly_points"].items():
            self.assertEqual(sorted(by_week), list(range(3, 15)))
            self.assertTrue(all(v > 0 for v in by_week.values()))


class TestPointsBacktestScoring(unittest.TestCase):
    """scripts.run_points_backtest's scoring is pure; pinned against hand-computed values.
    Written alongside the script, not before it -- a specification of the definition in
    AUDIT_PLAN.md (bias = sim mean - real; z = (real - sim mean)/sim sd; cover80 = share of
    real inside the simulated 10-90% band), not a failing-first regression test."""

    def test_bias_z_and_coverage_match_hand_computation(self):
        from scripts.run_points_backtest import score_checkpoint, summarise
        sims = np.zeros((5, 14))
        sims[:, 2] = [100.0, 110.0, 120.0, 130.0, 140.0]     # week 3: mean 120, sd 15.811
        sims[:, 3] = [200.0, 200.0, 200.0, 200.0, 200.0]     # week 4: degenerate, sd 0
        raw = {"checkpoint_week": 3,
               "weekly_scores": {"A": sims},
               "real_weekly_points": {"A": {3: 105.0, 4: 200.0}}}
        rows = score_checkpoint(raw)
        self.assertEqual([(r["week"], r["real"]) for r in rows], [(3, 105.0), (4, 200.0)])
        wk3 = rows[0]
        self.assertAlmostEqual(wk3["bias"], 15.0)
        self.assertAlmostEqual(wk3["z"], (105.0 - 120.0) / np.std(sims[:, 2], ddof=1), places=9)
        self.assertTrue(wk3["in80"])       # 10th pct = 104, 90th = 136
        self.assertFalse(wk3["in50"])      # 25th = 110, 75th = 130
        self.assertTrue(np.isnan(rows[1]["z"]), "sd 0 must give nan z, not a division error")
        s = summarise(rows)
        self.assertEqual(s["n"], 2)
        self.assertAlmostEqual(s["bias"], 7.5)
        self.assertAlmostEqual(s["cover80"], 1.0)
        self.assertAlmostEqual(s["cover50"], 0.5)

    def test_naive_forecast_is_scored_alongside_and_summarised(self):
        """The showcase review's baseline comparison (2026-09-05): the same rows carry
        the projections-only naive forecast, and the summary reports both models' MAE
        and bias so "the machinery beats a spreadsheet" is a logged number, not a claim."""
        from scripts.run_points_backtest import score_checkpoint, summarise
        sims = np.zeros((5, 14))
        sims[:, 2] = [100.0, 110.0, 120.0, 130.0, 140.0]
        raw = {"checkpoint_week": 3,
               "weekly_scores": {"A": sims},
               "real_weekly_points": {"A": {3: 105.0}},
               "naive_weekly_forecast": {"A": {3: 130.0}}}
        rows = score_checkpoint(raw)
        self.assertAlmostEqual(rows[0]["naive"], 130.0)
        s = summarise(rows)
        self.assertAlmostEqual(s["naive_bias"], 25.0)          # 130 - 105
        self.assertAlmostEqual(s["naive_mae"], 25.0)
        self.assertAlmostEqual(s["engine_mae"], 15.0)          # |120 - 105|

    def test_weeks_before_the_checkpoint_are_never_scored(self):
        from scripts.run_points_backtest import score_checkpoint
        raw = {"checkpoint_week": 6, "weekly_scores": {"A": np.ones((3, 14))},
               "real_weekly_points": {"A": {6: 1.0, 7: 1.0}}}
        self.assertEqual(sorted(r["week"] for r in score_checkpoint(raw)), [6, 7])

    def test_optimal_target_columns_appear_when_supplied_and_stay_absent_otherwise(self):
        # F25's corrected target: the sim never claimed to predict managers' start/sit
        # errors, so the gate ALSO scores against realized optimal-lineup points. Old
        # columns unchanged for continuity; opt columns None when the target is absent.
        from scripts.run_points_backtest import score_checkpoint, summarise
        sims = np.zeros((5, 14))
        sims[:, 2] = [100.0, 110.0, 120.0, 130.0, 140.0]
        raw = {"checkpoint_week": 3, "weekly_scores": {"A": sims},
               "real_weekly_points": {"A": {3: 105.0}}}
        rows = score_checkpoint(raw)
        self.assertIsNone(rows[0]["real_opt"])
        self.assertNotIn("cover80_opt", summarise(rows))

        raw["real_optimal_points"] = {"A": {3: 125.0}}
        rows = score_checkpoint(raw)
        r = rows[0]
        self.assertAlmostEqual(r["real_opt"], 125.0)
        self.assertAlmostEqual(r["z_opt"], (125.0 - 120.0) / np.std(sims[:, 2], ddof=1), places=9)
        s_ = summarise(rows)
        self.assertAlmostEqual(s_["bias_opt"], -5.0, msg="sim mean minus optimal target")
        self.assertIn("sd_z_opt", s_)
        # recentred coverage: with one row the shift absorbs the offset exactly
        self.assertAlmostEqual(s_["cover80_opt_centered"], 1.0)

    def test_real_optimal_points_solves_each_weeks_actual_roster(self):
        # Optimal from the season bundle's own per-week rosters (the era roster, not the
        # frozen final one): bench 9.0 must replace the started 2.0 in the FLEX-less QB+RB
        # slot world.
        from scripts.run_points_backtest import real_optimal_points
        bundle = {"roster_positions": ["QB", "RB", "BN"],
                  "settings": {"playoff_week_start": 15},
                  "roster_map": {"1": "A"},
                  "matchups": {"3": [{"roster_id": 1, "matchup_id": 1, "points": 22.0,
                                      "players": ["q1", "r1", "r2"], "starters": ["q1", "r1"],
                                      "players_points": {"q1": 20.0, "r1": 2.0, "r2": 9.0}}]}}
        positions = {"q1": ["QB"], "r1": ["RB"], "r2": ["RB"]}
        out = real_optimal_points(bundle, positions)
        self.assertAlmostEqual(out["A"][3], 29.0, msg="QB 20 + best RB 9, not the started 2")


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload




class TestOutNowProxy(unittest.TestCase):
    """F4 step 3: the backtest has no injury-status history, so a player whose last two
    recorded non-bye weeks before the checkpoint are 0.0 enters the checkpoint out (stage 2).
    Written before mark_out_now existed; failed with ImportError."""

    def test_two_trailing_non_bye_zeros_mark_a_player_out(self):
        from fantasy_sim.backtest_season import mark_out_now
        base = {"Out Guy": {"pos": "RB", "mean": 9.0, "bye": 0, "team": "SEA"},
                "One Zero": {"pos": "WR", "mean": 9.0, "bye": 0, "team": "DET"},
                "Bye Then Zero": {"pos": "WR", "mean": 9.0, "bye": 3, "team": "DET"},
                "Zero Zero Then Bye": {"pos": "TE", "mean": 7.5, "bye": 4, "team": "GB"},
                "Never Played": {"pos": "K", "mean": 8.0, "bye": 0, "team": "KC"}}
        wa = {"week_1": {"player_scores": {"Out Guy": 12.0, "One Zero": 10.0, "Bye Then Zero": 8.0, "Zero Zero Then Bye": 0.0}},
              "week_2": {"player_scores": {"Out Guy": 0.0, "One Zero": 9.0, "Bye Then Zero": 7.0, "Zero Zero Then Bye": 0.0}},
              "week_3": {"player_scores": {"Out Guy": 0.0, "One Zero": 0.0, "Bye Then Zero": 0.0, "Zero Zero Then Bye": 6.0}},
              "week_4": {"player_scores": {"Zero Zero Then Bye": 0.0}}}
        marked = mark_out_now(base, wa)
        self.assertEqual(sorted(marked), ["Out Guy"])
        self.assertEqual(base["Out Guy"]["injury_status"], "IR")
        self.assertNotIn("injury_status", base["One Zero"], "a single trailing zero is not an absence")
        self.assertNotIn("injury_status", base["Bye Then Zero"], "the bye-week zero must not count")
        self.assertNotIn("injury_status", base["Zero Zero Then Bye"], "the last non-bye week was a real game")
        self.assertNotIn("injury_status", base["Never Played"])


class TestNaiveWeeklyForecast(unittest.TestCase):
    """The comparison baseline the 2026-09-05 showcase review asked for: a
    projections-only static forecast (Hungarian on checkpoint means, byes excluded,
    nothing else) that the full simulation must beat to justify its machinery. Pure over
    an optimal-score callable so the test needs no engine."""

    def test_bye_players_are_excluded_per_week_and_weeks_span_checkpoint_to_last(self):
        from fantasy_sim.backtest_season import naive_weekly_forecast
        rosters = {"A": ["P1", "P2"]}
        baselines = {"P1": {"mean": 10.0, "bye": 5}, "P2": {"mean": 8.0, "bye": 6}}
        calls = []
        def optimal(names):
            calls.append(tuple(sorted(names)))
            return float(len(names) * 7)
        out = naive_weekly_forecast(optimal, rosters, baselines, checkpoint_week=5, last_week=6)
        self.assertEqual(sorted(out["A"]), [5, 6])
        self.assertIn(("P2",), calls)          # week 5: P1 on bye, excluded
        self.assertIn(("P1",), calls)          # week 6: P2 on bye, excluded
        self.assertEqual(out["A"][5], 7.0)



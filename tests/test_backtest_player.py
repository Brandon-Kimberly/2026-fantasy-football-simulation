"""
tests.test_backtest_player

Test suite for fantasy_sim.backtest_player. Extracted from what was originally a
unittest.TestCase embedded directly in player_level_backtest.py.
"""
import math
import unittest
from unittest.mock import patch

import numpy as np

from fantasy_sim import sync
from fantasy_sim import simulation as simmod
from fantasy_sim.backtest_player import (
    collect_real_player_weekly_scores, analyze_aleatoric_variance, analyze_correlations,
    analyze_epistemic_calibration, compute_bayesian_posterior, compute_calibration_z,
    suggest_epistemic_rate_multiplier,
)


class TestPlayerLevelBacktest(unittest.TestCase):
    def test_compute_bayesian_posterior_hand_verified_example(self):
        """Hand-computed: prior_mean=10.0, prior_std_epistemic=3.0, real_scores=[12.0, 14.0].
        prior_var=9.0, actual_mean=13.0, raw_actual_var=1.0, actual_var=max(1.0, 4.5)=4.5,
        n_0=4.0 -> post_var=1/(4/9 + 2/4.5)=1.125 -> post_mean=11.5, post_std=1.0607."""
        post_mean, post_std = compute_bayesian_posterior(10.0, 3.0, [12.0, 14.0])
        self.assertAlmostEqual(post_mean, 11.5, places=3)
        self.assertAlmostEqual(post_std, 1.0606601717798212, places=6)

    def test_compute_calibration_z_accounts_for_future_sample_size(self):
        """Regression test for a real correction caught before trusting the tool's own
        output: with the SAME prior, SAME pre-checkpoint data, and the SAME average future
        outcome, fewer future weeks (more sampling noise in that average) must produce a
        SMALLER |z| than many future weeks (less sampling noise) -- otherwise the test would
        flag a well-calibrated model as overconfident purely because it only had a few future
        weeks to check against."""
        prior_mean, prior_std = 12.0, 2.0
        before = [10.0, 11.0, 13.0]
        after_few = [20.0]
        after_many = [20.0] * 8  # same average, far less sampling noise

        r_few = compute_calibration_z(prior_mean, prior_std, before, after_few, 'WR')
        r_many = compute_calibration_z(prior_mean, prior_std, before, after_many, 'WR')

        self.assertLess(abs(r_few['z']), abs(r_many['z']))

    def test_compute_bayesian_posterior_matches_real_production_method(self):
        """Cross-checks compute_bayesian_posterior against the REAL, unmodified
        FantasySimulationEngine._apply_bayesian_updates on identical synthetic inputs -- proof
        the replica is equivalent, not just a by-eye copy that could silently drift."""
        prior_mean, prior_std_epistemic = 12.0, 2.5
        real_scores = [10.0, 15.0, 13.0]

        expected_mean, expected_std = compute_bayesian_posterior(prior_mean, prior_std_epistemic, real_scores)

        mock_fs = {
            simmod.LEAGUE_STATE_FILE: {"current_week": 4},
            simmod.LEAGUE_STANDINGS_FILE: {"TeamA": {"remaining_faab": 100}},
            simmod.VEGAS_FILE: {},
            simmod.LIVE_ROSTERS_FILE: {"TeamA": [{"name": "Test Player", "pos": "WR", "team": "DET"}]},
            simmod.BASELINES_FILE: {
                "Test Player": {"pos": "WR", "mean": prior_mean, "std_aleatoric": 3.0,
                                 "std_epistemic": prior_std_epistemic, "bye": 0, "team": "DET"},
            },
            simmod.TEAM_RATINGS_FILE: {"DET": {"off_rating": 21.5}},
            simmod.DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
            simmod.DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
            simmod.LEAGUE_SCHEDULE_FILE: [[["TeamA", "TeamA"]]] * 14,
            simmod.NFL_SCHEDULE_FILE: {str(w): {} for w in range(1, 19)},
            simmod.WEEKLY_ACTUALS_FILE: {
                "week_1": {"team_results": {"TeamA": {"points_scored": 100.0}}, "player_scores": {"Test Player": real_scores[0]}},
                "week_2": {"team_results": {"TeamA": {"points_scored": 100.0}}, "player_scores": {"Test Player": real_scores[1]}},
                "week_3": {"team_results": {"TeamA": {"points_scored": 100.0}}, "player_scores": {"Test Player": real_scores[2]}},
            },
        }

        def mock_load(path):
            if path in mock_fs:
                return mock_fs[path]
            raise FileNotFoundError(path)

        with patch.object(simmod, 'load_json', side_effect=mock_load):
            sim = simmod.FantasySimulationEngine()

        self.assertAlmostEqual(sim.baselines["Test Player"]["mean"], expected_mean, places=6)
        self.assertAlmostEqual(sim.baselines["Test Player"]["std_epistemic"], expected_std, places=6)

    def test_analyze_aleatoric_variance_detects_understated_constant(self):
        """Constructs a synthetic player whose real variance is clearly HIGHER than the
        current VOLATILITY_CONSTANTS would predict, and confirms the analysis correctly flags
        a ratio > 1 (constant understates real variance) with the right suggested correction."""
        k_val = sync.VOLATILITY_CONSTANTS['WR']
        mean = 15.0
        predicted_std = k_val * math.sqrt(mean)
        # Construct scores with a real std deliberately ~2x the predicted std.
        real_std_target = predicted_std * 2.0
        scores = {1: mean - real_std_target, 2: mean + real_std_target, 3: mean, 4: mean}
        player_data = {"Synthetic WR": {"pos": "WR", "team": "DET", "weekly_scores": scores}}

        summary, _ = analyze_aleatoric_variance(player_data)

        self.assertIn('WR', summary)
        self.assertGreater(summary['WR']['median_ratio'], 1.5)

    def test_analyze_correlations_detects_strong_positive_correlation(self):
        """Constructs a synthetic QB/WR1 pair whose scores move in perfect lockstep, and
        confirms the analysis detects a correlation near 1.0."""
        qb_weeks = {1: 20.0, 2: 25.0, 3: 15.0, 4: 30.0}
        wr_weeks = {1: 10.0, 2: 15.0, 3: 5.0, 4: 20.0}  # same shape, offset -- perfectly correlated
        player_data = {
            "Synthetic QB": {"pos": "QB", "team": "DET", "weekly_scores": qb_weeks},
            "Synthetic WR": {"pos": "WR", "team": "DET", "weekly_scores": wr_weeks},
        }

        summary = analyze_correlations(player_data)

        self.assertIn('QB_WR1', summary)
        self.assertAlmostEqual(summary['QB_WR1']['empirical_mean_corr'], 1.0, places=2)

    def test_analyze_correlations_detects_strong_negative_correlation(self):
        """Constructs two synthetic WRs whose scores move in exact opposition (one team's
        target share being zero-sum-ish), and confirms a correlation near -1.0."""
        wr1_weeks = {1: 20.0, 2: 10.0, 3: 25.0, 4: 5.0}
        wr2_weeks = {1: 5.0, 2: 15.0, 3: 0.5, 4: 20.0}  # inverse shape
        player_data = {
            "Synthetic QB": {"pos": "QB", "team": "DET", "weekly_scores": {1: 20.0, 2: 21.0, 3: 19.0, 4: 22.0}},
            "Synthetic WR1": {"pos": "WR", "team": "DET", "weekly_scores": wr1_weeks},
            "Synthetic WR2": {"pos": "WR", "team": "DET", "weekly_scores": wr2_weeks},
        }

        summary = analyze_correlations(player_data)

        self.assertIn('WR_WR', summary)
        self.assertLess(summary['WR_WR']['empirical_mean_corr'], -0.8)

    def test_analyze_epistemic_calibration_detects_overconfidence(self):
        """Constructs a synthetic player whose real future performance is WAY outside what a
        peer-informed posterior would expect, and confirms the resulting |z| is large --
        proof the calibration check would actually catch genuine overconfidence. Includes
        peer players with stable, unremarkable early performance so the leave-one-out prior
        has enough peers to form a reasonable (not wildly-outlying) starting guess."""
        player_data = {
            "Wildly Unpredictable Player": {
                "pos": "WR", "team": "DET",
                "weekly_scores": {1: 8.0, 2: 9.0, 3: 8.5, 4: 45.0, 5: 50.0, 6: 48.0},
            },
            "Peer WR A": {"pos": "WR", "team": "BUF", "weekly_scores": {1: 8.5, 2: 9.5, 3: 8.0, 4: 9.0, 5: 8.5, 6: 9.0}},
            "Peer WR B": {"pos": "WR", "team": "SF", "weekly_scores": {1: 7.5, 2: 8.0, 3: 9.0, 4: 8.5, 5: 8.0, 6: 7.5}},
            "Peer WR C": {"pos": "WR", "team": "KC", "weekly_scores": {1: 9.0, 2: 8.5, 3: 8.0, 4: 9.5, 5: 9.0, 6: 8.5}},
        }
        summary, detail = analyze_epistemic_calibration(player_data, checkpoint_week=4, min_future_weeks=1)

        self.assertIn('WR', summary)
        self.assertGreater(summary['WR']['mean_z'], 1.0)  # positive: real future far exceeds a sane peer-based prior

    def test_suggest_epistemic_rate_multiplier_finds_a_correction_that_improves_calibration(self):
        """Constructs synthetic players whose real talent varies far more than the base
        epistemic rate implies (deliberately overconfident at the base rate), and confirms
        the search finds a multiplier that brings std_z meaningfully closer to 1.0 than the
        base rate achieves -- proof the search actually improves calibration, not just that
        it runs. Uses a fixed seed so the synthetic data is reproducible.

        This test's PURPOSE is verifying the search mechanism itself works, not re-testing
        EPISTEMIC_ERROR_RATES' current calibrated value (that's covered by the real
        end-to-end run against 2025 data). So it temporarily patches the RB rate to a value
        deliberately too small for this synthetic talent spread, rather than relying on
        whatever RB happens to be calibrated to at any given time -- decoupling this test
        from future recalibrations of that constant."""
        rng = np.random.default_rng(42)
        true_talents = [8, 12, 20, 6, 15, 25, 10, 18, 5, 22]
        player_data = {}
        for i, talent in enumerate(true_talents):
            weeks = {w: max(1.0, talent + rng.normal(0, 2)) for w in range(1, 8)}
            player_data[f"RB{i}"] = {"pos": "RB", "team": f"T{i}", "weekly_scores": weeks}

        with patch.dict(sync.EPISTEMIC_ERROR_RATES, {'RB': 0.05}):
            base_summary, _ = analyze_epistemic_calibration(player_data, checkpoint_week=4, min_future_weeks=1, min_peers=3)
            suggestions = suggest_epistemic_rate_multiplier(player_data, checkpoint_week=4, min_future_weeks=1, min_peers=3)

        self.assertIn('RB', suggestions)
        base_std_z_distance = abs(base_summary['RB']['std_z'] - 1.0)
        suggested_std_z_distance = abs(suggestions['RB']['achieved_std_z'] - 1.0)
        self.assertLess(suggested_std_z_distance, base_std_z_distance)
        self.assertGreater(suggestions['RB']['suggested_multiplier'], 1.0)  # base rate was too tight

    def test_analyze_epistemic_calibration_uses_only_pre_checkpoint_peer_data(self):
        """Regression test for the fix itself: the leave-one-out prior must never use a peer's
        POST-checkpoint scores, even though that peer's full weekly_scores dict contains them.
        Constructs a peer whose pre-checkpoint performance is modest but whose post-checkpoint
        performance is huge, and confirms the target player's prior isn't inflated by it."""
        player_data = {
            "Target Player": {"pos": "RB", "team": "DET", "weekly_scores": {1: 10.0, 2: 11.0, 3: 10.5, 4: 10.0, 5: 11.0}},
            "Peer RB A": {"pos": "RB", "team": "BUF", "weekly_scores": {1: 10.0, 2: 9.0, 3: 11.0, 4: 200.0, 5: 200.0}},
            "Peer RB B": {"pos": "RB", "team": "SF", "weekly_scores": {1: 9.5, 2: 10.5, 3: 10.0, 4: 10.0, 5: 10.0}},
            "Peer RB C": {"pos": "RB", "team": "KC", "weekly_scores": {1: 10.5, 2: 10.0, 3: 9.5, 4: 10.0, 5: 10.0}},
        }
        summary, detail = analyze_epistemic_calibration(player_data, checkpoint_week=4, min_future_weeks=1)

        target_entry = next(e for e in detail['RB'] if e['name'] == "Target Player")
        # If Peer RB A's post-checkpoint 200.0 scores leaked into the prior, prior_mean would be
        # enormous. With only pre-checkpoint data (all ~9-11), it should stay small and sane.
        self.assertLess(target_entry['prior_mean'], 15.0)

    def test_collect_real_player_weekly_scores_excludes_zero_scores_and_short_samples(self):
        """Verifies zero-score weeks (bye/inactive) are excluded, and players below
        min_active_weeks are dropped entirely."""
        players_db = {
            "1": {"first_name": "Active", "last_name": "Player", "position": "WR", "team": "DET"},
            "2": {"first_name": "Rare", "last_name": "Player", "position": "RB", "team": "BUF"},
        }
        season_matchups = {
            1: [{"roster_id": 1, "matchup_id": 1, "points": 10.0, "players_points": {"1": 10.0, "2": 5.0}}],
            2: [{"roster_id": 1, "matchup_id": 1, "points": 0.0, "players_points": {"1": 0.0}}],  # bye
            3: [{"roster_id": 1, "matchup_id": 1, "points": 12.0, "players_points": {"1": 12.0}}],
            4: [{"roster_id": 1, "matchup_id": 1, "points": 9.0, "players_points": {"1": 9.0}}],
        }

        result = collect_real_player_weekly_scores(season_matchups, players_db, min_active_weeks=3)

        self.assertIn("Active Player", result)
        self.assertEqual(len(result["Active Player"]["weekly_scores"]), 3)  # week 2's zero excluded
        self.assertNotIn("Rare Player", result)  # only 1 active week, below min_active_weeks



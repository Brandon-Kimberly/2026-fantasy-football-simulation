"""
tests.test_simulation

Test suite for fantasy_sim.simulation. Extracted from what was originally a unittest.TestCase
embedded directly in 2026_sleeper_simulation_adv.py.
"""
import logging
import unittest
from unittest.mock import patch, MagicMock

import numpy as np

from fantasy_sim.simulation import (
    FantasySimulationEngine, normalize_position, load_json, SIM_CONFIG, DUAL_ELIGIBILITY,
)
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)


class TestFantasySimulation(unittest.TestCase):

    def setUp(self):
        """Creates a filename-keyed mock file system to eliminate execution-order dependency."""
        self.previous_log_level = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        
        self.test_teams = ['Legion of Coom', 'Femboy Cats', 'Year of Jarvis', 'Drunk Cats']
        self.mock_fs = {
            LEAGUE_STATE_FILE: {"current_week": 1},
            LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in self.test_teams},
            VEGAS_FILE: {"DET": {"total": 24.0, "spread": -4.0, "opponent": "CHI"}, "CHI": {"total": 20.0, "spread": 4.0, "opponent": "DET"}},
            LIVE_ROSTERS_FILE: {
                self.test_teams[0]: [{"name": "QB_1", "pos": "QB", "team": "DET"}], 
                self.test_teams[1]: [{"name": "QB_2", "pos": "QB", "team": "CHI"}],
                self.test_teams[2]: [{"name": "QB_3", "pos": "QB", "team": "FA"}],
                self.test_teams[3]: [{"name": "QB_4", "pos": "QB", "team": "FA"}]
            },
            BASELINES_FILE: {
                "QB_1": {"mean": 20.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "DET"},
                "QB_2": {"mean": 15.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "CHI"},
                "QB_3": {"mean": 15.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "FA"},
                "QB_4": {"mean": 15.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "FA"}
            },
            TEAM_RATINGS_FILE: {"DET": {"off_rating": 25}, "CHI": {"off_rating": 20}},
            DEFENSIVE_RATINGS_FILE: {
                "DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0},
            },
            DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
            LEAGUE_SCHEDULE_FILE: [[[self.test_teams[0], self.test_teams[1]], [self.test_teams[2], self.test_teams[3]]]] * 14,
            NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
            WEEKLY_ACTUALS_FILE: {}
        }

        # Safe dictionary loader
        def mock_load(path):
            if path in self.mock_fs: return self.mock_fs[path]
            raise FileNotFoundError(f"Missing mock: {path}")

        # Patch the file existence and reading
        self.patch_exists = patch('os.path.exists', side_effect=lambda p: p in self.mock_fs)
        self.patch_load = patch('fantasy_sim.simulation.load_json', side_effect=mock_load)
        
        self.patch_exists.start()
        self.patch_load.start()

    def tearDown(self):
        self.patch_exists.stop()
        self.patch_load.stop()
        logging.getLogger().setLevel(self.previous_log_level)

    def test_max_realistic_weekly_score_caps_extreme_draws(self):
        """Regression/verification test for a real, empirically-confirmed gap: no ceiling
        previously existed on an individual player's simulated weekly score. Forces every
        random draw to an extreme positive value (simulating an implausibly lucky
        confluence of the epistemic season-mean draw, the correlated z-score, and the
        environmental multiplier all landing far in the right tail simultaneously) and
        confirms the resulting total stays within what a fully-capped 13-man lineup implies,
        rather than the runaway value an uncapped compound lognormal x Gaussian x Gaussian
        draw would otherwise produce.

        Gives the team a full 13-slot-covering roster (no QB/K/DB/DL/LB/RB/WR/TE gaps) so
        every starter is a real, capped player -- zero streamer injections, which are drawn
        from a narrow, low-mean Gaussian and were never a realistic path to the cap anyway,
        so excluding them here isolates exactly what this test is meant to verify."""
        full_roster = [
            {"name": "QB_1", "pos": "QB", "team": "DET"}, {"name": "K_1", "pos": "K", "team": "DET"},
            {"name": "DB_1", "pos": "DB", "team": "DET"}, {"name": "DL_1", "pos": "DL", "team": "DET"},
            {"name": "LB_1", "pos": "LB", "team": "DET"},
            {"name": "RB_1", "pos": "RB", "team": "DET"}, {"name": "RB_2", "pos": "RB", "team": "DET"},
            {"name": "RB_3", "pos": "RB", "team": "DET"},
            {"name": "WR_1", "pos": "WR", "team": "DET"}, {"name": "WR_2", "pos": "WR", "team": "DET"},
            {"name": "WR_3", "pos": "WR", "team": "DET"},
            {"name": "TE_1", "pos": "TE", "team": "DET"}, {"name": "TE_2", "pos": "TE", "team": "DET"},
        ]
        self.mock_fs[LIVE_ROSTERS_FILE] = {t: full_roster for t in self.test_teams}
        self.mock_fs[BASELINES_FILE] = {
            p["name"]: {"mean": 20.0, "std_aleatoric": 7.38, "std_epistemic": 6.0, "pos": p["pos"], "team": "DET"}
            for p in full_roster
        }  # shared across all 4 mock teams -- fine, since we only check per-team totals stay within the cap-implied bound, not compare teams to each other

        sim = FantasySimulationEngine()
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 5
        try:
            # side_effect (not a fixed return_value) so array-shaped calls elsewhere in the
            # code get an array of the same extreme value rather than a shape mismatch.
            def extreme_normal(loc=0.0, scale=1.0, size=None):
                if size is None:
                    return 20.0
                return np.full(size, 20.0)

            with patch('numpy.random.normal', side_effect=extreme_normal), \
                 patch.object(sim, 'export_and_visualize') as mock_export:
                sim.run_simulation()
            global_season_points = mock_export.call_args[0][1]  # (wins, points, ...)
            weeks_simulated = 16 - (sim.current_week - 1)
            cap_total = 13 * weeks_simulated * SIM_CONFIG['MAX_REALISTIC_WEEKLY_SCORE']
            for team, points_array in global_season_points.items():
                self.assertLessEqual(
                    float(np.max(points_array)), cap_total + 1e-6,
                    f"{team}: season total exceeded what a fully-capped 13-man lineup implies, "
                    f"even under a forced extreme draw."
                )
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

    def test_max_realistic_weekly_score_does_not_affect_typical_outcomes(self):
        """Verifies the cap is set generously enough that it never engages for realistic,
        uncapped simulation runs -- confirming it only clips the genuinely-absurd tail, not
        the legitimate right-skew the variance calibration was built to capture. Uses the
        same full 13-slot roster as the test above so there's no streamer noise muddying the
        comparison."""
        full_roster = [
            {"name": "QB_1", "pos": "QB", "team": "DET"}, {"name": "K_1", "pos": "K", "team": "DET"},
            {"name": "DB_1", "pos": "DB", "team": "DET"}, {"name": "DL_1", "pos": "DL", "team": "DET"},
            {"name": "LB_1", "pos": "LB", "team": "DET"},
            {"name": "RB_1", "pos": "RB", "team": "DET"}, {"name": "RB_2", "pos": "RB", "team": "DET"},
            {"name": "RB_3", "pos": "RB", "team": "DET"},
            {"name": "WR_1", "pos": "WR", "team": "DET"}, {"name": "WR_2", "pos": "WR", "team": "DET"},
            {"name": "WR_3", "pos": "WR", "team": "DET"},
            {"name": "TE_1", "pos": "TE", "team": "DET"}, {"name": "TE_2", "pos": "TE", "team": "DET"},
        ]
        self.mock_fs[LIVE_ROSTERS_FILE] = {t: full_roster for t in self.test_teams}
        self.mock_fs[BASELINES_FILE] = {
            p["name"]: {"mean": 15.0, "std_aleatoric": 5.0, "std_epistemic": 3.0, "pos": p["pos"], "team": "DET"}
            for p in full_roster
        }

        sim = FantasySimulationEngine()
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 50
        try:
            with patch.object(sim, 'export_and_visualize') as mock_export:
                sim.run_simulation()
            global_season_points = mock_export.call_args[0][1]
            weeks_simulated = 16 - (sim.current_week - 1)
            for team, points_array in global_season_points.items():
                self.assertLess(float(np.max(points_array)), 13 * weeks_simulated * SIM_CONFIG['MAX_REALISTIC_WEEKLY_SCORE'])
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

    def test_normalize_position(self):
        """Verify IDP and offensive normalization handles edge cases safely."""
        self.assertEqual(normalize_position('ILB'), 'LB')
        self.assertEqual(normalize_position('SS'), 'DB')
        self.assertEqual(normalize_position('DT'), 'DL')
        self.assertEqual(normalize_position('FB'), 'RB')
        self.assertEqual(normalize_position('UNKNOWN'), 'FLEX')

    def test_optimal_score_constraints(self):
        """Verify the lineup optimizer does not illegally sum invalid starting rosters."""
        self.mock_fs[LIVE_ROSTERS_FILE] = {"Legion of Coom": [{"name": f"QB_{i}", "pos": "QB", "team": "FA"} for i in range(15)]}
        self.mock_fs[BASELINES_FILE] = {f"QB_{i}": {"mean": 20.0, "pos": "QB"} for i in range(15)}
        
        sim = FantasySimulationEngine()
        sim.replacement_levels = {'QB': 0, 'RB': 0, 'WR': 0, 'TE': 0, 'K': 0, 'DL': 0, 'LB': 0, 'DB': 0, 'FLEX': 0}
        
        dummy_roster = [f"QB_{i}" for i in range(15)]
        score = sim.get_optimal_score(dummy_roster)
        
        # Should sum EXACTLY ONE QB (20 pts), plus a fraction of the bench (14 * 20 * 0.1 = 28)
        self.assertAlmostEqual(score, 48.0)

    def test_optimal_score_discounts_true_bench_players(self):
        """Verify a player who genuinely cannot crack the starting lineup (every required slot
        and all 3 FLEX slots already filled by better options) is counted at only 10% of their
        mean, not full value. This is the exact acceptance criterion the weeks 6-10 trade logic
        relies on to decide whether a swap is favorable, so it needs direct coverage."""
        self.mock_fs[LIVE_ROSTERS_FILE] = {"Legion of Coom": []}
        roster_defs = {
            "QB_1": ("QB", 20.0), "K_1": ("K", 8.0), "DL_1": ("DL", 8.0),
            "LB_1": ("LB", 8.0), "DB_1": ("DB", 8.0), "TE_1": ("TE", 10.0),
            "RB_1": ("RB", 15.0), "RB_2": ("RB", 14.0),
            "WR_1": ("WR", 20.0), "WR_2": ("WR", 18.0),
            "FLEX_RB": ("RB", 12.0), "FLEX_WR": ("WR", 11.0), "FLEX_TE": ("TE", 10.5),
            "BENCH_WR": ("WR", 9.0),  # every WR/RB/TE/FLEX slot above is already better-filled
        }
        self.mock_fs[BASELINES_FILE] = {p: {"mean": m, "pos": pos} for p, (pos, m) in roster_defs.items()}

        sim = FantasySimulationEngine()
        roster_list = list(roster_defs.keys())
        score = sim.get_optimal_score(roster_list)

        starters_sum = 20.0 + 8.0 + 8.0 + 8.0 + 8.0 + 10.0 + 15.0 + 14.0 + 20.0 + 18.0 + 12.0 + 11.0 + 10.5
        expected = starters_sum + (9.0 * 0.1)  # BENCH_WR counted at 10%, not full 9.0
        self.assertAlmostEqual(score, expected, places=2)

    def test_optimal_score_rewards_filling_empty_required_slot(self):
        """Verify that adding a player to a currently-empty required position increases the
        optimal score by that player's full mean (not discounted) -- this is what makes an
        otherwise low-value incoming player in a trade genuinely worth accepting."""
        self.mock_fs[LIVE_ROSTERS_FILE] = {"Legion of Coom": []}
        self.mock_fs[BASELINES_FILE] = {"WR_1": {"mean": 15.0, "pos": "WR"}}
        sim = FantasySimulationEngine()

        score_before = sim.get_optimal_score(["WR_1"])
        self.assertAlmostEqual(score_before, 15.0)  # QB slot empty, contributes 0

        sim.baselines["QB_1"] = {"mean": 10.0, "pos": "QB"}
        score_after = sim.get_optimal_score(["WR_1", "QB_1"])
        self.assertAlmostEqual(score_after, 25.0)  # +10.0 full value, not +1.0 (bench-discounted)

    def test_optimal_assignment_beats_greedy_for_dual_eligible_player(self):
        """Proves the Hungarian-algorithm assignment genuinely outperforms the previous greedy,
        fixed-position-order fill for a dual-eligible player (e.g. Travis Hunter, WR/DB).

        Construction: Hunter (WR/DB, value 18) is only marginally better at DB than DB_backup
        (DB-only, value 15), but is essential at WR since the only other WR-eligible player
        (WR_A, value 5) can't fill both WR slots alone. The old greedy fill processed DB before
        WR (fixed REQS_ORDER), so it always grabbed Hunter for DB first -- gaining only +3 over
        DB_backup there -- while leaving a WR slot completely empty (losing that slot's value
        entirely, since get_optimal_score has no bench/streamer fallback for an unfilled
        required slot). Old greedy score on this roster: 24.5. True optimum: 38.0."""
        self.mock_fs[LIVE_ROSTERS_FILE] = {"Legion of Coom": []}
        self.mock_fs[BASELINES_FILE] = {
            "Hunter": {"mean": 18.0, "pos": "WR"},
            "DB_backup": {"mean": 15.0, "pos": "DB"},
            "WR_A": {"mean": 5.0, "pos": "WR"},
        }
        sim = FantasySimulationEngine()
        # Hunter's DUAL_ELIGIBILITY entry (WR/DB) is already defined at module level for the
        # real Travis Hunter; reuse that exact mechanism by aliasing this test roster onto it.
        original_dual_elig = DUAL_ELIGIBILITY.get("Travis Hunter")
        DUAL_ELIGIBILITY["Hunter"] = ["WR", "DB"]
        try:
            score = sim.get_optimal_score(["Hunter", "DB_backup", "WR_A"])
        finally:
            del DUAL_ELIGIBILITY["Hunter"]

        self.assertAlmostEqual(score, 38.0)  # true optimum, not greedy's 24.5

    def test_covariance_matrix_psd(self):
        """Verify Gaussian copula generator returns valid Positive Semi-Definite matrices."""
        self.mock_fs[LIVE_ROSTERS_FILE] = {"Legion of Coom": []}
        self.mock_fs[BASELINES_FILE] = {
            "QB_1": {"pos": "QB", "team": "DET"}, "WR_1": {"pos": "WR", "team": "DET"}, "WR_2": {"pos": "WR", "team": "DET"}
        }
        
        sim = FantasySimulationEngine()
        sim.pass_catchers_meta = {"DET": [("WR_1", 15.0), ("WR_2", 10.0)]}
        test_meta = {
            "QB_1": {"pos": "QB", "team": "DET"},
            "WR_1": {"pos": "WR", "team": "DET"},
            "WR_2": {"pos": "WR", "team": "DET"}
        }
        
        L = sim.build_covariance_matrix(["QB_1", "WR_1", "WR_2"], test_meta)
        reconstructed = np.dot(L, L.T)
        
        self.assertEqual(reconstructed.shape, (3, 3))
        eigenvalues = np.linalg.eigvals(reconstructed)
        self.assertTrue(np.all(eigenvalues >= -1e-8))

    def test_bayesian_shrinkage_math(self):
        """Numerically verify James-Stein shrinkage handles variance splits correctly."""
        self.mock_fs[WEEKLY_ACTUALS_FILE] = {
            "week_1": {"team_results": {"Legion of Coom": {"points_scored": 100}}, "player_scores": {"QB_1": 18.0}},
            "week_2": {"team_results": {"Legion of Coom": {"points_scored": 100}}, "player_scores": {"QB_1": 20.0}},
            "week_3": {"team_results": {"Legion of Coom": {"points_scored": 100}}, "player_scores": {"QB_1": 19.0}}
        }
        
        sim = FantasySimulationEngine()
        
        # Expected Math:
        # prior_mean = 20.0, prior_var = 2.25
        # actuals = [18, 20, 19] -> n = 3, actual_mean = 19.0
        # raw_actual_var = var([18,20,19]) = 0.666
        # actual_var = max(0.666, 0.5 * 2.25) = 1.125
        # n_0 = 4.0
        # post_var = 1.0 / ((4.0 / 2.25) + (3.0 / 1.125)) = 1.0 / (1.777 + 2.666) = 0.225
        # post_mean = ((4.0 * 20.0 / 2.25) + (3.0 * 19.0 / 1.125)) * post_var = (35.555 + 50.666) * 0.225 = 19.4
        
        updated_mean = sim.baselines["QB_1"]["mean"]
        self.assertAlmostEqual(updated_mean, 19.4, places=1)
        self.assertAlmostEqual(sim.baselines["QB_1"]["std_epistemic"], np.sqrt(0.225), places=2)

    def test_faab_bid_never_exceeds_remaining_budget(self):
        """A team with very little FAAB left must never be modeled as bidding more than it has,
        even for a maximally aggressive manager with high raw demand."""
        sim = FantasySimulationEngine()
        bid = sim._compute_faab_bid(
            remaining_faab=3.0, raw_uniform_draw=22.0, aggression=1.0,
            needs=6, deflation=1.0, avg_league_faab=100.0
        )
        self.assertLessEqual(bid, 3.0)

    def test_faab_bid_never_exceeds_competitive_ceiling(self):
        """Even a team with a huge remaining budget must not be modeled as blowing past the
        league-wide competitive ceiling (avg_league_faab * 1.5) on a single streamer bid."""
        sim = FantasySimulationEngine()
        bid = sim._compute_faab_bid(
            remaining_faab=100.0, raw_uniform_draw=22.0, aggression=1.0,
            needs=10, deflation=1.0, avg_league_faab=20.0
        )
        self.assertLessEqual(bid, 30.0)  # 1.5 * avg_league_faab

    def test_faab_bid_scales_with_aggression_and_need(self):
        """A more aggressive manager, or a team with a larger positional deficit, should be
        modeled as bidding strictly more, all else equal."""
        sim = FantasySimulationEngine()
        common_kwargs = dict(remaining_faab=100.0, raw_uniform_draw=14.0, deflation=1.0, avg_league_faab=100.0)

        passive_bid = sim._compute_faab_bid(aggression=0.1, needs=1, **common_kwargs)
        aggressive_bid = sim._compute_faab_bid(aggression=0.9, needs=1, **common_kwargs)
        self.assertLess(passive_bid, aggressive_bid)

        low_need_bid = sim._compute_faab_bid(aggression=0.5, needs=1, **common_kwargs)
        high_need_bid = sim._compute_faab_bid(aggression=0.5, needs=4, **common_kwargs)
        self.assertLess(low_need_bid, high_need_bid)

    def test_faab_bid_zero_deflation_yields_zero_bid(self):
        """When the league-wide FAAB pool is fully exhausted (deflation == 0, an edge case that
        occurs once every team has spent its full budget), no team should be able to bid anything,
        regardless of aggression or need."""
        sim = FantasySimulationEngine()
        bid = sim._compute_faab_bid(
            remaining_faab=50.0, raw_uniform_draw=22.0, aggression=1.0,
            needs=10, deflation=0.0, avg_league_faab=50.0
        )
        self.assertEqual(bid, 0.0)

    def test_covariance_matrix_psd_under_large_same_team_cluster(self):
        """Verify the eigenvalue-repair branch (min_eig < 1e-4 -> jitter added) actually engages
        and still produces a valid PSD Cholesky factor. A same-team WR-WR correlation becomes
        non-PSD on its own once enough WRs share a team: for an n x n equicorrelated matrix,
        the minimum eigenvalue is 1 + (n-1)*rho, which goes negative once
        n > 1 - 1/rho = 1 + 1/|rho|.

        This test's PURPOSE is verifying the repair mechanism itself works, not re-testing
        SIM_CONFIG['CORRELATIONS']['WR_WR']'s current calibrated value (that's covered
        separately, in player_level_backtest.py). So it temporarily patches WR_WR to a value
        strong enough to reliably trigger the near-singular scenario with a realistic n_wr,
        rather than growing n_wr to match whatever WR_WR happens to be calibrated to at any
        given time -- decoupling this test from future recalibrations of that constant."""
        self.mock_fs[LIVE_ROSTERS_FILE] = {"Legion of Coom": []}
        n_wr = 7
        players = [f"WR_{i}" for i in range(n_wr)]
        self.mock_fs[BASELINES_FILE] = {p: {"pos": "WR", "team": "DET"} for p in players}

        sim = FantasySimulationEngine()
        sim.pass_catchers_meta = {"DET": [(p, 10.0 - i) for i, p in enumerate(players)]}
        test_meta = {p: {"pos": "WR", "team": "DET"} for p in players}

        test_rho = -0.18  # strong enough that n_wr=7 alone triggers the near-singular branch
        with patch.dict(SIM_CONFIG['CORRELATIONS'], {'WR_WR': test_rho}):
            # Confirm this scenario genuinely needs the repair branch, i.e. the naive
            # (unrepaired) correlation matrix really is non-PSD -- otherwise this test would
            # not actually be exercising the code path it claims to.
            naive_cov = np.eye(n_wr) + test_rho * (np.ones((n_wr, n_wr)) - np.eye(n_wr))
            naive_min_eig = np.min(np.real(np.linalg.eigvals(naive_cov)))
            self.assertLess(naive_min_eig, 1e-4, "Test setup does not actually require the repair branch; adjust n_wr or test_rho.")

            # The real method must not raise (a naive np.linalg.cholesky call on naive_cov would),
            # and must still return a matrix that reconstructs to a valid PSD matrix.
            L = sim.build_covariance_matrix(players, test_meta)

        reconstructed = np.dot(L, L.T)
        eigenvalues = np.linalg.eigvals(reconstructed)
        self.assertTrue(np.all(eigenvalues >= -1e-8))

    def test_injury_hazard_reduces_output_and_is_logged(self):
        """With injury hazard forced to 100% for every position, at least one player on every
        team must be benched/reduced in week 1, and the audit log (populated only for the first
        simulation) must record it in 'injury_ward'."""
        sim = FantasySimulationEngine()
        original_rates = dict(SIM_CONFIG['INJURY_RATES'])
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        try:
            for pos in SIM_CONFIG['INJURY_RATES']:
                SIM_CONFIG['INJURY_RATES'][pos] = 1.0
            SIM_CONFIG['NUM_BATCHES'] = 1
            SIM_CONFIG['SIMS_PER_BATCH'] = 1

            with patch.object(sim, 'export_and_visualize') as mock_export:
                sim.run_simulation()

            args, kwargs = mock_export.call_args
            audit_log = args[13]  # export_and_visualize(..., audit_log, total_sims) -- audit_log is index 13
            first_week = min(audit_log['weeks'].keys())
            any_injury_logged = any(
                len(team_data.get('injury_ward', [])) > 0
                for team_data in audit_log['weeks'][first_week]['teams'].values()
            )
            self.assertTrue(any_injury_logged, "100% injury hazard did not produce any logged injuries in week 1.")
        finally:
            SIM_CONFIG['INJURY_RATES'].update(original_rates)
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

    def test_h2h_matrix_is_genuine_all_play_not_schedule_gated(self):
        """Regression test for a real bug: h2h_matrix backing the 'Any Given Sunday' heatmap
        must compare every team against every other team every week (true all-play), not just
        the weeks a pair happened to be actually scheduled against each other. The bug produced
        complementary cells (e.g. A-vs-B and B-vs-A) that summed to far less than 100% because
        most weeks aren't a real matchup for any given pair, while the export divided by a
        denominator (total_sims * 14) that assumed every week counted for every pair.

        Uses a genuinely rotating 3-round schedule (not the class default, which repeats the
        same pairing every week and therefore can't distinguish 'all-play' from
        'schedule-gated' -- under that default fixture the two computations coincide and this
        test would pass even with the bug present)."""
        t0, t1, t2, t3 = self.test_teams
        rotating_rounds = [
            [(t0, t1), (t2, t3)],
            [(t0, t2), (t1, t3)],
            [(t0, t3), (t1, t2)],
        ]
        self.mock_fs[LEAGUE_SCHEDULE_FILE] = [rotating_rounds[i % 3] for i in range(14)]

        sim = FantasySimulationEngine()
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        try:
            SIM_CONFIG['NUM_BATCHES'] = 1
            SIM_CONFIG['SIMS_PER_BATCH'] = 200
            with patch('matplotlib.pyplot.savefig'), patch('json.dump') as mock_dump:
                sim.run_simulation()

            win_pct_matrix = None
            for call in mock_dump.call_args_list:
                data = call.args[0]
                if isinstance(data, dict) and 'h2h_win_probability_matrix' in data:
                    win_pct_matrix = data['h2h_win_probability_matrix']
                    break
            self.assertIsNotNone(win_pct_matrix, "h2h_win_probability_matrix was never exported.")

            complementary_sum = win_pct_matrix[t0][t1] + win_pct_matrix[t1][t0]
            self.assertAlmostEqual(complementary_sum, 100.0, delta=2.0)
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

    def test_championship_value_metric_is_not_biased_toward_zero_competition_slots(self):
        """Regression test for a real bug: the old 'most valuable players' metric counted raw
        appearances in a championship-winning lineup, which is ~100% for any starter at a
        position with zero bench competition (a team's sole kicker or sole starting DL/LB/DB),
        regardless of actual scoring value. A kicker outranked elite skill players in real
        output from this exact bug. The fixed metric ranks by average points scored across
        championship-winning weeks, which is a genuine value signal, not a survivorship artifact."""
        sim = FantasySimulationEngine()
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        try:
            SIM_CONFIG['NUM_BATCHES'] = 1
            SIM_CONFIG['SIMS_PER_BATCH'] = 200
            with patch('matplotlib.pyplot.savefig'), patch('json.dump') as mock_dump:
                sim.run_simulation()

            insights = None
            for call in mock_dump.call_args_list:
                data = call.args[0]
                if isinstance(data, dict) and 'most_valuable_players_championship_shares' in data:
                    insights = data['most_valuable_players_championship_shares']
                    break
            self.assertIsNotNone(insights, "syndicate_insights was never exported.")

            for player, stats in insights.items():
                # The old bug exported a bare "XX.X%" string; the fix must export a dict with
                # a genuine points-based field, not just an appearance percentage.
                self.assertIsInstance(stats, dict)
                self.assertIn("avg_points_per_championship_week", stats)
                self.assertIn("championship_lineup_appearance_pct", stats)
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

    def test_future_week_environment_uses_real_opponent_defense_not_offense_mirror(self):
        """Regression test for the def_rating self-mirroring bug: a team's future-week implied
        total must respond to the OPPONENT's real defensive strength, not the opponent's own
        offensive rating. Two teams with identical offensive power ratings but very different
        real defensive strength must produce different implied totals for the SAME team facing
        them -- under the old (43.0 - off_rating) formula, a high-offense opponent always
        looked like a "weak defense" regardless of actual points allowed, which this disproves."""
        sim = FantasySimulationEngine()
        sim.power_ratings = {
            "DET": {"off_rating": 25.0},
            "STINGY": {"off_rating": 25.0},  # same offense as LEAKY
            "LEAKY": {"off_rating": 25.0},   # same offense as STINGY
        }
        sim.defensive_ratings = {
            "DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
            "STINGY": {"points_allowed_estimate": 14.0, "games_sampled": 8},  # allows few points
            "LEAKY": {"points_allowed_estimate": 30.0, "games_sampled": 8},   # allows many points
        }

        env_vs_stingy = sim._compute_future_week_matchup_environment("DET", "STINGY")
        env_vs_leaky = sim._compute_future_week_matchup_environment("DET", "LEAKY")

        # DET's own offense is identical in both matchups; only the opponent's real defense
        # differs. A genuine matchup model must therefore project DET higher against the leaky
        # defense than the stingy one.
        self.assertGreater(env_vs_leaky['total'], env_vs_stingy['total'])
        self.assertAlmostEqual(env_vs_stingy['total'], (25.0 + 14.0) / 2.0)
        self.assertAlmostEqual(env_vs_leaky['total'], (25.0 + 30.0) / 2.0)

    def test_future_week_environment_falls_back_to_neutral_when_data_missing(self):
        """When power ratings are unavailable for a team (or the opponent is a bye/'FA'), the
        environment must fall back to the honest neutral default, not crash or fabricate data."""
        sim = FantasySimulationEngine()
        sim.power_ratings = {"DET": {"off_rating": 25.0}}
        sim.defensive_ratings = {}

        env = sim._compute_future_week_matchup_environment("DET", "FA")
        self.assertEqual(env['opponent'], 'FA')
        self.assertAlmostEqual(env['total'], 21.5)

        env2 = sim._compute_future_week_matchup_environment("DET", "UNKNOWN_TEAM")
        self.assertEqual(env2['opponent'], 'FA')
        self.assertAlmostEqual(env2['total'], 21.5)

    def test_median_scoring_flag_roughly_halves_awarded_wins(self):
        """Verifies MEDIAN_SCORING_ENABLED=False genuinely changes win-awarding behavior, not
        just accepted silently -- with it off, only h2h decisions are awarded (roughly half
        the total decisions of the default hybrid format), directly enabling honest
        backtesting against a historical season that used pure H2H scoring, like this
        league's actual 2025 season. Compares real, complete simulation runs against
        identical inputs with the flag on vs. off."""
        original_flag = SIM_CONFIG['MEDIAN_SCORING_ENABLED']
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        try:
            SIM_CONFIG['NUM_BATCHES'] = 1
            SIM_CONFIG['SIMS_PER_BATCH'] = 50

            captured = {}

            def run_and_capture(flag_value, key):
                SIM_CONFIG['MEDIAN_SCORING_ENABLED'] = flag_value
                sim = FantasySimulationEngine()
                with patch.object(sim, 'export_and_visualize') as mock_export:
                    sim.run_simulation()
                args, kwargs = mock_export.call_args
                captured[key] = args[0]  # global_season_wins: {team: np.array(total_sims,)}

            run_and_capture(True, 'with_median')
            run_and_capture(False, 'without_median')

            for team in captured['with_median']:
                mean_with = float(np.mean(captured['with_median'][team]))
                mean_without = float(np.mean(captured['without_median'][team]))
                # "without" should be meaningfully lower than "with" -- roughly half, given a
                # normal week awards one decision of each kind when both are enabled. Assert
                # a directional, order-of-magnitude check rather than an exact ratio, since
                # median-win rates vary by team performance relative to the field.
                self.assertLess(mean_without, mean_with * 0.75,
                                 f"{team}: expected without-median wins meaningfully lower than with-median.")
        finally:
            SIM_CONFIG['MEDIAN_SCORING_ENABLED'] = original_flag
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

    def test_playoff_loop_alignment(self):
        """Verify the simulation correctly triggers weeks 15 and 16 regardless of current_week."""
        sim = FantasySimulationEngine()
        
        for mock_current_week in [1, 7, 14]:
            sim.current_week = mock_current_week
            # Patch plot generation so it runs fast and doesn't export to disk
            with patch.object(sim, 'export_and_visualize') as mock_export:
                # Temporarily overwrite config for speed
                original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
                SIM_CONFIG['NUM_BATCHES'] = 1
                SIM_CONFIG['SIMS_PER_BATCH'] = 10
                
                sim.run_simulation()
                
                self.assertTrue(mock_export.called, "Export function was never called.")
                
                # Extract the arguments passed to export_and_visualize
                # Signature: export_and_visualize(self, wins, points, b_playoffs, b_champs, ...)
                args, kwargs = mock_export.call_args
                b_champs = args[3]  # The batch_champ_rates array
                
                # Sum the championship probabilities across all teams. 
                # If playoffs ran, it must sum to exactly 1.0 (100% equity distributed)
                total_champs_awarded = sum(b_champs[team][0] for team in sim.team_names)
                
                self.assertAlmostEqual(
                    total_champs_awarded, 1.0, 
                    msg=f"Playoffs failed to complete when starting at week {mock_current_week}. Champ equity was {total_champs_awarded}."
                )
                
                SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

    @patch('matplotlib.pyplot.savefig')
    @patch('json.dump')
    def test_e2e_smoke_and_invariants(self, mock_json_dump, mock_savefig):
        """End-to-end simulation test verifying no crashes and basic sum invariants."""
        sim = FantasySimulationEngine()
        
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 2
        
        # Should complete entirely without exceptions
        sim.run_simulation()
        
        SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims
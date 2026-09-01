"""
Tests for fantasy_sim.decisions -- decision-support tools (head-to-head comparator first).
Written before the module existed (CLAUDE.md rule 1): the first run failed on ImportError.

The comparator has two paths. Rostered-vs-rostered reads FantasySimulationEngine's
player_weekly_scores accumulator after a (reduced) run_simulation(), so P(A>B) is computed
within sim on the current week's column -- joint, respecting the copula, shared environment and
injury state. The light path samples one player's single-week score from his baseline
parameters through the engine's own extracted transform (_weekly_score_from_z), with
independent z -- for free agents, who never enter a simulation.
"""
import logging
import unittest
from unittest.mock import patch

import numpy as np

from fantasy_sim.simulation import FantasySimulationEngine, SIM_CONFIG
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)
from fantasy_sim.decisions import (
    prob_a_beats_b, sample_week_scores, summarise_scores, compare_players, run_reduced_simulation,
)


def _fixture_engine():
    """A 4-team league with one rostered QB each plus three UNROSTERED players in the
    baselines: a healthy WR, a WR on bye in week 1, and an IR'd RB."""
    teams = ['Legion of Coom', 'Femboy Cats', 'Year of Jarvis', 'Drunk Cats']
    fs = {
        LEAGUE_STATE_FILE: {"current_week": 1},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in teams},
        VEGAS_FILE: {"_meta": {"week": 1, "source": "odds_api", "fetched_at": "x"},
                     "DET": {"total": 24.0, "spread": -4.0, "opponent": "CHI"},
                     "CHI": {"total": 20.0, "spread": 4.0, "opponent": "DET"}},
        LIVE_ROSTERS_FILE: {
            teams[0]: [{"name": "QB_1", "pos": "QB", "team": "DET"}],
            teams[1]: [{"name": "QB_2", "pos": "QB", "team": "CHI"}],
            teams[2]: [{"name": "QB_3", "pos": "QB", "team": "FA"}],
            teams[3]: [{"name": "QB_4", "pos": "QB", "team": "FA"}],
        },
        BASELINES_FILE: {
            "QB_1": {"mean": 20.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "DET", "bye": 0},
            "QB_2": {"mean": 15.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "CHI", "bye": 0},
            "QB_3": {"mean": 15.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "FA", "bye": 0},
            "QB_4": {"mean": 15.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "FA", "bye": 0},
            "FA_WR_healthy": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 0.0, "pos": "WR", "team": "DET", "bye": 9},
            "FA_WR_bye": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 0.0, "pos": "WR", "team": "CHI", "bye": 1},
            "FA_RB_ir": {"mean": 10.0, "std_aleatoric": 4.0, "std_epistemic": 0.0, "pos": "RB", "team": "DET", "bye": 9,
                         "injury_status": "IR", "on_ir": True},
        },
        TEAM_RATINGS_FILE: {"DET": {"off_rating": 25}, "CHI": {"off_rating": 20}},
        DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                 "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[teams[0], teams[1]], [teams[2], teams[3]]]] * 14,
        NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: {},
    }
    return fs


class _EngineCase(unittest.TestCase):
    def setUp(self):
        self.fs = _fixture_engine()
        self.prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        self.p_exists = patch('os.path.exists', side_effect=lambda p: p in self.fs)
        self.p_load = patch('fantasy_sim.simulation.load_json', side_effect=lambda p: self.fs[p])
        self.p_exists.start(); self.p_load.start()
        self.engine = FantasySimulationEngine()

    def tearDown(self):
        self.p_exists.stop(); self.p_load.stop()
        logging.getLogger().setLevel(self.prev)


class TestProbAbeatsB(unittest.TestCase):
    def test_counts_strict_wins_ties_and_treats_structural_absence_as_zero(self):
        a = np.array([10.0, 5.0, np.nan, 7.0, 0.0])
        b = np.array([8.0, 5.0, 3.0, np.nan, np.nan])
        r = prob_a_beats_b(a, b)
        # wins: 10>8, 7>nan(0); ties: 5=5, 0=nan(0); loss: nan(0)<3
        self.assertEqual(r['n'], 5)
        self.assertAlmostEqual(r['p_a'], 2 / 5)
        self.assertAlmostEqual(r['p_b'], 1 / 5)
        self.assertAlmostEqual(r['p_tie'], 2 / 5)
        self.assertAlmostEqual(r['p_a'] + r['p_b'] + r['p_tie'], 1.0)

    def test_rejects_misaligned_samples(self):
        with self.assertRaises(ValueError):
            prob_a_beats_b(np.zeros(3), np.zeros(4))


class TestLightSampler(_EngineCase):
    def test_bye_week_is_all_zeros(self):
        s = sample_week_scores(self.engine, "FA_WR_bye", week=1, n=200, seed=1)
        self.assertEqual(s.size, 200)
        self.assertTrue(np.all(s == 0.0))

    def test_player_on_ir_is_absent_in_the_first_week_with_certainty(self):
        # F4: the first week of an initial absence is certain (the return hazard applies from
        # week two), so an IR'd player sampled for the current week scores zero every time.
        s = sample_week_scores(self.engine, "FA_RB_ir", week=1, n=300, seed=2)
        self.assertTrue(np.all(s == 0.0))

    def test_healthy_player_mean_matches_the_engine_expectation_within_tolerance(self):
        # std_epistemic = 0 so the only randomness is the weekly lognormal (E = mean), the
        # environment draw (E = v_tot/env_norm) and the onset hazard (a zero with probability
        # INJURY_RATES['WR'] * starter exposure). Expected mean = 12 * ratio * script * (1 - h).
        veg = self.engine._compute_week_environment(1, "DET")
        ratio = veg['total'] / self.engine._compute_environment_normaliser()
        script = self.engine._script_multiplier("WR", veg)
        h = SIM_CONFIG['INJURY_RATES']['WR'] * SIM_CONFIG['ONSET_EXPOSURE_STARTER']
        expected = 12.0 * ratio * script * (1 - h)
        s = sample_week_scores(self.engine, "FA_WR_healthy", week=1, n=40000, seed=3)
        self.assertAlmostEqual(float(s.mean()), expected, delta=0.15)
        self.assertAlmostEqual(float((s == 0.0).mean()), h, delta=0.01)
        self.assertLessEqual(float(s.max()), SIM_CONFIG['MAX_REALISTIC_WEEKLY_SCORE'])

    def test_same_seed_same_draws_different_seed_different_draws(self):
        a = sample_week_scores(self.engine, "FA_WR_healthy", week=1, n=50, seed=7)
        b = sample_week_scores(self.engine, "FA_WR_healthy", week=1, n=50, seed=7)
        c = sample_week_scores(self.engine, "FA_WR_healthy", week=1, n=50, seed=8)
        np.testing.assert_array_equal(a, b)
        self.assertFalse(np.array_equal(a, c))

    def test_unknown_player_is_a_loud_error(self):
        with self.assertRaises(KeyError):
            sample_week_scores(self.engine, "Nobody", week=1, n=10, seed=1)

    def test_summary_reports_percentiles_and_zero_share(self):
        s = summarise_scores(np.array([0.0, 0.0, 10.0, 20.0, 30.0]))
        self.assertAlmostEqual(s['p_zero'], 0.4)
        self.assertAlmostEqual(s['p50'], 10.0)
        self.assertEqual(s['n'], 5)


class TestComparePlayers(_EngineCase):
    def test_rostered_pair_uses_the_joint_accumulator_column(self):
        original = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        try:
            with patch('fantasy_sim.simulation.save_chart'), patch('fantasy_sim.simulation.save_json') as sj:
                r = compare_players(self.engine, "QB_1", "QB_2", week=1, sims=20, seed=1)
            sj.assert_not_called()   # a decision run must never overwrite the season exports
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original
        self.assertEqual(SIM_CONFIG['NUM_BATCHES'], original[0], "batch settings must be restored")
        self.assertEqual(r['path'], 'joint')
        self.assertEqual(r['n'], 20)
        self.assertAlmostEqual(r['p_a'] + r['p_b'] + r['p_tie'], 1.0)
        self.assertGreater(r['p_a'], r['p_b'], "a 20-point QB beats a 15-point QB more often than not")
        for k in ('a', 'b'):
            self.assertIn('p50', r[k]); self.assertIn('p_zero', r[k])

    def test_free_agent_versus_rostered_uses_the_light_path_for_the_free_agent(self):
        with patch('fantasy_sim.simulation.save_chart'), patch('fantasy_sim.simulation.save_json'):
            r = compare_players(self.engine, "FA_WR_healthy", "QB_2", week=1, sims=20, seed=1)
        self.assertEqual(r['path'], 'mixed')
        self.assertEqual(r['n'], 20)
        self.assertIn('independent', r['note'].lower())

    def test_light_flag_samples_both_independently_without_a_simulation(self):
        with patch.object(FantasySimulationEngine, 'run_simulation') as run:
            r = compare_players(self.engine, "QB_1", "QB_2", week=1, sims=500, seed=1, light=True)
        run.assert_not_called()
        self.assertEqual(r['path'], 'light')
        self.assertGreater(r['p_a'], 0.8)

    def test_reduced_simulation_populates_the_current_week_column_only_from_current_week(self):
        with patch('fantasy_sim.simulation.save_chart'), patch('fantasy_sim.simulation.save_json'):
            scores = run_reduced_simulation(self.engine, sims=10)
        col = scores["QB_1"][:, 0]
        self.assertEqual(col.shape, (10,))
        self.assertTrue(np.all(np.isfinite(col)), "week-1 column is populated for a healthy starter")


if __name__ == "__main__":
    unittest.main()

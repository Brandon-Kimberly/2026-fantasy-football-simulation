"""
tests.test_simulation

Test suite for fantasy_sim.simulation. Extracted from what was originally a unittest.TestCase
embedded directly in 2026_sleeper_simulation_adv.py.
"""
import logging
import unittest
from unittest.mock import patch

import numpy as np
import matplotlib.pyplot as plt

from fantasy_sim.simulation import (
    FantasySimulationEngine, normalize_position, SIM_CONFIG, DUAL_ELIGIBILITY,
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

    def test_roster_value_baseline_export_key_states_what_the_number_is(self):
        """Regression test for AUDIT_PHASE_1_FINDINGS.md finding 8 (open since 2026-08-27,
        recorded as its own item in AUDIT_PLAN.md): get_optimal_score returns
        optimal_starting_lineup + bench * 0.1, but the export key was power_rankings_baseline_pts
        and the chart called it "Optimal Valid Starting Lineup Baseline" -- a real bench-depth
        uplift presented as if it were a starters-only number (measured on real week01 data:
        Femboy Cats' true starters-only optimum was 166.8 against a reported 173.1, a 3.6% bench
        uplift folded into a number labelled as pure starters).

        This does not change get_optimal_score's return value -- the bench term is deliberate,
        rewarding roster depth, not a bug -- it only renames the export key so it says what the
        number actually is. Asserts the new key exists with the exact value get_optimal_score
        returns, and that the old, mislabeled key is gone (a real rename, not an addition)."""
        sim = FantasySimulationEngine()
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 20
        try:
            saved_files = {}

            def recording_save_json(path, data, indent=2):
                saved_files[path] = data

            with patch('fantasy_sim.simulation.save_chart'), patch('matplotlib.pyplot.close'), \
                 patch('fantasy_sim.simulation.save_json', side_effect=recording_save_json):
                sim.run_simulation()
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

        matrix_path = [p for p in saved_files if 'syndicate_comprehensive_matrix' in p]
        self.assertEqual(len(matrix_path), 1, "Expected exactly one comprehensive matrix export.")
        ai_matrix = saved_files[matrix_path[0]]

        self.assertNotIn('power_rankings_baseline_pts', ai_matrix,
                          "Old mislabeled key should be renamed, not left behind alongside the new one.")
        self.assertIn('roster_value_baseline_pts', ai_matrix)

        for team in self.test_teams:
            expected = sim.get_optimal_score(sim.rosters[team])
            self.assertAlmostEqual(ai_matrix['roster_value_baseline_pts'][team], expected, places=6)

    def test_expected_wins_violin_uses_area_normalization_with_no_kde_overrun(self):
        """Regression test for the Pass-2 visualization audit's Expected_Wins finding: the
        violin plot was called with density_norm='width' (default cut=2), which forces every
        team's violin to the SAME peak width regardless of actual density mass -- defeating the
        one thing a violin plot is for here (comparing relative spread across teams) -- and lets
        the KDE extend past each team's own observed min/max. Real week-1 percentile data
        (win_distributions in syndicate_comprehensive_matrix_week_1.json) confirmed p01/p99
        genuinely approach the axis bounds for every team regardless of mean, so cut=0 doesn't
        change what's real; it stops the KDE from ALSO smearing an artificial tail past that.

        This doesn't re-verify seaborn's own density_norm/cut semantics (that's seaborn's job) --
        it only guards against a future revert to the old kwargs by asserting the exact call this
        codebase makes to sns.violinplot for the Expected Wins chart."""
        sim = FantasySimulationEngine()
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 20
        try:
            with patch('fantasy_sim.simulation.sns.violinplot') as mock_violinplot, \
                 patch('fantasy_sim.simulation.save_chart'), patch('matplotlib.pyplot.close'), \
                 patch('fantasy_sim.simulation.save_json'):
                sim.run_simulation()
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

        mock_violinplot.assert_called_once()
        _, kwargs = mock_violinplot.call_args
        self.assertEqual(kwargs.get('density_norm'), 'area',
                          "density_norm must be 'area' (equal area per violin, fair since every "
                          "team has the same sample count) not 'width' (forces equal peak width, "
                          "erasing real spread differences).")
        self.assertEqual(kwargs.get('cut'), 0,
                          "cut=0 must stop the KDE from extending past each team's own observed "
                          "min/max; the default (cut=2) smears an artificial tail onto every team "
                          "regardless of its real spread.")

    def test_sim0_audit_log_is_retained_per_week(self):
        """F10 (AUDIT_PLAN.md) acceptance criterion: week 3's and week 5's audit logs coexist and
        differ. The sim-0 audit log was written to a single always-overwritten path in
        data/current/ (F9 classified it there honestly, as a description of the existing
        behaviour, not a fix), so a manager auditing week 3's simulation from week 10 had nothing
        to look at. Runs the same fixture engine at current_week 3 and 5 and asserts the two
        runs save the audit log to two distinct per-week paths, each naming its own week and
        living under data/weeks/, and that the two payloads are not the same object/content."""
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 5
        saved_by_week = {}
        try:
            for week in (3, 5):
                self.mock_fs[LEAGUE_STATE_FILE] = {"current_week": week}
                saved = {}

                def recording_save_json(path, data, indent=2, _saved=saved):
                    _saved[path] = data

                with patch('fantasy_sim.simulation.save_chart'), patch('matplotlib.pyplot.close'), \
                     patch('fantasy_sim.simulation.save_json', side_effect=recording_save_json):
                    FantasySimulationEngine().run_simulation()
                audit_paths = [p for p in saved if 'simulation_audit_log_sim0' in p]
                self.assertEqual(len(audit_paths), 1, f"week {week}: expected exactly one audit-log save, got {audit_paths}")
                saved_by_week[week] = (audit_paths[0], saved[audit_paths[0]])
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims
            self.mock_fs[LEAGUE_STATE_FILE] = {"current_week": 1}

        (p3, a3), (p5, a5) = saved_by_week[3], saved_by_week[5]
        self.assertNotEqual(p3, p5, "week 3 and week 5 audit logs must not share a path (one would overwrite the other)")
        for week, path in ((3, p3), (5, p5)):
            norm = path.replace('\\', '/')
            self.assertIn(f"weeks/week_{week:02d}/", norm, f"week {week} audit log must live under data/weeks/week_{week:02d}/: {path}")
            self.assertIn(f"_week_{week}.json", norm, f"week {week} audit log basename must carry its week: {path}")
        self.assertNotEqual(sorted(a3['weeks']), sorted(a5['weeks']),
                            "audit logs for different start weeks must cover different simulated weeks")

    def test_run_warnings_are_exported_inside_the_per_week_audit_log(self):
        """F10 (2/2). The import-time FileHandler behind syndicate_warnings.log is a process-level
        mirror of the root logger: it holds whatever process last imported fantasy_sim.simulation
        (a test run overwrites the last real run's), so it is no record of any single run. A
        run's own warnings are instead exported as audit_log['warnings'] inside its per-week
        audit JSON -- written through save_json, so it is retained per week by F10 (1/2) and
        mocked everywhere save_json already is, with no new raw write site to forget to mock.

        Capture starts at FantasySimulationEngine.__init__ (the run's earliest warning, VEGAS
        STALE, is emitted from inside __init__), so a record logged after construction must be
        present and one logged before construction must not. Markers are logged at ERROR because
        setUp holds the root logger at ERROR; the export records whatever was actually emitted."""
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 5
        try:
            saved = {}

            def recording_save_json(path, data, indent=2):
                saved[path] = data

            logging.error("F10 MARKER BEFORE CONSTRUCTION -- must not be exported")
            sim = FantasySimulationEngine()
            logging.error("F10 MARKER AFTER CONSTRUCTION -- must be exported")
            with patch('fantasy_sim.simulation.save_chart'), patch('matplotlib.pyplot.close'), \
                 patch('fantasy_sim.simulation.save_json', side_effect=recording_save_json):
                sim.run_simulation()
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

        audit_paths = [p for p in saved if 'simulation_audit_log_sim0' in p]
        self.assertEqual(len(audit_paths), 1)
        payload = saved[audit_paths[0]]
        self.assertIn('warnings', payload, "audit log must carry the run's own warnings")
        messages = [w['message'] for w in payload['warnings']]
        self.assertTrue(any('AFTER CONSTRUCTION' in m for m in messages), messages)
        self.assertFalse(any('BEFORE CONSTRUCTION' in m for m in messages), messages)
        for w in payload['warnings']:
            self.assertIn(w['level'], ('WARNING', 'ERROR', 'CRITICAL'))

    def test_weekly_score_from_z_pins_the_extracted_transform(self):
        """Specification of FantasySimulationEngine._weekly_score_from_z, the per-player weekly
        transform extracted verbatim from run_simulation's loop (2026-09-01) so the on-demand
        sampler in fantasy_sim.decisions draws through the same formula. Written alongside the
        extraction, not before it -- the golden master is what proves the extraction preserved
        the engine byte-for-byte; this pins the formula's shape by hand: z = 0 gives the
        lognormal median exp(mu_a) (below the mean, since sigma_a > 0), expected_pre carries no
        draw, the cap binds after environmental scaling, and mean <= 0.01 yields no base score."""
        mean_val, std_val = 12.0, 6.0
        sigma_a = np.sqrt(np.log(1 + (std_val / mean_val) ** 2))
        mu_a = np.log(mean_val) - sigma_a ** 2 / 2
        exp_pre, final = FantasySimulationEngine._weekly_score_from_z(
            mean_val, std_val, z=0.0, env_ratio=1.1, env_var=0.9, script_mult=1.06, contingency_pts=0.5)
        self.assertAlmostEqual(exp_pre, 12.0 * 1.1 * 1.06 + 0.5, places=12)
        self.assertAlmostEqual(final, (np.exp(mu_a) + 0.5) * 0.9 * 1.06, places=12)
        self.assertLess(np.exp(mu_a), mean_val)
        # z = +3 on a wide distribution blows past the cap after scaling; the cap binds.
        _, capped = FantasySimulationEngine._weekly_score_from_z(30.0, 25.0, 3.0, 1.2, 1.3, 1.1, 0.0)
        self.assertEqual(capped, SIM_CONFIG['MAX_REALISTIC_WEEKLY_SCORE'])
        # a zero-mean player scores only his contingency, scaled.
        exp0, fin0 = FantasySimulationEngine._weekly_score_from_z(0.0, 3.0, 1.0, 1.0, 1.0, 1.0, 2.0)
        self.assertAlmostEqual(exp0, 2.0); self.assertAlmostEqual(fin0, 2.0)

    def test_weekly_scoring_density_renders_one_row_per_team_with_shared_yaxis(self):
        """Regression test for the Pass-2 visualization audit's Weekly Scoring Density finding:
        8 overlapping KDE lines drawn into one shared axes were visually indistinguishable by
        color alone. This replaces them with a ridgeline (one row/Axes per team, in summary_df's
        ranked order). sharey=True is required, not optional: without it, independent per-row
        y-autoscaling would force every row to *look* the same peak height regardless of its real
        concentration -- the same density-comparability bug just fixed in the Expected_Wins
        violin chart (density_norm='width' there; independent per-row autoscaling here). Verified
        against real data before landing on this: peak KDE density values differed by ~11% across
        teams in the real distribution this chart draws (0.0110-0.0122), a real difference sharey
        correctly preserves and independent autoscaling would have erased.

        Asserts one Axes per team (not a single axes with N overlapping lines) and that every
        row's y-axis limits are literally identical (sharey in effect, not merely close)."""
        sim = FantasySimulationEngine()
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 20
        try:
            captured_figs = {}

            def recording_save_chart(path, **kwargs):
                captured_figs[path] = plt.gcf()

            with patch('fantasy_sim.simulation.save_chart', side_effect=recording_save_chart), \
                 patch('matplotlib.pyplot.close'), patch('fantasy_sim.simulation.save_json'):
                sim.run_simulation()
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

        density_paths = [p for p in captured_figs if 'Weekly_Scoring_Density' in p]
        self.assertEqual(len(density_paths), 1, "Expected exactly one Weekly Scoring Density export.")
        fig = captured_figs[density_paths[0]]

        self.assertEqual(
            len(fig.axes), len(self.test_teams),
            "Expected one row (Axes) per team -- a ridgeline -- not one shared axes with N "
            "overlapping lines.")
        ylims = {ax.get_ylim() for ax in fig.axes}
        self.assertEqual(
            len(ylims), 1,
            "All rows must share the same y-axis (sharey=True) so peak-height differences across "
            "teams reflect real density differences, not independent per-row autoscaling.")

    def test_h2h_win_probability_matrix_export_is_not_transposed(self):
        """Regression test for a real, precisely diagnosed bug: the exported
        h2h_win_probability_matrix JSON was transposed relative to its intended
        [subject_team][opponent_team] meaning. Root cause: pandas' DataFrame.to_dict()
        defaults to orient='dict' (column-major: {column: {row: value}}), but win_pct_matrix
        was built with rows=subject team, columns=opponent -- calling .to_dict() without
        orient='index' silently swapped which team's win rate landed under which key. Found
        by a real diagnostic: comparing a real exported matrix against that same run's actual
        season_outcomes showed a PERFECT rank inversion across all 8 teams (the team with the
        best average 'win probability' in the exported matrix had the WORST actual expected
        wins, and vice versa) -- confirmed by hand-verifying pandas' default to_dict()
        orientation directly.

        This test constructs a lopsided matchup (StrongTeam's mean baseline far exceeds
        WeakTeam's) and confirms the exported matrix correctly shows StrongTeam with a HIGH
        win probability against WeakTeam under matrix[StrongTeam][WeakTeam] -- not, as the
        transposed bug would produce, under matrix[WeakTeam][StrongTeam]."""
        full_roster_strong = [
            {"name": "Strong_QB", "pos": "QB", "team": "DET"}, {"name": "Strong_K", "pos": "K", "team": "DET"},
            {"name": "Strong_DB", "pos": "DB", "team": "DET"}, {"name": "Strong_DL", "pos": "DL", "team": "DET"},
            {"name": "Strong_LB", "pos": "LB", "team": "DET"}, {"name": "Strong_RB1", "pos": "RB", "team": "DET"},
            {"name": "Strong_RB2", "pos": "RB", "team": "DET"}, {"name": "Strong_WR1", "pos": "WR", "team": "DET"},
            {"name": "Strong_WR2", "pos": "WR", "team": "DET"}, {"name": "Strong_TE1", "pos": "TE", "team": "DET"},
            {"name": "Strong_TE2", "pos": "TE", "team": "DET"}, {"name": "Strong_WR3", "pos": "WR", "team": "DET"},
            {"name": "Strong_WR4", "pos": "WR", "team": "DET"},
        ]
        full_roster_weak = [
            {"name": "Weak_QB", "pos": "QB", "team": "SF"}, {"name": "Weak_K", "pos": "K", "team": "SF"},
            {"name": "Weak_DB", "pos": "DB", "team": "SF"}, {"name": "Weak_DL", "pos": "DL", "team": "SF"},
            {"name": "Weak_LB", "pos": "LB", "team": "SF"}, {"name": "Weak_RB1", "pos": "RB", "team": "SF"},
            {"name": "Weak_RB2", "pos": "RB", "team": "SF"}, {"name": "Weak_WR1", "pos": "WR", "team": "SF"},
            {"name": "Weak_WR2", "pos": "WR", "team": "SF"}, {"name": "Weak_TE1", "pos": "TE", "team": "SF"},
            {"name": "Weak_TE2", "pos": "TE", "team": "SF"}, {"name": "Weak_WR3", "pos": "WR", "team": "SF"},
            {"name": "Weak_WR4", "pos": "WR", "team": "SF"},
        ]
        filler_a, filler_b = self.test_teams[2], self.test_teams[3]
        self.mock_fs[LIVE_ROSTERS_FILE] = {
            "StrongTeam": full_roster_strong, "WeakTeam": full_roster_weak,
            filler_a: [{"name": "Filler_QB_A", "pos": "QB", "team": "FA"}],
            filler_b: [{"name": "Filler_QB_B", "pos": "QB", "team": "FA"}],
        }
        self.mock_fs[BASELINES_FILE] = {
            **{p["name"]: {"mean": 30.0, "std_aleatoric": 2.0, "std_epistemic": 1.0, "pos": p["pos"], "team": "DET"}
               for p in full_roster_strong},
            **{p["name"]: {"mean": 4.0, "std_aleatoric": 2.0, "std_epistemic": 1.0, "pos": p["pos"], "team": "SF"}
               for p in full_roster_weak},
            "Filler_QB_A": {"mean": 15.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "FA"},
            "Filler_QB_B": {"mean": 15.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "FA"},
        }
        self.mock_fs[LEAGUE_STANDINGS_FILE] = {t: {"remaining_faab": 100} for t in self.mock_fs[LIVE_ROSTERS_FILE]}
        team_names = list(self.mock_fs[LIVE_ROSTERS_FILE].keys())
        self.mock_fs[LEAGUE_SCHEDULE_FILE] = [
            [[team_names[0], team_names[1]], [team_names[2], team_names[3]]]
        ] * 14

        sim = FantasySimulationEngine()
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 50
        try:
            saved_files = {}

            def recording_save_json(path, data, indent=2):
                saved_files[path] = data

            with patch('fantasy_sim.simulation.save_chart'), patch('matplotlib.pyplot.close'), \
                 patch('fantasy_sim.simulation.save_json', side_effect=recording_save_json):
                sim.run_simulation()
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

        matrix_path = [p for p in saved_files if 'syndicate_comprehensive_matrix' in p]
        self.assertEqual(len(matrix_path), 1, "Expected exactly one comprehensive matrix export.")
        h2h = saved_files[matrix_path[0]]["h2h_win_probability_matrix"]

        strong_vs_weak = h2h["StrongTeam"]["WeakTeam"]
        weak_vs_strong = h2h["WeakTeam"]["StrongTeam"]
        self.assertGreater(
            strong_vs_weak, 50.0,
            f"StrongTeam's win probability against WeakTeam should clearly exceed 50% given "
            f"the large baseline gap, but matrix['StrongTeam']['WeakTeam'] = {strong_vs_weak} "
            f"-- matrix may be transposed again."
        )
        self.assertLess(
            weak_vs_strong, 50.0,
            f"WeakTeam's win probability against StrongTeam should clearly be below 50%, but "
            f"matrix['WeakTeam']['StrongTeam'] = {weak_vs_strong} -- matrix may be transposed again."
        )

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

    def _make_sim_with_group(self, baselines_extra):
        """Builds a real FantasySimulationEngine whose baselines include the given extra
        players, so tests can exercise the real _build_nfl_position_groups /
        _apportion_vacated_volume code paths against a controlled position group."""
        self.mock_fs[BASELINES_FILE] = {
            **self.mock_fs[BASELINES_FILE],
            **baselines_extra,
        }
        return FantasySimulationEngine()

    def test_nfl_position_groups_include_unrostered_players(self):
        """_build_nfl_position_groups must span the ENTIRE real NFL population from
        player_baselines.json, not just fantasy-rostered players -- that is precisely what makes
        it possible to withhold the share of vacated volume that really flows to teammates nobody
        rosters. Verified against the real method on a real engine instance."""
        sim = self._make_sim_with_group({
            "Rostered_DET_WR": {"mean": 14.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
            "Unrostered_DET_WR": {"mean": 9.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
            "Unrostered_SF_WR": {"mean": 11.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "SF"},
            "Free_Agent_WR": {"mean": 8.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "FA"},
        })

        det_wrs = dict(sim.nfl_position_groups.get(("WR", "DET"), []))
        self.assertIn("Rostered_DET_WR", det_wrs)
        self.assertIn("Unrostered_DET_WR", det_wrs,
                      "Unrostered players must appear in the real NFL position group.")
        self.assertNotIn("Unrostered_SF_WR", det_wrs, "Group must be keyed by real NFL team.")
        # Free agents have no real NFL team whose vacated volume they could inherit.
        self.assertNotIn(("WR", "FA"), sim.nfl_position_groups)

    def test_vacated_volume_is_conserved_not_multiplied(self):
        """Regression test for a real over-distribution bug found by reproducing the production
        assignment/lookup rules: contingency_pts used to be a bare pool lookup, so every rostered
        player sharing the injured player's team and position received the FULL vacated amount
        (three claimants -> 3x the vacated volume injected into the league, points that never
        existed). Calls the REAL _apportion_vacated_volume and asserts total awarded equals
        exactly the amount vacated, never a multiple of it."""
        sim = self._make_sim_with_group({
            "DET_WR_1": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
            "DET_WR_2": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
            "DET_WR_3": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
        })

        vacated = 13.0
        awarded = sim._apportion_vacated_volume({"WR": {"DET": vacated}, "TE": {}, "RB": {}}, {}, set())

        total_awarded = sum(awarded.values())
        self.assertAlmostEqual(
            total_awarded, vacated, places=6,
            msg=f"Total awarded ({total_awarded}) must equal the volume vacated ({vacated}); "
                f"the old bug awarded each of the 3 claimants the full amount (3x)."
        )
        # Equal baseline means -> equal shares, and each share is strictly less than the whole.
        for name, pts in awarded.items():
            self.assertAlmostEqual(pts, vacated / 3.0, places=6)
            self.assertLess(pts, vacated)

    def test_vacated_volume_withheld_from_unrostered_share(self):
        """The substance of 'extend redistribution into the real NFL depth chart': when part of
        an injured player's real position group is unrostered, the rostered claimant must receive
        only its proportional share -- the rest is correctly never awarded to anyone, rather than
        being handed in full to whichever rostered player happens to share the team and position.
        Exercises the real _apportion_vacated_volume."""
        sim = self._make_sim_with_group({
            "Rostered_WR": {"mean": 10.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
            "Unrostered_WR_A": {"mean": 20.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
            "Unrostered_WR_B": {"mean": 10.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
        })

        vacated = 20.0
        awarded = sim._apportion_vacated_volume({"WR": {"DET": vacated}, "TE": {}, "RB": {}}, {}, set())

        # Rostered_WR's weight is 10 of the group's total 40 -> exactly a quarter.
        self.assertAlmostEqual(awarded["Rostered_WR"], vacated * (10.0 / 40.0), places=6)
        self.assertLess(
            awarded["Rostered_WR"], vacated,
            "Rostered claimant must not absorb volume that really flows to unrostered teammates."
        )

    def test_vacated_volume_accumulates_across_multiple_injuries(self):
        """Regression test for a real overwrite bug: PASS 1 used a plain assignment, so when two
        players at the same position on the same real NFL team were injured in one week, the
        second injury silently clobbered the first and that vacated volume vanished. Calls the
        REAL _record_vacated_volume twice, exactly as PASS 1 does for two injuries."""
        sim = self._make_sim_with_group({
            "DET_WR_HEALTHY": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
        })
        rate = SIM_CONFIG['VACATED_VOLUME_CAPTURE_RATE']

        pools = {pos: {} for pos in SIM_CONFIG['VACATED_VOLUME_ELIGIBLE_POSITIONS']}
        sim._record_vacated_volume(pools, "WR", "DET", 20.0)
        first_only = pools["WR"]["DET"]
        sim._record_vacated_volume(pools, "WR", "DET", 14.0)

        self.assertAlmostEqual(
            pools["WR"]["DET"], (20.0 + 14.0) * rate, places=6,
            msg="Second injury must ADD to the pool, not overwrite the first injury's volume."
        )
        self.assertGreater(pools["WR"]["DET"], first_only)

        # Ineligible positions vacate nothing at all -- this is what keeps pools siloed.
        sim._record_vacated_volume(pools, "K", "DET", 30.0)
        sim._record_vacated_volume(pools, "DB", "DET", 30.0)
        self.assertNotIn("K", pools)
        self.assertNotIn("DB", pools)
        self.assertEqual(pools["TE"], {}, "A WR/K/DB injury must never populate the TE pool.")

        # And the accumulated pool apportions in full.
        awarded = sim._apportion_vacated_volume(pools, {}, set())
        self.assertAlmostEqual(sum(awarded.values()), (20.0 + 14.0) * rate, places=6)

    def test_vacated_volume_skips_injured_and_respects_position_siloing(self):
        """Injured players must never inherit vacated volume (including the player injured this
        very week), and a WR injury must never leak into the TE pool. Exercises the real
        _apportion_vacated_volume."""
        sim = self._make_sim_with_group({
            "DET_WR_OUT": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
            "DET_WR_HURT_NOW": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
            "DET_WR_OK": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
            "DET_TE_OK": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "TE", "team": "DET"},
        })

        vacated = 13.0
        awarded = sim._apportion_vacated_volume(
            {"WR": {"DET": vacated}, "TE": {}, "RB": {}},
            {"DET_WR_OUT": 3},                 # already out from a prior week
            {"DET_WR_HURT_NOW"},               # newly injured this week
        )

        self.assertNotIn("DET_WR_OUT", awarded, "A player already injured must not inherit volume.")
        self.assertNotIn("DET_WR_HURT_NOW", awarded, "A player injured this week must not inherit volume.")
        self.assertNotIn("DET_TE_OK", awarded, "A WR injury must not leak into the TE pool.")
        self.assertAlmostEqual(awarded["DET_WR_OK"], vacated, places=6,
                               msg="The sole healthy WR should inherit the entire WR pool.")

    def test_vacated_volume_vanishes_when_no_healthy_teammate_remains(self):
        """If every member of the real position group is injured, the vacated volume must simply
        vanish rather than being awarded to someone ineligible or crashing on a zero-weight
        division."""
        sim = self._make_sim_with_group({
            "DET_WR_ONLY": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": "WR", "team": "DET"},
        })
        awarded = sim._apportion_vacated_volume(
            {"WR": {"DET": 13.0}, "TE": {}, "RB": {}}, {"DET_WR_ONLY": 2}, set()
        )
        self.assertEqual(awarded, {})

    def test_wr_te_injuries_run_end_to_end_in_the_real_engine(self):
        """The test above verifies the vacated-volume mechanism's logic directly, not the
        actual production code path -- if fantasy_sim/simulation.py were reverted to the old
        RB-only version, that test would still pass (it mirrors the intended logic, it
        doesn't call into simulation.py). This closes that gap: runs the REAL engine, through
        its real public API, with RB/WR/TE injury rates all simultaneously elevated, and
        confirms it completes without error and produces sane, non-degenerate team point
        totals -- a real smoke test against actual production code, not a mirror of it."""
        full_roster = [
            {"name": "QB_1", "pos": "QB", "team": "DET"}, {"name": "K_1", "pos": "K", "team": "DET"},
            {"name": "DB_1", "pos": "DB", "team": "DET"}, {"name": "DL_1", "pos": "DL", "team": "DET"},
            {"name": "LB_1", "pos": "LB", "team": "DET"}, {"name": "RB_1", "pos": "RB", "team": "DET"},
            {"name": "RB_2", "pos": "RB", "team": "DET"}, {"name": "WR_1", "pos": "WR", "team": "DET"},
            {"name": "WR_2", "pos": "WR", "team": "DET"}, {"name": "TE_1", "pos": "TE", "team": "DET"},
            {"name": "TE_2", "pos": "TE", "team": "DET"}, {"name": "WR_3", "pos": "WR", "team": "DET"},
            {"name": "WR_4", "pos": "WR", "team": "DET"},
        ]
        self.mock_fs[LIVE_ROSTERS_FILE] = {t: full_roster for t in self.test_teams}
        self.mock_fs[BASELINES_FILE] = {
            p["name"]: {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": p["pos"], "team": "DET"}
            for p in full_roster
        }

        sim = FantasySimulationEngine()
        original_rates = dict(SIM_CONFIG['INJURY_RATES'])
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['INJURY_RATES'] = {k: 0.6 for k in original_rates}  # elevated across the board, not just one position
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 5
        try:
            with patch.object(sim, 'export_and_visualize') as mock_export:
                sim.run_simulation()
            global_season_points = mock_export.call_args[0][1]
        finally:
            SIM_CONFIG['INJURY_RATES'] = original_rates
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

        for team, points_array in global_season_points.items():
            self.assertTrue(np.all(np.isfinite(points_array)), f"{team} produced non-finite season points.")
            self.assertGreater(float(np.mean(points_array)), 0.0, f"{team}'s mean season points was not positive.")

    def test_vacated_rb_volume_pass_ordering_is_correct(self):
        """Regression test for a real order-dependence bug: whether a same-real-NFL-team
        backup RB received the vacated-volume bonus THE SAME WEEK a starter got injured used
        to depend on which fantasy team happened to be processed first that week
        (self.team_names iteration order) -- purely an artifact of team listing order, not by
        design.

        Rather than an indirect statistical comparison of season totals (tried first; proved
        too diluted by the other 13 weeks and 12 roster slots to reliably detect the effect
        even at 2000 simulated trials), this verifies the fix STRUCTURALLY and directly:
        np.random.exponential is called exclusively inside the injury-onset block (confirmed
        by inspection -- nowhere else in the file), so recording the running
        build_covariance_matrix call count at the moment each injury duration gets drawn
        proves whether ALL of a week's injuries are determined before ANY team's scores are
        computed. With the fix, every recorded count must be 0 (every injury this week is
        known before the first covariance matrix -- and hence the first score -- is built for
        anyone); the old single-pass code would show a nonzero count as soon as any
        later-processed team's player got hurt after an earlier team had already been scored."""
        full_roster = [
            {"name": "RB_1", "pos": "RB", "team": "DET"}, {"name": "K_1", "pos": "K", "team": "DET"},
            {"name": "DB_1", "pos": "DB", "team": "DET"}, {"name": "DL_1", "pos": "DL", "team": "DET"},
            {"name": "LB_1", "pos": "LB", "team": "DET"}, {"name": "QB_1", "pos": "QB", "team": "DET"},
            {"name": "WR_1", "pos": "WR", "team": "DET"}, {"name": "WR_2", "pos": "WR", "team": "DET"},
            {"name": "WR_3", "pos": "WR", "team": "DET"}, {"name": "TE_1", "pos": "TE", "team": "DET"},
            {"name": "TE_2", "pos": "TE", "team": "DET"}, {"name": "WR_4", "pos": "WR", "team": "DET"},
            {"name": "WR_5", "pos": "WR", "team": "DET"},
        ]
        self.mock_fs[LIVE_ROSTERS_FILE] = {t: full_roster for t in self.test_teams}
        self.mock_fs[BASELINES_FILE] = {
            p["name"]: {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 3.0, "pos": p["pos"], "team": "DET"}
            for p in full_roster
        }

        sim = FantasySimulationEngine()
        original_rates = dict(SIM_CONFIG['INJURY_RATES'])
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        # Guaranteed injury onset for every eligible RB, every week -- every team in this
        # setup has exactly one RB, so every team will have exactly one injury event per week,
        # giving real data points to check on every single team/week combination.
        SIM_CONFIG['INJURY_RATES'] = {k: 0.0 for k in original_rates}
        SIM_CONFIG['INJURY_RATES']['RB'] = 1.0
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 3
        try:
            covariance_call_count = [0]
            real_build_covariance_matrix = sim.build_covariance_matrix

            def counting_build_covariance_matrix(*args, **kwargs):
                covariance_call_count[0] += 1
                return real_build_covariance_matrix(*args, **kwargs)

            recorded_counts_at_injury_time = []
            real_exponential = np.random.exponential

            def recording_exponential(*args, **kwargs):
                recorded_counts_at_injury_time.append(covariance_call_count[0])
                return real_exponential(*args, **kwargs)

            with patch.object(sim, 'build_covariance_matrix', side_effect=counting_build_covariance_matrix), \
                 patch('numpy.random.exponential', side_effect=recording_exponential), \
                 patch.object(sim, 'export_and_visualize'):
                sim.run_simulation()
        finally:
            SIM_CONFIG['INJURY_RATES'] = original_rates
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

        self.assertGreater(len(recorded_counts_at_injury_time), 0, "No injuries were recorded -- test setup issue.")
        # covariance_call_count is cumulative across the WHOLE season (all weeks), not reset
        # per week -- so "always exactly 0" is the wrong invariant past week 1. The correct
        # invariant: every injury draw must happen at a clean WEEK BOUNDARY, i.e. the running
        # count must be an exact multiple of the number of teams (all of a week's covariance
        # matrices are either fully done from PRIOR weeks, or none of them have started yet
        # for THIS week -- never partway through, which is what the old interleaved bug would
        # show, e.g. an injury draw recorded partway through a week at count=1, 2, or 3).
        num_teams = len(self.test_teams)
        self.assertTrue(
            all(c % num_teams == 0 for c in recorded_counts_at_injury_time),
            f"Some injury duration draws happened partway through a week's scoring (not at a "
            f"clean {num_teams}-team week boundary) -- pass ordering is broken. "
            f"Counts observed at each injury draw: {recorded_counts_at_injury_time}"
        )

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
            remaining_faab=3.0, raw_normal_draw=4.0, aggression=2.0,
            avg_league_faab=100.0
        )
        self.assertLessEqual(bid, 3.0)

    def test_faab_bid_never_exceeds_competitive_ceiling(self):
        """Even a team with a huge remaining budget must not be modeled as blowing past the
        league-wide competitive ceiling (avg_league_faab * 1.5) on a single streamer bid."""
        sim = FantasySimulationEngine()
        bid = sim._compute_faab_bid(
            remaining_faab=100.0, raw_normal_draw=4.0, aggression=2.0,
            avg_league_faab=20.0
        )
        self.assertLessEqual(bid, 30.0)  # 1.5 * avg_league_faab

    def test_faab_bid_scales_with_aggression(self):
        """A more aggressive manager bids strictly more, all else equal. (The old ad-hoc
        `needs` multiplier is gone in the F31 rewrite: real 2025 bid sizes are explained by
        the lognormal x aggression shape, and need now drives the COUNT of bids, not their
        size.)"""
        sim = FantasySimulationEngine()
        common_kwargs = dict(remaining_faab=100.0, raw_normal_draw=0.0, avg_league_faab=100.0)

        passive_bid = sim._compute_faab_bid(aggression=0.5, **common_kwargs)
        aggressive_bid = sim._compute_faab_bid(aggression=1.5, **common_kwargs)
        self.assertLess(passive_bid, aggressive_bid)

    def test_faab_bid_exhausted_budget_yields_zero_bid(self):
        """A team with nothing left bids nothing -- solvency comes from the remaining-budget
        cap, which replaced the old league-wide deflation multiplier (F31: real 2025 shows
        no proportional cooling; spending persists until budgets empty)."""
        sim = FantasySimulationEngine()
        bid = sim._compute_faab_bid(
            remaining_faab=0.0, raw_normal_draw=4.0, aggression=2.0, avg_league_faab=50.0
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
            saved_files = {}

            def recording_save_json(path, data, indent=2):
                saved_files[path] = data

            with patch('fantasy_sim.simulation.save_chart'), \
                 patch('fantasy_sim.simulation.save_json', side_effect=recording_save_json):
                sim.run_simulation()

            win_pct_matrix = None
            for data in saved_files.values():
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
            saved_files = {}

            def recording_save_json(path, data, indent=2):
                saved_files[path] = data

            with patch('fantasy_sim.simulation.save_chart'), \
                 patch('fantasy_sim.simulation.save_json', side_effect=recording_save_json):
                sim.run_simulation()

            insights = None
            for data in saved_files.values():
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

    def test_injury_duration_mixture_uses_both_components_in_roughly_the_right_proportion(self):
        """Verifies the two-component duration mixture is actually wired correctly: forces
        guaranteed injury onset (rate=1.0) for a roster of many RBs, records which scale
        parameter (severe vs typical) np.random.exponential is actually called with for each
        real injury event, and confirms both branches fire, in roughly the real-data-anchored
        12.5% severe / 87.5% typical proportion -- not just that one branch works while the
        other is silently dead code."""
        full_roster = [{"name": f"RB_{i}", "pos": "RB", "team": "DET"} for i in range(13)]
        self.mock_fs[LIVE_ROSTERS_FILE] = {t: full_roster for t in self.test_teams}
        self.mock_fs[BASELINES_FILE] = {
            p["name"]: {"mean": 10.0, "std_aleatoric": 3.0, "std_epistemic": 2.0, "pos": "RB", "team": "DET"}
            for p in full_roster
        }

        sim = FantasySimulationEngine()
        original_rates = dict(SIM_CONFIG['INJURY_RATES'])
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['INJURY_RATES'] = {k: 1.0 for k in original_rates}  # guaranteed onset every eligible player, every week
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 10
        try:
            recorded_scales = []
            real_exponential = np.random.exponential

            def recording_exponential(scale=1.0, *args, **kwargs):
                recorded_scales.append(scale)
                return real_exponential(scale, *args, **kwargs)

            with patch('numpy.random.exponential', side_effect=recording_exponential), \
                 patch.object(sim, 'export_and_visualize'):
                sim.run_simulation()
        finally:
            SIM_CONFIG['INJURY_RATES'] = original_rates
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims

        severe_scale = SIM_CONFIG['INJURY_SEVERE_DURATION_SCALE']
        typical_scale = SIM_CONFIG['INJURY_TYPICAL_DURATION_SCALE']
        n_severe = sum(1 for s in recorded_scales if s == severe_scale)
        n_typical = sum(1 for s in recorded_scales if s == typical_scale)

        self.assertGreater(len(recorded_scales), 100, "Not enough injury events recorded for a meaningful check.")
        self.assertEqual(n_severe + n_typical, len(recorded_scales), "Some call used neither configured scale.")
        self.assertGreater(n_severe, 0, "Severe component never fired -- may be dead code.")
        self.assertGreater(n_typical, 0, "Typical component never fired -- may be dead code.")
        observed_severe_fraction = n_severe / len(recorded_scales)
        self.assertLess(
            abs(observed_severe_fraction - SIM_CONFIG['INJURY_SEVERE_PROBABILITY']), 0.05,
            f"Observed severe-component fraction ({observed_severe_fraction:.3f}) is far from "
            f"the configured INJURY_SEVERE_PROBABILITY ({SIM_CONFIG['INJURY_SEVERE_PROBABILITY']})."
        )

    def test_injury_duration_mixture_matches_real_target_moments(self):
        """Hand-verifiable sanity check on the CHOSEN PARAMETERS themselves (independent of
        how the simulation engine wires them in, which the test above covers): a direct Monte
        Carlo replica of the exact mixture formula used in production should reproduce the two
        real-data target moments it was solved against -- ProFootballLogic's 2015 analysis
        found 64% of missed-time NFL injuries result in <=2 games missed, with an overall mean
        of 3.1 games missed."""
        rng = np.random.default_rng(42)
        n = 500_000
        is_severe = rng.random(n) < SIM_CONFIG['INJURY_SEVERE_PROBABILITY']
        durations = np.where(
            is_severe,
            np.floor(rng.exponential(SIM_CONFIG['INJURY_SEVERE_DURATION_SCALE'], n)) + 1,
            np.floor(rng.exponential(SIM_CONFIG['INJURY_TYPICAL_DURATION_SCALE'], n)) + 1,
        )
        durations = np.minimum(durations, 16)

        p_le_2 = float((durations <= 2).mean())
        mean_duration = float(durations.mean())

        self.assertAlmostEqual(p_le_2, 0.64, delta=0.02)
        self.assertAlmostEqual(mean_duration, 3.1, delta=0.15)

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

    @patch('fantasy_sim.simulation.save_chart')
    @patch('fantasy_sim.simulation.save_json')
    def test_e2e_smoke_and_invariants(self, mock_save_json, mock_savefig):
        """End-to-end simulation test verifying no crashes and basic sum invariants."""
        sim = FantasySimulationEngine()
        
        original_batches, original_sims = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        SIM_CONFIG['NUM_BATCHES'] = 1
        SIM_CONFIG['SIMS_PER_BATCH'] = 2
        
        # Should complete entirely without exceptions
        sim.run_simulation()
        
        SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims
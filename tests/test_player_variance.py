"""
Characterisation tests for fantasy_sim.player_variance, written before the module exists
(CLAUDE.md rule 1). _player_summary is a pure function over a synthetic (sims, weeks) array
with NaN for structural absences (bye/injury-clocked weeks), so it's tested directly -- no real
engine or data/ files needed. Render functions are tested with the save_chart wrapper (not
matplotlib.pyplot.savefig directly -- save_chart bundles directory creation with the render, so
mocking it skips both instead of leaving an empty directory behind) and matplotlib.pyplot.close
mocked (this codebase's established convention), but return their Figure so the low-n
hatching/annotation can be asserted directly on the created Axes rather than by inspecting a
saved PNG's pixels.
"""
import unittest
from unittest.mock import patch

import numpy as np

from fantasy_sim.player_variance import (
    _player_summary, _flatten_observed, MIN_WEEKS_THRESHOLD, build_player_variance_report,
    render_boom_bust_chart, render_floor_ceiling_chart,
)


def _scores(total_sims, weeks_pattern):
    """weeks_pattern: list of per-week values, or None for a structurally-absent week (bye /
    injury-clocked), applied identically across every sim -- enough to test the summary math
    without needing per-sim variation."""
    n_weeks = len(weeks_pattern)
    arr = np.full((total_sims, n_weeks), np.nan)
    for w, v in enumerate(weeks_pattern):
        if v is not None:
            arr[:, w] = v
    return arr


class TestFlattenObserved(unittest.TestCase):
    def test_drops_nan_structural_absences(self):
        arr = _scores(4, [10.0, None, 20.0])
        flat = _flatten_observed(arr)
        self.assertEqual(sorted(flat.tolist()), [10.0] * 4 + [20.0] * 4)


class TestPlayerSummary(unittest.TestCase):
    def test_percentiles_and_moments_computed_from_observed_weeks_only(self):
        # 4 sims x 3 weeks, week 2 is a bye (NaN) for everyone -- must not pull percentiles
        # toward 0, the exact bug class this codebase already caught for team-level scores.
        arr = _scores(4, [10.0, None, 30.0])
        summary = _player_summary(arr, total_sims=4)
        self.assertEqual(summary['n_observed'], 8)
        self.assertAlmostEqual(summary['avg_weeks_observed'], 2.0)
        self.assertEqual(summary['min'], 10.0)
        self.assertEqual(summary['max'], 30.0)
        self.assertAlmostEqual(summary['mean'], 20.0)
        self.assertIsNotNone(summary['histogram'])

    def test_low_n_flag_follows_the_threshold(self):
        # avg_weeks_observed = 2 weeks out of, say, a 14-week season -- well under
        # MIN_WEEKS_THRESHOLD (6 as proposed; re-verified against real data before finalizing).
        low = _player_summary(_scores(10, [5.0, None, None, None, None, None]), total_sims=10)
        high = _player_summary(_scores(10, [5.0] * 10), total_sims=10)
        self.assertLess(low['avg_weeks_observed'], MIN_WEEKS_THRESHOLD)
        self.assertGreaterEqual(high['avg_weeks_observed'], MIN_WEEKS_THRESHOLD)
        self.assertTrue(low['low_n'])
        self.assertFalse(high['low_n'])

    def test_zero_observations_is_none_not_a_fabricated_value(self):
        arr = _scores(4, [None, None])
        summary = _player_summary(arr, total_sims=4)
        self.assertEqual(summary['n_observed'], 0)
        self.assertIsNone(summary['mean'])
        self.assertIsNone(summary['p50'])
        self.assertTrue(summary['low_n'])
        self.assertIsNone(summary['histogram'])


class FakeEngine:
    """Stands in for FantasySimulationEngine after a real run_simulation() call: only the
    attributes fantasy_sim.player_variance actually reads."""
    def __init__(self):
        self.current_week = 1
        self.rosters = {'Team A': ['Healthy', 'Hurt']}
        self.meta = {'Team A': {'Healthy': {'pos': 'WR'}, 'Hurt': {'pos': 'RB'}}}
        self.player_weekly_scores = {
            'Healthy': _scores(5, [10.0, 12.0, 11.0, 10.5, 13.0, 11.5, 12.5, 11.0, 10.0, 12.0]),  # 10 weeks
            'Hurt': _scores(5, [8.0, None, None]),  # 1 week
        }


class TestBuildPlayerVarianceReport(unittest.TestCase):
    @patch('matplotlib.pyplot.close')
    @patch('fantasy_sim.player_variance.save_chart')
    @patch('fantasy_sim.player_variance.save_json')
    def test_builds_one_report_entry_per_rostered_player_with_correct_flags(
        self, mock_save_json, mock_savefig, mock_close
    ):
        report = build_player_variance_report(FakeEngine())
        names = {e['name']: e for e in report['Team A']}
        self.assertEqual(set(names), {'Healthy', 'Hurt'})
        self.assertFalse(names['Healthy']['low_n'])
        self.assertTrue(names['Hurt']['low_n'])
        mock_save_json.assert_called_once()


class TestRenderSmoke(unittest.TestCase):
    """See module docstring: save_chart/matplotlib.pyplot.close are mocked, but the render
    functions return their Figure so hatching/annotations can be asserted on the real Axes."""

    @patch('matplotlib.pyplot.close')
    @patch('fantasy_sim.player_variance.save_chart')
    def test_floor_ceiling_hatches_low_n_bars(self, mock_savefig, mock_close):
        entries = [
            {'name': 'Healthy', 'pos': 'WR', 'p10': 8.0, 'p50': 11.0, 'p90': 14.0,
             'avg_weeks_observed': 13.0, 'low_n': False},
            {'name': 'Hurt', 'pos': 'RB', 'p10': 5.0, 'p50': 8.0, 'p90': 11.0,
             'avg_weeks_observed': 1.7, 'low_n': True},
        ]
        fig = render_floor_ceiling_chart('Team A', entries, week=1)
        ax = fig.axes[0]
        # matplotlib bar labels default to '_nolegend_'; identify by y-tick order instead.
        self.assertEqual(len(ax.patches), 2)
        hatch_by_position = [bar.get_hatch() for bar in ax.patches]
        self.assertIn('///', hatch_by_position)
        self.assertIn(None, hatch_by_position)
        mock_savefig.assert_called_once()

    @patch('matplotlib.pyplot.close')
    @patch('fantasy_sim.player_variance.save_chart')
    def test_floor_ceiling_excludes_zero_observation_players_without_crashing(self, mock_savefig, mock_close):
        entries = [
            {'name': 'Healthy', 'pos': 'WR', 'p10': 8.0, 'p50': 11.0, 'p90': 14.0,
             'avg_weeks_observed': 13.0, 'low_n': False},
            {'name': 'NeverPlayed', 'pos': 'TE', 'p10': None, 'p50': None, 'p90': None,
             'avg_weeks_observed': 0.0, 'low_n': True},
        ]
        fig = render_floor_ceiling_chart('Team A', entries, week=1)
        self.assertEqual(len(fig.axes[0].patches), 1)
        mock_savefig.assert_called_once()

    @patch('matplotlib.pyplot.close')
    @patch('fantasy_sim.player_variance.save_chart')
    def test_boom_bust_caps_yaxis_near_pooled_99th_percentile_not_the_rare_max(
        self, mock_savefig, mock_close
    ):
        # The engine hard-caps any single draw at MAX_REALISTIC_WEEKLY_SCORE (80.0), so a rare
        # outlier genuinely reaches 80 -- matplotlib's default auto-scaling then stretches the
        # whole chart's y-axis to fit that one point, squashing the real distribution (which
        # sits mostly under ~25 here) into a sliver at the bottom. Only 1 of 100 values is the
        # outlier (under 1%), so a 99th-percentile cap must NOT be dragged up to it.
        entries = [
            {'name': 'A', 'pos': 'WR', 'p50': 10.0, 'low_n': False, 'avg_weeks_observed': 13.0},
            {'name': 'B', 'pos': 'RB', 'p50': 9.0, 'low_n': False, 'avg_weeks_observed': 13.0},
        ]
        raw_scores = {
            'A': np.concatenate([np.full(99, 10.0), np.array([80.0])]),
            'B': np.full(100, 9.0),
        }
        fig = render_boom_bust_chart('Team A', entries, raw_scores, week=1)
        ax = fig.axes[0]
        self.assertLess(ax.get_ylim()[1], 50.0)

    @patch('matplotlib.pyplot.close')
    @patch('fantasy_sim.player_variance.save_chart')
    def test_boom_bust_annotates_low_n_players_with_week_count(self, mock_savefig, mock_close):
        entries = [
            {'name': 'Healthy', 'pos': 'WR', 'p50': 11.0, 'low_n': False, 'avg_weeks_observed': 13.0},
            {'name': 'Hurt', 'pos': 'RB', 'p50': 8.0, 'low_n': True, 'avg_weeks_observed': 1.7},
        ]
        raw_scores = {
            'Healthy': np.array([9.0, 10.0, 11.0, 12.0, 13.0]),
            'Hurt': np.array([7.0, 8.0, 9.0]),
        }
        fig = render_boom_bust_chart('Team A', entries, raw_scores, week=1)
        ax = fig.axes[0]
        labels = [t.get_text() for t in ax.get_xticklabels()]
        self.assertTrue(any('Healthy' == lbl for lbl in labels))
        self.assertTrue(any('Hurt' in lbl and 'n=1.7' in lbl for lbl in labels))
        mock_savefig.assert_called_once()

    @patch('matplotlib.pyplot.close')
    @patch('fantasy_sim.player_variance.save_chart')
    def test_boom_bust_excludes_players_with_no_observations(self, mock_savefig, mock_close):
        entries = [
            {'name': 'Healthy', 'pos': 'WR', 'p50': 11.0, 'low_n': False, 'avg_weeks_observed': 13.0},
            {'name': 'NeverPlayed', 'pos': 'TE', 'p50': None, 'low_n': True, 'avg_weeks_observed': 0.0},
        ]
        raw_scores = {'Healthy': np.array([9.0, 10.0, 11.0]), 'NeverPlayed': np.array([])}
        fig = render_boom_bust_chart('Team A', entries, raw_scores, week=1)
        ax = fig.axes[0]
        labels = [t.get_text() for t in ax.get_xticklabels()]
        self.assertEqual(len(labels), 1)
        mock_savefig.assert_called_once()


if __name__ == '__main__':
    unittest.main()

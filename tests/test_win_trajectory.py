"""
Characterisation tests for fantasy_sim.win_trajectory, written before the module exists
(CLAUDE.md rule 1). This module reads a single already-written syndicate_comprehensive_matrix
file (no engine instantiation, no new computation -- see module docstring) and re-visualizes
expected_cumulative_wins_by_week, already exported there, as one overlay chart instead of the
engine's own per-team-faceted percentile-band chart (All_Teams_Trajectories.png). Deliberately
NOT a duplicate of that chart -- this exists specifically for direct team-to-team comparison on
shared axes, which faceted subplots don't give you.
"""
import unittest
from unittest.mock import patch

from fantasy_sim.win_trajectory import (
    extract_trajectories, render_win_trajectory_chart, build_win_trajectory_chart,
)


class TestExtractTrajectories(unittest.TestCase):
    def test_pulls_expected_cumulative_wins_by_week_per_team(self):
        ai_matrix = {
            "weekly_trajectories": {
                "Team A": {"expected_cumulative_wins_by_week": [1.0, 2.0, 3.0]},
                "Team B": {"expected_cumulative_wins_by_week": [0.5, 1.5, 2.0]},
            }
        }
        trajectories = extract_trajectories(ai_matrix)
        self.assertEqual(trajectories, {
            "Team A": [1.0, 2.0, 3.0],
            "Team B": [0.5, 1.5, 2.0],
        })


class TestRenderWinTrajectoryChart(unittest.TestCase):
    @patch('matplotlib.pyplot.close')
    @patch('fantasy_sim.win_trajectory.save_chart')
    def test_renders_one_line_per_team(self, mock_save_chart, mock_close):
        trajectories = {
            "Team A": [1.0, 2.0, 3.0, 4.0],
            "Team B": [0.5, 1.0, 1.5, 2.0],
        }
        fig = render_win_trajectory_chart(trajectories, week=1)
        ax = fig.axes[0]
        # 2 team lines + 1 break-even reference line.
        self.assertEqual(len(ax.get_lines()), 3)
        team_labels = {line.get_label() for line in ax.get_lines()} & set(trajectories)
        self.assertEqual(team_labels, set(trajectories))
        mock_save_chart.assert_called_once()

    @patch('matplotlib.pyplot.close')
    @patch('fantasy_sim.win_trajectory.save_chart')
    def test_uses_save_chart_not_bare_matplotlib_savefig(self, mock_save_chart, mock_close):
        # Regression coverage for the exact bug this module must not reintroduce: a bare
        # matplotlib.pyplot.savefig call would create data/weeks/week_NN/ eagerly even under a
        # mock, since only save_chart bundles ensure_dir_for with the real write (see
        # fantasy_sim.storage.save_chart's docstring).
        render_win_trajectory_chart({"Team A": [1.0, 2.0]}, week=1)
        mock_save_chart.assert_called_once()


class TestBuildWinTrajectoryChart(unittest.TestCase):
    @patch('fantasy_sim.win_trajectory.render_win_trajectory_chart')
    @patch('fantasy_sim.win_trajectory.load_json')
    def test_reads_the_named_week_and_delegates_to_render(self, mock_load_json, mock_render):
        mock_load_json.return_value = {
            "weekly_trajectories": {"Team A": {"expected_cumulative_wins_by_week": [1.0, 2.0]}}
        }
        build_win_trajectory_chart(week=3)
        mock_render.assert_called_once_with({"Team A": [1.0, 2.0]}, 3)


if __name__ == '__main__':
    unittest.main()

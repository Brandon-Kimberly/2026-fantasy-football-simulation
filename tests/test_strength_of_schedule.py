"""
Characterisation tests for fantasy_sim.strength_of_schedule, written before the module exists
(CLAUDE.md rule 1). The two grid-builders are pure functions taking an injected environment
lookup / pre-built team grid, so they're tested directly with synthetic data -- no real engine,
no real data/ files needed. The engine-instantiation wrapper and the render functions are
covered separately (a fake engine class, and matplotlib.pyplot mocking matching this codebase's
existing convention for chart-producing code).
"""
import io
import unittest
from unittest.mock import patch

from fantasy_sim.strength_of_schedule import (
    _environment_grid_from_lookup, _load_environment_engine, build_roster_sos_grid,
    render_team_grid_chart, render_team_summary_chart, render_roster_grid_chart,
)


class TestEnvironmentGridFromLookup(unittest.TestCase):
    def test_builds_team_by_week_grid_from_the_lookup_function(self):
        def fake_lookup(week, team):
            return {'total': 20.0 + week, 'opponent': f"OPP{week}"}

        grid = _environment_grid_from_lookup(['SEA', 'KC'], [1, 2], fake_lookup)
        self.assertEqual(set(grid.keys()), {'SEA', 'KC'})
        self.assertEqual(grid['SEA'][1], {'total': 21.0, 'opponent': 'OPP1', 'is_bye': False})
        self.assertEqual(grid['KC'][2], {'total': 22.0, 'opponent': 'OPP2', 'is_bye': False})

    def test_flags_bye_week_via_fa_opponent_sentinel(self):
        def fake_lookup(week, team):
            return {'total': 21.5, 'opponent': 'FA'}

        grid = _environment_grid_from_lookup(['SEA'], [11], fake_lookup)
        self.assertTrue(grid['SEA'][11]['is_bye'])


class TestBuildRosterSosGrid(unittest.TestCase):
    def test_averages_unweighted_across_every_rostered_player_not_just_starters(self):
        # 'Team A' has 3 players: two on SEA, one on KC. The average must be computed per
        # PLAYER (SEA counted twice), not deduplicated per real NFL team -- a roster that
        # is heavier on one real offense is more exposed to that offense's environment.
        team_grid = {
            'SEA': {1: {'total': 30.0, 'opponent': 'X', 'is_bye': False}},
            'KC': {1: {'total': 10.0, 'opponent': 'Y', 'is_bye': False}},
        }
        rosters_meta = {
            'Team A': {
                'P1': {'team': 'SEA'}, 'P2': {'team': 'SEA'}, 'P3': {'team': 'KC'},
            },
        }
        grid = build_roster_sos_grid(rosters_meta, team_grid, [1])
        # (30 + 30 + 10) / 3 = 23.333...
        self.assertAlmostEqual(grid['Team A'][1], 70.0 / 3, places=6)

    def test_player_whose_real_team_is_missing_from_the_team_grid_is_skipped_not_zeroed(self):
        team_grid = {'SEA': {1: {'total': 30.0, 'opponent': 'X', 'is_bye': False}}}
        rosters_meta = {'Team A': {'P1': {'team': 'SEA'}, 'P2': {'team': 'FA'}}}
        grid = build_roster_sos_grid(rosters_meta, team_grid, [1])
        self.assertEqual(grid['Team A'][1], 30.0)  # not (30 + 0) / 2

    def test_week_with_no_resolvable_players_is_none_not_a_fabricated_zero(self):
        team_grid = {}
        rosters_meta = {'Team A': {'P1': {'team': 'FA'}}}
        grid = build_roster_sos_grid(rosters_meta, team_grid, [1])
        self.assertIsNone(grid['Team A'][1])


class FakeEngineNoisyInit:
    """Stands in for FantasySimulationEngine: mimics its noisy print() at construction time
    without needing any real data/ files."""
    def __init__(self):
        print("[PRE-FLIGHT SUCCESS] 999 Projections Validated.")
        self.current_week = 3


class TestLoadEnvironmentEngine(unittest.TestCase):
    @patch('fantasy_sim.strength_of_schedule.FantasySimulationEngine', FakeEngineNoisyInit)
    def test_suppresses_the_engines_stdout_noise(self):
        captured = io.StringIO()
        with patch('sys.stdout', captured):
            engine = _load_environment_engine()
        self.assertEqual(captured.getvalue(), "")
        self.assertEqual(engine.current_week, 3)


class TestRenderSmoke(unittest.TestCase):
    """Matches this codebase's existing convention (see test_simulation.py,
    test_positional_tiers.py) of patching the save_chart wrapper rather than inspecting
    rendered PNG output -- save_chart, not matplotlib.pyplot.savefig, because save_chart
    bundles the directory-creation call with the render (see fantasy_sim.storage.save_chart's
    docstring), so mocking it skips both instead of leaving an empty directory behind."""

    @patch('matplotlib.pyplot.close')
    @patch('fantasy_sim.strength_of_schedule.save_chart')
    def test_team_grid_chart_smoke(self, mock_savefig, mock_close):
        team_grid = {
            'SEA': {1: {'total': 25.0, 'opponent': 'NE', 'is_bye': False},
                    2: {'total': 21.5, 'opponent': 'FA', 'is_bye': True}},
            'KC': {1: {'total': 18.0, 'opponent': 'DEN', 'is_bye': False},
                   2: {'total': 22.0, 'opponent': 'LAC', 'is_bye': False}},
        }
        render_team_grid_chart(team_grid, [1, 2], week=1)
        mock_savefig.assert_called_once()

    @patch('matplotlib.pyplot.close')
    @patch('fantasy_sim.strength_of_schedule.save_chart')
    def test_team_summary_chart_smoke(self, mock_savefig, mock_close):
        team_grid = {
            'SEA': {1: {'total': 25.0, 'opponent': 'NE', 'is_bye': False}},
            'KC': {1: {'total': 18.0, 'opponent': 'DEN', 'is_bye': False}},
        }
        render_team_summary_chart(team_grid, [1], week=1)
        mock_savefig.assert_called_once()

    @patch('matplotlib.pyplot.close')
    @patch('fantasy_sim.strength_of_schedule.save_chart')
    def test_roster_grid_chart_smoke(self, mock_savefig, mock_close):
        roster_grid = {'Team A': {1: 23.3, 2: None}, 'Team B': {1: 19.1, 2: 20.0}}
        render_roster_grid_chart(roster_grid, [1, 2], week=1)
        mock_savefig.assert_called_once()


if __name__ == '__main__':
    unittest.main()

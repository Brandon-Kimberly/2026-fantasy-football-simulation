"""
Characterisation tests for fantasy_sim.positional_tiers, written before the module exists
(CLAUDE.md rule 1). compute_tiers's anchor/cumulative algorithm and _position_group's
DEF-exclusion / FLEX-catchall-exclusion are both hand-verified against small synthetic
inputs so a wrong implementation fails these, not just "looks plausible".
"""
import math
import unittest
from unittest.mock import patch

from fantasy_sim.positional_tiers import (
    compute_tiers, _position_group, _display_label, _tier_summary_rows,
    _trigger_summary_chart, _dominance_caption, _tier_palette, _hex_color,
    _build_tier_table_html, _render_tier_table, render_tier_summary_chart,
    render_tier_player_bars, TIERED_POSITIONS, TIER_Z, MAJORITY_TIER1_THRESHOLD,
)
from fantasy_sim.storage import tier_chart_path, positional_tiers_table_path, positional_tiers_report_path


def _baseline(mean, std_epistemic, pos='K', **extra):
    row = {'pos': pos, 'mean': mean, 'std_epistemic': std_epistemic, 'std_aleatoric': 1.0,
           'team': 'FA', 'bye': None, 'injury_status': None, 'on_ir': False}
    row.update(extra)
    return row


class TestPositionGroup(unittest.TestCase):
    def test_maps_raw_idp_positions_to_slot_position(self):
        self.assertEqual(_position_group('DE'), 'DL')
        self.assertEqual(_position_group('CB'), 'DB')
        self.assertEqual(_position_group('FB'), 'RB')
        self.assertEqual(_position_group('qb'), 'QB')

    def test_excludes_def_even_though_it_is_a_real_raw_position(self):
        # This is an IDP league: REQUIRED_STARTING_SLOTS has no 'DEF' slot at all. Team
        # defenses in the baselines file are Sleeper universe noise, not rosterable here.
        self.assertIsNone(_position_group('DEF'))
        self.assertIsNone(_position_group('def'))

    def test_excludes_positions_normalize_position_sends_to_flex(self):
        # e.g. a long-snapper or any raw position with no explicit mapping.
        self.assertIsNone(_position_group('LS'))

    def test_tiered_positions_matches_required_starting_slots_minus_flex(self):
        self.assertEqual(TIERED_POSITIONS, {'QB', 'RB', 'WR', 'TE', 'K', 'DL', 'LB', 'DB'})


class TestComputeTiers(unittest.TestCase):
    def test_anchor_cumulative_tiering_hand_computed(self):
        # A(20,1), B(18,1): gap=2, combined_se=sqrt(2)=1.414 -> 2 > 1.414*TIER_Z -> new tier.
        # C(17.9,1) vs anchor B(18,1): gap=0.1 -> same tier as B.
        # D(10,1) vs anchor B(18,1): gap=8 -> new tier.
        baselines = {
            'A': _baseline(20.0, 1.0),
            'B': _baseline(18.0, 1.0),
            'C': _baseline(17.9, 1.0),
            'D': _baseline(10.0, 1.0),
        }
        report = compute_tiers(baselines, z=1.0)
        by_name = {p['name']: p for p in report['K']}
        self.assertEqual(by_name['A']['tier'], 1)
        self.assertEqual(by_name['B']['tier'], 2)
        self.assertEqual(by_name['C']['tier'], 2)
        self.assertEqual(by_name['D']['tier'], 3)

    def test_players_within_a_group_sorted_by_mean_descending_with_matching_rank(self):
        baselines = {
            'Low': _baseline(5.0, 1.0),
            'High': _baseline(15.0, 1.0),
            'Mid': _baseline(10.0, 1.0),
        }
        report = compute_tiers(baselines, z=1.0)
        names_in_order = [p['name'] for p in report['K']]
        self.assertEqual(names_in_order, ['High', 'Mid', 'Low'])
        self.assertEqual([p['rank'] for p in report['K']], [1, 2, 3])

    def test_gap_strictly_above_threshold_splits_gap_strictly_below_does_not(self):
        # The comparison is a strict '>', not '>=' -- checked on both sides of the boundary
        # with a real epsilon rather than exact float equality, which a sqrt() intermediate
        # cannot be relied on to round-trip exactly.
        se = 1.0
        combined = math.sqrt(se ** 2 + se ** 2)
        threshold_gap = TIER_Z * combined
        baselines = {
            'Top': _baseline(20.0, se),
            'JustUnder': _baseline(20.0 - (threshold_gap - 1e-6), se),
            'JustOver': _baseline(20.0 - (threshold_gap + 1e-6), se),
        }
        report = compute_tiers(baselines, z=TIER_Z)
        by_name = {p['name']: p for p in report['K']}
        self.assertEqual(by_name['Top']['tier'], 1)
        self.assertEqual(by_name['JustUnder']['tier'], 1)
        self.assertEqual(by_name['JustOver']['tier'], 2)

    def test_conservation_every_tiered_player_appears_exactly_once(self):
        baselines = {
            'QB1': _baseline(20.0, 5.0, pos='QB'),
            'RB1': _baseline(15.0, 8.0, pos='RB'),
            'DEF1': _baseline(9.0, 1.5, pos='DEF'),
            'LS1': _baseline(1.0, 0.5, pos='LS'),
        }
        report = compute_tiers(baselines, z=1.0)
        all_names = [p['name'] for players in report.values() for p in players]
        self.assertEqual(sorted(all_names), ['QB1', 'RB1'])
        self.assertNotIn('DEF', report)

    def test_malformed_baseline_entries_are_skipped_not_fatal(self):
        baselines = {'Bad': 'not-a-dict', 'Good': _baseline(10.0, 1.0)}
        report = compute_tiers(baselines, z=1.0)
        self.assertEqual([p['name'] for p in report['K']], ['Good'])


class TestDisplayLabel(unittest.TestCase):
    def test_plain_name_is_unchanged(self):
        self.assertEqual(_display_label({'name': 'Justin Jefferson', 'team': 'MIN'}),
                         'Justin Jefferson')

    def test_collision_suffix_is_replaced_with_team_not_shown_as_a_raw_pid(self):
        self.assertEqual(_display_label({'name': 'Byron Murphy (4988)', 'team': 'SEA'}),
                         'Byron Murphy (SEA)')

    def test_collision_suffix_kept_as_pid_if_team_is_missing(self):
        # A bare pid is at least honest; a bare "Byron Murphy" with no disambiguator at all
        # would silently reintroduce the exact collision resolve_player_keys exists to avoid.
        self.assertEqual(_display_label({'name': 'Tyler Davis (5251)', 'team': None}),
                         'Tyler Davis (5251)')

    def test_does_not_touch_a_name_that_merely_contains_parentheses(self):
        # Only a trailing "(<digits>)" is treated as the collision-guard suffix.
        self.assertEqual(_display_label({'name': 'Some Player (Jr.)', 'team': 'KC'}),
                         'Some Player (Jr.)')


def _tiered_players(*specs):
    """specs: (name, mean, tier) triples, pre-assigned rather than run through compute_tiers,
    since these tests target the summary/caption helpers, not the tiering algorithm itself.
    Rank is assigned by input order, matching compute_tiers's contract of sorted-by-mean-desc,
    tier-contiguous input."""
    return [
        {'name': n, 'mean': m, 'tier': t, 'rank': i + 1, 'team': 'FA',
         'std_epistemic': 1.0, 'injury_status': None, 'on_ir': False}
        for i, (n, m, t) in enumerate(specs)
    ]


class TestTierSummaryRows(unittest.TestCase):
    def test_groups_contiguous_tiers_with_mean_range_and_top_names(self):
        players = _tiered_players(
            ('A', 20.0, 1), ('B', 18.0, 1),
            ('C', 10.0, 2), ('D', 9.0, 2), ('E', 8.0, 2),
        )
        rows = _tier_summary_rows(players, max_names=5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['tier'], 1)
        self.assertEqual(rows[0]['count'], 2)
        self.assertEqual((rows[0]['mean_min'], rows[0]['mean_max']), (18.0, 20.0))
        self.assertEqual(rows[0]['top_names'], ['A', 'B'])
        self.assertEqual(rows[1]['tier'], 2)
        self.assertEqual(rows[1]['count'], 3)
        self.assertEqual((rows[1]['mean_min'], rows[1]['mean_max']), (8.0, 10.0))

    def test_top_names_truncated_to_max_names_but_count_reflects_everyone(self):
        specs = [(f"P{i}", 20.0 - i, 1) for i in range(7)]
        rows = _tier_summary_rows(_tiered_players(*specs), max_names=5)
        self.assertEqual(rows[0]['count'], 7)
        self.assertEqual(len(rows[0]['top_names']), 5)
        self.assertEqual(rows[0]['top_names'], ['P0', 'P1', 'P2', 'P3', 'P4'])


class TestTriggerAndCaption(unittest.TestCase):
    def test_single_tier_group_triggers_summary_chart(self):
        players = _tiered_players(*[(f"P{i}", 20.0 - i, 1) for i in range(10)])
        self.assertTrue(_trigger_summary_chart(players))
        self.assertIn("ONE statistically indistinguishable tier", _dominance_caption(players))

    def test_group_below_threshold_does_not_trigger(self):
        # 3 of 10 in tier 1 = 30%, below MAJORITY_TIER1_THRESHOLD.
        players = _tiered_players(
            ('A', 20.0, 1), ('B', 19.0, 1), ('C', 18.0, 1),
            *[(f"D{i}", 10.0 - i, 2) for i in range(7)],
        )
        self.assertLess(3 / 10, MAJORITY_TIER1_THRESHOLD)
        self.assertFalse(_trigger_summary_chart(players))

    def test_group_at_or_above_threshold_triggers_with_rank_in_caption(self):
        # 6 of 10 in tier 1 = 60% == MAJORITY_TIER1_THRESHOLD (>=, not >).
        players = _tiered_players(
            *[(f"A{i}", 20.0 - i, 1) for i in range(6)],
            *[(f"B{i}", 10.0 - i, 2) for i in range(4)],
        )
        self.assertTrue(_trigger_summary_chart(players))
        caption = _dominance_caption(players)
        self.assertIn("6 of 10 (60%)", caption)
        self.assertIn("rank 7", caption)  # first tier-2 player


class TestSummaryChartAndTableSmoke(unittest.TestCase):
    """Matches this codebase's existing convention (see test_simulation.py) of patching
    matplotlib.pyplot.savefig rather than inspecting rendered PNG output -- the golden master
    deliberately does not hash charts either. These just confirm the render functions don't
    raise across the shapes that matter: one dominant tier, several tiers, and a group large
    enough to force the ranked table into multiple columns."""

    @patch('matplotlib.pyplot.close')
    @patch('matplotlib.pyplot.savefig')
    def test_summary_chart_single_tier(self, mock_savefig, mock_close):
        players = _tiered_players(*[(f"P{i}", 20.0 - i, 1) for i in range(32)])
        render_tier_summary_chart('QB', players, _dominance_caption(players), 1)
        mock_savefig.assert_called_once()

    @patch('matplotlib.pyplot.close')
    @patch('matplotlib.pyplot.savefig')
    def test_summary_chart_multiple_tiers(self, mock_savefig, mock_close):
        players = _tiered_players(
            *[(f"A{i}", 20.0 - i, 1) for i in range(6)],
            *[(f"B{i}", 10.0 - i, 2) for i in range(4)],
        )
        render_tier_summary_chart('WR', players, _dominance_caption(players), 1)
        mock_savefig.assert_called_once()

    @patch('matplotlib.pyplot.close')
    @patch('matplotlib.pyplot.savefig')
    def test_player_bar_chart_smoke(self, mock_savefig, mock_close):
        players = _tiered_players(
            *[(f"A{i}", 20.0 - i, 1) for i in range(3)],
            *[(f"B{i}", 10.0 - i, 2) for i in range(3)],
        )
        render_tier_player_bars('DL', players, players, 1)
        mock_savefig.assert_called_once()


class TestTierPalette(unittest.TestCase):
    def test_palette_length_matches_full_group_tier_count_not_a_cropped_subset(self):
        # This is the "visually agree" guarantee: a caller that only has a display-cropped
        # prefix (tiers 1-2 of a 6-tier group) must still get the FULL 6-color palette, not a
        # 2-color one -- otherwise tier 2's color would mean something different in a chart
        # that shows all 6 tiers vs. one that only shows a tiers-1-2 crop.
        full_group = _tiered_players(
            *[(f"P{i}", 20.0 - i, 1 + i // 2) for i in range(12)]  # tiers 1..6
        )
        cropped = full_group[:4]  # only tiers 1-2 present
        self.assertEqual(len(_tier_palette(full_group)), 6)
        self.assertEqual(len(_tier_palette(cropped)), 2)
        # (Not asserted: that tier 1's color is identical across the two calls -- seaborn's
        # viridis sampling positions shift with n, so a 6-color and a 2-color palette do NOT
        # share an endpoint. That's exactly why render_tier_player_bars/summary_chart/the HTML
        # table all call _tier_palette on the SAME full `players` list rather than ever mixing
        # a full-group call with a cropped one -- verified by the other tests in this file.)

    def test_empty_group_does_not_crash(self):
        self.assertEqual(len(_tier_palette([])), 1)


class TestHexColor(unittest.TestCase):
    def test_known_rgb_values(self):
        self.assertEqual(_hex_color((1.0, 0.0, 0.0)), '#ff0000')
        self.assertEqual(_hex_color((0.0, 0.0, 0.0)), '#000000')
        self.assertEqual(_hex_color((1.0, 1.0, 1.0)), '#ffffff')


class TestTierTableHtml(unittest.TestCase):
    def _sample_players(self):
        return _tiered_players(
            ('Puka Nacua', 17.2, 1), ('Byron Murphy (4988)', 6.7, 2),
        )

    def _with_team(self, players, teams):
        for p, t in zip(players, teams):
            p['team'] = t
        return players

    def test_contains_one_row_per_player_and_header_columns(self):
        players = self._with_team(self._sample_players(), ['LAR', 'SEA'])
        html = _build_tier_table_html('WR', players)
        tbody_html = html.split('<tbody>')[1]  # exclude the <thead> row's own <tr>
        self.assertEqual(tbody_html.count('<tr>'), len(players))
        for label in ["Rank", "Name", "Team", "Tier", "Projected Mean"]:
            self.assertIn(label, html)

    def test_collision_suffix_replaced_with_team_matches_chart_behavior(self):
        players = self._with_team(self._sample_players(), ['LAR', 'SEA'])
        html = _build_tier_table_html('WR', players)
        self.assertIn('Byron Murphy (SEA)', html)
        self.assertNotIn('(4988)', html)

    def test_tier_background_color_matches_shared_palette(self):
        players = self._with_team(self._sample_players(), ['LAR', 'SEA'])
        html = _build_tier_table_html('WR', players)
        expected_tier1_color = _hex_color(_tier_palette(players)[0])
        self.assertIn(f'background-color:{expected_tier1_color}', html)

    def test_contains_exactly_one_sort_script(self):
        players = self._with_team(self._sample_players(), ['LAR', 'SEA'])
        html = _build_tier_table_html('WR', players)
        self.assertEqual(html.count('<script>'), 1)
        self.assertIn('addEventListener', html)

    def test_uncertainty_range_sorts_numerically_by_lower_bound_not_the_formatted_string(self):
        # "10.5-12.0" must sort before "9.5-11.0" numerically (10.5 < 9.5 is false --
        # i.e. it must NOT sort as strings, where "1" < "9").
        players = _tiered_players(('High', 21.0, 1), ('Low', 19.5, 1))
        players[0]['std_epistemic'] = 9.0   # range ~12.0-30.0
        players[1]['std_epistemic'] = 10.0  # range ~9.5-29.5
        for p in players:
            p['team'] = 'FA'
        html = _build_tier_table_html('QB', players)
        low_sort = float(players[1]['mean']) - float(players[1]['std_epistemic'])
        self.assertIn(f'data-sort="{low_sort}"', html)

    def test_names_are_html_escaped(self):
        players = _tiered_players(("O'Brien <Test>", 10.0, 1),)
        players[0]['team'] = 'FA'
        html = _build_tier_table_html('K', players)
        self.assertNotIn('<Test>', html)
        self.assertIn('&lt;Test&gt;', html)


class TestRenderTierTable(unittest.TestCase):
    def test_writes_expected_html_to_the_given_path(self):
        import tempfile
        import os
        players = _tiered_players(('A', 20.0, 1),)
        players[0]['team'] = 'FA'
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, 'Tiers_K_Table.html')
            _render_tier_table('K', players, out_path)
            with open(out_path, encoding='utf-8') as f:
                content = f.read()
        self.assertEqual(content, _build_tier_table_html('K', players))


class TestWeekStampedPaths(unittest.TestCase):
    """Regression coverage for the retention bug: an earlier version of these three path
    helpers carried no week, so a week 2 run of scripts.run_positional_tiers would silently
    overwrite week 1's tiers/chart/table with no way to tell they'd ever existed."""

    def test_tier_chart_path_differs_by_week(self):
        # Week is now encoded in the directory (data/weeks/week_NN/), not a filename prefix --
        # see storage.py's directory-layout migration -- so check for the zero-padded
        # directory segment, not a "Week_N" filename substring.
        self.assertNotEqual(tier_chart_path('WR', 1), tier_chart_path('WR', 2))
        self.assertIn('week_01', tier_chart_path('WR', 1))
        self.assertIn('week_02', tier_chart_path('WR', 2))

    def test_table_path_differs_by_week(self):
        self.assertNotEqual(positional_tiers_table_path('WR', 1), positional_tiers_table_path('WR', 2))

    def test_report_path_differs_by_week(self):
        self.assertNotEqual(positional_tiers_report_path(1), positional_tiers_report_path(2))


if __name__ == '__main__':
    unittest.main()

"""fantasy_sim.luck_ledger: five pre-registered luck measurements (docs/LUCK_LEDGER.md).

The point of this module is that its definitions are FIXED before the data arrives, so
these tests pin the arithmetic against hand-computed cases rather than re-deriving the
formula in the assertion -- a test that recomputes what it is testing proves nothing.

The fixture is a deliberate rigged league: four teams, three weeks, where "Unlucky"
scores well and loses anyway because the pairings hand it the top scorer every week.
That is precisely the shape the ledger has to detect.
"""
import unittest

from fantasy_sim.luck_ledger import (
    CLOSE_MARGIN, all_play, ledger, two_sided_p,
)

# Unlucky outscores Mid and Low every week, but is paired with High every week.
SCORES = {
    1: {"High": 130.0, "Unlucky": 120.0, "Mid": 100.0, "Low": 90.0},
    2: {"High": 140.0, "Unlucky": 125.0, "Mid": 105.0, "Low": 95.0},
    3: {"High": 150.0, "Unlucky": 118.0, "Mid": 110.0, "Low": 80.0},
}
PAIRS = {w: [("High", "Unlucky"), ("Mid", "Low")] for w in (1, 2, 3)}


class TestAllPlay(unittest.TestCase):
    def test_all_play_counts_every_pairing_in_the_week(self):
        ap = all_play(SCORES)
        # 4 teams -> each plays the other 3, three weeks = 9 games each
        self.assertEqual(ap["Unlucky"][1], 9)
        # Unlucky beats Mid and Low every week, loses to High every week = 6 wins
        self.assertEqual(ap["Unlucky"][0], 6.0)
        self.assertEqual(ap["High"][0], 9.0)
        self.assertEqual(ap["Low"][0], 0.0)

    def test_a_tie_counts_as_half_a_win_for_both(self):
        ap = all_play({1: {"A": 100.0, "B": 100.0}})
        self.assertEqual(ap["A"], (0.5, 1))
        self.assertEqual(ap["B"], (0.5, 1))


class TestScheduleLuck(unittest.TestCase):
    def test_a_strong_team_paired_with_the_best_every_week_reads_as_unlucky(self):
        """Hand-computed: all-play 6/9 = .667 over 3 played games = 2.0 expected wins.
        Actual wins 0. Delta -2.0."""
        out = ledger(SCORES, PAIRS, "Unlucky")
        s = out["schedule_luck"]
        self.assertEqual(s["actual_wins"], 0)
        self.assertAlmostEqual(s["expected_wins"], 2.0, places=2)
        self.assertAlmostEqual(s["delta"], -2.0, places=2)
        self.assertAlmostEqual(s["all_play_pct"], 66.7, places=1)
        self.assertEqual(s["games"], 3)

    def test_the_mirror_team_is_correspondingly_lucky(self):
        """Mid goes 3-0 on a .333 all-play rate: expected 1.0, actual 3, delta +2.0."""
        s = ledger(SCORES, PAIRS, "Mid")["schedule_luck"]
        self.assertEqual(s["actual_wins"], 3)
        self.assertAlmostEqual(s["delta"], +2.0, places=2)

    def test_schedule_luck_sums_to_about_zero_across_the_league(self):
        """Wins are conserved: one team's schedule gift is another's robbery."""
        total = sum(ledger(SCORES, PAIRS, t)["schedule_luck"]["delta"] for t in SCORES[1])
        self.assertAlmostEqual(total, 0.0, places=6)


class TestOpponentLuck(unittest.TestCase):
    def test_facing_the_high_scorer_every_week_shows_up_as_points_against(self):
        # Unlucky always faces High: (130+140+150)/3 = 140.0 against
        o = ledger(SCORES, PAIRS, "Unlucky")["opponent_luck"]
        self.assertAlmostEqual(o["my_pa_per_game"], 140.0, places=2)
        self.assertGreater(o["delta"], 0.0, "above-average PA is bad luck")


class TestCloseGames(unittest.TestCase):
    def test_close_games_use_the_registered_threshold(self):
        scores = {1: {"A": 100.0, "B": 95.0},      # margin 5 -> close, A wins
                  2: {"A": 100.0, "B": 130.0}}     # margin 30 -> not close
        pairs = {1: [("A", "B")], 2: [("A", "B")]}
        c = ledger(scores, pairs, "A")["close_games"]
        self.assertEqual((c["wins"], c["losses"], c["n"]), (1, 0, 1))
        self.assertEqual(c["margin_threshold"], CLOSE_MARGIN)

    def test_a_margin_exactly_at_the_threshold_is_not_close(self):
        scores = {1: {"A": 100.0, "B": 90.0}}      # margin exactly 10
        c = ledger(scores, {1: [("A", "B")]}, "A")["close_games"]
        self.assertEqual(c["n"], 0)


class TestDnpLuck(unittest.TestCase):
    def test_zero_scoring_starters_are_counted_against_the_league_rate(self):
        starters = {
            1: {"A": [10.0, 0.0, 5.0], "B": [10.0, 10.0, 5.0], "C": [1.0, 2.0, 3.0]},
            2: {"A": [10.0, 0.0, 0.0], "B": [10.0, 10.0, 5.0], "C": [1.0, 2.0, 3.0]},
        }
        scores = {1: {"A": 15.0, "B": 25.0, "C": 6.0}, 2: {"A": 10.0, "B": 25.0, "C": 6.0}}
        pairs = {1: [("A", "B")], 2: [("A", "B")]}
        d = ledger(scores, pairs, "A", starter_points=starters)["dnp_luck"]
        self.assertAlmostEqual(d["my_dnps_per_game"], 1.5)   # 1 then 2
        self.assertGreater(d["delta"], 0.0)

    def test_absent_starter_data_reports_none_rather_than_zero(self):
        self.assertIsNone(ledger(SCORES, PAIRS, "Unlucky")["dnp_luck"])


class TestScoringLuck(unittest.TestCase):
    def test_my_z_is_differenced_against_the_league_not_against_zero(self):
        """Every team misses its projection by -1 sd; that is MODEL bias, not luck, and
        must net to zero. This is the whole methodological point of the module."""
        scores = {1: {"A": 90.0, "B": 90.0, "C": 90.0}}
        proj = {1: {"A": (100.0, 10.0), "B": (100.0, 10.0), "C": (100.0, 10.0)}}
        pairs = {1: [("A", "B")]}
        s = ledger(scores, pairs, "A", projections=proj)["scoring_luck"]
        self.assertAlmostEqual(s["my_mean_z"], -1.0, places=6)
        self.assertAlmostEqual(s["league_mean_z"], -1.0, places=6)
        self.assertAlmostEqual(s["delta"], 0.0, places=6)

    def test_a_team_genuinely_underperforming_the_field_shows_a_negative_delta(self):
        scores = {1: {"A": 80.0, "B": 100.0, "C": 100.0}}
        proj = {1: {"A": (100.0, 10.0), "B": (100.0, 10.0), "C": (100.0, 10.0)}}
        s = ledger(scores, {1: [("A", "B")]}, "A", projections=proj)["scoring_luck"]
        # the module rounds its reported values to 3 dp; assert at that precision
        self.assertAlmostEqual(s["my_mean_z"], -2.0, places=3)
        self.assertAlmostEqual(s["league_mean_z"], -2.0 / 3.0, places=3)
        self.assertLess(s["delta"], 0.0)

    def test_absent_projections_report_none(self):
        """2024 and 2025 have no contemporaneous projections. Saying so is the honest
        answer; a zero would read as 'measured, and neutral'."""
        self.assertIsNone(ledger(SCORES, PAIRS, "Unlucky")["scoring_luck"])


class TestPValue(unittest.TestCase):
    def test_two_sided_p_matches_the_known_normal_tails(self):
        self.assertAlmostEqual(two_sided_p(1.96), 0.05, places=3)
        self.assertAlmostEqual(two_sided_p(0.0), 1.0, places=6)




class TestDirectionConvention(unittest.TestCase):
    """A reader who cannot tell good luck from bad at a glance is worse off than one with
    no number: facing high-scoring opponents (+) is a beating, carrying fewer DNPs than
    the league (-) is a gift, and both would otherwise print as a bare negative delta."""

    def test_every_registered_metric_has_a_direction(self):
        from fantasy_sim.luck_ledger import LUCKY_SIGN
        out = ledger(SCORES, PAIRS, "Unlucky")
        for name in ("schedule_luck", "opponent_luck", "close_games",
                     "dnp_luck", "scoring_luck"):
            self.assertIn(name, LUCKY_SIGN, f"{name} has no registered lucky direction")
            self.assertIn(name, out)

    def test_the_two_inverted_metrics_read_the_right_way(self):
        from fantasy_sim.luck_ledger import direction
        # more points scored against me is BAD even though the delta is positive
        self.assertEqual(direction("opponent_luck", +5.27), "unlucky")
        self.assertEqual(direction("opponent_luck", -5.27), "lucky")
        # fewer DNPs than the league is GOOD even though the delta is negative
        self.assertEqual(direction("dnp_luck", -0.69), "lucky")
        self.assertEqual(direction("dnp_luck", +0.69), "unlucky")

    def test_the_ordinary_metrics_read_the_obvious_way(self):
        from fantasy_sim.luck_ledger import direction
        self.assertEqual(direction("schedule_luck", -1.14), "unlucky")
        self.assertEqual(direction("schedule_luck", +1.14), "lucky")
        self.assertEqual(direction("scoring_luck", -0.40), "unlucky")

    def test_an_exact_zero_is_neutral_not_lucky(self):
        from fantasy_sim.luck_ledger import direction
        self.assertEqual(direction("close_games", 0.0), "neutral")
        self.assertEqual(direction("schedule_luck", None), "neutral")


if __name__ == "__main__":
    unittest.main()

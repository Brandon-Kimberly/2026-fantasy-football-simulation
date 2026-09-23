"""B11: the live tracker treats the two weekly legs as independent when they share a score.

`live_matchup` prints "expected wins 1.17 of 2 (2-0 ~34%, 0-2 ~17%; legs treated as
independent)". They are not independent: the head-to-head result and the median result
both turn on MY score. A big day wins both, a bad day loses both, so P(2-0) and P(0-2) are
both understated and P(1-1) is overstated.

`median_leg()` already draws all eight rosters jointly and returns the full per-draw
matrix, so the fix is arithmetic on draws that already exist -- B11's "one function, no
new model".

A NOTE ON THE TWO H2H NUMBERS, which this surfaced. The printed `P(win head-to-head)`
comes from `win_probability()`, a closed-form normal approximation on the margin. The
matrix instead sums per-player draws truncated at zero (`np.maximum(0.0, ...)`). They
therefore need not agree, and the truncation matters most when a remaining player's mean
is small relative to his sd. The joint block reports the marginal IMPLIED BY ITS OWN
DRAWS so that its three numbers are internally consistent, and the tests below pin that
consistency rather than pretending the two methods coincide.

Written before the function existed and confirmed failing (rule 1).
"""
import unittest

import numpy as np

NAMES = ["ME", "OPP", "C", "D", "E", "F", "G", "H"]


def _matrix(me, opp, others=100.0, sims=None):
    """Rows are teams, columns are draws -- the shape median_leg() returns."""
    me = np.asarray(me, dtype=float)
    opp = np.asarray(opp, dtype=float)
    n = sims or me.size
    rows = [me, opp] + [np.full(n, others + i) for i in range(6)]
    return np.vstack(rows)


class TestTheFunctionExists(unittest.TestCase):
    def test_joint_legs_is_importable(self):
        from scripts.live_matchup import joint_legs
        self.assertTrue(callable(joint_legs))


class TestCertainOutcomes(unittest.TestCase):
    """B11 acceptance 1: nothing left to play means exactly one cell holds all the mass."""

    def test_a_certain_double_win_is_two_zero_with_probability_one(self):
        from scripts.live_matchup import joint_legs
        m = _matrix(np.full(500, 200.0), np.full(500, 150.0), others=100.0)
        r = joint_legs(m, NAMES, "ME", "OPP")
        self.assertAlmostEqual(r["p_2_0"], 1.0)
        self.assertAlmostEqual(r["p_1_1"], 0.0)
        self.assertAlmostEqual(r["p_0_2"], 0.0)

    def test_a_certain_double_loss_is_zero_two_with_probability_one(self):
        from scripts.live_matchup import joint_legs
        m = _matrix(np.full(500, 10.0), np.full(500, 150.0), others=100.0)
        r = joint_legs(m, NAMES, "ME", "OPP")
        self.assertAlmostEqual(r["p_0_2"], 1.0)
        self.assertAlmostEqual(r["p_2_0"], 0.0)

    def test_a_certain_split_is_one_one(self):
        """Beat the opponent, miss the median: possible when the opponent is the worst
        team in the league and everyone else is above me."""
        from scripts.live_matchup import joint_legs
        m = _matrix(np.full(500, 90.0), np.full(500, 50.0), others=100.0)
        r = joint_legs(m, NAMES, "ME", "OPP")
        self.assertAlmostEqual(r["p_1_1"], 1.0)


class TestTheLegsAreCorrelated(unittest.TestCase):
    """B11 acceptance 2: when one uncertain player decides both legs, the joint tails are
    FATTER than the independent product."""

    def test_shared_uncertainty_fattens_both_tails(self):
        from scripts.live_matchup import joint_legs
        rng = np.random.default_rng(11)
        swing = rng.normal(120.0, 40.0, 20000)          # my score: one big unknown
        m = _matrix(swing, np.full(20000, 118.0), others=119.0)
        r = joint_legs(m, NAMES, "ME", "OPP")
        indep_2_0 = r["p_h2h"] * r["p_median"]
        indep_0_2 = (1 - r["p_h2h"]) * (1 - r["p_median"])
        self.assertGreater(r["p_2_0"] + r["p_0_2"], indep_2_0 + indep_0_2,
                           "B11: a shared score makes 2-0 and 0-2 more likely, not less")
        self.assertGreater(r["p_2_0"], indep_2_0)
        self.assertGreater(r["p_0_2"], indep_0_2)

    def test_the_independent_product_is_reported_for_comparison(self):
        """The point of the change is visible only beside the number it replaces."""
        from scripts.live_matchup import joint_legs
        rng = np.random.default_rng(3)
        m = _matrix(rng.normal(120.0, 40.0, 5000), np.full(5000, 118.0), others=119.0)
        r = joint_legs(m, NAMES, "ME", "OPP")
        self.assertIn("p_2_0_independent", r)
        self.assertIn("p_0_2_independent", r)
        self.assertAlmostEqual(r["p_2_0_independent"], r["p_h2h"] * r["p_median"])


class TestInternalConsistency(unittest.TestCase):
    def test_the_three_cells_sum_to_one(self):
        from scripts.live_matchup import joint_legs
        rng = np.random.default_rng(5)
        m = _matrix(rng.normal(120.0, 30.0, 4000), rng.normal(118.0, 30.0, 4000), others=119.0)
        r = joint_legs(m, NAMES, "ME", "OPP")
        self.assertAlmostEqual(r["p_2_0"] + r["p_1_1"] + r["p_0_2"], 1.0, places=9)

    def test_the_marginals_come_from_the_same_draws(self):
        """So the block cannot print three cells that disagree with their own margins."""
        from scripts.live_matchup import joint_legs
        rng = np.random.default_rng(7)
        m = _matrix(rng.normal(120.0, 30.0, 4000), rng.normal(118.0, 30.0, 4000), others=119.0)
        r = joint_legs(m, NAMES, "ME", "OPP")
        self.assertAlmostEqual(r["p_2_0"] + (r["p_1_1"] - r["p_median_only"]), r["p_h2h"],
                               places=9)
        self.assertAlmostEqual(r["p_2_0"] + r["p_median_only"], r["p_median"], places=9)

    def test_expected_wins_matches_the_sum_of_marginals(self):
        from scripts.live_matchup import joint_legs
        rng = np.random.default_rng(9)
        m = _matrix(rng.normal(120.0, 30.0, 4000), rng.normal(118.0, 30.0, 4000), others=119.0)
        r = joint_legs(m, NAMES, "ME", "OPP")
        self.assertAlmostEqual(r["expected_wins"], r["p_h2h"] + r["p_median"], places=9)
        self.assertAlmostEqual(r["expected_wins"],
                               2 * r["p_2_0"] + r["p_1_1"], places=9)


class TestEdges(unittest.TestCase):
    def test_no_opponent_yields_only_the_median_leg(self):
        """A bye week, or a team with no scheduled opponent."""
        from scripts.live_matchup import joint_legs
        m = _matrix(np.full(100, 130.0), np.full(100, 0.0), others=100.0)
        r = joint_legs(m, NAMES, "ME", None)
        self.assertIsNone(r["p_h2h"])
        self.assertAlmostEqual(r["p_median"], 1.0)
        self.assertIsNone(r["p_2_0"])

    def test_an_unknown_team_is_refused_rather_than_guessed(self):
        from scripts.live_matchup import joint_legs
        m = _matrix(np.full(10, 1.0), np.full(10, 1.0))
        with self.assertRaises(KeyError):
            joint_legs(m, NAMES, "NOBODY", "OPP")


if __name__ == "__main__":
    unittest.main()

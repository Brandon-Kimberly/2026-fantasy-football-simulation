"""Both season charts open at week 1 with a team already on wins, so the climb from 0-0 is
invisible.

B30 filled the completed-week columns with real results, which fixed the "everyone is 0-0
until today" defect. What it left is the opposite reading: the first plotted point is week 1,
and a team that went 2-0 starts its line at 2. Nothing on the chart says where the season
began, so the first week's movement -- often the largest single step -- is not drawn at all.

Requested by the owner after seeing the corrected charts: make it clear everyone started at
0-0 and show where they went from there.

**This is a PRESENTATION change and must stay one.** The hashed `trajectories` array is 14
columns, weeks 1..14, and that is what the golden master pins. Prepending the origin at PLOT
time leaves the array untouched, so this adds no golden movement of its own -- the only
golden delta in this work remains B30's completed-week fill.

Week 0 is 0.0 for every team by definition: no week has been played, so no decision has been
awarded. That is true of the mean line and of every percentile band, which all collapse to
the same point.
"""
import unittest


class TestTheOriginIsPrepended(unittest.TestCase):
    def test_a_zero_is_prepended_to_the_series(self):
        from fantasy_sim.win_trajectory import with_origin
        self.assertEqual(with_origin([2.0, 2.0, 3.57]), [0.0, 2.0, 2.0, 3.57])

    def test_the_x_axis_starts_at_zero(self):
        """The weeks that go with the series: week 0 is the origin, then 1..n."""
        from fantasy_sim.win_trajectory import origin_weeks
        self.assertEqual(origin_weeks([2.0, 2.0, 3.57]), [0, 1, 2, 3])

    def test_the_series_itself_is_not_mutated(self):
        """The caller's list is the exported, golden-pinned data -- plotting must not touch
        it."""
        from fantasy_sim.win_trajectory import with_origin
        original = [2.0, 2.0]
        with_origin(original)
        self.assertEqual(original, [2.0, 2.0])

    def test_an_empty_series_still_yields_the_origin(self):
        from fantasy_sim.win_trajectory import with_origin, origin_weeks
        self.assertEqual(with_origin([]), [0.0])
        self.assertEqual(origin_weeks([]), [0])

    def test_a_numpy_array_is_accepted(self):
        """The grid chart passes numpy means and percentiles, not lists."""
        import numpy as np
        from fantasy_sim.win_trajectory import with_origin
        self.assertEqual(with_origin(np.array([1.5, 2.5])), [0.0, 1.5, 2.5])

    def test_lengths_line_up_for_plotting(self):
        """matplotlib raises if x and y differ in length; this is the property that matters."""
        from fantasy_sim.win_trajectory import with_origin, origin_weeks
        for n in (0, 1, 5, 14):
            series = [1.0] * n
            self.assertEqual(len(with_origin(series)), len(origin_weeks(series)), n)


class TestTheDataIsUnchanged(unittest.TestCase):
    """GREEN BY DESIGN. The exported series stays 14 weeks; only the drawing gains a point."""

    def test_the_exported_trajectory_length_is_untouched(self):
        from fantasy_sim.win_trajectory import extract_trajectories
        matrix = {"weekly_trajectories": {
            "Quantum Ferrets": {"expected_cumulative_wins_by_week": [1.0] * 14}}}
        self.assertEqual(len(extract_trajectories(matrix)["Quantum Ferrets"]), 14,
                         "the hashed array must remain weeks 1..14")


if __name__ == "__main__":
    unittest.main()

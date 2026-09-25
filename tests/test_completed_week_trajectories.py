"""Completed weeks are plotted as zero, so every chart says the season started today.

`global_trajectories` is allocated as `np.zeros((total_sims, 14))` and the simulation loop
writes only `range(self.current_week - 1, 16)`. Every column BEFORE the current week is
therefore never written and stays at 0.0 — while `sim_wins` is correctly seeded from
`self.actual_total_wins`, so the projected half is right and only the history is blank.

Two charts read that array and both mislead:

  * "Cumulative win trajectories" — each team's line sits on the x-axis through every
    completed week and then jumps to its banked total, implying an 0-0 league up to today.
  * "Expected wins over the simulated season" — same source, same flat opening.

Reported by the owner from the week-3 report: with two weeks played he is 2-2, and the chart
showed zero wins for weeks 1 and 2.

**Which record to draw is not obvious, and the wrong one is visibly wrong.** Summing the
per-week `h2h_win` + `median_win` flags out of `weekly_actuals.json` gives the RECOMPUTED
record, which after a mid-season scoring change is not what the league banked (F83/F84).
Measured live on 2026-09-25:

    team              wk1   wk2   recomputed   banked
    Quantum Ferrets   2.0   1.0      3.0        2.0     <- 2-2, and the owner said so
    Cosmic Badgers    1.0   0.0      1.0        2.0
    (six others)                     match      match

So the per-week flags alone would draw the owner at 3 wins after two weeks and then start the
projection from 2 — a visible discontinuity at exactly the boundary the chart exists to show,
and a number the league does not recognise.

**The endpoint is anchored to the banked record** — `actual_total_wins`, which is what F84
made authoritative and what `sim_wins` already starts from, so the history meets the forecast
with no step. The per-week flags supply the SHAPE of the earlier weeks, because no per-week
banked record exists anywhere: Sleeper stores a total, and the whole of F83 is that completed
weeks are re-derived rather than kept. Where the two disagree the difference lands on the last
completed week, and a backward clamp keeps the series monotonic — cumulative wins cannot fall.

That attribution is a genuine limitation, recorded here rather than hidden: the disagreement
is known in total and unknowable per week, so the last completed week absorbs it.

Written before the change.
"""
import unittest

TEAMS = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis"]


def _actuals(per_week):
    """per_week: {team: [wins in wk1, wk2, ...]} -> weekly_actuals-shaped dict."""
    out = {}
    n = max(len(v) for v in per_week.values())
    for i in range(n):
        rows = {}
        for t, seq in per_week.items():
            w = seq[i] if i < len(seq) else 0.0
            # split the week's wins across the two legs the way a real week does
            rows[t] = {"h2h_win": 1.0 if w >= 1 else 0.0,
                       "median_win": 1.0 if w >= 2 else 0.0,
                       "points_scored": 100.0}
        out[f"week_{i + 1}"] = {"team_results": rows, "player_scores": {}}
    return out


class TestTheCompletedWeeksAreFilled(unittest.TestCase):
    def test_a_completed_week_is_not_zero(self):
        """The defect itself."""
        from fantasy_sim.simulation import completed_week_cumulative
        got = completed_week_cumulative(
            _actuals({t: [2, 1] for t in TEAMS}), TEAMS, current_week=3,
            banked_wins={t: 3 for t in TEAMS})
        self.assertEqual(len(got["Quantum Ferrets"]), 2)
        self.assertNotEqual(got["Quantum Ferrets"][0], 0.0)

    def test_the_series_is_cumulative_not_per_week(self):
        from fantasy_sim.simulation import completed_week_cumulative
        got = completed_week_cumulative(
            _actuals({t: [2, 1] for t in TEAMS}), TEAMS, current_week=3,
            banked_wins={t: 3 for t in TEAMS})
        self.assertEqual(got["Quantum Ferrets"], [2.0, 3.0])

    def test_the_last_completed_week_equals_the_BANKED_total(self):
        """The live case: per-week flags sum to 3, the league banked 2, and 2 is what the
        owner's record says. The forecast starts from the banked number, so the history has
        to end there or the chart steps at the boundary."""
        from fantasy_sim.simulation import completed_week_cumulative
        got = completed_week_cumulative(
            _actuals({"Quantum Ferrets": [2, 1]}), ["Quantum Ferrets"], current_week=3,
            banked_wins={"Quantum Ferrets": 2})
        self.assertEqual(got["Quantum Ferrets"][-1], 2.0)

    def test_a_banked_total_ABOVE_the_recomputed_sum_is_honoured_too(self):
        """The other live disagreement ran the other way."""
        from fantasy_sim.simulation import completed_week_cumulative
        got = completed_week_cumulative(
            _actuals({"Cosmic Badgers": [1, 0]}), ["Cosmic Badgers"], current_week=3,
            banked_wins={"Cosmic Badgers": 2})
        self.assertEqual(got["Cosmic Badgers"], [1.0, 2.0])

    def test_the_series_never_decreases(self):
        """Cumulative wins cannot fall. A banked total below the running sum has to clamp the
        earlier weeks down, not draw a line that goes backwards."""
        from fantasy_sim.simulation import completed_week_cumulative
        got = completed_week_cumulative(
            _actuals({"Quantum Ferrets": [2, 2, 2]}), ["Quantum Ferrets"], current_week=4,
            banked_wins={"Quantum Ferrets": 3})
        series = got["Quantum Ferrets"]
        self.assertEqual(series[-1], 3.0)
        self.assertTrue(all(b >= a for a, b in zip(series, series[1:])), series)
        self.assertTrue(all(v >= 0 for v in series), series)

    def test_week_one_has_no_completed_weeks(self):
        """The week01 golden scenario: nothing is banked, nothing to fill, and the array must
        be left exactly as the simulation writes it."""
        from fantasy_sim.simulation import completed_week_cumulative
        self.assertEqual(
            completed_week_cumulative({}, TEAMS, current_week=1, banked_wins={t: 0 for t in TEAMS}),
            {t: [] for t in TEAMS})

    def test_a_missing_weekly_actuals_entry_does_not_raise(self):
        from fantasy_sim.simulation import completed_week_cumulative
        got = completed_week_cumulative({}, ["Quantum Ferrets"], current_week=3,
                                        banked_wins={"Quantum Ferrets": 2})
        self.assertEqual(got["Quantum Ferrets"][-1], 2.0)

    def test_a_team_absent_from_the_banked_record_falls_back_to_the_sum(self):
        """No banked figure is not a reason to draw zeros."""
        from fantasy_sim.simulation import completed_week_cumulative
        got = completed_week_cumulative(
            _actuals({"Quantum Ferrets": [2, 1]}), ["Quantum Ferrets"], current_week=3,
            banked_wins={})
        self.assertEqual(got["Quantum Ferrets"], [2.0, 3.0])


class TestTheForecastIsUntouched(unittest.TestCase):
    """GREEN BY DESIGN. Only columns BEFORE the current week are written; every simulated
    column stays exactly as the engine produced it."""

    def test_only_completed_weeks_are_returned(self):
        from fantasy_sim.simulation import completed_week_cumulative
        got = completed_week_cumulative(
            _actuals({t: [2, 1, 2, 1, 2] for t in TEAMS}), TEAMS, current_week=6,
            banked_wins={t: 8 for t in TEAMS})
        self.assertEqual(len(got["Quantum Ferrets"]), 5,
                         "current_week=6 means weeks 1-5 are complete and week 6 is not")

    def test_it_never_returns_more_than_fourteen(self):
        """The trajectory array is 14 wide; the playoffs are weeks 15-16 and are not plotted."""
        from fantasy_sim.simulation import completed_week_cumulative
        got = completed_week_cumulative(
            _actuals({t: [1] * 16 for t in TEAMS}), TEAMS, current_week=17,
            banked_wins={t: 16 for t in TEAMS})
        self.assertLessEqual(len(got["Quantum Ferrets"]), 14)


if __name__ == "__main__":
    unittest.main()

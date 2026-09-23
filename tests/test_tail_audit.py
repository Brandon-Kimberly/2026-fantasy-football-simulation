"""B12: promote the scratchpad "how improbable is this?" audit into `live_matchup --tail`.

`howbad.py` answers the question that gets asked at 4pm on a Sunday: given how much of
each game has actually been played, how far below expectation is this roster, and how
rare is that? Scoring accrues over game time, so expectation scales with elapsed time and
variance with its square root.

    played           = 1 - clock_fraction_remaining
    expected_by_now  = week_expectation * played
    sd_by_now        = sd * sqrt(played)
    z                = (points_so_far - expected_by_now) / sd_by_now

TWO THINGS THE SCRATCHPAD GOT WRONG.

1. It rebuilt each player's NAME from the raw cache and looked him up in
   `engine.baselines` by that name -- the B17 collision path, on the 220 colliding names
   in the cache. The port never does this: `live_matchup.team_states` already returns
   per-starter rows carrying `scored`, `mean`, `sd` and `frac`, keyed by pid and built
   from `week_projections` (F50-correct). The tail is arithmetic on rows that exist.
2. It hardcoded the opponent's team name.

ONE THING IT GOT RIGHT AND THE PORT KEEPS, with the caveat made explicit: the me-cold /
them-hot joint treats the two rosters as independent. Unlike B11 -- where both legs
turned on MY score and independence was simply wrong -- these are disjoint player sets,
so independence is defensible. It is not exact: players on the same NFL game are
correlated, which is what live_matchup's --inflate exists for. The tool labels the number
rather than presenting it as precise.

Written before the function existed and confirmed failing (rule 1).
"""
import math
import unittest

NORM = lambda z: 0.5 * math.erfc(-z / math.sqrt(2))       # noqa: E731


def _row(pid, name, scored, mean, sd, frac, nfl="DET"):
    """The shape live_matchup.team_states already produces."""
    return {"pid": pid, "name": name, "pos": "WR", "nfl": nfl, "status": "Q2 10:00",
            "scored": scored, "left": mean * frac, "mean": mean, "sd": sd, "frac": frac}


class TestOnlyStartedGamesCount(unittest.TestCase):
    def test_a_pregame_player_is_excluded(self):
        from scripts.live_matchup import tail_audit
        st = {"banked": 0.0, "rows": [_row("1", "Pregame Guy", 0.0, 15.0, 6.0, frac=1.0)]}
        r = tail_audit(st)
        self.assertEqual(r["players"], [])
        self.assertIsNone(r["z"], "nothing has been played, so there is nothing to judge")

    def test_a_finished_player_counts_fully(self):
        from scripts.live_matchup import tail_audit
        st = {"banked": 4.0, "rows": [_row("1", "Done", 4.0, 16.0, 8.0, frac=0.0)]}
        r = tail_audit(st)
        self.assertEqual(len(r["players"]), 1)
        p = r["players"][0]
        self.assertAlmostEqual(p["expected_by_now"], 16.0)
        self.assertAlmostEqual(p["sd_by_now"], 8.0)
        self.assertAlmostEqual(p["z"], (4.0 - 16.0) / 8.0)

    def test_a_half_played_game_scales_mean_linearly_and_sd_by_sqrt(self):
        from scripts.live_matchup import tail_audit
        st = {"banked": 3.0, "rows": [_row("1", "Half", 3.0, 20.0, 10.0, frac=0.5)]}
        p = tail_audit(st)["players"][0]
        self.assertAlmostEqual(p["expected_by_now"], 10.0)
        self.assertAlmostEqual(p["sd_by_now"], 10.0 * math.sqrt(0.5))
        self.assertAlmostEqual(p["z"], (3.0 - 10.0) / (10.0 * math.sqrt(0.5)))


class TestTheTeamAggregate(unittest.TestCase):
    ROWS = [_row("1", "A", 2.0, 20.0, 10.0, frac=0.0),      # done, badly
            _row("2", "B", 5.0, 10.0, 5.0, frac=0.5),       # half played
            _row("3", "C", 0.0, 15.0, 6.0, frac=1.0)]       # not started: excluded

    def test_expectation_and_variance_accumulate_over_played_fractions_only(self):
        from scripts.live_matchup import tail_audit
        r = tail_audit({"banked": 7.0, "rows": self.ROWS})
        self.assertAlmostEqual(r["expected_by_now"], 20.0 + 5.0)
        self.assertAlmostEqual(r["sd_by_now"], math.sqrt(10.0 ** 2 + (5.0 * math.sqrt(0.5)) ** 2))
        self.assertAlmostEqual(r["scored"], 7.0)

    def test_the_team_z_and_its_tail_probability(self):
        from scripts.live_matchup import tail_audit
        r = tail_audit({"banked": 7.0, "rows": self.ROWS})
        sd = math.sqrt(10.0 ** 2 + (5.0 * math.sqrt(0.5)) ** 2)
        self.assertAlmostEqual(r["z"], (7.0 - 25.0) / sd)
        self.assertAlmostEqual(r["p_this_bad"], NORM(r["z"]))

    def test_one_in_n_is_the_reciprocal_of_the_tail(self):
        from scripts.live_matchup import tail_audit
        r = tail_audit({"banked": 7.0, "rows": self.ROWS})
        self.assertAlmostEqual(r["one_in"], 1.0 / r["p_this_bad"])

    def test_players_are_ranked_worst_first(self):
        from scripts.live_matchup import tail_audit
        r = tail_audit({"banked": 7.0, "rows": self.ROWS})
        zs = [p["z"] for p in r["players"]]
        self.assertEqual(zs, sorted(zs), "the man to be angry about goes first")

    def test_the_banked_total_is_preferred_over_summing_rows(self):
        """Sleeper's `points` is authoritative; summing per-player points silently drops
        anything it counts that the starter rows do not."""
        from scripts.live_matchup import tail_audit
        r = tail_audit({"banked": 99.0, "rows": self.ROWS})
        self.assertAlmostEqual(r["scored"], 99.0)


class TestEdges(unittest.TestCase):
    def test_a_zero_sd_player_does_not_divide_by_zero(self):
        from scripts.live_matchup import tail_audit
        st = {"banked": 1.0, "rows": [_row("1", "Certain", 1.0, 5.0, 0.0, frac=0.0)]}
        p = tail_audit(st)["players"][0]
        self.assertEqual(p["z"], 0.0)

    def test_an_empty_roster_reports_nothing_rather_than_raising(self):
        from scripts.live_matchup import tail_audit
        r = tail_audit({"banked": 0.0, "rows": []})
        self.assertIsNone(r["z"])
        self.assertIsNone(r["p_this_bad"])


class TestTheJointIsLabelledNotAsserted(unittest.TestCase):
    """B11 fixed a joint that was wrong because both legs shared one score. THIS joint is
    across two disjoint rosters, where independence is defensible but not exact -- players
    in the same NFL game are correlated. The tool must say so rather than imply precision."""

    def test_the_joint_reports_both_sides_and_names_its_assumption(self):
        from scripts.live_matchup import tail_joint
        mine = {"z": -2.0, "p_this_bad": NORM(-2.0)}
        theirs = {"z": 1.5, "p_this_bad": NORM(1.5)}
        j = tail_joint(mine, theirs)
        self.assertAlmostEqual(j["p_joint"], NORM(-2.0) * (1 - NORM(1.5)))
        self.assertAlmostEqual(j["one_in"], 1.0 / j["p_joint"])
        self.assertIn("independen", j["caveat"].lower())
        self.assertIn("same", j["caveat"].lower())

    def test_a_missing_side_yields_no_joint(self):
        from scripts.live_matchup import tail_joint
        self.assertIsNone(tail_joint({"z": None, "p_this_bad": None},
                                     {"z": 1.0, "p_this_bad": 0.8}))




class TestTheJointLabelMatchesTheSign(unittest.TestCase):
    """Found by running --tail on a completed week.

    The output read "me this cold AND them this hot" while the opponent was running at
    z -0.12, i.e. cold. The arithmetic was right -- P(mine <= z) * P(theirs >= z) -- but
    the sentence described the wrong world, and a number with a wrong label is worse than
    no number.
    """

    def test_a_hot_opponent_is_described_as_hot(self):
        from scripts.live_matchup import tail_joint
        j = tail_joint({"z": -2.0, "p_this_bad": NORM(-2.0)},
                       {"z": 1.5, "p_this_bad": NORM(1.5)})
        self.assertIn("hot", j["label"])

    def test_a_cold_opponent_is_not_described_as_hot(self):
        from scripts.live_matchup import tail_joint
        j = tail_joint({"z": -2.0, "p_this_bad": NORM(-2.0)},
                       {"z": -0.12, "p_this_bad": NORM(-0.12)})
        self.assertNotIn("this hot", j["label"])
        self.assertIn("cold", j["label"])


class TestStaleProjectionsOnAPastWeek(unittest.TestCase):
    """Also found by running it: `--tail --week 2` scored a completed week against
    TODAY's expectations. A quarterback who has since gone on IR carried a week-2
    expectation of 0.00, so a 8.72-point game read as +1.13 sigma ABOVE expectation.

    The tool is built for live use, where this cannot arise. For a past week it must say
    so rather than quietly present contaminated z-scores.
    """

    def test_a_past_week_is_flagged_as_using_todays_projections(self):
        from scripts.live_matchup import tail_caveat
        self.assertIsNotNone(tail_caveat(week=2, current_week=3))
        self.assertIn("today", tail_caveat(week=2, current_week=3).lower())

    def test_the_current_week_needs_no_caveat(self):
        from scripts.live_matchup import tail_caveat
        self.assertIsNone(tail_caveat(week=3, current_week=3))


if __name__ == "__main__":
    unittest.main()

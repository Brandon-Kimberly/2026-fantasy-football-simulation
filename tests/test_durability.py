"""B10: does an injury designation predict a later DNP, above the positional base rate?

B10 is a STUDY, and adoption is explicitly "a separate, later decision". This file builds
the estimator the study needs and pins the two things that make such a study honest.

WHY AN ESTIMATOR AND NOT JUST AN ANSWER. The study cannot run today. The data inventory,
measured rather than assumed:

    season_2025.json           0 designations. B10 guessed "partially recoverable for
                               2025"; the committed 2025 export carries roster_map,
                               final_standings and matchups (roster_id, matchup_id,
                               points, players, starters, players_points) and no
                               injury field of any kind.
    designations.jsonl (B21)   week 3 only, 28 rows, first written 2026-09-23.
    first_recorded_scores      weeks 1-2 only (B19 writes a week once it COMPLETES).

A lift needs a designation in week W and an outcome in week W+1. Designations exist for
week 3; outcomes exist for weeks 1-2. The overlap is EMPTY. So the deliverable is the
estimator plus a measured inventory, and the study runs itself when the weeks arrive.

Nothing here asserts "n == 0" -- a test that encodes today's data shortage fails the
moment the shortage ends, which is the wrong direction for a test to fail. The inventory
lives in `docs/audit/` and in `scripts.durability_study`, which reports what is missing.

THE DENOMINATOR IS THE DEFECT (F63). B21's `append_designations` writes ONLY players
carrying a designation, and says so on purpose: "A row per healthy man per week is 150
rows of 'nothing happened', and the question is about designations, not roll call."
For B10 that is exactly backwards. "Above the positional base rate" is a comparison
against the UNDESIGNATED, and a log of only the designated has no undesignated in it.
Who was healthy in week 4 is not recoverable afterwards from any retained file:
`live_rosters.json` is overwritten every sync and the players cache holds only today.

B10's TRAP, pinned below: do not proxy durability from scores. A low score is not an
injury. Only a designation makes a player designated, and only an exact 0.0 counts as a
DNP -- 1.2 points is a bad game, not an absence.

Written before `fantasy_sim.durability` existed and confirmed failing (rule 1).
"""
import unittest


# Four players, three weeks. Hand-computed so every number below is checkable by eye.
#   P1  designated wk1, wk2         -> wk3 DNP        (designated, DNP)
#   P2  designated wk2              -> wk3 played     (designated, played)
#   P3  never designated            -> wk3 DNP        (undesignated, DNP)
#   P4  never designated            -> wk3 played     (undesignated, played)
DESIGNATIONS = [
    {"week": 1, "player_id": "P1", "injury_status": "Questionable"},
    {"week": 2, "player_id": "P1", "injury_status": "Questionable"},
    {"week": 2, "player_id": "P2", "injury_status": "Doubtful"},
]
SCORES = [
    {"week": 2, "player_id": "P1", "points": 8.0},
    {"week": 2, "player_id": "P2", "points": 11.0},
    {"week": 2, "player_id": "P3", "points": 9.0},
    {"week": 2, "player_id": "P4", "points": 7.0},
    {"week": 3, "player_id": "P1", "points": 0.0},
    {"week": 3, "player_id": "P2", "points": 14.0},
    {"week": 3, "player_id": "P3", "points": 0.0},
    {"week": 3, "player_id": "P4", "points": 6.0},
]
POSITIONS = {"P1": "WR", "P2": "WR", "P3": "RB", "P4": "RB"}


class TestTheDesignationFeature(unittest.TestCase):
    def test_it_counts_designated_weeks_in_the_trailing_window(self):
        from fantasy_sim.durability import designation_counts
        got = designation_counts(DESIGNATIONS, through_week=2, lookback=3)
        self.assertEqual(got.get("P1"), 2)
        self.assertEqual(got.get("P2"), 1)
        self.assertNotIn("P3", got)

    def test_the_window_excludes_weeks_outside_the_lookback(self):
        from fantasy_sim.durability import designation_counts
        got = designation_counts(DESIGNATIONS, through_week=2, lookback=1)
        self.assertEqual(got.get("P1"), 1, "only week 2 is inside a 1-week lookback")

    def test_a_week_is_counted_once_however_many_rows_it_has(self):
        """B21 dedupes on (week, pid, STATUS), so a Friday Questionable that becomes a
        Sunday Out is two rows in one week. That is one designated week, not two."""
        from fantasy_sim.durability import designation_counts
        rows = DESIGNATIONS + [{"week": 2, "player_id": "P1", "injury_status": "Out"}]
        self.assertEqual(designation_counts(rows, through_week=2, lookback=3).get("P1"), 2)


class TestTheOutcome(unittest.TestCase):
    def test_a_zero_is_a_dnp(self):
        from fantasy_sim.durability import dnp_flags
        self.assertIs(dnp_flags(SCORES, week=3).get("P1"), True)

    def test_a_low_score_is_not_a_dnp(self):
        """B10's trap, stated in the item: do not proxy durability from scores."""
        from fantasy_sim.durability import dnp_flags
        rows = [{"week": 3, "player_id": "X", "points": 1.2}]
        self.assertIs(dnp_flags(rows, week=3).get("X"), False)

    def test_a_player_absent_from_the_feed_is_absent_not_a_dnp(self):
        """Never scored is 'unknown', and silently reading it as a DNP would manufacture
        the very signal the study is trying to detect."""
        from fantasy_sim.durability import dnp_flags
        self.assertNotIn("P9", dnp_flags(SCORES, week=3))


class TestTheStudy(unittest.TestCase):
    def test_it_builds_the_two_by_two_from_the_hand_computed_fixture(self):
        from fantasy_sim.durability import study
        r = study(DESIGNATIONS, SCORES, lookback=3)
        self.assertEqual(r["pairs"], [(2, 3)], "week 2 designations -> week 3 outcomes")
        self.assertEqual(r["designated"]["n"], 2)
        self.assertEqual(r["designated"]["dnp"], 1)
        self.assertEqual(r["undesignated"]["n"], 2)
        self.assertEqual(r["undesignated"]["dnp"], 1)

    def test_the_lift_is_the_ratio_of_the_two_rates(self):
        from fantasy_sim.durability import study
        r = study(DESIGNATIONS, SCORES, lookback=3)
        self.assertAlmostEqual(r["designated"]["rate"], 0.5)
        self.assertAlmostEqual(r["undesignated"]["rate"], 0.5)
        self.assertAlmostEqual(r["lift"], 1.0)

    def test_a_real_signal_shows_up_as_a_lift_above_one(self):
        des = [{"week": 1, "player_id": f"D{i}", "injury_status": "Questionable"}
               for i in range(4)]
        sc = [{"week": 1, "player_id": f"D{i}", "points": 5.0} for i in range(4)]
        sc += [{"week": 2, "player_id": f"D{i}", "points": 0.0 if i < 3 else 5.0}
               for i in range(4)]
        sc += [{"week": w, "player_id": f"H{i}", "points": 5.0}
               for i in range(8) for w in (1, 2)]
        from fantasy_sim.durability import study
        r = study(des, sc, lookback=3)
        self.assertEqual(r["designated"]["dnp"], 3)
        self.assertEqual(r["undesignated"]["dnp"], 0)
        self.assertIsNone(r["lift"], "an undesignated rate of 0 makes the ratio undefined")
        self.assertGreater(r["risk_difference"], 0.7,
                           "the difference stays defined where the ratio does not")

    def test_the_population_is_only_players_seen_in_both_weeks(self):
        """A player who appears in week W+1 but not W was not observable at W."""
        from fantasy_sim.durability import study
        sc = SCORES + [{"week": 3, "player_id": "NEW", "points": 0.0}]
        self.assertEqual(study(DESIGNATIONS, sc, lookback=3)["undesignated"]["n"], 2)

    def test_no_usable_pair_reports_zero_rather_than_inventing_a_number(self):
        from fantasy_sim.durability import study
        r = study([{"week": 9, "player_id": "P1", "injury_status": "Out"}], SCORES)
        self.assertEqual(r["pairs"], [])
        self.assertEqual(r["designated"]["n"], 0)
        self.assertIsNone(r["lift"])

    def test_it_stratifies_by_position_when_positions_are_supplied(self):
        """B10 asks for lift above the POSITIONAL base rate, not the pooled one."""
        from fantasy_sim.durability import study
        r = study(DESIGNATIONS, SCORES, lookback=3, positions=POSITIONS)
        self.assertIn("WR", r["by_position"])
        self.assertEqual(r["by_position"]["WR"]["designated"]["n"], 2)
        self.assertEqual(r["by_position"]["RB"]["undesignated"]["n"], 2)


class TestItRefusesToOverclaim(unittest.TestCase):
    def test_a_wilson_interval_on_a_tiny_sample_spans_most_of_the_unit_range(self):
        """The guard against reading a lift off n=4. If the interval does not blow up on
        a tiny sample the study will happily report a 'signal' that is one coin flip."""
        from fantasy_sim.durability import wilson
        lo, hi = wilson(1, 2)
        self.assertLess(lo, 0.15)
        self.assertGreater(hi, 0.85)

    def test_the_result_carries_the_sample_size_it_was_computed_from(self):
        from fantasy_sim.durability import study
        r = study(DESIGNATIONS, SCORES, lookback=3)
        self.assertEqual(r["n"], 4)
        self.assertIn("ci", r["designated"])

    def test_it_says_outright_when_the_sample_cannot_support_adoption(self):
        from fantasy_sim.durability import study
        r = study(DESIGNATIONS, SCORES, lookback=3)
        self.assertFalse(r["adoptable"])
        self.assertIn("reason", r)


class TestTheDenominatorGap(unittest.TestCase):
    """F63. B10's test is 'above the base rate', which needs the UNDESIGNATED. B21's log
    writes only the designated, so the denominator has to come from somewhere, and where
    it comes from changes what the number means."""

    def test_the_study_records_where_its_denominator_came_from(self):
        from fantasy_sim.durability import study
        r = study(DESIGNATIONS, SCORES, lookback=3)
        self.assertIn("population_source", r)

    def test_a_supplied_roll_call_narrows_the_population_to_it(self):
        """Once B21 records the roll call, the undesignated are exactly the rostered men
        with no designation -- not 'everyone in the stats feed', which silently includes
        players nobody was tracking."""
        from fantasy_sim.durability import study
        roll = {2: {"P1", "P2", "P3"}, 3: {"P1", "P2", "P3"}}
        r = study(DESIGNATIONS, SCORES, lookback=3, roll_call=roll)
        self.assertEqual(r["undesignated"]["n"], 1, "P4 is outside the roll call")
        self.assertEqual(r["population_source"], "roll_call")


if __name__ == "__main__":
    unittest.main()

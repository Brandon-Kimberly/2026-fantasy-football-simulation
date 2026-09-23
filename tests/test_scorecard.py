"""B24: a decision scorecard, with the duplicates collapsed.

Twice in one week the owner asked "did we make bad calls?" and the answer was assembled
by hand. The week-1 version listed EIGHT decisions; the real week-1 lineup record shows
what those actually were:

    FLEX  Ladd McConkey     alt Christian Watson   +4.24
    FLEX  Malik Nabers      alt Christian Watson   +0.73
    FLEX  Breece Hall       alt Christian Watson   +0.71
    WR    DeVonta Smith     alt Christian Watson   +3.94
    WR    Chris Olave       alt Christian Watson   +2.30
    LB    Nick Bolton       alt Fred Warner        +1.73
    RB    Jahmyr Gibbs      alt Tony Pollard      +19.72
    RB    Javonte Williams  alt Tony Pollard       +6.87

Three decisions, not eight. One bench player eligible at five slots is ONE call, and
counting him five times turns a single judgement into a pattern of failure. B24 says the
deduplication is the useful part, and it is.

WHICH STARTER THE ALTERNATIVE IS PAIRED WITH matters and is not obvious. It is the
SMALLEST margin -- Watson against Breece Hall at +0.71, not against McConkey at +4.24.
The smallest margin is the swap that was actually available; pairing him with the biggest
would score a decision nobody was ever close to making.

TWO RULES FROM THE TRAPS, both pinned:

  * score ONLY against the pre-kickoff lineup record. Re-solving the lineup with the
    week's results in hand is the lookahead leakage CLAUDE.md's statistical conventions
    forbid outright.
  * an UNRESOLVED call -- the alternative has no recorded score -- is marked, never
    scored. Counting it as correct is how a scorecard flatters itself.

Written before the module existed and confirmed failing (rule 1).
"""
import unittest

# The real week-1 shape, trimmed.
RECORD = {
    "team": "Quantum Ferrets", "week": 1,
    "lineup": [
        {"slot": "FLEX", "name": "Ladd McConkey", "expected": 14.98,
         "alternative": "Christian Watson", "margin": 4.24},
        {"slot": "FLEX", "name": "Malik Nabers", "expected": 11.47,
         "alternative": "Christian Watson", "margin": 0.73},
        {"slot": "FLEX", "name": "Breece Hall", "expected": 11.45,
         "alternative": "Christian Watson", "margin": 0.71},
        {"slot": "WR", "name": "DeVonta Smith", "expected": 14.68,
         "alternative": "Christian Watson", "margin": 3.94},
        {"slot": "WR", "name": "Chris Olave", "expected": 13.05,
         "alternative": "Christian Watson", "margin": 2.30},
        {"slot": "LB", "name": "Nick Bolton", "expected": 11.66,
         "alternative": "Fred Warner", "margin": 1.73},
        {"slot": "RB", "name": "Jahmyr Gibbs", "expected": 28.83,
         "alternative": "Tony Pollard", "margin": 19.72},
        {"slot": "DB", "name": "Cole Bishop", "expected": 12.37,
         "alternative": None, "margin": 12.37},
    ],
}
ACTUALS = {"Breece Hall": 8.0, "Christian Watson": 15.0,     # Watson beat Hall: WRONG call
           "Nick Bolton": 14.0, "Fred Warner": 6.0,          # Bolton beat Warner: RIGHT
           "Jahmyr Gibbs": 22.0, "Cole Bishop": 9.0}         # Pollard has no score


class TestTheDeduplication(unittest.TestCase):
    """B24: 'the deduplication is the useful part'."""

    def test_eight_rows_become_three_decisions(self):
        from fantasy_sim.scorecard import decisions_from_lineup
        d = decisions_from_lineup(RECORD)
        self.assertEqual(len(d), 3)
        self.assertEqual({x["alternative"] for x in d},
                         {"Christian Watson", "Fred Warner", "Tony Pollard"})

    def test_a_slot_with_no_alternative_is_not_a_decision(self):
        from fantasy_sim.scorecard import decisions_from_lineup
        self.assertNotIn("Cole Bishop",
                         {x["started"] for x in decisions_from_lineup(RECORD)})

    def test_every_slot_the_alternative_touched_is_still_listed(self):
        """Collapsed, not discarded -- the owner should see it was five slots."""
        from fantasy_sim.scorecard import decisions_from_lineup
        w = next(x for x in decisions_from_lineup(RECORD)
                 if x["alternative"] == "Christian Watson")
        self.assertEqual(w["n_slots"], 5)
        self.assertEqual(set(w["slots"]), {"FLEX", "WR"})


class TestThePairing(unittest.TestCase):
    def test_the_alternative_is_paired_with_the_smallest_margin(self):
        """The swap actually available, not the one nobody considered."""
        from fantasy_sim.scorecard import decisions_from_lineup
        w = next(x for x in decisions_from_lineup(RECORD)
                 if x["alternative"] == "Christian Watson")
        self.assertEqual(w["started"], "Breece Hall")
        self.assertAlmostEqual(w["margin"], 0.71)

    def test_a_lone_slot_pairs_with_itself(self):
        from fantasy_sim.scorecard import decisions_from_lineup
        f = next(x for x in decisions_from_lineup(RECORD)
                 if x["alternative"] == "Fred Warner")
        self.assertEqual(f["started"], "Nick Bolton")


class TestScoring(unittest.TestCase):
    def test_a_call_the_alternative_beat_is_wrong(self):
        from fantasy_sim.scorecard import decisions_from_lineup, score_decisions
        s = {x["alternative"]: x for x in
             score_decisions(decisions_from_lineup(RECORD), ACTUALS)}
        w = s["Christian Watson"]
        self.assertEqual(w["outcome"], "wrong")
        self.assertAlmostEqual(w["cost"], 15.0 - 8.0)

    def test_a_call_the_starter_won_is_right(self):
        from fantasy_sim.scorecard import decisions_from_lineup, score_decisions
        s = {x["alternative"]: x for x in
             score_decisions(decisions_from_lineup(RECORD), ACTUALS)}
        self.assertEqual(s["Fred Warner"]["outcome"], "right")
        self.assertAlmostEqual(s["Fred Warner"]["cost"], 0.0)

    def test_an_alternative_with_no_score_is_unresolved_not_correct(self):
        from fantasy_sim.scorecard import decisions_from_lineup, score_decisions
        s = {x["alternative"]: x for x in
             score_decisions(decisions_from_lineup(RECORD), ACTUALS)}
        self.assertEqual(s["Tony Pollard"]["outcome"], "unresolved")
        self.assertIsNone(s["Tony Pollard"]["cost"])

    def test_the_summary_never_counts_unresolved_as_right(self):
        from fantasy_sim.scorecard import decisions_from_lineup, score_decisions, summarise
        got = summarise(score_decisions(decisions_from_lineup(RECORD), ACTUALS))
        self.assertEqual(got["decisions"], 3)
        self.assertEqual(got["right"], 1)
        self.assertEqual(got["wrong"], 1)
        self.assertEqual(got["unresolved"], 1)
        self.assertEqual(got["right"] + got["wrong"] + got["unresolved"], got["decisions"])

    def test_the_hit_rate_is_over_RESOLVED_calls_only(self):
        from fantasy_sim.scorecard import decisions_from_lineup, score_decisions, summarise
        got = summarise(score_decisions(decisions_from_lineup(RECORD), ACTUALS))
        self.assertAlmostEqual(got["hit_rate"], 0.5, msg="1 right of 2 resolved, not of 3")

    def test_wrong_calls_are_ranked_by_what_they_cost(self):
        from fantasy_sim.scorecard import decisions_from_lineup, score_decisions
        rows = score_decisions(decisions_from_lineup(RECORD), ACTUALS)
        wrong = [r for r in rows if r["outcome"] == "wrong"]
        self.assertEqual([r["cost"] for r in wrong],
                         sorted((r["cost"] for r in wrong), reverse=True))


class TestNoLookahead(unittest.TestCase):
    """B24's trap, and CLAUDE.md's statistical conventions: lineups are chosen on
    expected_pre, never on realised scores."""

    def test_the_module_never_re_solves_a_lineup(self):
        import ast
        import inspect
        from fantasy_sim import scorecard
        tree = ast.parse(inspect.getsource(scorecard))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
                node.body = node.body[1:]
        code = ast.unparse(tree)
        for forbidden in ("optimize_lineup", "roster_gaps", "_solve_optimal_assignment"):
            self.assertNotIn(forbidden, code,
                             "scoring must read the PRE-KICKOFF record, never re-solve it")

    def test_expectations_come_from_the_record_not_recomputed(self):
        from fantasy_sim.scorecard import decisions_from_lineup
        w = next(x for x in decisions_from_lineup(RECORD)
                 if x["alternative"] == "Christian Watson")
        self.assertAlmostEqual(w["expected"], 11.45, msg="Breece Hall's recorded value")


if __name__ == "__main__":
    unittest.main()

"""B13: bid sizing ignores competition and the marginal value over the fallback.

The v1 heuristic is a share of budget proportional to VORP. The market comparable printed
beside it (median bid/VORP from the decision log) priced Roquan at ~$22; the owner bid
$15 and NOBODY ELSE BID. The same comparable would have priced Mahomes at ~$35, when the
right bid was $15-18 -- because the fallback (Shough) was already worth +7.63 of Mahomes'
+12.60, and only three rivals carried a single QB, all healthy.

Both errors have the same two causes, and v2 takes both as inputs:

  1. **Marginal value over the FALLBACK, not raw value.** If the man you would claim
     anyway is worth 60% of the man you want, you are bidding for the other 40%.
  2. **Competition.** A player only costs what someone else will pay. Rivals who do not
     need the position, or have no budget, are not bidders.

THE TRAP B13 NAMES, and it shapes what can be claimed. The decision log's clearing prices
are CENSORED: you see the winning bid, never the runner-up. "Nobody else bid" does not
mean the price was $1 -- it means the price was AT MOST my bid. So a suggestion below a
bid I won is not evidence of error, and the scoring below treats those two cases
differently rather than averaging them together.

v1 is NOT removed. B13: keep it printed beside v2, labelled, until B14's ledger has a
season of claims to score them against.

Written before the function existed and confirmed failing (rule 1).
"""
import unittest


class TestTheShape(unittest.TestCase):
    def test_it_returns_a_range_with_its_reasoning(self):
        from fantasy_sim.decisions import suggest_bid_v2
        r = suggest_bid_v2(claim_value=12.6, fallback_value=7.63, rivals_needing=3,
                           rival_faab=[100, 100, 100], my_faab=100)
        for k in ("low", "high", "point", "marginal", "competition_factor", "reasoning"):
            self.assertIn(k, r)
        self.assertLessEqual(r["low"], r["point"])
        self.assertLessEqual(r["point"], r["high"])

    def test_the_reasoning_names_both_causes(self):
        from fantasy_sim.decisions import suggest_bid_v2
        r = suggest_bid_v2(12.6, 7.63, 3, [100, 100, 100], 100)
        text = r["reasoning"].lower()
        self.assertIn("fallback", text)
        self.assertIn("rival", text)


class TestMarginalOverFallback(unittest.TestCase):
    def test_a_strong_fallback_cuts_the_bid(self):
        """B13's Mahomes case: the fallback was worth 7.63 of his 12.60."""
        from fantasy_sim.decisions import suggest_bid_v2
        weak = suggest_bid_v2(12.6, 0.0, 3, [100, 100, 100], 100)
        strong = suggest_bid_v2(12.6, 7.63, 3, [100, 100, 100], 100)
        self.assertLess(strong["point"], weak["point"])

    def test_a_fallback_as_good_as_the_claim_collapses_to_the_minimum(self):
        from fantasy_sim.decisions import suggest_bid_v2
        r = suggest_bid_v2(10.0, 10.0, 3, [100, 100, 100], 100)
        self.assertEqual(r["point"], 1, "nothing is being bought; bid the minimum")

    def test_a_fallback_better_than_the_claim_is_not_a_negative_bid(self):
        from fantasy_sim.decisions import suggest_bid_v2
        self.assertEqual(suggest_bid_v2(5.0, 9.0, 3, [100], 100)["point"], 1)


class TestCompetition(unittest.TestCase):
    def test_no_rival_needs_him_so_the_bid_falls_to_near_the_minimum(self):
        """B13's Roquan case: priced at ~$22, bid $15, nobody else bid at all."""
        from fantasy_sim.decisions import suggest_bid_v2
        alone = suggest_bid_v2(11.9, 4.0, rivals_needing=0, rival_faab=[], my_faab=100)
        contested = suggest_bid_v2(11.9, 4.0, rivals_needing=5,
                                   rival_faab=[100] * 5, my_faab=100)
        self.assertLess(alone["point"], contested["point"])
        self.assertLessEqual(alone["point"], 5,
                             "an uncontested claim should not cost a sixth of the budget")

    def test_broke_rivals_are_not_bidders(self):
        from fantasy_sim.decisions import suggest_bid_v2
        rich = suggest_bid_v2(11.9, 4.0, 4, [100, 100, 100, 100], 100)
        broke = suggest_bid_v2(11.9, 4.0, 4, [2, 1, 0, 3], 100)
        self.assertLess(broke["point"], rich["point"],
                        "a rival who cannot pay is not competition")

    def test_aggression_raises_the_range(self):
        from fantasy_sim.decisions import suggest_bid_v2
        mild = suggest_bid_v2(11.9, 4.0, 3, [100] * 3, 100, rival_aggression=[0.2] * 3)
        wild = suggest_bid_v2(11.9, 4.0, 3, [100] * 3, 100, rival_aggression=[2.0] * 3)
        self.assertLess(mild["point"], wild["point"])


class TestBounds(unittest.TestCase):
    def test_it_never_exceeds_my_budget(self):
        from fantasy_sim.decisions import suggest_bid_v2
        r = suggest_bid_v2(30.0, 0.0, 7, [100] * 7, my_faab=9)
        self.assertLessEqual(r["high"], 9)

    def test_it_never_goes_below_the_league_minimum(self):
        from fantasy_sim.decisions import suggest_bid_v2
        self.assertGreaterEqual(suggest_bid_v2(0.1, 0.0, 0, [], 100)["low"], 1)

    def test_a_broke_team_can_still_bid_the_minimum(self):
        from fantasy_sim.decisions import suggest_bid_v2
        r = suggest_bid_v2(12.0, 0.0, 3, [100] * 3, my_faab=1)
        self.assertEqual(r["point"], 1)


class TestV1IsKept(unittest.TestCase):
    """B13: keep the old heuristic printed beside v2, labelled, until B14's ledger can
    score them against a season of claims."""

    def test_suggest_bid_still_exists_unchanged(self):
        from fantasy_sim.decisions import suggest_bid
        self.assertEqual(suggest_bid(vorp=5.0, fills="hole", remaining_faab=100,
                                     league_avg_faab=100), 20)


class TestCensoringIsRespected(unittest.TestCase):
    """B13's trap: a winning bid is an upper bound on the price, not the price."""

    def test_a_suggestion_below_a_bid_i_won_is_not_scored_as_an_error(self):
        from fantasy_sim.decisions import score_bid_suggestion
        s = score_bid_suggestion(suggested=8, winning_bid=15, i_won=True)
        self.assertFalse(s["error"])
        self.assertIn("at most", s["note"].lower())

    def test_a_suggestion_above_a_bid_i_won_is_an_overpay(self):
        from fantasy_sim.decisions import score_bid_suggestion
        s = score_bid_suggestion(suggested=25, winning_bid=15, i_won=True)
        self.assertTrue(s["error"])
        self.assertAlmostEqual(s["miss"], 10)

    def test_losing_to_a_higher_bid_scores_the_shortfall(self):
        from fantasy_sim.decisions import score_bid_suggestion
        s = score_bid_suggestion(suggested=5, winning_bid=20, i_won=False)
        self.assertTrue(s["error"])
        self.assertAlmostEqual(s["miss"], 15)

    def test_outbidding_someone_elses_win_is_not_an_error(self):
        """If a rival won at 20 and I would have bid 25, I would have got him."""
        from fantasy_sim.decisions import score_bid_suggestion
        self.assertFalse(score_bid_suggestion(25, 20, i_won=False)["error"])


if __name__ == "__main__":
    unittest.main()

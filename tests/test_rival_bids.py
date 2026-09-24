"""T2: the ledger cannot record the losing bids, which this league can see.

After a waiver run the owner can read every bid on a claim -- 29 / 21 / 20 on one QB in
week 3. The ledger keeps only `winning_bid_if_visible`, which **for a claim I won is an
UPPER BOUND on the price, never the price**, and is scored through a censoring rule for
exactly that reason (B13, `decisions.score_bid_suggestion`).

The losing bids destroy the censoring. With rivals at 21 and 20, the **exact clearing
price is 22** -- one more than the best rival -- and "would this suggestion have won?" is
answerable for every suggestion, whether I won or lost. That turns a bound into a
measurement, and F61 is the finding that says measurement is the only way to settle whether
any bid heuristic here works at all (correlation(VORP, winning bid) = -0.136 on 26 claims).

**THE FIRST-PRICE TRAP, and why both numbers are kept.** Sleeper runs a first-price
auction: the winner pays their own bid, not the second price. So `winning_bid_if_visible`
answers *"what did it cost me"* and the clearing price answers *"what would have won"*, and
they are different questions. A test pins that recording rivals never overwrites what was
paid.

**APPEND-ONLY, THROUGH F64's SUPERSESSION.** Nothing is mutated. Recording rivals appends
an amended copy of the live row, which `live_rows` then picks up as the latest for that
`(player_id, week)`. That means the original row becomes "superseded" -- and it must NOT be
reported as a raised bid, which is what the superseded block currently says all of them
are. The amendment carries `amends: "rival_bids"` so the two can be told apart.

Written before the change: **13 of 15 red** (12 on the missing `clearing_price` /
`record_rival_bids` / `n_exact`, one on the scoring itself). The two green are F64
supersession and the unresolved-claim rule, which must survive unchanged. Note the third
"guard" below is red too -- it asserts an `n_exact` field that does not exist yet -- which
is said here rather than counted as a passing guard.
"""
import json
import os
import tempfile
import unittest


def _row(**kw):
    base = {"season": "2026", "week": 3, "player": "Quarterback One", "player_id": "111",
            "pos": "QB", "bid_placed": 29, "suggested_v1": 8, "suggested_v2_point": 15,
            "suggested_v2_low": 12, "suggested_v2_high": 18, "vorp_at_bid": 3.0,
            "rivals_needing": 2, "remaining_faab": 100.0,
            "placed_at": "2026-09-23T10:00:00Z", "won": None,
            "winning_bid_if_visible": None}
    base.update(kw)
    return base


class TestTheClearingPrice(unittest.TestCase):
    def test_it_is_one_more_than_the_best_rival(self):
        from fantasy_sim.bid_ledger import clearing_price
        self.assertEqual(clearing_price(_row(rival_bids=[21, 20])), 22)

    def test_a_row_with_no_rivals_recorded_has_none(self):
        """Absence is unknown, not zero -- the rule this ledger already follows for an
        unresolved outcome. A clearing price of 0 would score every suggestion as a win."""
        from fantasy_sim.bid_ledger import clearing_price
        self.assertIsNone(clearing_price(_row()))
        self.assertIsNone(clearing_price(_row(rival_bids=[])))

    def test_junk_in_the_field_is_ignored_rather_than_crashing_the_review(self):
        from fantasy_sim.bid_ledger import clearing_price
        self.assertIsNone(clearing_price(_row(rival_bids=["", None])))


class TestRecordingRivals(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "bid_ledger.jsonl")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(_row(), sort_keys=True) + "\n")

    def _rows(self):
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def test_it_appends_an_amended_copy_and_mutates_nothing(self):
        from fantasy_sim.bid_ledger import record_rival_bids
        self.assertEqual(record_rival_bids("111", 3, [21, 20], path=self.path), 1)
        rows = self._rows()
        self.assertEqual(len(rows), 2, "append-only: the original row survives verbatim")
        self.assertEqual(rows[0], _row())
        self.assertEqual(rows[1]["rival_bids"], [21, 20])
        self.assertEqual(rows[1]["amends"], "rival_bids")

    def test_the_amendment_carries_the_original_terms_forward(self):
        """It supersedes the original under F64, so anything it drops is lost from the
        live view -- the bid placed and both suggestions have to come with it."""
        from fantasy_sim.bid_ledger import live_rows, record_rival_bids
        record_rival_bids("111", 3, [21, 20], path=self.path)
        live = live_rows(self._rows())
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["bid_placed"], 29)
        self.assertEqual(live[0]["suggested_v1"], 8)
        self.assertEqual(live[0]["suggested_v2_point"], 15)
        self.assertEqual(live[0]["rival_bids"], [21, 20])

    def test_it_never_overwrites_what_was_paid(self):
        """First-price auction: the winner pays their own bid. `winning_bid_if_visible` is
        "what did it cost me" and the clearing price is "what would have won"; overwriting
        the first with the second would destroy the only record of the actual cost."""
        from fantasy_sim.bid_ledger import live_rows, record_rival_bids
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(_row(won=True, winning_bid_if_visible=29), sort_keys=True) + "\n")
        record_rival_bids("111", 3, [21, 20], path=self.path)
        live = live_rows(self._rows())[0]
        self.assertEqual(live["winning_bid_if_visible"], 29)

    def test_bids_are_stored_highest_first_so_the_best_rival_is_obvious(self):
        from fantasy_sim.bid_ledger import record_rival_bids
        record_rival_bids("111", 3, [20, 21, 5], path=self.path)
        self.assertEqual(self._rows()[1]["rival_bids"], [21, 20, 5])

    def test_an_unknown_claim_is_refused_rather_than_inventing_a_row(self):
        """A row written here with no bid and no suggestions would enter the calibration
        as a claim that was never placed."""
        from fantasy_sim.bid_ledger import record_rival_bids
        with self.assertRaises(ValueError):
            record_rival_bids("999", 3, [21], path=self.path)


class TestCalibrationUsesTheExactPrice(unittest.TestCase):
    def test_a_row_with_rivals_is_scored_against_22_not_29(self):
        """The backlog's acceptance criterion. Won at 29, rivals 21 and 20: a suggestion of
        15 is short of the 22 that would have won, by 7 -- not excused by the censoring
        rule just because I happened to win."""
        from fantasy_sim.bid_ledger import calibration
        rows = [_row(won=True, winning_bid_if_visible=29, rival_bids=[21, 20],
                     suggested_v1=15, suggested_v2_point=25)]
        cal = calibration(rows)
        self.assertEqual(cal["n"], 1)
        self.assertEqual(cal["n_exact"], 1)
        self.assertEqual(cal["v1"]["errors"], 1)
        self.assertAlmostEqual(cal["v1"]["total_miss"], 7.0, msg="22 - 15")
        self.assertEqual(cal["v2"]["errors"], 0, "25 clears 22, so it would have won")

    def test_without_rivals_the_censored_rule_still_applies(self):
        """Won at 29, no rivals recorded: 15 is NOT shown to be wrong, because the true
        price is only known to be at most 29."""
        from fantasy_sim.bid_ledger import calibration
        cal = calibration([_row(won=True, winning_bid_if_visible=29, suggested_v1=15,
                                suggested_v2_point=25)])
        self.assertEqual(cal["n_exact"], 0)
        self.assertEqual(cal["v1"]["errors"], 0)

    def test_the_two_kinds_are_counted_separately_in_the_summary(self):
        from fantasy_sim.bid_ledger import calibration
        cal = calibration([
            _row(won=True, winning_bid_if_visible=29, rival_bids=[21, 20], suggested_v1=15),
            _row(player_id="222", won=True, winning_bid_if_visible=12, suggested_v1=15),
        ])
        self.assertEqual((cal["n"], cal["n_exact"]), (2, 1))
        self.assertIn("exact", cal["note"])

    def test_a_lost_claim_with_rivals_is_scored_the_same_way(self):
        """The clearing price does not care who won -- that is the whole point of having
        it. Lost, the winner bid 21: 22 would have won, 15 would not."""
        from fantasy_sim.bid_ledger import calibration
        cal = calibration([_row(won=False, winning_bid_if_visible=21, rival_bids=[21, 20],
                                suggested_v1=15, suggested_v2_point=22)])
        self.assertEqual(cal["v1"]["errors"], 1)
        self.assertAlmostEqual(cal["v1"]["total_miss"], 7.0)
        self.assertEqual(cal["v2"]["errors"], 0)


class TestGuardsThatMustNotRegress(unittest.TestCase):
    """GREEN BY DESIGN. Properties the T2 change could plausibly break."""

    def test_an_unresolved_claim_is_still_not_evidence(self):
        from fantasy_sim.bid_ledger import calibration
        cal = calibration([_row(rival_bids=[21, 20])])
        self.assertEqual((cal["n"], cal["unresolved"]), (0, 1))

    def test_a_ledger_with_no_rival_bids_anywhere_reports_zero_exact(self):
        from fantasy_sim.bid_ledger import calibration
        self.assertEqual(calibration([_row(won=True, winning_bid_if_visible=12)])["n_exact"], 0)

    def test_a_raise_and_an_amendment_on_ONE_claim_are_labelled_separately(self):
        """Caught on the live ledger. The real Mahomes claim was $25 raised to $29 and then
        amended with the rivals, so it has TWO superseded rows for two different reasons. A
        per-claim label reported the raise as an amendment; the reason is read per ROW from
        its own successor."""
        from fantasy_sim.bid_ledger import supersession_reasons
        raised = _row(bid_placed=25, placed_at="2026-09-23T10:00:00Z")
        live_bid = _row(bid_placed=29, placed_at="2026-09-23T11:00:00Z")
        amendment = _row(bid_placed=29, placed_at="2026-09-24T09:00:00Z",
                         rival_bids=[21, 20], amends="rival_bids")
        why = supersession_reasons([raised, live_bid, amendment])
        self.assertEqual(why[id(raised)], "bid raised before the waiver run")
        self.assertEqual(why[id(live_bid)], "rival-bid amendment")
        self.assertNotIn(id(amendment), why, "the live row is not superseded")

    def test_F64_supersession_still_collapses_a_raised_bid_to_one_claim(self):
        from fantasy_sim.bid_ledger import live_rows
        rows = [_row(bid_placed=25, placed_at="2026-09-23T10:00:00Z"),
                _row(bid_placed=29, placed_at="2026-09-23T11:00:00Z")]
        self.assertEqual([r["bid_placed"] for r in live_rows(rows)], [29])


if __name__ == "__main__":
    unittest.main()

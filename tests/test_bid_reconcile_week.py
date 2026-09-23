"""F65: the ledger could never resolve a claim, because Sleeper counts the week differently.

Found the morning after the first real claims. Two waivers were WON -- a QB at $29 and a
K at $2, both confirmed on the roster -- and `scripts.bid_review` still printed
"resolved 0". Nothing errored. The dataset simply stayed empty.

    ledger row      week 3     (league_state.current_week when the bid was placed)
    Sleeper txn     week 2     (the `leg` when the claim was SUBMITTED)

`reconcile` matched on `(player_id, week)`, so every claim placed after a week rolled
over but submitted before it missed by one and resolved to nothing. Daily waivers
(`daily_waivers: 1`) make that the NORMAL case here, not an edge: a claim sits from
submission until the next 09:00 run, and the league's week advances in between.

WHY THIS IS THE WORST PLACE FOR A SILENT FAILURE. `AUDIT_PLAN.md` records B14's ledger as
"the only route to settling" B13 after F61 measured correlation(VORP, winning bid) at
-0.136. A ledger that never resolves anything reports `no resolved claims yet` forever --
which is indistinguishable from "the waiver run has not happened", the honest empty state
this module was carefully built to show. It would have looked correct indefinitely.

WHY NOT JUST WIDEN THE WEEK TO +/-1. Because it breaks F64's rule that the same player in
a different week is a different claim: two claims a week apart would cross-match. TIME
separates them and the week cannot.

WHY NOT MATCH ON TIME ALONE, NAIVELY. A transaction's `created` is its SUBMISSION, and a
bid RAISED later keeps the original submission time -- the real Mahomes row was created
2026-09-22T19:25Z and the $29 was placed 2026-09-23T13:0xZ. So the transaction legitimately
PRE-dates the ledger row it belongs to, and any "must be after the bid" rule would reject
the very claim it was written for. Proximity, not ordering.

Written before the fix and confirmed failing (rule 1).
"""
import unittest


def _ledger(pid="4046", week=3, placed_at="2026-09-23T13:02:00Z", bid=29):
    return {"player_id": pid, "week": week, "placed_at": placed_at, "bid_placed": bid,
            "player": "Claimed Guy", "suggested_v1": 12, "suggested_v2_point": 4}


def _txn(pid="4046", week=2, created="2026-09-22T19:25:27Z", bid=29, mine=True):
    return {"type": "waiver", "week": week, "faab_bid": bid, "is_mine": mine,
            "created": created, "adds": [{"name": "Claimed Guy", "player_id": pid}]}


class TestTheWeekOffsetNoLongerLosesTheClaim(unittest.TestCase):
    """The exact shapes that failed on 2026-09-23."""

    def test_a_claim_submitted_before_the_week_rolled_over_still_resolves(self):
        from fantasy_sim.bid_ledger import reconcile
        got = reconcile([_ledger()], [_txn()])[0]
        self.assertIs(got["won"], True)
        self.assertEqual(got["winning_bid_if_visible"], 29)

    def test_a_loss_resolves_too_and_carries_the_rivals_price(self):
        from fantasy_sim.bid_ledger import reconcile
        got = reconcile([_ledger(pid="12545", bid=3)],
                        [_txn(pid="12545", bid=5, mine=False)])[0]
        self.assertIs(got["won"], False)
        self.assertEqual(got["winning_bid_if_visible"], 5)

    def test_an_exact_week_match_still_works(self):
        from fantasy_sim.bid_ledger import reconcile
        got = reconcile([_ledger()], [_txn(week=3)])[0]
        self.assertIs(got["won"], True)


class TestItDoesNotOvermatch(unittest.TestCase):
    """F64's rule survives: the same player in a different week is a different claim."""

    def test_a_claim_a_week_later_does_not_steal_the_earlier_outcome(self):
        from fantasy_sim.bid_ledger import reconcile
        rows = [_ledger(week=3, placed_at="2026-09-23T13:00:00Z", bid=29),
                _ledger(week=4, placed_at="2026-09-30T13:00:00Z", bid=7)]
        txns = [_txn(week=2, created="2026-09-22T19:25:00Z", bid=29, mine=True),
                _txn(week=3, created="2026-09-29T19:25:00Z", bid=11, mine=False)]
        got = reconcile(rows, txns)
        self.assertEqual([r["winning_bid_if_visible"] for r in got], [29, 11])
        self.assertEqual([r["won"] for r in got], [True, False])

    def test_a_transaction_far_from_any_bid_resolves_nothing(self):
        """A claim on the same player two months earlier is not this claim."""
        from fantasy_sim.bid_ledger import reconcile
        got = reconcile([_ledger()], [_txn(created="2026-07-20T19:25:00Z")])[0]
        self.assertIsNone(got["won"])

    def test_absence_is_still_unresolved_not_a_loss(self):
        from fantasy_sim.bid_ledger import reconcile
        got = reconcile([_ledger()], [])[0]
        self.assertIsNone(got["won"])
        self.assertIsNone(got["winning_bid_if_visible"])

    def test_matching_is_still_by_player_id_not_name(self):
        """B17: 220 colliding names, seven involving a rostered player."""
        from fantasy_sim.bid_ledger import reconcile
        got = reconcile([_ledger(pid="7525")], [_txn(pid="13977")])[0]
        self.assertIsNone(got["won"], "the other man's claim must not resolve this bid")


class TestATransactionMayPredateTheBidItBelongsTo(unittest.TestCase):
    """The revised-bid case, and the reason an 'after the bid' rule would be wrong.

    Sleeper's `created` is the SUBMISSION time and survives an edit, so raising $25 to $29
    leaves the transaction stamped before the ledger row carrying the live price.
    """

    def test_a_transaction_created_before_the_row_still_matches(self):
        from fantasy_sim.bid_ledger import reconcile
        row = _ledger(placed_at="2026-09-23T13:02:00Z")
        got = reconcile([row], [_txn(created="2026-09-22T19:25:27Z")])[0]
        self.assertIs(got["won"], True)

    def test_a_row_with_no_timestamp_falls_back_to_the_week(self):
        """Older rows predate `placed_at`. They must not silently stop resolving."""
        from fantasy_sim.bid_ledger import reconcile
        row = _ledger(placed_at=None, week=2)
        got = reconcile([row], [_txn(week=2)])[0]
        self.assertIs(got["won"], True)


class TestTheDiscrepancyIsSurfacedNotHidden(unittest.TestCase):
    """The K was recorded at $1 and cleared at $2. The ledger records INTENT; the
    transaction records what Sleeper charged. When they disagree the calibration is being
    scored against a bid that was not placed, so say so rather than absorb it."""

    def test_a_price_that_differs_from_the_recorded_bid_is_flagged(self):
        from fantasy_sim.bid_ledger import reconcile
        got = reconcile([_ledger(bid=1)], [_txn(bid=2, mine=True)])[0]
        self.assertEqual(got["bid_mismatch"], 2)

    def test_no_flag_when_they_agree(self):
        from fantasy_sim.bid_ledger import reconcile
        got = reconcile([_ledger(bid=29)], [_txn(bid=29, mine=True)])[0]
        self.assertIsNone(got.get("bid_mismatch"))

    def test_a_loss_never_flags_a_mismatch(self):
        """A rival's winning price is not my bid and is not supposed to equal it."""
        from fantasy_sim.bid_ledger import reconcile
        got = reconcile([_ledger(bid=3)], [_txn(bid=5, mine=False)])[0]
        self.assertIsNone(got.get("bid_mismatch"))


if __name__ == "__main__":
    unittest.main()

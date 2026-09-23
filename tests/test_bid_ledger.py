"""B14: a claim-outcome ledger, so bid heuristics can be scored instead of argued about.

Every week-3 claim was priced by intuition after the Roquan overpay, and there is no
record of *suggested vs placed vs winning vs outcome*. Without it F61 cannot be settled
and the next overpay gets argued exactly the same way.

WHY A SEPARATE LEDGER AND NOT THE DECISION LOG. Measured before building this: all 26
waiver rows in `decision_log.jsonl` are COMPLETED transactions, because a lost waiver
claim never becomes a transaction at all. The decision log is therefore a record of WINS
ONLY, and can never tell you about the claims you lost -- which is precisely the half
that calibrates a bid. So this ledger is written at BID time, before the outcome exists,
and reconciled against the decision log afterwards.

WHAT IT MUST PRESERVE. F61's censoring: a winning bid is an upper bound on the price,
never the price. Reconciliation records `winning_bid_if_visible` and leaves the runner-up
unknown, and calibration scores through `score_bid_suggestion` rather than averaging an
absolute distance that means four different things.

Written before the module existed and confirmed failing (rule 1).
"""
import json
import os
import tempfile
import unittest


def _row(**kw):
    base = {"season": "2026", "week": 3, "player": "Patrick Mahomes", "player_id": "4046",
            "pos": "QB", "bid_placed": 25, "suggested_v1": 12,
            "suggested_v2_point": 3, "suggested_v2_low": 1, "suggested_v2_high": 5,
            "vorp_at_bid": 4.5, "fallback_vorp": 2.2, "rivals_needing": 0}
    base.update(kw)
    return base


class TestTheWriter(unittest.TestCase):
    def test_it_writes_one_row_per_claim(self):
        from fantasy_sim.bid_ledger import record_bid
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bid_ledger.jsonl")
            self.assertEqual(record_bid(_row(), path=p), 1)
            with open(p, encoding="utf-8") as fh:
                rows = [json.loads(x) for x in fh if x.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["player"], "Patrick Mahomes")
        self.assertIsNone(rows[0]["won"], "the outcome does not exist yet at bid time")

    def test_it_is_append_only(self):
        from fantasy_sim.bid_ledger import record_bid
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bid_ledger.jsonl")
            record_bid(_row(), path=p)
            record_bid(_row(player="Tyler Shough", player_id="11560", bid_placed=3), path=p)
            with open(p, encoding="utf-8") as fh:
                rows = [json.loads(x) for x in fh if x.strip()]
        self.assertEqual([r["player"] for r in rows], ["Patrick Mahomes", "Tyler Shough"])

    def test_it_stamps_when_the_bid_was_placed(self):
        from fantasy_sim.bid_ledger import record_bid
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bid_ledger.jsonl")
            record_bid(_row(), path=p)
            with open(p, encoding="utf-8") as fh:
                r = json.loads(fh.readline())
        self.assertTrue(r["placed_at"].endswith("Z"))

    def test_a_write_failure_never_breaks_the_caller(self):
        """Same contract as append_projection_log: a ledger is a record, not a dependency."""
        from fantasy_sim.bid_ledger import record_bid
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "not-a-dir.txt")
            open(bad, "w").close()
            self.assertEqual(record_bid(_row(), path=os.path.join(bad, "x.jsonl")), 0)


class TestReconciliation(unittest.TestCase):
    """The decision log holds WINS ONLY, so absence is ambiguous and must not be read as
    a loss until the waiver run has actually happened."""

    LEDGER = [_row(player="Won Guy", player_id="1", bid_placed=25),
              _row(player="Lost Guy", player_id="2", bid_placed=5),
              _row(player="Pending Guy", player_id="3", bid_placed=9)]
    DECISION = [
        {"type": "waiver", "week": 3, "is_mine": True, "faab_bid": 25,
         "adds": [{"name": "Won Guy", "player_id": "1"}]},
        {"type": "waiver", "week": 3, "is_mine": False, "faab_bid": 30,
         "adds": [{"name": "Lost Guy", "player_id": "2"}]},
    ]

    def test_a_claim_i_won_is_marked_won_with_my_bid_as_the_price(self):
        from fantasy_sim.bid_ledger import reconcile
        got = {r["player"]: r for r in reconcile(self.LEDGER, self.DECISION)}
        self.assertTrue(got["Won Guy"]["won"])
        self.assertEqual(got["Won Guy"]["winning_bid_if_visible"], 25)

    def test_a_claim_a_rival_won_is_marked_lost_with_their_bid(self):
        from fantasy_sim.bid_ledger import reconcile
        got = {r["player"]: r for r in reconcile(self.LEDGER, self.DECISION)}
        self.assertFalse(got["Lost Guy"]["won"])
        self.assertEqual(got["Lost Guy"]["winning_bid_if_visible"], 30)

    def test_a_claim_nobody_won_stays_unresolved(self):
        """Absence means the run has not happened, or the player went unclaimed. It does
        NOT mean I lost -- reading it that way would invent losses."""
        from fantasy_sim.bid_ledger import reconcile
        got = {r["player"]: r for r in reconcile(self.LEDGER, self.DECISION)}
        self.assertIsNone(got["Pending Guy"]["won"])
        self.assertIsNone(got["Pending Guy"]["winning_bid_if_visible"])

    def test_matching_is_by_player_id_not_name(self):
        """B17: 220 colliding names in the cache, seven involving a rostered player."""
        from fantasy_sim.bid_ledger import reconcile
        ledger = [_row(player="DeVonta Smith", player_id="7525", bid_placed=4)]
        decision = [{"type": "waiver", "week": 3, "is_mine": False, "faab_bid": 12,
                     "adds": [{"name": "DeVonta Smith", "player_id": "13977"}]}]
        self.assertIsNone(reconcile(ledger, decision)[0]["won"],
                          "the CB's claim must not resolve the WR's bid")


class TestCalibration(unittest.TestCase):
    def test_it_scores_both_heuristics_through_the_censoring_rule(self):
        from fantasy_sim.bid_ledger import calibration
        rows = [dict(_row(suggested_v1=30, suggested_v2_point=20), won=True,
                     winning_bid_if_visible=25),
                dict(_row(suggested_v1=5, suggested_v2_point=22), won=False,
                     winning_bid_if_visible=20)]
        c = calibration(rows)
        self.assertEqual(c["n"], 2)
        # v1: overpaid by 5 on the win, short by 15 on the loss = 2 errors, miss 20
        self.assertEqual(c["v1"]["errors"], 2)
        self.assertAlmostEqual(c["v1"]["total_miss"], 20.0)
        # v2: under a bid I won (no error) and above a rival's win (no error) = 0 errors
        self.assertEqual(c["v2"]["errors"], 0)

    def test_unresolved_rows_are_excluded_not_counted_as_correct(self):
        from fantasy_sim.bid_ledger import calibration
        c = calibration([dict(_row(), won=None, winning_bid_if_visible=None)])
        self.assertEqual(c["n"], 0)
        self.assertIn("unresolved", c["note"].lower())

    def test_it_refuses_to_declare_a_winner_on_too_few_claims(self):
        """F61 measured r = -0.136 at n = 26. Announcing a victor at n = 2 would be the
        same noise-fitting that finding warns against."""
        from fantasy_sim.bid_ledger import calibration
        rows = [dict(_row(suggested_v1=30, suggested_v2_point=20), won=True,
                     winning_bid_if_visible=25)]
        self.assertIn("too few", calibration(rows)["verdict"].lower())


if __name__ == "__main__":
    unittest.main()

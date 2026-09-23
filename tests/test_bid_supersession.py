"""F64: a bid you RAISE before the waiver run is one claim, and the ledger counted two.

Found by using it. The owner placed $25 on a QB, reconsidered on fresh evidence, and
raised to $29 before the daily run. `record_bid` appends, `reconcile` matches on
(player_id, week), and `calibration` scores every row -- so the same claim would be filled
with the same outcome twice and scored twice, once against a bid that was never live.

That is not a cosmetic double count. This ledger exists for exactly one purpose: B13's
acceptance is unmeetable (F61 measured correlation(VORP, winning bid) = -0.136), and
`docs/AUDIT_PLAN.md` records B14's ledger as "the only route to settling it". A dataset
that counts a revised bid twice, at two different prices, for one outcome, cannot settle
anything -- and it biases toward whichever price the owner happened to type first.

THE FIX IS SUPERSESSION, NOT MUTATION. The earlier row is kept: it is true that the bid
was $25 at that hour, and a later question ("how often does he revise, and which
direction?") needs it. Only the LATEST row per (player_id, week) is live; the rest are
marked superseded and excluded from scoring. An append-only log stays append-only.

WHICH ROW IS LATEST is `placed_at`, not file order. Rows arrive in file order today, but
an append-only log read by timestamp is robust to a backfill, and the same mistake in
`decision_scorecard` -- sorting by path instead of by time -- is already an F-numbered
finding from this session.

Written before supersession existed and confirmed failing (rule 1).
"""
import unittest


def _row(pid, week, bid, placed_at, **kw):
    base = {"player_id": pid, "week": week, "bid_placed": bid, "placed_at": placed_at,
            "player": f"P{pid}", "suggested_v1": 10, "suggested_v2_point": 4,
            "won": None, "winning_bid_if_visible": None}
    base.update(kw)
    return base


# One claim, revised. Plus an unrelated claim that must not be touched.
REVISED = [
    _row("4046", 3, 25, "2026-09-23T10:20:39Z"),
    _row("4046", 3, 29, "2026-09-23T13:02:00Z"),
    _row("12545", 3, 3, "2026-09-23T10:20:40Z"),
]


class TestOnlyTheLatestBidIsLive(unittest.TestCase):
    def test_a_revised_claim_collapses_to_one_row(self):
        from fantasy_sim.bid_ledger import live_rows
        got = live_rows(REVISED)
        self.assertEqual(len(got), 2)
        self.assertEqual({r["player_id"] for r in got}, {"4046", "12545"})

    def test_the_surviving_row_carries_the_raised_bid(self):
        from fantasy_sim.bid_ledger import live_rows
        got = {r["player_id"]: r for r in live_rows(REVISED)}
        self.assertEqual(got["4046"]["bid_placed"], 29)

    def test_latest_is_by_timestamp_not_file_order(self):
        """The same mistake decision_scorecard made with paths. A backfilled row appended
        later is not a later BID."""
        from fantasy_sim.bid_ledger import live_rows
        out_of_order = [REVISED[1], REVISED[0]]          # $29 written first
        got = {r["player_id"]: r for r in live_rows(out_of_order)}
        self.assertEqual(got["4046"]["bid_placed"], 29)

    def test_a_row_with_no_timestamp_never_supersedes_one_that_has_it(self):
        from fantasy_sim.bid_ledger import live_rows
        rows = [_row("4046", 3, 25, "2026-09-23T10:20:39Z"), _row("4046", 3, 99, None)]
        got = {r["player_id"]: r for r in live_rows(rows)}
        self.assertEqual(got["4046"]["bid_placed"], 25)

    def test_the_same_player_in_a_different_week_is_a_different_claim(self):
        from fantasy_sim.bid_ledger import live_rows
        rows = REVISED + [_row("4046", 4, 7, "2026-09-30T10:00:00Z")]
        self.assertEqual(len(live_rows(rows)), 3)

    def test_the_superseded_row_is_kept_not_deleted(self):
        """It is true that the bid was $25 at that hour, and 'how often is a bid revised,
        and which way' is a question this ledger should still be able to answer."""
        from fantasy_sim.bid_ledger import superseded_rows
        got = superseded_rows(REVISED)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["bid_placed"], 25)


class TestCalibrationScoresTheLiveBidOnce(unittest.TestCase):
    def test_a_revised_claim_is_one_resolved_claim_not_two(self):
        from fantasy_sim.bid_ledger import calibration
        rows = [dict(r, won=True, winning_bid_if_visible=29) for r in REVISED]
        self.assertEqual(calibration(rows)["n"], 2,
                         "two distinct claims, not three rows")

    def test_the_price_that_was_never_live_is_not_scored(self):
        from fantasy_sim.bid_ledger import live_rows
        rows = [dict(r, won=True, winning_bid_if_visible=29) for r in REVISED]
        live = {r["player_id"]: r["bid_placed"] for r in live_rows(rows)}
        self.assertNotIn(25, live.values())


if __name__ == "__main__":
    unittest.main()

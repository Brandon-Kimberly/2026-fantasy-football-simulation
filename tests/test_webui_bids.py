"""
tests.test_webui_bids -- what it took to win a claim (docs/WEB_UI_ROADMAP.md UI-W4, on
Decision 4's failed-claims log).

The fixture's decision log has one waiver claim: the owner won Nick Bolton (7648) with a bid of 7
in week 2. The planted failed-claims log adds:
  f1  a third team lost Bolton at 4       (outbid, beaten by t1)
  f2  a fourth team lost Bolton at 6      (outbid, beaten by t1) -- the next best bid
  f3  a second team lost player 1234 at 12 to a fifth team's 20, a claim the decision log
      does not carry                       (outbid, beaten by "tx9")
  f4  a third team's claim failed on a full roster -- not a bid anyone beat, left out
Worked by hand:
  Bolton     paid 7, next best 6, paid 1 above it, two losing bids
  1234       paid 20, next best 12, paid 8 above it
  per team   the owner: one contested win, 1 above; the fifth team: one, 8 above
  median     paid above the next bid: (1 + 8) / 2 = 4.5
  the bands  the owner's bid-ledger row for Bolton suggested 3-5; winning took 7 (6 + 1), so
             the whole suggested range was below the price
The last waiver run's table gains the next best bid beside each winning one.
"""
import json
import os
import tempfile
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import plant
    from tests.test_webui_routes import TEAMS
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def failed(txid, team, pid, name, bid, reason, won_by, processed="2026-09-16T19:00:00Z", week=2):
    return {"transaction_id": txid, "type": "waiver", "week": week, "created": "2026-09-15T12:00:00Z", "processed": processed,
            "team": team, "is_mine": team == MY_TEAM, "player_id": pid, "name": name, "faab_bid": bid, "reason": reason,
            "note": "", "won_by": won_by, "drops": []}


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)
        t1 = {"transaction_id": "t1", "team": MY_TEAM, "faab_bid": 7}
        rows = [failed("f1", TEAMS[2], "7648", "Nick Bolton", 4, "outbid", t1),
                failed("f2", TEAMS[3], "7648", "Nick Bolton", 6, "outbid", t1),
                failed("f3", TEAMS[1], "1234", "Somebody Else", 12, "outbid", {"transaction_id": "tx9", "team": TEAMS[4], "faab_bid": 20},
                       processed="2026-09-23T19:00:00Z", week=3),
                failed("f4", TEAMS[2], "5555", "Roster Squeeze", 10, "roster_full", None)]
        self.write("logs/failed_claims.jsonl", rows)
        self.write("logs/bid_ledger.jsonl", [{"player": "Nick Bolton", "player_id": "7648", "week": 2, "bid_placed": 7,
                                              "placed_at": "2026-09-15T10:00:00Z", "suggested_v2_low": 3, "suggested_v2_high": 5,
                                              "suggested_v2_point": 4, "suggested_v1": 6}])

    def tearDown(self):
        self.td.cleanup()

    def write(self, rel, rows):
        p = os.path.join(self.td.name, "data", *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows) + "\n")

    def get(self, path, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)


class TestClearingPrices(Case):
    def setUp(self):
        super().setUp()
        from webui.players_page import clearing_prices
        self.c = clearing_prices(self.root, MY_TEAM)

    def test_each_contested_claim(self):
        by = {r["player"]: r for r in self.c["claims"]}
        b = by["Nick Bolton"]
        self.assertEqual((b["winner"], b["paid"], b["next_bid"], b["next_team"], b["above"], b["losing"]), (MY_TEAM, 7, 6, TEAMS[3], 1, 2))
        s = by["Somebody Else"]
        self.assertEqual((s["winner"], s["paid"], s["next_bid"], s["above"]), (TEAMS[4], 20, 12, 8))
        self.assertNotIn("Roster Squeeze", by, "a full roster is not a bid anyone beat")
        self.assertEqual([r["player"] for r in self.c["claims"]], ["Somebody Else", "Nick Bolton"], "newest run first")

    def test_per_team_and_the_median(self):
        self.assertEqual(self.c["by_team"][MY_TEAM], {"contested": 1, "above": 1})
        self.assertEqual(self.c["by_team"][TEAMS[4]], {"contested": 1, "above": 8})
        self.assertEqual(self.c["median_above"], 4.5)

    def test_the_owners_suggested_range_against_the_price(self):
        (row,) = self.c["bands"]
        self.assertEqual((row["player"], row["low"], row["high"], row["price"], row["placed"], row["verdict"]),
                         ("Nick Bolton", 3, 5, 7, 7, "below"))


class TestAUnionMergedLog(Case):
    """The sync writes the failed-claims log and the scheduled run can race it, so it is
    union-merged (.gitattributes, F87's rule), and a union merge can leave a row twice. Every
    reader keeps the first row per transaction_id, as the decision log's readers do."""

    def test_a_duplicated_row_is_counted_once(self):
        p = os.path.join(self.td.name, "data", "logs", "failed_claims.jsonl")
        with open(p, encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(lines[1])                                  # f2 again, as a union merge leaves it
        from webui.players_page import clearing_prices, waiver_run
        bolton = next(c for c in clearing_prices(self.root, MY_TEAM)["claims"] if c["player"] == "Nick Bolton")
        self.assertEqual(bolton["losing"], 2)
        self.assertEqual(len(waiver_run(self.root, MY_TEAM)["claims"][0]["losing"]), 2)


class TestTheBoard(Case):
    def test_the_waiver_run_shows_the_next_best_bid(self):
        from webui.players_page import waiver_run
        (claim,) = waiver_run(self.root, MY_TEAM)["claims"]
        self.assertEqual(claim["losing"], [{"team": TEAMS[3], "bid": 6}, {"team": TEAMS[2], "bid": 4}])

    def test_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                text = visible_text(self.get("/waivers", mode))
                self.assertIn("What it took to win", text)
                self.assertIn("Next best bid", text)
                self.assertIn("paid 4.5 more than the next bid", text)
                self.assertNotIn("Losing bids are not recorded", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

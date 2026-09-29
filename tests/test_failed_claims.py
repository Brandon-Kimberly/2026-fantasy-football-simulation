"""
tests.test_failed_claims -- the losing waiver bids, logged at sync (docs/WEB_UI_ROADMAP.md
Decision 4, UI-W4; owner ruling 2026-09-29: extend the sync).

Sleeper's transactions feed carries every claim, won or lost: a lost claim is a `waiver`
with `status: "failed"`, its bid in settings.waiver_bid and Sleeper's reason in
metadata.notes ("This player was claimed by another owner." or "Unfortunately, your roster
will have too many players after this transaction.", the only two seen in this league through
week 3). The decision log keeps completed transactions only, and its contract stays as it is.
The lost claims go to their own append-only log, data/logs/failed_claims.jsonl, deduped on
transaction_id.

A lost claim is paired with the claim that beat it by the moment the waiver run processed
both -- `status_updated`, identical to the millisecond for every one of the 11 outbid claims
checked in the live league on 2026-09-29 -- and never by the week: two of those 11 were
submitted in leg 3 and beaten by claims submitted in leg 2 (F65's boundary). Pairing happens
at sync, because the decision log does not keep the run time.

Written before the failed-claims log existed.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

T1 = 1_789_574_651_938          # the week-1 run
T2 = 1_790_179_451_000          # a later run, after the week boundary
ROSTER_MAP = {1: "Quantum Ferrets", 2: "Neon Walruses", 3: "Rocket Pandas", 4: "Turbo Llamas"}
PLAYERS_DB = {"500": {"first_name": "Player", "last_name": "Five"}, "600": {"first_name": "Player", "last_name": "Six"},
              "700": {"first_name": "Player", "last_name": "Seven"}, "900": {"first_name": "Cut", "last_name": "Man"}}
OUTBID = "This player was claimed by another owner."
FULL = "Unfortunately, your roster will have too many players after this transaction."


def tx(txid, roster, pid, bid, status="complete", run=T1, leg=1, note=None, kind="waiver", drops=None):
    return {"transaction_id": txid, "type": kind, "status": status, "leg": leg, "created": run - 86_400_000,
            "status_updated": run, "roster_ids": [roster], "adds": {pid: roster}, "drops": drops,
            "settings": {"waiver_bid": bid} if kind == "waiver" else None,
            "metadata": {"notes": note} if note else None, "consenter_ids": [roster]}


FEED = {
    1: [tx("w1", 2, "500", 21),                                              # won at 21
        tx("f1", 1, "500", 15, "failed", note=OUTBID, drops={"900": 1}),     # runner-up
        tx("f2", 3, "500", 9, "failed", note=OUTBID),
        tx("f3", 4, "600", 10, "failed", note=FULL),                         # lost to its own roster
        tx("fa", 1, "600", None, kind="free_agent")],
    2: [tx("w2", 3, "700", 22, run=T2, leg=2)],                              # won, submitted in leg 2
    3: [tx("f4", 1, "700", 8, "failed", run=T2, leg=3, note=OUTBID)],        # beaten across the boundary
}


class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, "decision_log.jsonl")
        self.failed = os.path.join(self.td.name, "failed_claims.jsonl")

    def tearDown(self):
        self.td.cleanup()

    def run_ingest(self, feed=FEED, week=3):
        import fantasy_sim.sync as syncmod

        def fake_get(url, timeout=None):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = feed.get(int(url.rsplit("/", 1)[-1]), [])
            return m
        with patch("requests.get", side_effect=fake_get):
            return syncmod.ingest_transactions(ROSTER_MAP, week, {}, PLAYERS_DB, my_team="Quantum Ferrets", path=self.path)

    def rows(self, p):
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]


class TestTheLog(Case):
    def test_lost_claims_go_to_their_own_log_and_the_decision_log_is_unchanged(self):
        self.run_ingest()
        self.assertEqual(sorted(r["transaction_id"] for r in self.rows(self.path)), ["fa", "w1", "w2"])
        self.assertEqual(sorted(r["transaction_id"] for r in self.rows(self.failed)), ["f1", "f2", "f3", "f4"])

    def test_a_row_carries_the_bid_the_reason_and_who_beat_it(self):
        self.run_ingest()
        f1 = next(r for r in self.rows(self.failed) if r["transaction_id"] == "f1")
        self.assertEqual((f1["team"], f1["player_id"], f1["name"], f1["faab_bid"], f1["reason"]),
                         ("Quantum Ferrets", "500", "Player Five", 15, "outbid"))
        self.assertTrue(f1["is_mine"])
        self.assertEqual(f1["won_by"], {"transaction_id": "w1", "team": "Neon Walruses", "faab_bid": 21})
        self.assertEqual([d["player_id"] for d in f1["drops"]], ["900"])
        self.assertEqual(f1["processed"], "2026-09-16T16:04:11Z")

    def test_a_full_roster_is_its_own_reason_and_has_no_winner(self):
        self.run_ingest()
        f3 = next(r for r in self.rows(self.failed) if r["transaction_id"] == "f3")
        self.assertEqual((f3["reason"], f3["won_by"]), ("roster_full", None))

    def test_the_pairing_is_by_the_run_not_the_week(self):
        self.run_ingest()
        f4 = next(r for r in self.rows(self.failed) if r["transaction_id"] == "f4")
        self.assertEqual(f4["week"], 3)
        self.assertEqual(f4["won_by"], {"transaction_id": "w2", "team": "Rocket Pandas", "faab_bid": 22})

    def test_a_second_sync_appends_nothing(self):
        self.run_ingest()
        self.run_ingest()
        self.assertEqual(len(self.rows(self.failed)), 4)

    def test_lost_claims_are_logged_even_when_no_transaction_is_new(self):
        self.run_ingest(feed={1: [tx("w1", 2, "500", 21)]})
        self.run_ingest()
        self.assertEqual(len(self.rows(self.failed)), 4)

    def test_an_unwritable_failed_log_warns_and_the_decision_log_still_lands(self):
        os.makedirs(self.failed)                  # a directory where the file should be
        with self.assertLogs(level="WARNING") as logs:
            n = self.run_ingest()
        self.assertEqual(n, 3)
        self.assertTrue(any("FAILED CLAIMS" in m for m in logs.output))

    def test_the_real_log_lives_in_data_logs(self):
        from fantasy_sim.storage import FAILED_CLAIMS_FILE
        self.assertEqual(os.path.normpath(FAILED_CLAIMS_FILE), os.path.normpath(os.path.join("data", "logs", "failed_claims.jsonl")))


if __name__ == "__main__":
    unittest.main()

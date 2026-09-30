"""
tests.test_sync_audit_fixes -- defects in this session's sync-side work, found by the
post-roadmap review of 2026-09-29 and confirmed by probe before this file was written.

1. A failed week fetch stranded a lost claim. Its winner can be in another week's list (F65's
   cross-leg pairing), and when that week's fetch failed the claim was written with no winner
   and, being deduped, never paired again. Now an unpaired outbid claim waits for a sync that
   fetched every week.
2. One torn line in failed_claims.jsonl (a process killed mid-append) made every later sync
   warn "FAILED CLAIMS: ..." -- a warning the canonical gate did not know, so every window went
   REPORT_ONLY. Bad lines are skipped as the other logs' readers skip them, and the warning (a
   web-only log, never read by the forecast) is benign to the gate.
3. A roster-full failure was given a winner when another team won the player in the same run;
   only an outbid claim has one.
4. The image cache had no overall limit: a hanging image server could hold a sync for about
   ten seconds per file, some 230 files. It stops after a run of network errors and after a
   time budget, and says how many it skipped.
5. A failed matchups fetch dropped that week from weekly_lineups.json until the next good
   sync. A week not fetched this time keeps its last copy.
6. The Waiver board's "last waiver run" grouped winning claims by the Pacific date they were
   SUBMITTED; one run's winners are submitted over several days. The decision log now records
   when Sleeper processed a claim, and the run is grouped by that when it is known.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tests.test_failed_claims import FEED, PLAYERS_DB, ROSTER_MAP, tx


def _ingest(path, feed, week=3, fail_weeks=()):
    import fantasy_sim.sync as syncmod

    def fake_get(url, timeout=None):
        wk = int(url.rsplit("/", 1)[-1])
        m = MagicMock()
        m.status_code = 500 if wk in fail_weeks else 200
        m.json.return_value = [] if wk in fail_weeks else feed.get(wk, [])
        return m
    with patch("requests.get", side_effect=fake_get):
        return syncmod.ingest_transactions(ROSTER_MAP, week, {}, PLAYERS_DB, my_team="Quantum Ferrets", path=path)


def _rows(p):
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, "decision_log.jsonl")
        self.failed = os.path.join(self.td.name, "failed_claims.jsonl")

    def tearDown(self):
        self.td.cleanup()


class TestAFailedWeekDoesNotStrandAClaim(Case):
    def test_the_claim_waits_and_pairs_on_a_complete_sync(self):
        _ingest(self.path, FEED, fail_weeks=(2,))                 # f4's winner (w2) sits in week 2
        self.assertNotIn("f4", [r["transaction_id"] for r in _rows(self.failed)], "not written unpaired")
        _ingest(self.path, FEED)
        f4 = [r for r in _rows(self.failed) if r["transaction_id"] == "f4"]
        self.assertEqual(len(f4), 1)
        self.assertEqual(f4[0]["won_by"]["transaction_id"], "w2")


class TestATornLine(Case):
    def test_a_bad_line_is_skipped_and_logging_goes_on(self):
        with open(self.failed, "w", encoding="utf-8") as fh:
            fh.write('{"transaction_id": "tor\nnull\n')
        _ingest(self.path, FEED)
        ids = [r.get("transaction_id") for r in _rows_tolerant(self.failed)]
        self.assertIn("f1", ids)

    def test_the_warning_is_benign_to_the_gate(self):
        from scripts.canonical_gate import classify_degraded_entry
        self.assertEqual(classify_degraded_entry(
            "WARNING | FAILED CLAIMS: the lost waiver claims could not be logged to data/logs/failed_claims.jsonl "
            "(PermissionError); a later sync will pick them up."), "benign")


def _rows_tolerant(p):
    out = []
    with open(p, encoding="utf-8") as fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict):
                out.append(r)
    return out


class TestRosterFullHasNoWinner(Case):
    def test_only_an_outbid_claim_is_paired(self):
        full = "Unfortunately, your roster will have too many players after this transaction."
        feed = {1: [tx("w1", 2, "500", 21), tx("f9", 3, "500", 30, "failed", note=full)]}
        _ingest(self.path, feed, week=1)
        (f9,) = _rows(self.failed)
        self.assertEqual((f9["reason"], f9["won_by"]), ("roster_full", None))


class TestTheImageCacheHasALimit(unittest.TestCase):
    def test_a_run_of_network_errors_stops_it(self):
        from fantasy_sim.images import cache_images
        calls = []

        def down(url, timeout=None):
            calls.append(url)
            raise ConnectionError("image server down")
        with tempfile.TemporaryDirectory() as td:
            n = cache_images([str(i) for i in range(100, 160)], teams=(), dest=td, get=down)
        self.assertLessEqual(len(calls), 5, "stops after a run of errors")
        self.assertEqual(n["skipped"], 60 - len(calls))

    def test_a_time_budget_stops_it(self):
        from fantasy_sim.images import cache_images
        ticks = iter(range(0, 10_000, 30))                        # every call costs 30 seconds

        class R:
            status_code, content = 200, b"\x89PNG\r\n\x1a\n0"
        with tempfile.TemporaryDirectory() as td:
            n = cache_images([str(i) for i in range(100, 120)], teams=(), dest=td,
                             get=lambda url, timeout=None: R(), clock=lambda: next(ticks), budget_s=90)
        self.assertLess(n["fetched"], 20)
        self.assertGreater(n["skipped"], 0)


class TestLineupsKeepAWeekNotFetched(unittest.TestCase):
    def test_merge(self):
        from fantasy_sim.sync import _merge_weekly_lineups
        old = {"week_1": {"A": {"starters": ["1"]}}, "week_2": {"A": {"starters": ["2"]}}}
        new = {"week_1": {"A": {"starters": ["9"]}}}                 # week 2's fetch failed this time
        self.assertEqual(_merge_weekly_lineups(old, new), {"week_1": {"A": {"starters": ["9"]}}, "week_2": {"A": {"starters": ["2"]}}})

    def test_the_sync_merges_before_it_saves(self):
        import inspect
        from fantasy_sim import sync
        src = inspect.getsource(sync._sync_body)
        self.assertIn("_merge_weekly_lineups(", src)


class TestTheRunIsWhenSleeperProcessedIt(Case):
    def test_the_decision_log_records_the_processing_time(self):
        _ingest(self.path, FEED)
        w1 = next(r for r in _rows(self.path) if r["transaction_id"] == "w1")
        self.assertEqual(w1["processed"], "2026-09-16T16:04:11Z")


try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.paths import Root
    from tests.test_webui_objects import plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheWaiverRunGroupsByProcessing(unittest.TestCase):
    def test_claims_submitted_on_different_days_in_one_run(self):
        from webui.players_page import waiver_run
        with tempfile.TemporaryDirectory() as td:
            plant(td)
            rows = [{"transaction_id": f"r{i}", "type": "waiver", "week": 3, "created": created, "processed": "2026-09-23T16:03:00Z",
                     "is_mine": False, "teams": ["Neon Walruses"], "faab_bid": 5,
                     "adds": [{"name": f"P{i}", "player_id": str(900 + i), "to_team": "Neon Walruses", "projection": {}}], "drops": []}
                    for i, created in enumerate(("2026-09-21T10:00:00Z", "2026-09-22T18:00:00Z", "2026-09-23T09:00:00Z"))]
            p = os.path.join(td, "data", "logs", "decision_log.jsonl")
            with open(p, "a", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
            run = waiver_run(Root(td), MY_TEAM)
        self.assertEqual(run["date"], "2026-09-23")
        self.assertEqual(sorted(c["adds"][0]["name"] for c in run["claims"]), ["P0", "P1", "P2"])


if __name__ == "__main__":
    unittest.main()

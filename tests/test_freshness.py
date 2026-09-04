"""
tests.test_freshness -- the one-glance "has sync run this week, and did it succeed" check.
Written before fantasy_sim.freshness existed. assess() is a pure function over what the script
reads from disk and (optionally) Sleeper, so every verdict is pinned without touching either.
"""
import unittest

from fantasy_sim.freshness import assess, OK, DEGRADED, STALE

MANIFEST = {"started_at": "2026-09-08T12:00:00Z", "finished_at": "2026-09-08T12:04:00Z",
            "current_week": 2, "season": "2026", "degraded": [], "ok": True,
            "files": {"league_state.json": 1.0}}
T_SYNC = 1_000_000.0          # epoch seconds of started_at, as the script derives it
FILES = {n: T_SYNC + 10 for n in ("league_state.json", "player_baselines.json", "vegas_totals.json")}


class TestAssess(unittest.TestCase):
    def test_everything_fresh_is_ok(self):
        status, reasons = assess(MANIFEST, T_SYNC, FILES, vegas_week=2, export_mtime=T_SYNC + 600, nfl_week=2)
        self.assertEqual(status, OK); self.assertEqual(reasons, [])

    def test_no_manifest_is_stale(self):
        status, reasons = assess(None, None, FILES, vegas_week=2, export_mtime=T_SYNC + 600, nfl_week=2)
        self.assertEqual(status, STALE); self.assertTrue(any("no completed sync" in r for r in reasons))

    def test_file_older_than_sync_start_or_missing_is_stale(self):
        files = dict(FILES); files["player_baselines.json"] = T_SYNC - 5
        status, reasons = assess(MANIFEST, T_SYNC, files, 2, T_SYNC + 600, 2)
        self.assertEqual(status, STALE); self.assertTrue(any("player_baselines.json" in r for r in reasons))
        files["player_baselines.json"] = None
        status, reasons = assess(MANIFEST, T_SYNC, files, 2, T_SYNC + 600, 2)
        self.assertEqual(status, STALE); self.assertTrue(any("missing" in r for r in reasons))

    def test_vegas_stamped_for_another_week_is_stale(self):
        status, reasons = assess(MANIFEST, T_SYNC, FILES, vegas_week=1, export_mtime=T_SYNC + 600, nfl_week=2)
        self.assertEqual(status, STALE); self.assertTrue(any("vegas" in r.lower() for r in reasons))

    def test_simulation_export_missing_or_older_than_sync_is_stale(self):
        status, reasons = assess(MANIFEST, T_SYNC, FILES, 2, export_mtime=None, nfl_week=2)
        self.assertEqual(status, STALE); self.assertTrue(any("simulation" in r for r in reasons))
        status, reasons = assess(MANIFEST, T_SYNC, FILES, 2, export_mtime=T_SYNC - 1, nfl_week=2)
        self.assertEqual(status, STALE)

    def test_nfl_week_rolled_past_the_sync_is_stale_and_unknown_week_is_not(self):
        status, reasons = assess(MANIFEST, T_SYNC, FILES, 2, T_SYNC + 600, nfl_week=3)
        self.assertEqual(status, STALE); self.assertTrue(any("week" in r for r in reasons))
        status, _ = assess(MANIFEST, T_SYNC, FILES, 2, T_SYNC + 600, nfl_week=None)   # --offline
        self.assertEqual(status, OK)

    def test_tolerated_failures_are_degraded_not_stale(self):
        m = dict(MANIFEST); m["degraded"] = ["ODDS: no key", "WEATHER: timeout"]
        status, reasons = assess(m, T_SYNC, FILES, 2, T_SYNC + 600, 2)
        self.assertEqual(status, DEGRADED); self.assertEqual(len(reasons), 2)

    def test_stale_outranks_degraded(self):
        m = dict(MANIFEST); m["degraded"] = ["ODDS: no key"]
        status, reasons = assess(m, T_SYNC, FILES, 2, export_mtime=None, nfl_week=2)
        self.assertEqual(status, STALE); self.assertGreaterEqual(len(reasons), 2)


class TestLogsGitState(unittest.TestCase):
    """The mechanical half of the log-push discipline (owner, 2026-09-04): the
    irreplaceable logs are git-tracked to survive a machine loss (R1), but nothing
    surfaced "appended locally, never pushed". logs_git_state is pure over two git
    outputs; check_freshness prints its verdict as an ACTION line."""

    def test_clean_and_pushed_is_all_clear(self):
        from fantasy_sim.freshness import logs_git_state
        uncommitted, ahead = logs_git_state("", "0")
        self.assertEqual(uncommitted, []); self.assertEqual(ahead, 0)

    def test_modified_and_new_log_files_both_count_as_uncommitted(self):
        from fantasy_sim.freshness import logs_git_state
        porcelain = (" M data/logs/projection_log.jsonl\n"
                     "?? data/logs/predictions_2027.jsonl\n")
        uncommitted, _ = logs_git_state(porcelain, "0")
        self.assertEqual(uncommitted, ["data/logs/predictions_2027.jsonl",
                                       "data/logs/projection_log.jsonl"])

    def test_ahead_count_parses_and_no_upstream_reads_as_unknown(self):
        from fantasy_sim.freshness import logs_git_state
        self.assertEqual(logs_git_state("", "3")[1], 3)
        self.assertIsNone(logs_git_state("", None)[1])
        self.assertIsNone(logs_git_state("", "not-a-number")[1])



if __name__ == "__main__":
    unittest.main()

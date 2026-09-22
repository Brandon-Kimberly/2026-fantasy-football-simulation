"""F56 / backlog B5: the projection log records WHAT was projected, never WHICH CODE
projected it.

`projection_log.jsonl` rows carry `synced_at` and nothing else identifying the build
that wrote them. The live log holds 77 distinct sync stamps, 24 of them in week 2 alone,
spanning the F52 boundary -- and the only way to tell a pre-F52 row (ESPN blend dead)
from a post-F52 row is to match its timestamp against `git log` by hand.

That matters because January's calibration is required to PARTITION at two boundaries
that do not coincide: the IDP scoring change (F49) and the ESPN/blend restoration
(F52 + F54). Neither is recoverable from the log alone.

WHY A SIDECAR, not fields on each row. `tests/golden_sync.py` hashes
projection_log.jsonl byte-exactly, so widening the row schema forces a golden
regeneration -- which CLAUDE.md classifies MAJOR. Worse, a `git_commit` embedded in a
byte-pinned file changes on every commit, and the golden harness freezes `datetime` but
has no equivalent seam for git HEAD: the golden would pass once and fail forever after.
One row per sync in a separate file, joined on `synced_at`, records the same fact,
touches no pinned bytes, and stays PATCH.

Written before the implementation and confirmed failing (rule 1).
"""
import json
import os
import tempfile
import unittest

from fantasy_sim import sync

REQUIRED = {"synced_at", "git_commit", "schema_version", "season", "week",
            "espn_rows", "total_rows"}

ROWS = [
    {"season": "2026", "week": 3, "synced_at": "2026-09-22T03:54:41Z",
     "player_id": "1", "name": "Blended Guy", "espn_mean": 12.5, "sleeper_mean": 11.0},
    {"season": "2026", "week": 3, "synced_at": "2026-09-22T03:54:41Z",
     "player_id": "2", "name": "Sleeper Only", "espn_mean": None, "sleeper_mean": 9.0},
    {"season": "2026", "week": 3, "synced_at": "2026-09-22T03:54:41Z",
     "player_id": "3", "name": "Also Blended", "espn_mean": 8.0, "sleeper_mean": 8.5},
]


class TestSyncProvenanceRow(unittest.TestCase):
    def test_the_writer_exists(self):
        self.assertTrue(
            hasattr(sync, "append_sync_provenance"),
            "F56: sync needs a provenance writer so a projection-log row can be "
            "attributed to the build that produced it")

    def test_it_writes_one_row_per_sync_with_every_required_field(self):
        self.assertTrue(hasattr(sync, "append_sync_provenance"), "F56: see above")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sync_provenance.jsonl")
            sync.append_sync_provenance(ROWS, "2026", 3, "2026-09-22T03:54:41Z", path=p)
            lines = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
        self.assertEqual(len(lines), 1, "one row per sync, not one per player")
        missing = REQUIRED - set(lines[0])
        self.assertEqual(missing, set(), f"provenance row is missing {missing}")

    def test_espn_coverage_is_counted_from_the_rows_themselves(self):
        """The one fact that cannot be recovered later: did the blend actually fire.
        Pre-F52 week-2 syncs show 0/152 and post-fix ones 110/150 -- that IS the
        boundary, and counting it here is what makes the partition mechanical."""
        self.assertTrue(hasattr(sync, "append_sync_provenance"), "F56: see above")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sync_provenance.jsonl")
            sync.append_sync_provenance(ROWS, "2026", 3, "2026-09-22T03:54:41Z", path=p)
            row = json.loads(open(p, encoding="utf-8").readline())
        self.assertEqual(row["espn_rows"], 2)
        self.assertEqual(row["total_rows"], 3)

    def test_the_join_key_matches_the_projection_log(self):
        """Attribution works by joining on synced_at, so the value written here must be
        byte-identical to the one the projection rows carry."""
        self.assertTrue(hasattr(sync, "append_sync_provenance"), "F56: see above")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sync_provenance.jsonl")
            sync.append_sync_provenance(ROWS, "2026", 3, ROWS[0]["synced_at"], path=p)
            row = json.loads(open(p, encoding="utf-8").readline())
        self.assertEqual(row["synced_at"], ROWS[0]["synced_at"])

    def test_appending_twice_keeps_both_syncs(self):
        """Append-only, exactly like the projection log it describes: a re-sync within a
        week adds a row, it does not replace one."""
        self.assertTrue(hasattr(sync, "append_sync_provenance"), "F56: see above")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sync_provenance.jsonl")
            sync.append_sync_provenance(ROWS, "2026", 3, "2026-09-22T03:54:41Z", path=p)
            sync.append_sync_provenance(ROWS, "2026", 3, "2026-09-22T19:12:25Z", path=p)
            lines = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
        self.assertEqual([r["synced_at"] for r in lines],
                         ["2026-09-22T03:54:41Z", "2026-09-22T19:12:25Z"])

    def test_a_write_failure_never_breaks_a_sync(self):
        """Same contract as append_projection_log: provenance is a record, not a
        dependency. An unwritable path logs and returns 0."""
        self.assertTrue(hasattr(sync, "append_sync_provenance"), "F56: see above")
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "not-a-dir.txt")
            open(bad, "w").close()
            n = sync.append_sync_provenance(ROWS, "2026", 3, "x",
                                            path=os.path.join(bad, "nested.jsonl"))
        self.assertEqual(n, 0)

    def test_empty_rows_write_nothing(self):
        self.assertTrue(hasattr(sync, "append_sync_provenance"), "F56: see above")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sync_provenance.jsonl")
            self.assertEqual(sync.append_sync_provenance([], "2026", 3, "x", path=p), 0)
            self.assertFalse(os.path.exists(p))


class TestSchemaVersionIsSourced(unittest.TestCase):
    def test_the_schema_version_is_a_named_config_constant(self):
        """Rule 5. A bare integer in sync.py would be exactly the unsourced literal this
        repo keeps having to chase down."""
        from fantasy_sim import config
        self.assertTrue(hasattr(config, "PROJECTION_LOG_SCHEMA_VERSION"),
                        "F56: the schema version belongs in config.py with a comment")
        self.assertIsInstance(config.PROJECTION_LOG_SCHEMA_VERSION, int)
        self.assertGreaterEqual(config.PROJECTION_LOG_SCHEMA_VERSION, 1)


class TestGitHelper(unittest.TestCase):
    def test_storage_exposes_a_git_head_helper_that_degrades_to_none(self):
        """Four separate _git implementations already exist across scripts/. This adds a
        fifth only because sync (a library) cannot import from scripts/; it must return
        None rather than raise outside a checkout, or a sync on a runner without git
        history would die on a provenance field."""
        from fantasy_sim import storage
        self.assertTrue(hasattr(storage, "git_head_short"),
                        "F56: sync needs a git helper that lives in the library")
        v = storage.git_head_short()
        self.assertTrue(v is None or (isinstance(v, str) and len(v) >= 7))


if __name__ == "__main__":
    unittest.main()

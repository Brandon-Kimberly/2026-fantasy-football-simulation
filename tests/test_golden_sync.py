"""Sync-stage golden tests -- the instrument F28 deferred, built as F29 pre-work.

One hermetic run of generate_player_baselines from committed fixture inputs (see
tests/golden_sync.py for why, and for the coverage limit: ESPN client parsing sits
outside, snapshotted at the fetch_espn_projections boundary). Sensitivity was verified
at build time: reverting VOLATILITY_CONSTANTS['DL'] to its old 1.5 placeholder in-memory
changes the baselines hash -- the exact class of change the ENGINE golden was blind to
in F28 (byte-identical fixtures through a 44% DL sd change).

An intended behaviour change regenerates with `py -3.10 -m tests.golden_sync
--regenerate` and is MAJOR under the release policy's sync-time clause; the commit
explains the deltas.
"""
import json
import os
import unittest

from tests.golden_sync import EXPECTED, run_golden_sync


class TestGoldenSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import logging
        logging.disable(logging.WARNING)   # ~40 known name-collision warnings per run
        try:
            cls.result = run_golden_sync()
        finally:
            logging.disable(logging.NOTSET)
        with open(EXPECTED, encoding="utf-8") as f:
            cls.expected = json.load(f)

    def test_baselines_byte_exact(self):
        self.assertEqual(
            self.result["baselines_sha256"], self.expected["baselines_sha256"],
            "player_baselines.json no longer reproduces byte-exactly from pinned pre-sync "
            "inputs. A sync-time constant or blend change did this: either revert it, or "
            "regenerate (tests.golden_sync --regenerate) and explain the deltas in the "
            "commit -- that regeneration is MAJOR (release policy, sync-time clause).")

    def test_projection_log_byte_exact(self):
        self.assertEqual(
            self.result["projection_log_sha256"], self.expected["projection_log_sha256"],
            "the F7 projection-log rows this sync would append have changed -- the log is "
            "next season's only source for measuring projection error, so a format or "
            "content change here must be deliberate and explained.")

    def test_baseline_count_pinned(self):
        self.assertEqual(
            self.result["n_baselines"], self.expected["n_baselines"],
            "the number of generated baselines changed -- players appearing or vanishing "
            "from the model is a behaviour change in its own right (compare the engine "
            "golden's export-count rule).")

"""F57 / backlog B6: a source that returns EMPTY is indistinguishable from a source that
returns nothing to report.

Six `except Exception:` blocks in `sync.py` and `clients/espn.py` resolve to `pass`,
`continue` or `return {}`. F52 was exactly this shape and survived a fortnight: ESPN
returned an empty payload every sync, the blend went dead, the epistemic disagreement
signal went dead, F29's K/IDP subscore channel went dead -- and every one of those
failures looked, from outside, like "ESPN had nothing to say this week".

The manifest already records WARNINGS (`degraded`). It records no positive statement of
what each source actually delivered, so the absence of a warning cannot be read as
success -- which is the only reading that would have caught F52.

This adds that positive statement: a `sources` block, `{name: {ok, rows, fallback}}`,
one entry per source per sync. `check_freshness` reads it and a source that delivered
zero rows is DEGRADED by construction, warning or no warning.

THE TRAP THESE TESTS EXIST TO PIN (B6 names it): four of the six sites are inside loops
-- 32 teams for weather, 14 positions for the league-wide stats feed, ~2000 players for
the ESPN parse. One notice per source per sync, never one per iteration, or a routine
degraded day becomes a thousand-line manifest nobody reads.

Written before the implementation and confirmed failing (rule 1).
"""
import logging
import unittest
import unittest.mock

from fantasy_sim import sync
from fantasy_sim.freshness import assess, OK, DEGRADED, STALE

MANIFEST = {"started_at": "2026-09-08T12:00:00Z", "finished_at": "2026-09-08T12:04:00Z",
            "current_week": 2, "season": "2026", "degraded": [], "ok": True,
            "files": {"league_state.json": 1.0}}
T_SYNC = 1_000_000.0
FILES = {n: T_SYNC + 10 for n in ("league_state.json", "player_baselines.json", "vegas_totals.json")}


def _with_sources(sources):
    return dict(MANIFEST, sources=sources)


class TestTheLedgerExists(unittest.TestCase):
    def test_sync_exposes_a_source_ledger(self):
        for name in ("reset_sources", "record_source", "collected_sources"):
            self.assertTrue(hasattr(sync, name),
                            f"F57: sync needs {name}() so the manifest can say what each "
                            f"external source actually delivered")

    def test_a_recorded_source_round_trips(self):
        sync.reset_sources()
        sync.record_source("espn_projections", ok=True, rows=110)
        got = sync.collected_sources()
        self.assertEqual(got["espn_projections"]["ok"], True)
        self.assertEqual(got["espn_projections"]["rows"], 110)
        self.assertIsNone(got["espn_projections"]["fallback"])

    def test_reset_clears_the_previous_sync(self):
        sync.reset_sources()
        sync.record_source("vegas_odds", rows=32)
        sync.reset_sources()
        self.assertEqual(sync.collected_sources(), {})

    def test_a_failure_records_the_fallback_it_took(self):
        sync.reset_sources()
        sync.record_source("espn_projections", ok=False, rows=0,
                           fallback="Sleeper-only projections")
        got = sync.collected_sources()["espn_projections"]
        self.assertFalse(got["ok"])
        self.assertEqual(got["fallback"], "Sleeper-only projections")


class TestTheLoopTrap(unittest.TestCase):
    """B6's named trap. Weather is fetched per team (32), the league-wide stats feed per
    position (14), the ESPN payload parsed per player (~2000). Recording must ACCUMULATE
    into one entry, not append 32 of them."""

    def test_recording_the_same_source_repeatedly_keeps_one_entry_and_sums_rows(self):
        sync.reset_sources()
        for _ in range(32):
            sync.record_source("weather", rows=1)
        got = sync.collected_sources()
        self.assertEqual(list(got), ["weather"], "one entry per source per sync")
        self.assertEqual(got["weather"]["rows"], 32)

    def test_one_failure_among_many_marks_the_source_not_ok(self):
        """A source is ok only if nothing fell back. 30 good teams and 2 failed weather
        lookups is a degraded source, and the row count says how degraded."""
        sync.reset_sources()
        for _ in range(30):
            sync.record_source("weather", rows=1)
        for _ in range(2):
            sync.record_source("weather", ok=False, rows=0, fallback="wind=0, precip=0")
        got = sync.collected_sources()["weather"]
        self.assertFalse(got["ok"])
        self.assertEqual(got["rows"], 30)
        self.assertEqual(got["fallback"], "wind=0, precip=0")


class TestTheManifestCarriesSources(unittest.TestCase):
    def test_write_sync_manifest_includes_the_sources_block(self):
        import datetime as _dt
        sync.reset_sources()
        sync.record_source("espn_projections", ok=True, rows=110)
        written = {}
        with unittest.mock.patch.object(sync, "save_json",
                                        side_effect=lambda p, d: written.update(d)):
            sync.write_sync_manifest(_dt.datetime(2026, 9, 8, 12, 0, 0), 2, "2026", [], False,
                                     path="ignored.json")
        self.assertIn("sources", written,
                      "F57: the manifest must carry a positive record of each source")
        self.assertEqual(written["sources"]["espn_projections"]["rows"], 110)


class TestFreshnessReadsSources(unittest.TestCase):
    def test_a_source_with_zero_rows_is_degraded_and_named(self):
        m = _with_sources({"espn_projections": {"ok": True, "rows": 0, "fallback": None}})
        status, reasons = assess(m, T_SYNC, FILES, 2, T_SYNC + 600, 2)
        self.assertEqual(status, DEGRADED)
        self.assertTrue(any("espn_projections" in r for r in reasons),
                        f"the failing source must be NAMED, got {reasons}")

    def test_a_source_that_fell_back_is_degraded_even_with_rows(self):
        m = _with_sources({"weather": {"ok": False, "rows": 30, "fallback": "wind=0"}})
        status, reasons = assess(m, T_SYNC, FILES, 2, T_SYNC + 600, 2)
        self.assertEqual(status, DEGRADED)
        self.assertTrue(any("weather" in r for r in reasons))

    def test_healthy_sources_are_silent(self):
        m = _with_sources({"espn_projections": {"ok": True, "rows": 110, "fallback": None},
                           "vegas_odds": {"ok": True, "rows": 32, "fallback": None}})
        status, reasons = assess(m, T_SYNC, FILES, 2, T_SYNC + 600, 2)
        self.assertEqual(status, OK)
        self.assertEqual(reasons, [])

    def test_a_manifest_without_sources_still_assesses(self):
        """Every manifest written before F57 has no sources block. Those must read as
        OK-if-otherwise-fine, not as 'every source failed'."""
        status, reasons = assess(MANIFEST, T_SYNC, FILES, 2, T_SYNC + 600, 2)
        self.assertEqual(status, OK)
        self.assertEqual(reasons, [])

    def test_stale_still_outranks_a_failed_source(self):
        m = _with_sources({"espn_projections": {"ok": True, "rows": 0, "fallback": None}})
        status, reasons = assess(m, T_SYNC, FILES, vegas_week=1, export_mtime=T_SYNC + 600, nfl_week=2)
        self.assertEqual(status, STALE)
        self.assertTrue(any("espn_projections" in r for r in reasons),
                        "a STALE verdict must still surface the dead source")


class TestTheSixSitesAreLoud(unittest.TestCase):
    """Each of the six silent fallbacks either names itself in a WARNING or carries a
    comment saying why it is harmless. These pin the four that must be loud."""

    def test_espn_league_construction_failure_is_named(self):
        from fantasy_sim.clients import espn
        with unittest.mock.patch.object(espn, "_espn_league", side_effect=RuntimeError("401")):
            with self.assertLogs(level=logging.WARNING) as cm:
                proj, subs = espn.fetch_espn_projection_data("2026", 3)
        self.assertEqual((proj, subs), ({}, {}))
        self.assertTrue(any("ESPN" in m for m in cm.output), cm.output)

    def test_espn_free_agents_failure_is_named(self):
        """THE F52 SITE. When this call fails or returns nothing, the entire points-level
        blend is dead and the manifest said nothing at all."""
        from fantasy_sim.clients import espn

        class _League:
            teams = []

            def free_agents(self, **kw):
                raise RuntimeError("boom")

        with unittest.mock.patch.object(espn, "_espn_league", return_value=_League()):
            with self.assertLogs(level=logging.WARNING) as cm:
                espn.fetch_espn_projection_data("2026", 3)
        self.assertTrue(any("free_agents" in m or "ESPN" in m for m in cm.output), cm.output)

    def test_the_espn_parse_loop_warns_once_with_a_count(self):
        """~2000 players. One line saying how many failed, not 2000 lines."""
        from fantasy_sim.clients import espn

        class _Bad:
            name = "Some Guy"

            @property
            def position(self):
                raise RuntimeError("malformed")

        class _League:
            teams = []

            def free_agents(self, **kw):
                return [_Bad() for _ in range(50)]

        with unittest.mock.patch.object(espn, "_espn_league", return_value=_League()):
            with self.assertLogs(level=logging.WARNING) as cm:
                espn.fetch_espn_projection_data("2026", 3)
        parse = [m for m in cm.output if "unreadable" in m or "could not be parsed" in m]
        self.assertEqual(len(parse), 1, f"one aggregated line, got {len(parse)}: {parse}")
        self.assertTrue(any("50" in m for m in parse), parse)

    def test_the_league_wide_stats_feed_warns_once_naming_the_positions(self):
        """14 positions. One line naming which failed, not 14."""
        sync.reset_sources()
        with self.assertLogs(level=logging.WARNING) as cm:
            out = sync.fetch_league_wide_player_scores(
                "2026", 3, {"pts_ppr": 1.0},
                fetch=unittest.mock.Mock(side_effect=RuntimeError("503")))
        self.assertEqual(out, {})
        feed = [m for m in cm.output if "STATS" in m or "stats feed" in m]
        self.assertEqual(len(feed), 1, f"one aggregated line, got {len(feed)}: {feed}")


if __name__ == "__main__":
    unittest.main()

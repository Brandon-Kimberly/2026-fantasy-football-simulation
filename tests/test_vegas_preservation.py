"""C3: a failed odds fetch overwrites REAL same-week lines with the 21.5 fallback.

Observed 2026-09-23. A sync ran with a pre-rotation `ODDS_API_KEY`, got a 401, and wrote
`vegas_totals.json` with `source: fallback_api_error` -- every team at the flat 21.5 with
`opponent: FA` -- on top of a file that carried real week-3 lines from a sync three hours
earlier. Every week-level projection degraded until the next good sync. `data/current/` is
not tracked by git, so there was nothing to restore; the only fix was another sync with a
live key.

The sync WARNED, loudly and correctly (F57). It destroyed the data anyway.

**WHY IT WRITES UNCONDITIONALLY, which is not a bug and must survive this change.**
`_write_vegas`'s docstring records Phase 3 finding 1: two of the three in-season fallback
paths used to return WITHOUT writing, which left the week-1 table on disk for the rest of
the season, and the engine then applied week-1 lines -- week-1 opponents included -- to
every current week. "Always write" is the fix for that, and a naive "never overwrite a
real file" would reintroduce it exactly.

**So the rule is narrower than "don't overwrite".** A real file is worth keeping only when
it is real AND for the SAME week. Specifically:

    existing _meta          incoming            outcome
    ----------------------  ------------------  ---------------------------------
    odds_api, same week     any fallback        KEEP existing, stamp stale_since
    odds_api, older week    any fallback        REPLACE (Phase 3 finding 1)
    any fallback, any week  any fallback        REPLACE (a fresh stamp is honest)
    anything                odds_api            REPLACE (real data always wins)

Last week's real lines are not this week's. That is the distinction Phase 3 finding 1 was
about, and it is why the week check is not optional.

Written before the change and confirmed failing (rule 1).
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fantasy_sim.config import DEFAULT_FALLBACK_TOTALS

REAL_WEEK3 = {
    "GB": {"total": 23.5, "spread": -4.5, "opponent": "ATL",
           "wind_mph": 7.21, "precip_in": 0.0, "weather_source": "forecast"},
    "ATL": {"total": 19.0, "spread": 4.5, "opponent": "GB",
            "wind_mph": 7.21, "precip_in": 0.0, "weather_source": "forecast"},
}


def _existing(week, source, payload=None):
    """A vegas_totals.json already on disk, stamped."""
    from fantasy_sim.sync import _stamp_vegas
    return _stamp_vegas(payload if payload is not None else REAL_WEEK3, week, source)


class _Harness(unittest.TestCase):
    """Runs the REAL `_write_vegas` against a temp file. `generate_nfl_power_ratings` is
    patched out -- it is a separate concern with its own tests, and letting it run here
    would test the ratings model rather than the write rule."""

    def _write(self, incoming, week, source, existing=None):
        from fantasy_sim import sync
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "vegas_totals.json")
            if existing is not None:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(existing, fh)
            with patch.object(sync, "VEGAS_FILE", path), \
                 patch.object(sync, "generate_nfl_power_ratings", lambda *_a, **_k: None):
                returned = sync._write_vegas(incoming, week, source)
            with open(path, encoding="utf-8") as fh:
                on_disk = json.load(fh)
        return returned, on_disk


class TestARealSameWeekFileSurvivesAFailedFetch(_Harness):
    """The defect."""

    def test_the_real_lines_are_still_on_disk(self):
        _r, disk = self._write(DEFAULT_FALLBACK_TOTALS, 3, "fallback_api_error",
                               existing=_existing(3, "odds_api"))
        self.assertEqual(disk["GB"]["total"], 23.5,
                         "a 401 must not flatten real week-3 lines to 21.5")
        self.assertEqual(disk["GB"]["opponent"], "ATL")

    def test_the_source_still_reads_odds_api_because_the_data_is_real(self):
        _r, disk = self._write(DEFAULT_FALLBACK_TOTALS, 3, "fallback_api_error",
                               existing=_existing(3, "odds_api"))
        self.assertEqual(disk["_meta"]["source"], "odds_api")

    def test_it_is_stamped_stale_so_the_keep_is_visible(self):
        """Silently keeping the old file would be its own quiet failure -- a reader must be
        able to tell 'fetched this sync' from 'kept from an earlier one'."""
        _r, disk = self._write(DEFAULT_FALLBACK_TOTALS, 3, "fallback_api_error",
                               existing=_existing(3, "odds_api"))
        self.assertIn("stale_since", disk["_meta"])
        self.assertTrue(str(disk["_meta"]["stale_since"]))

    def test_the_returned_payload_matches_what_was_kept(self):
        """The caller hands the return value onward; returning the fallback while writing
        the real file would split the two."""
        returned, disk = self._write(DEFAULT_FALLBACK_TOTALS, 3, "fallback_api_error",
                                     existing=_existing(3, "odds_api"))
        self.assertEqual(returned["GB"]["total"], disk["GB"]["total"])

    def test_every_fallback_source_is_covered_not_just_the_api_error(self):
        """Three paths reach the fallback: no key, api error, empty payload. A dead key is
        the one observed, but an empty payload destroys the file just as thoroughly."""
        for source in ("fallback_no_api_key", "fallback_api_error", "fallback_empty_payload"):
            _r, disk = self._write(DEFAULT_FALLBACK_TOTALS, 3, source,
                                   existing=_existing(3, "odds_api"))
            self.assertEqual(disk["GB"]["total"], 23.5, f"{source} flattened the real file")


class TestPhase3Finding1Survives(_Harness):
    """The trap. 'Always write' exists because two fallback paths used to return without
    writing, leaving the week-1 table on disk all season while the engine applied week-1
    opponents to every week. A blanket 'never overwrite a real file' reintroduces it."""

    def test_an_OLDER_real_file_is_replaced_by_the_fallback(self):
        _r, disk = self._write(DEFAULT_FALLBACK_TOTALS, 3, "fallback_api_error",
                               existing=_existing(2, "odds_api"))
        self.assertEqual(disk["_meta"]["week"], 3)
        self.assertEqual(disk["_meta"]["source"], "fallback_api_error")
        self.assertNotIn("GB", disk if "GB" not in DEFAULT_FALLBACK_TOTALS else {},
                         "last week's real lines are not this week's")

    def test_a_newer_real_file_is_also_replaced(self):
        """Defensive: a file stamped for a LATER week is equally not this week's."""
        _r, disk = self._write(DEFAULT_FALLBACK_TOTALS, 3, "fallback_api_error",
                               existing=_existing(4, "odds_api"))
        self.assertEqual(disk["_meta"]["week"], 3)
        self.assertEqual(disk["_meta"]["source"], "fallback_api_error")

    def test_an_existing_fallback_is_replaced_by_a_fresh_one(self):
        """Nothing is preserved by keeping one flat table over another, and a fresh stamp
        is the honest record of when this sync ran."""
        _r, disk = self._write(DEFAULT_FALLBACK_TOTALS, 3, "fallback_api_error",
                               existing=_existing(3, "fallback_no_api_key"))
        self.assertEqual(disk["_meta"]["source"], "fallback_api_error")
        self.assertNotIn("stale_since", disk["_meta"])

    def test_real_data_always_overwrites_anything(self):
        _r, disk = self._write(REAL_WEEK3, 3, "odds_api",
                               existing=_existing(3, "fallback_api_error",
                                                  payload=DEFAULT_FALLBACK_TOTALS))
        self.assertEqual(disk["_meta"]["source"], "odds_api")
        self.assertEqual(disk["GB"]["total"], 23.5)
        self.assertNotIn("stale_since", disk["_meta"])

    def test_the_week1_verified_table_still_writes(self):
        """The preseason gate's own path must be unaffected."""
        _r, disk = self._write(REAL_WEEK3, 1, "week1_verified_table")
        self.assertEqual(disk["_meta"]["source"], "week1_verified_table")

    def test_no_existing_file_writes_the_fallback_as_before(self):
        _r, disk = self._write(DEFAULT_FALLBACK_TOTALS, 3, "fallback_api_error")
        self.assertEqual(disk["_meta"]["source"], "fallback_api_error")
        self.assertEqual(disk["_meta"]["week"], 3)


class TestAnUnreadableExistingFileDoesNotBreakTheSync(_Harness):
    """A record is not a dependency. Corrupt JSON on disk must not stop a sync writing."""

    def test_corrupt_existing_json_is_replaced(self):
        from fantasy_sim import sync
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "vegas_totals.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            with patch.object(sync, "VEGAS_FILE", path), \
                 patch.object(sync, "generate_nfl_power_ratings", lambda *_a, **_k: None):
                sync._write_vegas(DEFAULT_FALLBACK_TOTALS, 3, "fallback_api_error")
            with open(path, encoding="utf-8") as fh:
                disk = json.load(fh)
        self.assertEqual(disk["_meta"]["source"], "fallback_api_error")


if __name__ == "__main__":
    unittest.main()

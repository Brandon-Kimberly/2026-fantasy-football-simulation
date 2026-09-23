"""B16: docs/WAIVER_MECHANICS.md states two things the CODE also states.

These are GUARDS against drift, not characterisations of a defect -- there was no bug
here, the mechanics were simply written down nowhere. Most of that document describes
LEAGUE settings, which live on Sleeper and cannot be pinned by a test; the file says so
and carries the one-line command to re-verify them.

What can be pinned is the handful of claims the doc makes about this repo's own constants.
Those are exactly the claims that would go stale silently, so they are asserted here.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "WAIVER_MECHANICS.md")


def _doc():
    with open(DOC, encoding="utf-8") as fh:
        return fh.read()


class TestTheDocExistsAndIsLinked(unittest.TestCase):
    def test_the_document_exists(self):
        self.assertTrue(os.path.exists(DOC), "B16: docs/WAIVER_MECHANICS.md is missing")

    def test_the_readme_points_at_it(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            self.assertIn("docs/WAIVER_MECHANICS.md", fh.read(),
                          "B16 scope: a pointer from the README")


class TestTheDocMatchesTheCode(unittest.TestCase):
    """The doc's claims about THIS repo, tied to the source of truth."""

    def test_the_stated_active_roster_limit_matches_decisions(self):
        from fantasy_sim.decisions import ACTIVE_ROSTER_LIMIT
        m = re.search(r"ACTIVE_ROSTER_LIMIT\s*=\s*(\d+)", _doc())
        self.assertIsNotNone(m, "the doc should state the active roster limit")
        self.assertEqual(int(m.group(1)), ACTIVE_ROSTER_LIMIT,
                         "docs/WAIVER_MECHANICS.md has drifted from "
                         "decisions.ACTIVE_ROSTER_LIMIT")

    def test_the_stated_absence_statuses_match_sim_config(self):
        """The doc warns that an `NA` player cannot be IR-stashed yet still counts as
        absent. That warning is only correct while NA is in this tuple."""
        from fantasy_sim.config import SIM_CONFIG
        actual = SIM_CONFIG["INITIAL_ABSENCE_STATUSES"]
        doc = _doc()
        m = re.search(r"INITIAL_ABSENCE_STATUSES.*?is\s+`\(([^)]*)\)`", doc, re.S)
        self.assertIsNotNone(m, "the doc should quote INITIAL_ABSENCE_STATUSES")
        stated = tuple(x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip())
        self.assertEqual(stated, tuple(actual),
                         "docs/WAIVER_MECHANICS.md has drifted from "
                         "SIM_CONFIG['INITIAL_ABSENCE_STATUSES']")

    def test_the_na_trap_claim_still_holds(self):
        """If NA ever leaves the absence tuple, the doc's 'occupies an active slot while
        contributing nothing' paragraph becomes wrong and must be rewritten."""
        from fantasy_sim.config import SIM_CONFIG
        self.assertIn("NA", SIM_CONFIG["INITIAL_ABSENCE_STATUSES"])
        self.assertIn("reserve_allow_na", _doc())


class TestTheDocDoesNotLeakIdentities(unittest.TestCase):
    """This file is about the owner's live league, which makes it the likeliest place for
    a real team name or a league id to get written down. Neither may enter the repo."""

    def test_no_league_ids(self):
        doc = _doc()
        self.assertNotIn("SLEEPER_LEAGUE_ID'])", doc.replace("os.environ['SLEEPER_LEAGUE_ID']", ""),
                         "the id must be read from the environment, never inlined")
        self.assertIsNone(re.search(r"\b\d{15,}\b", doc),
                          "a bare numeric league id appears in the document")


if __name__ == "__main__":
    unittest.main()

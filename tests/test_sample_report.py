"""The sanitized-sample generator's sanitization logic (fast -- no report run here; the
full generation is the pages-sample workflow). Rewritten for F37 (2026-09-05): the
repository itself is pseudonymized now, so the generator no longer renames anything --
its remaining guards are the league-id leak check and the local-overlay marker."""
import unittest

from scripts.make_sample_report import SAMPLE_MY_TEAM, TEAMS, leak_check
from fantasy_sim.config import TEAM_NAME_MAP


class TestTheEmbedIsFoundInWhateverWeekTheRunProduced(unittest.TestCase):
    """The sample builder hardcoded week_01 (found live 2026-09-16).

    `make_sample_report` runs the real weekly report inside a scratch tree, and that
    report writes to whichever week the engine is on. The lookup was pinned to
    `data/decisions/week_01/archive`, so the build broke the moment the NFL week rolled
    to 2 -- FileNotFoundError on every scheduled pages-sample run, with the failing log's
    own last line reading `logged -> data/decisions/week_02/...`. It would have stayed
    broken for the remaining sixteen weeks of the season.

    Nothing but the public sample's freshness was at risk (Pages keeps serving the
    previous build), which is exactly why it could have gone unnoticed for a long time."""

    def _tree(self, root, week, names):
        import os
        d = os.path.join(root, "data", "decisions", f"week_{week:02d}", "archive")
        os.makedirs(d, exist_ok=True)
        for n in names:
            with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                f.write("<h1>x</h1>")
        return d

    def test_a_week_two_run_is_found(self):
        import os
        import tempfile
        from scripts.make_sample_report import newest_sample_embed
        with tempfile.TemporaryDirectory() as root:
            self._tree(root, 2, ["weekly_report_week2_20260916T042242Z_embed.html"])
            got = newest_sample_embed(os.path.join(root, "data", "decisions"))
            self.assertTrue(got.endswith("weekly_report_week2_20260916T042242Z_embed.html"))

    def test_week_one_still_works(self):
        import os
        import tempfile
        from scripts.make_sample_report import newest_sample_embed
        with tempfile.TemporaryDirectory() as root:
            self._tree(root, 1, ["weekly_report_week1_20260902T024712Z_embed.html"])
            got = newest_sample_embed(os.path.join(root, "data", "decisions"))
            self.assertIn("week_01", got)

    def test_the_newest_run_wins_across_weeks(self):
        import os
        import tempfile
        from scripts.make_sample_report import newest_sample_embed
        with tempfile.TemporaryDirectory() as root:
            self._tree(root, 1, ["weekly_report_week1_20260902T024712Z_embed.html"])
            self._tree(root, 2, ["weekly_report_week2_20260916T042242Z_embed.html"])
            got = newest_sample_embed(os.path.join(root, "data", "decisions"))
            self.assertIn("week2_20260916", got, "the run just produced is the one to publish")

    def test_a_failed_digest_is_never_published(self):
        import os
        import tempfile
        from scripts.make_sample_report import newest_sample_embed
        with tempfile.TemporaryDirectory() as root:
            self._tree(root, 2, ["weekly_report_week2_20260916T999999Z_FAILED_embed.html"])
            with self.assertRaises(SystemExit) as cm:
                newest_sample_embed(os.path.join(root, "data", "decisions"))
            self.assertIn("FAILED", str(cm.exception))

    def test_no_week_directory_at_all_fails_loudly(self):
        import os
        import tempfile
        from scripts.make_sample_report import newest_sample_embed
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "data", "decisions"), exist_ok=True)
            with self.assertRaises(SystemExit):
                newest_sample_embed(os.path.join(root, "data", "decisions"))


class TestSanitization(unittest.TestCase):
    def test_the_eight_team_names_are_distinct_and_cover_the_config(self):
        self.assertEqual(len(TEAMS), 8)
        self.assertEqual(len(set(TEAMS)), 8, "team names must be distinct")
        self.assertIn(SAMPLE_MY_TEAM, TEAMS)
        for v in TEAM_NAME_MAP.values():
            self.assertIn(v, TEAMS, f"unmapped team label {v!r} in config")

    def test_leak_check_catches_a_planted_identifier_and_passes_clean_text(self):
        forbidden = ["1234567890sentinel", "LOCAL VIEW"]
        dirty = "<html>… league 1234567890sentinel …</html>"
        self.assertEqual(leak_check(dirty, forbidden), ["1234567890sentinel"])
        self.assertEqual(leak_check("<html>Cosmic Badgers beat Quantum Ferrets</html>",
                                    forbidden), [])

    def test_real_names_are_disabled_explicitly_not_by_unsetting(self):
        """This used to assert `os.environ.pop(...)`, and popping was sufficient while
        an unset flag meant OFF. As of 2026-09-16 the CLI opts local runs IN, so an
        unset flag means ON -- popping would have handed this PUBLISHED artifact the
        owner's real league identities. It must be set to "0"."""
        import inspect
        import scripts.make_sample_report as m
        src = inspect.getsource(m.main)
        self.assertIn('os.environ["SHOW_REAL_TEAM_NAMES"] = "0"', src)
        self.assertNotIn('os.environ.pop("SHOW_REAL_TEAM_NAMES"', src,
                         "popping no longer disables it")

    def test_the_forbidden_list_covers_both_markers_and_the_real_names(self):
        """Defence in depth. The real names themselves were never in the forbidden list
        -- the only protection was the flag being off, which is precisely the assumption
        that changed. Both banner markers and the local identity map are now included."""
        import inspect
        import scripts.make_sample_report as m
        src = inspect.getsource(m.main)
        self.assertIn('"LOCAL VIEW"', src)
        self.assertIn("PRIVATE_MARKER", src)
        self.assertIn("identity_map.json", src)


if __name__ == "__main__":
    unittest.main()

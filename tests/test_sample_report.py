"""The sanitized-sample generator's sanitization logic (fast -- no report run here; the
full generation is the pages-sample workflow). Rewritten for F37 (2026-09-05): the
repository itself is pseudonymized now, so the generator no longer renames anything --
its remaining guards are the league-id leak check and the local-overlay marker."""
import unittest

from scripts.make_sample_report import SAMPLE_MY_TEAM, TEAMS, leak_check
from fantasy_sim.config import TEAM_NAME_MAP


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

    def test_the_local_overlay_marker_is_forbidden(self):
        """The owner's real-name legend (SHOW_REAL_TEAM_NAMES) renders a 'LOCAL VIEW'
        marker; a published sample containing it means the overlay leaked into a public
        artifact. The generator both clears the env flag and forbids the marker."""
        import inspect
        import scripts.make_sample_report as m
        src = inspect.getsource(m.main)
        self.assertIn('os.environ.pop("SHOW_REAL_TEAM_NAMES"', src)
        self.assertIn('"LOCAL VIEW"', src)


if __name__ == "__main__":
    unittest.main()

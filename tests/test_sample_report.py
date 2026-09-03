"""The sanitized-sample generator's sanitization logic (fast -- no report run here; the
full generation is scripts.make_sample_report, run manually when refreshing the sample)."""
import unittest

from scripts.make_sample_report import FICTIONAL, SAMPLE_MY_TEAM, leak_check
from fantasy_sim.config import TEAM_NAME_MAP


class TestSanitization(unittest.TestCase):
    def test_every_real_team_has_a_fictional_name_and_none_collide(self):
        # NOTE: TEAM_NAME_MAP's values may already be mutated in an odd import order, so
        # assert against FICTIONAL's own keys covering the map's real values when unmutated.
        self.assertEqual(len(FICTIONAL), 8)
        self.assertEqual(len(set(FICTIONAL.values())), 8, "fictional names must be distinct")
        for real, fake in FICTIONAL.items():
            self.assertNotEqual(real, fake)
            self.assertNotIn(real, fake)
        self.assertIn(SAMPLE_MY_TEAM, FICTIONAL.values())

    def test_leak_check_catches_a_planted_identifier_and_passes_clean_text(self):
        forbidden = list(FICTIONAL.keys()) + ["1310010483033522176"]
        dirty = "<html>… The Glutton beat someone …</html>"
        self.assertEqual(leak_check(dirty, forbidden), ["The Glutton"])
        clean = "<html>Cosmic Badgers beat Quantum Ferrets</html>"
        self.assertEqual(leak_check(clean, forbidden), [])

    def test_map_covers_the_live_config(self):
        # Valid whether or not the map's values have been sanitized in-process: every
        # configured value must be either a known real name or a known fictional one.
        for v in TEAM_NAME_MAP.values():
            self.assertTrue(v in FICTIONAL or v in FICTIONAL.values(),
                            f"unmapped team label {v!r} -- extend FICTIONAL before publishing")

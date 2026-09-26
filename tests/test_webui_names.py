"""
tests.test_webui_names -- the in-memory real-name overlay (docs/WEB_UI.md 2.6).

Hermetic: the mappings are hand-built fictional -> fictional. The one environment test
pins that an UNSET flag yields an empty overlay without any network (the library default
F48 keeps for exactly this reason).
"""
import os
import unittest
from unittest.mock import patch

from webui.names import Overlay


class TestOverlay(unittest.TestCase):
    def test_empty_overlay_is_disabled_and_is_the_identity(self):
        o = Overlay()
        self.assertFalse(o.enabled)
        self.assertEqual(o.text("Quantum Ferrets beat Neon Walruses"), "Quantum Ferrets beat Neon Walruses")
        self.assertEqual(o.html("<p>Quantum Ferrets</p>"), "<p>Quantum Ferrets</p>")
        self.assertEqual(o.text(None), "")

    def test_substitutes_every_occurrence_longest_name_first(self):
        o = Overlay({"Alpha": "X", "Alpha Beta": "Y"})
        self.assertTrue(o.enabled)
        self.assertEqual(o.text("Alpha Beta vs Alpha, Alpha Beta again"), "Y vs X, Y again")

    def test_blank_keys_and_values_are_dropped(self):
        o = Overlay({"": "real", "Fict": "", None: "x", "Keep": "Real"})
        self.assertEqual(o.mapping, {"Keep": "Real"})

    def test_html_localizes_and_marks_private_once(self):
        o = Overlay({"Quantum Ferrets": "Team Alpha"})
        out = o.html("<html><body><h1>Quantum Ferrets</h1></body></html>")
        self.assertIn("Team Alpha", out)
        self.assertNotIn("Quantum Ferrets", out)
        self.assertIn(o.marker(), out)
        self.assertEqual(o.html(out), out, "localize_names is idempotent on a marked document")

    def test_unset_flag_means_empty_overlay_and_no_network(self):
        env = {k: v for k, v in os.environ.items() if k not in ("SHOW_REAL_TEAM_NAMES", "GITHUB_ACTIONS")}
        with patch.dict(os.environ, env, clear=True), \
                patch("requests.get", side_effect=AssertionError("network reached")):
            self.assertEqual(Overlay.from_environment().mapping, {})

    def test_runner_never_gets_real_names_even_with_the_flag(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "SHOW_REAL_TEAM_NAMES": "1"}), \
                patch("requests.get", side_effect=AssertionError("network reached")):
            self.assertEqual(Overlay.from_environment().mapping, {})

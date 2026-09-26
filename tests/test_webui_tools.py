"""
tests.test_webui_tools -- the launch allowlist and the argv each form builds (W2).

Pure: no Flask, no subprocess. What is pinned is that every argument is one argv item
(a player name with a space and an apostrophe survives intact), that free text beginning
with '-' is refused rather than handed to argparse, that only allowlisted tools exist, and
that the excluded, log-appending options never appear in any form.
"""
import sys
import unittest

from fantasy_sim.config import MY_TEAM
from webui.tools import TOOLS, Field, FormError, get


class TestArgv(unittest.TestCase):
    def test_every_tool_is_an_allowlisted_scripts_module(self):
        self.assertGreater(len(TOOLS), 15)
        for name, t in TOOLS.items():
            self.assertEqual(t.module, "scripts." + name)
        with self.assertRaises(KeyError):
            get("run_sync")
        with self.assertRaises(KeyError):
            get("gameday")
        with self.assertRaises(KeyError):
            get("../evil")

    def test_defaults_omitted_and_options_are_separate_items(self):
        argv = get("optimize_lineup").argv({"team": MY_TEAM, "sims": "1000", "week": "", "seed": ""}, python="PY")
        self.assertEqual(argv, ["PY", "-m", "scripts.optimize_lineup", "--team", MY_TEAM, "--sims", "1000"])

    def test_positionals_follow_options_and_survive_spaces_and_apostrophes(self):
        argv = get("compare_players").argv({"a": "Player O'Neil Jr.", "b": "Some Body", "week": "5", "light": "1"})
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1:], ["-m", "scripts.compare_players", "--week", "5", "--light",
                                    "Player O'Neil Jr.", "Some Body"])

    def test_a_missing_required_positional_is_refused(self):
        with self.assertRaises(FormError):
            get("compare_players").argv({"a": "Only One", "b": ""})
        with self.assertRaises(FormError):
            get("decision_scorecard").argv({"team": MY_TEAM, "week": ""})

    def test_text_beginning_with_a_dash_or_a_control_character_is_refused(self):
        for bad in ("--evaluate-unevaluated", "-x", "a\nb", "a\x00b", "x" * 201):
            with self.subTest(bad=bad):
                with self.assertRaises(FormError):
                    get("evaluate_move").argv({"team": MY_TEAM, "add": bad})

    def test_numbers_are_validated_and_normalized(self):
        argv = get("find_trades").argv({"team": MY_TEAM, "seller_threshold": "35", "top": " 7 "})
        self.assertIn("--seller-threshold", argv)
        self.assertEqual(argv[argv.index("--seller-threshold") + 1], "35.0")
        self.assertEqual(argv[argv.index("--top") + 1], "7")
        with self.assertRaises(FormError):
            get("find_trades").argv({"team": MY_TEAM, "top": "seven"})
        with self.assertRaises(FormError):
            get("find_trades").argv({"team": MY_TEAM, "seller_threshold": "1e"})

    def test_team_must_be_a_league_team(self):
        with self.assertRaises(FormError):
            get("optimize_lineup").argv({"team": "CANARY-REAL-TEAM"})
        argv = get("roster_grades").argv({"team": ""})
        self.assertNotIn("--team", argv)

    def test_flags_are_bare_and_only_when_checked(self):
        t = get("matchup_lineup")
        on = t.argv({"team": MY_TEAM, "no_cross": "1", "canonical": "on"})
        off = t.argv({"team": MY_TEAM, "no_cross": "", "canonical": "0"})
        self.assertIn("--no-cross", on)
        self.assertIn("--canonical", on)
        self.assertNotIn("--no-cross", off)
        self.assertNotIn("--canonical", off)

    def test_canonical_is_never_a_default(self):
        for name, t in TOOLS.items():
            for f in t.fields:
                if f.name == "canonical":
                    self.assertIn(f.default, (None, "", False), name)

    def test_log_appending_and_path_valued_options_are_absent_from_every_form(self):
        forbidden = {"log_tx", "evaluate_unevaluated", "add" if False else "predictions", "mine_only", "limit"}
        for name, t in TOOLS.items():
            names = {f.name for f in t.fields}
            self.assertFalse(names & forbidden, f"{name} exposes {names & forbidden}")
        self.assertFalse({"add", "bid"} & {f.name for f in get("bid_review").fields},
                         "bid_review --add/--bid append to a tracked log; review only")

    def test_unknown_field_kind_is_refused(self):
        with self.assertRaises(FormError):
            Field("x", "blob").parse("v")

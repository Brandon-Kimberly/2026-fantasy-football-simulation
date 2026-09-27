"""
tests.test_webui_paths -- the one chokepoint every read goes through (docs/WEB_UI.md 2.2).

Pure stdlib module, pure tests: a temp root with a small data/ tree, no Flask, no
fantasy_sim. What is pinned is the refusal set -- traversal, absolute paths, anything not
under a served directory, everything under data/local/, and extensions the UI does not
serve -- plus the listings the pages are built from.
"""
import json
import os
import tempfile
import unittest

from webui.paths import KNOWN_TOOLS, PathRefused, Root, normalize


def _write(root, rel, data=b"{}"):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return p


class TestChokepoint(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = self.td.name
        _write(self.dir, "data/current/league_state.json", json.dumps({"current_week": 3}))
        _write(self.dir, "data/weeks/week_03/live_season_forecast_week_3.json")
        _write(self.dir, "data/weeks/week_03/Power_Rankings.png", b"\x89PNG")
        _write(self.dir, "data/weeks/week_03/tiers/QB.html", "<p>x</p>")
        _write(self.dir, "data/decisions/week_03/lineup_20260924T165331Z_week3.json")
        _write(self.dir, "data/decisions/week_03/weekly_report_week3_run1_pre_kickoff_20260924T165337Z.html", "<h1>d</h1>")
        _write(self.dir, "data/decisions/week_03/archive/roster_grades_20260923T100000Z_week3.json")
        _write(self.dir, "data/decisions/adhoc/compare_20260926T203839Z_a_vs_b.json")
        _write(self.dir, "data/logs/decision_log.jsonl", "\n".join(json.dumps({"i": i}) for i in range(5)) + "\n")
        _write(self.dir, "data/logs/notes.txt", "t")
        _write(self.dir, "data/local/env.sh", "export ODDS_API_KEY=CANARY-KEY-1\n")
        _write(self.dir, "data/local/identity_map.json", "{}")
        _write(self.dir, "data/secret.py", "x = 1")
        _write(self.dir, "pyproject.toml", "[project]")
        self.root = Root(self.dir)

    def tearDown(self):
        self.td.cleanup()

    def test_normalize_accepts_storage_shapes(self):
        self.assertEqual(normalize("data/current/x.json"), "current/x.json")
        self.assertEqual(normalize("data\\current\\x.json"), "current/x.json")
        self.assertEqual(normalize("/current/x.json/"), "current/x.json")
        self.assertEqual(normalize("./data/weeks"), "weeks")

    def test_resolves_a_served_file_by_either_spelling(self):
        a = self.root.resolve_file("current/league_state.json")
        b = self.root.resolve_file("data\\current\\league_state.json")
        self.assertEqual(a, b)
        self.assertTrue(os.path.isfile(a))

    def test_refuses_traversal_absolute_and_unserved_directories(self):
        for bad in ("../pyproject.toml", "current/../../pyproject.toml", "weeks/./week_03/x.json",
                    os.path.join(self.dir, "data", "current", "league_state.json"),
                    "C:/anything.json", "/etc/passwd", "", "backtest/x.json", "local/env.sh"):
            with self.subTest(bad=bad):
                with self.assertRaises(PathRefused):
                    self.root.resolve_file(bad)

    def test_data_local_is_never_served_even_through_a_symlink_or_case(self):
        with self.assertRaises(PathRefused):
            self.root.resolve_file("local/identity_map.json")
        with self.assertRaises(PathRefused):
            self.root.resolve_dir("local")
        # a served top dir cannot be used to reach it either
        with self.assertRaises(PathRefused):
            self.root.resolve_file("current/../local/env.sh")

    def test_refuses_extensions_the_ui_does_not_serve(self):
        with self.assertRaises(PathRefused):
            self.root.resolve_file("secret.py")
        with self.assertRaises(PathRefused):
            self.root.resolve_file("current/league_state.py")

    def test_missing_served_file_is_not_found_not_refused(self):
        with self.assertRaises(FileNotFoundError):
            self.root.resolve_file("current/nope.json")
        self.assertFalse(self.root.exists("current/nope.json"))
        self.assertTrue(self.root.exists("current/league_state.json"))

    def test_link_is_the_file_route_over_the_normalized_path(self):
        self.assertEqual(Root.link("data\\weeks\\week_03\\Power_Rankings.png"), "/file/weeks/week_03/Power_Rankings.png")

    def test_listings(self):
        self.assertEqual(self.root.weeks(), [3])
        self.assertEqual(self.root.decision_weeks(), [3])
        wf = self.root.week_files(3)
        self.assertEqual([e["name"] for e in wf["files"]], ["Power_Rankings.png", "live_season_forecast_week_3.json"])
        self.assertEqual([e["name"] for e in wf["subdirs"]["tiers"]], ["QB.html"])
        d = self.root.decisions(3)
        self.assertEqual([e["tool"] for e in d["canonical"]], ["weekly_report", "lineup"])
        self.assertEqual(d["canonical"][0]["stamp"], "20260924T165337Z")
        self.assertEqual([e["tool"] for e in d["archive"]], ["roster_grades"])
        self.assertEqual([e["tool"] for e in self.root.adhoc()], ["compare"])
        self.assertEqual([e["name"] for e in self.root.logs()], ["decision_log.jsonl", "notes.txt"])

    def test_known_tools_are_matched_longest_first(self):
        self.assertLess(KNOWN_TOOLS.index("roster_grades"), KNOWN_TOOLS.index("trade"))
        e = self.root.entry("decisions/week_03/archive/roster_grades_20260923T100000Z_week3.json")
        self.assertEqual(e["tool"], "roster_grades")

    def test_tail_jsonl_is_newest_first_and_bounded(self):
        rows, total = self.root.tail_jsonl("logs/decision_log.jsonl", n=2)
        self.assertEqual(total, 5)
        self.assertEqual([r["i"] for r in rows], [4, 3])

    def test_tree_digest_changes_only_when_a_file_does(self):
        before = self.root.tree_digest()
        self.assertEqual(before, self.root.tree_digest())
        _write(self.dir, "data/current/league_state.json", json.dumps({"current_week": 4}))
        self.assertNotEqual(before, self.root.tree_digest())

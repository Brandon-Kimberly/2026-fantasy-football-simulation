"""
tests.test_webui_calibration -- the two calibration reads reconciled (docs/WEB_UI_ROADMAP.md
UI-Q4; developer view only).

Two figures about the model's stated spread seemed to point opposite ways: the Accuracy page
finds this season's team-week results inside the stated 80% range more often than 80% (ranges
too wide), and docs/LUCK_LEDGER.md cites the points backtest's 80% coverage below 0.80 (ranges
too narrow). They measure different things. The Accuracy figure scores this season's quote
made days before the SAME week's games; the backtest re-runs the engine at 2025 checkpoints
(weeks 3, 6, 9, 12) and scores every later team-week, forecasts up to eleven weeks ahead, on
another season. The page says which is which, each with its sample size and the standard
error of a coverage rate at that size -- and reads the backtest's figure from its own log
(data/logs/points_backtest.jsonl, the newest line), never a copied number: the ledger's 0.654
is an older run's, and the newest logged run says otherwise.
"""
import json
import os
import tempfile
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.accuracy import backtest_read
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import visible_text
    from tests.test_webui_objects import plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

OLD = {"timestamp_utc": "2026-09-10T10:00:00+00:00", "checkpoints": [3, 6, 9, 12], "overall": {"n": 240, "cover80": 0.654}}
NEW = {"timestamp_utc": "2026-09-23T11:17:03+00:00", "checkpoints": [3, 6, 9, 12], "git_commit": "5999af8",
       "overall": {"n": 240, "cover80": 0.75, "bias": -2.786}}


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def log(self, *rows):
        with open(os.path.join(self.td.name, "data", "logs", "points_backtest.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))

    def page(self):
        st = Settings(self.root)
        st.set_mode("dev")
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return visible_text(app.test_client().get("/accuracy").get_data(as_text=True))


class TestTwoReads(Case):
    def test_the_backtest_figure_comes_from_its_newest_logged_run(self):
        self.log(OLD, NEW)
        b = backtest_read(self.root)
        self.assertEqual((b["cover80"], b["n"], b["checkpoints"]), (0.75, 240, [3, 6, 9, 12]))
        self.assertAlmostEqual(b["se"], (0.8 * 0.2 / 240) ** 0.5, places=6)

    def test_the_page_names_both_scopes(self):
        self.log(OLD, NEW)
        text = self.page()
        self.assertIn("Two readings of the stated spread", text)
        for s in ("75%", "240 team-weeks", "weeks 3, 6, 9 and 12", "the same week"):
            self.assertIn(s, text)
        self.assertNotIn("65%", text, "the older run's figure is not the one cited")

    def test_without_a_logged_backtest_the_page_says_so(self):
        self.assertIsNone(backtest_read(self.root))
        self.assertIn("no points backtest has been logged", self.page())


if __name__ == "__main__":
    unittest.main()

"""
tests.test_webui_story -- the season as a story (docs/WEB_UI_ROADMAP.md UI-H5).

The playoff-odds race shows where every team stood; it did not say why. The race now carries
numbered notes: each forecast-to-forecast step's biggest mover (UI-O3's measure: the largest
change in playoff odds, either way), and the three highest-graded moves from the decision log
(the paired simulation's playoff effect, largest either way), each on the week it was made.
A numbered marker sits on that team's line at that week, and a key under the chart says what
each number is. Both views.

The fixture: a planted week-2 forecast puts Crimson Marmots at 5.0% (86.5% at week 3), the
largest step, +81.5 -- it must sit on "wk 3". The decision log's top graded move is Rocket
Pandas' week-3 trade, +5.0.
"""
import json
import os
import re
import tempfile
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import CM, NW, plant
    from tests.test_webui_routes import TEAMS
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheStory(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        d = os.path.join(self.td.name, "data", "weeks", "week_02")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "live_season_forecast_week_2.json"), "w", encoding="utf-8") as fh:
            json.dump({t: {"current_state": {"actual_wins_banked": 1.0},
                           "forecast": {"playoff_probability_pct": {CM: 5.0, NW: 99.0}.get(t, 50.0), "playoff_standard_error": 0.5}}
                       for t in TEAMS}, fh)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_the_notes(self):
        from webui.glance import odds_race, race_story
        race = odds_race(self.root, MY_TEAM)
        notes = race_story(self.root, race)
        movers = [n for n in notes if n["kind"] == "mover"]
        self.assertEqual((movers[0]["label"], movers[0]["team"], movers[0]["delta"]), ("wk 3", CM, 81.5))
        moves = [n for n in notes if n["kind"] == "move"]
        self.assertEqual((moves[0]["label"], moves[0]["team"], moves[0]["delta"]), ("wk 3", TEAMS[2], 5.0))
        self.assertLessEqual(len(moves), 3)
        self.assertEqual([n["n"] for n in notes], list(range(1, len(notes) + 1)), "numbered in order")

    def test_the_page_marks_them_in_both_views(self):
        for mode in ("dev", "simple"):
            st = Settings(self.root)
            st.set_mode(mode)
            app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                             live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
            app.testing = True
            body = app.test_client().get("/forecasts").get_data(as_text=True)
            with self.subTest(mode=mode):
                marks = re.findall(r'<g class="note"><title>([^<]*)</title>', body)
                self.assertTrue(any("Crimson Marmots" in m and "wk 3" in m for m in marks), marks)
                text = visible_text(body)
                self.assertIn("+81.5", text)
                self.assertIn("the biggest move that step", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

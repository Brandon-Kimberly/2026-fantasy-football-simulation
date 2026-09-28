"""
tests.test_webui_wave4 -- the roadmap's fourth wave: game day and the big screen
(docs/WEB_UI_ROADMAP.md).

UI-M1, Home knows what day of the week it is. A fantasy week has phases and Home behaved
the same through all of them. Before the first kickoff it previews the game and says how
last week ended (as the league counted it -- F83); from the first kickoff the live number
leads (UI-F1); once nobody on either side has a game left the hero states the RESULT, not a
probability.
"""
import json
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui.glance import home_report
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import CB, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def kickoffs(root, when):
    p = os.path.join(root, "data", "current", "nfl_schedule.json")
    with open(p, encoding="utf-8") as fh:
        s = json.load(fh)
    s.setdefault("_meta", {}).setdefault("kickoffs", {})["3"] = [when]
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(s, fh)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPhase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)                       # week 3 current; week 2 lost as played, won re-scored
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def home(self, mode="simple"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client().get("/").get_data(as_text=True)

    def test_before_the_first_kickoff_is_the_preview(self):
        kickoffs(self.td.name, "2099-01-01T17:00:00Z")
        self.assertEqual(home_report(self.root, MY_TEAM)["phase"], "before")

    def test_after_a_kickoff_is_live(self):
        kickoffs(self.td.name, "2026-01-01T17:00:00Z")
        self.assertEqual(home_report(self.root, MY_TEAM)["phase"], "live")

    def test_last_week_as_the_league_counted_it(self):
        last = home_report(self.root, MY_TEAM)["last_result"]
        self.assertEqual((last["week"], last["opponent"], last["result"], last["rescored"]), (2, CB, "L", True))
        self.assertEqual((last["mine"], last["theirs"], last["quote"]), (148.52, 144.19, 0.71))

    def test_home_says_how_last_week_ended_in_both_views(self):
        kickoffs(self.td.name, "2099-01-01T17:00:00Z")
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                text = visible_text(self.home(mode))
                self.assertIn("Last week", text)
                self.assertIn("lost to", text)
                self.assertIn("re-scored", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

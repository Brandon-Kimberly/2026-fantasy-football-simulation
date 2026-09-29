"""
tests.test_webui_early_season -- early-season states that teach (docs/WEB_UI_ROADMAP.md UI-V8).

In week 1, before the first forecast and before any game is played, several pages were not
empty but WRONG. Found by rendering every page against this fixture on 2026-09-29:
  Home       "season outlook 0% to make the playoffs" -- a missing forecast read as 0%;
             "odds from the week-None forecast"; a standings table of "—%"
  League     "the playoff odds come from the week-? forecast"
  team page  "playoff odds —%", "title odds —%" (an empty odds dict, where Jinja's Undefined
             is "not none"), "later weeks from the week-? forecast"
  History    "No games on file yet." -- true, but not what will appear or when
  Forecasts  "No forecasts yet." -- the same
Each page now says what will appear, and from which week, in both views.

The fixture: the week-3 test tree rolled back to week 1 -- current week 1, no weekly results,
standings at zero, and no forecast of any week on disk.
"""
import json
import os
import shutil
import tempfile
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.render import slug
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

BROKEN = ("week-?", "week-None", "—%", " None", "0% to make the playoffs")


def week_one(td):
    plant(td)

    def rw(rel, fn):
        p = os.path.join(td, "data", *rel.split("/"))
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(fn(d), fh)
    rw("current/sync_manifest.json", lambda d: dict(d, current_week=1))
    rw("current/league_state.json", lambda d: dict(d, current_week=1))
    rw("current/weekly_actuals.json", lambda d: {})
    rw("current/league_standings.json",
       lambda d: {t: dict(v, h2h_wins=0, wins=0, losses=0, points_scored=0.0) for t, v in d.items()})
    shutil.rmtree(os.path.join(td, "data", "weeks"))
    os.makedirs(os.path.join(td, "data", "weeks"))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestWeekOne(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        week_one(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def text(self, path, mode):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return visible_text(r.get_data(as_text=True))

    def check(self, path, explains):
        for mode in ("dev", "simple"):
            with self.subTest(path=path, mode=mode):
                text = self.text(path, mode)
                self.assertEqual([b for b in BROKEN if b in text], [], "reads as broken")
                self.assertIn(explains, text, "says what will appear, and when")
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])

    def test_home(self):
        self.check("/", "Playoff odds appear after the first forecast")

    def test_league(self):
        self.check("/league", "after the first forecast")

    def test_the_team_page(self):
        self.check(f"/team/{slug(MY_TEAM)}", "after the first forecast")

    def test_history(self):
        self.check("/history", "once week 1 has been played")

    def test_forecasts(self):
        self.check("/forecasts", "The first forecast appears")


if __name__ == "__main__":
    unittest.main()

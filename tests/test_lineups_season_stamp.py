"""
tests.test_lineups_season_stamp -- the team page on a lineups file that carries its season.

Since 2026-09-29 the sync stamps weekly_lineups.json with "_season" (so a new season never
reads last season's lineups). webui.decision_quality.season walked every key and called .get
on the stamp's string value before checking the key was a week: every team page answered 500
on the live data. The fixtures carried no stamp, so the suite did not see it (found in the
language pass of 2026-09-30).
"""
import json
import os
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
    from tests.test_webui_objects import QF, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheStampIsNotAWeek(unittest.TestCase):
    def test_the_team_page_and_the_calls(self):
        with tempfile.TemporaryDirectory() as td:
            plant(td)
            p = os.path.join(td, "data", "current", "weekly_lineups.json")
            doc = {}
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    doc = json.load(fh)
            doc["_season"] = "2026"
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            root = Root(td)
            from webui.decision_quality import season
            season(root, QF)                                   # must not raise
            st = Settings(root)
            for mode in ("dev", "simple"):
                st.set_mode(mode)
                app = create_app(root, runner=FakeRunner(), csrf_token="tok", settings=st,
                                 live=LiveBoard(root, MY_TEAM, league_id=None, fetch=None))
                app.testing = True
                self.assertEqual(app.test_client().get(f"/team/{slug(QF)}").status_code, 200, mode)


if __name__ == "__main__":
    unittest.main()

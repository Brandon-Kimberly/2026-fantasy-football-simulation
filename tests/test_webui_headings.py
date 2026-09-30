"""
tests.test_webui_headings -- every page names itself with one <h1> (post-roadmap audit,
2026-09-29).

The crawl found Home and the TV view with none: Home opens on the matchup hero and the TV
view on its scoreboard, so a screen reader had no page heading to land on. Each now carries one
h1, visually hidden so neither design changes, in both views.
"""
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
    from tests.test_webui_objects import plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestOneHeading(unittest.TestCase):
    def test_home_and_the_tv_view(self):
        with tempfile.TemporaryDirectory() as td:
            plant(td)
            root = Root(td)
            for mode in ("dev", "simple"):
                st = Settings(root)
                st.set_mode(mode)
                app = create_app(root, runner=FakeRunner(), csrf_token="tok", settings=st,
                                 live=LiveBoard(root, MY_TEAM, league_id=None, fetch=None))
                app.testing = True
                for path in ("/", "/gameday"):
                    with self.subTest(mode=mode, path=path):
                        body = app.test_client().get(path).get_data(as_text=True)
                        heads = re.findall(r"<h1\b[^>]*>(.*?)</h1>", body, re.S)
                        self.assertEqual(len(heads), 1, heads)
                        self.assertTrue(re.sub(r"<[^>]+>", "", heads[0]).strip())


if __name__ == "__main__":
    unittest.main()

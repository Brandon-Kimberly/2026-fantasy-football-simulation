"""Two regressions found reviewing work done outside these sessions (2026-10-02).

  * A third-party script on every page. Commit a29a7f9 added Chart.js from a CDN to base.html --
    unpinned (`npm/chart.js`, whatever the latest is), and used by nothing. A third-party script
    runs with the page's full access: on the owner's pages, which carry the real-name overlay, and
    on the public site, whose visitors would each fetch it. Pages load scripts from this site only
    (webui/assets, UI-E2); the one outside resource is the Google Fonts stylesheet.
  * The job bar streaming on every page. The same commit turned the job bar's watchJob from
    polling to the event stream, undoing an audit fix of 2026-09-29 (a browser allows six
    connections per host; a stream held by every open tab stalls page loads). That one needs no
    new test: tests.test_webui_browser.TestTheJobBarPolls already pins it in a real browser, and
    it went red on that commit.
Written before the fix.
"""
import os
import re
import tempfile
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_objects import MY_TEAM, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = "https://fonts.googleapis.com/"


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestNoThirdPartyScripts(unittest.TestCase):
    def test_every_script_and_stylesheet_comes_from_this_site(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        plant(td.name)
        root = Root(td.name)
        for mode in ("simple", "dev"):
            st = Settings(root)
            st.set_mode(mode)
            app = create_app(root, runner=FakeRunner(), csrf_token="tok", settings=st,
                             live=LiveBoard(root, MY_TEAM, league_id=None, fetch=None))
            app.testing = True
            c = app.test_client()
            for path in ("/", "/league", "/players", "/playoffs", "/history", "/matchups", "/decisions"):
                with self.subTest(mode=mode, path=path):
                    html = c.get(path).get_data(as_text=True)
                    for src in re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', html):
                        self.assertTrue(src.startswith("/") and not src.startswith("//"), f"third-party script {src}")
                    for href in re.findall(r'<link rel="stylesheet" href="([^"]+)"', html):
                        self.assertTrue(href.startswith("/") or href.startswith(FONTS), f"third-party stylesheet {href}")


if __name__ == "__main__":
    unittest.main()

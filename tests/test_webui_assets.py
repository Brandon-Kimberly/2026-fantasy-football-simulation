"""UI-E2, revived for the public site (owner request 2026-09-30): shared, cacheable assets.

UI-E2 was deferred because a local server does not feel the weight. The public site does:
its first build was 455 MB, and every page carried the same ~63 KB stylesheet and ~30 KB
script inline. Pinned here:
  * every page links one stylesheet and one script (plus the table script shared with the
    reports) at /assets/<name>.<content hash>.<ext>, and no longer carries them inline --
    only the few lines that depend on the page stay inline (the palette, the site address);
  * an asset is served with a year's immutable caching, a page is still never cached, and a
    name that is not the current hash is a 404 -- so a changed file gets a new name;
  * the README screenshot guard covers the assets, or a CSS change would escape it;
  * the public export publishes the assets and links them under the site's base.
Written before the code.
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
    from tests.test_webui_objects import MY_TEAM, QF, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_MARK = "--plane:#f9f9f7"            # the first token of the shared stylesheet
JS_MARK = "window.watchJob = function"  # a function of the shared script
ASSET = re.compile(r'/assets/(site|table)\.[0-9a-f]{10}\.(css|js)')


class TestNames(unittest.TestCase):
    def test_the_name_carries_the_content_hash(self):
        from webui import assets
        a, b = assets.hashed_name("site.css", b"a {}"), assets.hashed_name("site.css", b"b {}")
        self.assertNotEqual(a, b)
        self.assertEqual(a, assets.hashed_name("site.css", b"a {}"))
        self.assertRegex(a, r"^site\.[0-9a-f]{10}\.css$")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestServed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from webui.render import slug
        cls.td = tempfile.TemporaryDirectory()
        plant(cls.td.name)
        cls.root = Root(cls.td.name)
        cls.paths = ["/", "/league", "/players", f"/team/{slug(QF)}", "/playoffs", "/history", "/matchups"]

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def client(self, mode="simple"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def test_every_page_links_the_shared_assets_and_carries_none_of_them(self):
        for mode in ("simple", "dev"):
            c = self.client(mode)
            for path in self.paths:
                with self.subTest(mode=mode, path=path):
                    html = c.get(path).get_data(as_text=True)
                    self.assertRegex(html, r'<link rel="stylesheet" href="/assets/site\.[0-9a-f]{10}\.css">')
                    self.assertRegex(html, r'<script src="/assets/site\.[0-9a-f]{10}\.js"></script>')
                    self.assertRegex(html, r'<script src="/assets/table\.[0-9a-f]{10}\.js"></script>')
                    self.assertFalse(CSS_MARK in html, "the stylesheet is still inline")
                    self.assertFalse(JS_MARK in html, "the script is still inline")
                    self.assertTrue("window.PALETTE =" in html, "the page's own data stays inline")

    def test_a_plain_page_is_light(self):
        html = self.client().get("/league").get_data(as_text=True)
        inline = sum(len(b) for b in re.findall(r"<(?:style|script)\b[^>]*>.*?</(?:style|script)>", html, re.S))
        self.assertLess(inline, 12 * 1024, f"{inline} bytes of inline style and script")

    def test_assets_are_cached_for_a_year_and_pages_never(self):
        c = self.client()
        page = c.get("/league")
        self.assertEqual(page.headers.get("Cache-Control"), "no-store")
        urls = sorted(set(m.group(0) for m in ASSET.finditer(page.get_data(as_text=True))))
        self.assertEqual(len(urls), 3, urls)
        for url in urls:
            with self.subTest(url=url):
                r = c.get(url)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers.get("Cache-Control"), "public, max-age=31536000, immutable")
                self.assertIn(r.mimetype, ("text/css",) if url.endswith(".css") else ("text/javascript", "application/javascript"))
                body = r.get_data(as_text=True)
                if "/site." in url:
                    self.assertIn(CSS_MARK if url.endswith(".css") else JS_MARK, body)

    def test_a_stale_or_made_up_name_is_not_found(self):
        c = self.client()
        for url in ("/assets/site.0000000000.css", "/assets/site.css", "/assets/../app.py", "/assets/nothing.1234567890.js"):
            with self.subTest(url=url):
                self.assertEqual(c.get(url).status_code, 404)


class TestTheScreenshotGuardSeesTheAssets(unittest.TestCase):
    def test_a_stylesheet_change_changes_the_fingerprint(self):
        from scripts.readme_shots import ui_hash
        with tempfile.TemporaryDirectory() as tmp:
            for rel, body in (("webui/templates/a.html", "<p>"), ("webui/render.py", "x = 1"),
                              ("webui/assets/site.css", "a { color: red }")):
                os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
                with open(os.path.join(tmp, rel), "w", encoding="utf-8") as fh:
                    fh.write(body)
            before = ui_hash(tmp)
            with open(os.path.join(tmp, "webui", "assets", "site.css"), "w", encoding="utf-8") as fh:
                fh.write("a { color: blue }")
            self.assertNotEqual(before, ui_hash(tmp))


if __name__ == "__main__":
    unittest.main()

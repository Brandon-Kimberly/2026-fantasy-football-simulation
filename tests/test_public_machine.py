"""The playoff machine on the public site, in the browser (owner request 2026-09-30).

The public site is static: it cannot run webui.outcomes per request, so its machine offered only
the presets, one page each. This gives visitors the whole machine: the export publishes the
forecast's simulated seasons once (playoffs/outcomes.json), the Playoffs page carries the picker,
and webui/assets/machine.js counts the matching seasons in the browser -- the same count as
webui.outcomes.conditional, the same 200-season refusal, the same standard error.

Two implementations of one number is the risk, so the browser test is a parity test: it picks
results in a real browser on the exported site and requires every team's playoff and title share,
and the count behind them, to equal what webui.outcomes computes in Python for the same picks.
Written before the code.
"""
import functools
import http.server
import json
import os
import re
import tempfile
import threading
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_objects import MY_TEAM, plant
    from tests.test_webui_outcomes import plant_export
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
try:
    from playwright.sync_api import sync_playwright
    from tests.test_webui_browser import launch
    HAS_PW = True
except ImportError:
    HAS_PW = False

BASE = "/syndicate-football"


def _export():
    from webui.static_site import export
    td = tempfile.TemporaryDirectory()
    plant(td.name)
    plant_export(td.name)          # 1,000 seasons, weeks 3-14; team 0 wins its week-3 game in 600
    out = tempfile.mkdtemp()
    export(Root(td.name), out, BASE, max_pages=250)
    return td, out


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td, cls.out = _export()
        with open(os.path.join(cls.out, "playoffs", "index.html"), encoding="utf-8") as fh:
            cls.page = fh.read()

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_the_seasons_are_published_once(self):
        with open(os.path.join(self.out, "playoffs", "outcomes.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(len(doc["seasons"]), 1000)
        self.assertEqual(doc["min"], 200)
        self.assertEqual(doc["spots"], 4)
        self.assertEqual(len(doc["teams"]), 8)

    def test_the_page_carries_the_picker_and_the_script(self):
        self.assertIn(f'<form id="pm-static" data-src="{BASE}/playoffs/outcomes.json"', self.page)
        self.assertIn('name="g3.0"', self.page)
        self.assertRegex(self.page, r'<script src="' + re.escape(BASE) + r'/assets/machine\.[0-9a-f]{10}\.js"></script>')
        self.assertNotIn('<form id="pm"', self.page, "the server's form stays off the public site")
        self.assertRegex(self.page, r'<tr[^>]* data-team="[^"]+"')

    def test_the_local_ui_keeps_its_server_machine(self):
        root = Root(self.td.name)
        st = Settings(root)
        st.set_mode("simple")
        app = create_app(root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        c = app.test_client()
        html = c.get("/playoffs").get_data(as_text=True)
        self.assertIn('<form id="pm"', html)
        self.assertNotIn("pm-static", html)
        self.assertNotIn("/machine.", html)
        self.assertEqual(c.get("/playoffs/outcomes.json").status_code, 404, "only the public export serves the seasons")


class TestTheRewriterLeavesDataAttributesAlone(unittest.TestCase):
    """Found building the picker: the exporter's link pattern matched `src=` inside `data-src=`
    (a word boundary sits after the hyphen) and rewrote a script's data address as a page link --
    base twice, and a trailing slash. Only real href / src / action attributes are links."""

    def test_data_attributes_are_not_links(self):
        from webui.static_site import _rewrite
        html = '<form id="f" data-src="/b/playoffs/outcomes.json" data-href="/league"><a data-href="/x" href="/league">L</a></form>'
        out = _rewrite(html, "/b", set(), "/playoffs")
        self.assertIn('data-src="/b/playoffs/outcomes.json"', out)
        self.assertIn('<form id="f" data-src="/b/playoffs/outcomes.json" data-href="/league">', out)
        self.assertIn('data-href="/x" href="/b/league/"', out)


class _Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        if path.startswith(BASE):
            path = path[len(BASE):] or "/"
        return super().translate_path(path)

    def log_message(self, *a):
        pass


@unittest.skipUnless(HAS_FLASK and HAS_PW, "flask or playwright not installed")
class TestTheBrowserAgreesWithPython(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = launch(cls.pw)
        if cls.browser is None:
            cls.pw.stop()
            raise unittest.SkipTest("no browser Playwright can launch")
        cls.td, cls.out = _export()
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=cls.out))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}{BASE}/playoffs/"
        from webui import outcomes
        with open(os.path.join(cls.out, "playoffs", "outcomes.json"), encoding="utf-8") as fh:
            cls.o = outcomes.parse(json.load(fh))

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.browser.close()
        cls.pw.stop()
        cls.td.cleanup()

    def read(self, page):
        res = page.locator("#pm-result")
        rows = page.eval_on_selector_all("#pm-result tr[data-team]", "rs => rs.map(r => [r.dataset.team, r.dataset.playoff, r.dataset.champ])")
        return int(res.get_attribute("data-n")), res.get_attribute("data-refused"), rows

    def check(self, page, picks):
        from webui import outcomes
        want = outcomes.conditional(self.o, picks)
        n, refused, rows = self.read(page)
        self.assertEqual(n, want["n"])
        self.assertEqual(refused == "1", want["refused"])
        self.assertEqual(len(rows), 8)
        for team, p, c in rows:
            w = want["teams"][team]
            if want["refused"]:
                self.assertEqual(p, "")
                continue
            self.assertAlmostEqual(float(p), w["playoff"], places=12, msg=team)
            self.assertAlmostEqual(float(c), w["champ"], places=12, msg=team)

    def test_picks_give_the_python_numbers(self):
        page = self.browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(self.url)
        page.wait_for_selector('#pm-static[data-ready="1"]')
        self.check(page, [])
        page.click('#pm-static label.a:has(input[name="g3.0"])')          # a visitor clicks the label
        page.wait_for_function("document.getElementById('pm-result').dataset.n === '600'")
        self.check(page, [("g", 3, 0, "a")])
        page.click('#pm-static label.b:has(input[name="g3.0"])')
        page.wait_for_function("document.getElementById('pm-result').dataset.n === '400'")
        self.check(page, [("g", 3, 0, "b")])
        page.click('#pm-static summary:has-text("Week 4")')               # week 4 starts folded, as it does for a visitor
        page.click('#pm-static label.b:has(input[name="g4.0"])')          # no season has it: refused
        page.wait_for_function("document.getElementById('pm-result').dataset.refused === '1'")
        self.check(page, [("g", 3, 0, "b"), ("g", 4, 0, "b")])
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()

"""
tests.test_public_site -- the public copy of the website on GitHub Pages (owner request 2026-09-30).

The owner wants a working version of the site anyone can open, in place of the legacy sample
report, free, and without real names or images. It is a static snapshot: after each week's
official run the Pages workflow renders every public page of the simple view, from the owner's
team's point of view, into plain HTML (scripts.build_public_site / webui.static_site), checks
it for every real identity, and publishes it.

Pinned here:
  * static mode: nothing that needs a server -- live scores, alerts, Tools, Chat, the view and
    theme forms, the playoff machine's picker -- is on a public page; the page scripts know the
    site's address (window.SITE);
  * the export: every public page, at a path Pages can serve (/base/league/, query strings as
    /q/<slug>/), every internal link rewritten to it and resolving to a file, no developer page,
    no API, no image, no POST form, and no developer vocabulary;
  * the leak check: every real team name, username and league id, fetched from Sleeper at build
    time, must be absent (case-insensitive, whole words), or nothing is published;
  * the workflows: the official run keeps its data for the site, and the Pages workflow builds
    and deploys it; the old sample and its workflow are gone.
Written before the code.
"""
import os
import re
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "/syndicate-football"

try:
    import flask  # noqa: F401 -- availability probe
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import QF, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


class TestAddresses(unittest.TestCase):
    def test_every_page_has_a_path_pages_can_serve(self):
        from webui.static_site import static_path
        self.assertEqual(static_path("/", BASE), BASE + "/")
        self.assertEqual(static_path("/league", BASE), BASE + "/league/")
        self.assertEqual(static_path("/matchups/week-3", BASE), BASE + "/matchups/week-3/")
        self.assertEqual(static_path("/players?owner=all", BASE), BASE + "/players/q/owner-all/")
        self.assertEqual(static_path("/luck?team=rocket-pandas", BASE), BASE + "/luck/q/team-rocket-pandas/")
        self.assertEqual(static_path("/league#standings", BASE), BASE + "/league/#standings")

    def test_what_stays_off_the_public_site(self):
        from webui.static_site import is_public
        for p in ("/", "/league", "/team/quantum-ferrets", "/matchups/week-2", "/history", "/playoffs", "/forecasts/week-3",
                  "/luck?team=x", "/decisions", "/player/100", "/players?owner=all", "/draft", "/history/2025"):
            self.assertTrue(is_public(p), p)
        for p in ("/tools", "/tools/waiver_targets", "/chat", "/chat/abc", "/api/live", "/img/players/1.jpg", "/file/x",
                  "/jobs", "/sync", "/system", "/status", "/health", "/logs", "/records", "/results", "/mode", "/theme",
                  "/gameday", "/trade", "/accuracy", "/playoffs/result", "/manifest.webmanifest", "//evil.com/x", "https://x.com"):
            self.assertFalse(is_public(p), p)


class TestTheLeakCheck(unittest.TestCase):
    def test_identities_come_from_the_league(self):
        from webui.static_site import forbidden_identities
        payload = {"/league/L1/users": [{"display_name": "sharkboy", "metadata": {"team_name": "Made Up Four"}},
                                         {"display_name": "x", "metadata": {}}],
                   "/league/L1/rosters": [{"roster_id": 1}],
                   "/league/L1": {"name": "Friends League 2026"}}
        got = forbidden_identities(["L1"], fetch=lambda path: payload[path])
        for s in ("sharkboy", "Made Up Four", "L1", "Friends League 2026"):
            self.assertIn(s, got)
        self.assertNotIn("x", got, "a name too short to test is not a word to forbid")

    def test_a_hit_refuses_to_publish(self):
        from webui.static_site import LeakFound, leak_check
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "index.html"), "w", encoding="utf-8") as fh:
                fh.write("<p>Quantum Ferrets beat the MADE UP FOUR.</p>")
            with self.assertRaises(LeakFound) as ctx:
                leak_check(td, ["Made Up Four"])
            self.assertIn("index.html", str(ctx.exception))
            self.assertNotIn("Made Up Four", str(ctx.exception), "the report names the file, never the identity")
            leak_check(td, ["Nobody Here", "rinker"])              # a fragment inside a word is no hit


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from webui.paths import Root
        from webui.static_site import export
        cls.td = tempfile.TemporaryDirectory()
        plant(cls.td.name)
        img = os.path.join(cls.td.name, "data", "images", "players")
        os.makedirs(img, exist_ok=True)
        with open(os.path.join(img, "100.jpg"), "wb") as fh:
            fh.write(b"\xff\xd8\xff" + b"0" * 32)
        cls.out = tempfile.mkdtemp()
        cls.report = export(Root(cls.td.name), cls.out, BASE)
        cls.pages = {}
        for d, _s, fs in os.walk(cls.out):
            for f in fs:
                if f.endswith(".html"):
                    p = os.path.join(d, f)
                    with open(p, encoding="utf-8") as fh:
                        cls.pages[os.path.relpath(p, cls.out).replace(os.sep, "/")] = fh.read()

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_the_public_pages_are_there(self):
        from webui.render import slug
        for rel in ("index.html", "league/index.html", f"team/{slug(QF)}/index.html", "matchups/week-2/index.html",
                    "history/index.html", "playoffs/index.html", "forecasts/index.html", "decisions/index.html"):
            self.assertIn(rel, self.pages, rel)
        self.assertGreaterEqual(len(self.pages), 20)

    def test_nothing_private_or_dynamic_was_exported(self):
        for rel in self.pages:
            self.assertFalse(re.match(r"^(tools|chat|api|img|file|jobs|sync|system|status|health|logs|records|results|gameday|trade|accuracy)/", rel), rel)
        for f in os.listdir(self.out):
            self.assertFalse(f.lower().endswith((".jpg", ".png", ".json")), f)

    def test_every_link_resolves_and_none_escapes_the_site(self):
        for rel, html in self.pages.items():
            for attr, url in re.findall(r'\b(href|src|action)="([^"]*)"', html):
                if url.startswith(("http://", "https://", "#", "mailto:", "data:", "javascript:")) or url == "":
                    continue
                with self.subTest(page=rel, url=url):
                    self.assertTrue(url.startswith(BASE + "/"), f"{attr}={url} is not on the site")
                    path = url[len(BASE):].split("#")[0]
                    target = os.path.join(self.out, *[p for p in path.split("/") if p], "index.html")
                    self.assertTrue(os.path.exists(target), f"{url} has no page")

    def test_no_server_features_and_no_images(self):
        for rel, html in self.pages.items():
            with self.subTest(page=rel):
                self.assertNotIn("/img/", html)
                self.assertNotRegex(html, r'<form[^>]*method="post"', "nothing to post to")
                self.assertNotIn('id="live-body"', html)
                self.assertNotIn('id="alertrow"', html)
                self.assertNotIn('id="chat-input"', html)
                self.assertIn("window.SITE", html)
        home = self.pages["index.html"]
        self.assertIn(QF, home, "the owner's team's point of view")
        self.assertNotIn(">Tools<", home)
        self.assertNotIn(">Chat<", home)
        self.assertNotIn('<form id="pm"', self.pages["playoffs/index.html"], "the picker needs a server")

    def test_plain_words_on_every_page(self):
        for rel, html in self.pages.items():
            with self.subTest(page=rel):
                self.assertEqual([t for t in DEV_TERMS if t in visible_text(html)], [])


class TestTheWorkflows(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(REPO, ".github", "workflows", name), encoding="utf-8") as fh:
            return fh.read()

    def test_the_official_run_keeps_the_data_for_the_site(self):
        wf = self.read("canonical-run.yml")
        self.assertIn("name: site-data", wf)

    def test_the_pages_workflow_builds_checks_and_deploys_the_site(self):
        wf = self.read("pages-site.yml")
        self.assertIn("workflows: [canonical-run]", wf)
        self.assertIn("python -m scripts.build_public_site", wf)
        self.assertIn("actions/deploy-pages", wf)
        self.assertIn("webui/**", wf)
        self.assertFalse(os.path.exists(os.path.join(REPO, ".github", "workflows", "pages-sample.yml")),
                         "the legacy sample report is retired")
        self.assertFalse(os.path.exists(os.path.join(REPO, "scripts", "make_sample_report.py")))


if __name__ == "__main__":
    unittest.main()

"""
tests.test_webui_ninth -- the audit's Phase 6 (docs/WEB_UI_AUDIT.md): the power features.

U11 a manual theme (system / light / dark) in the settings file, one POST route, the
dark tokens defined once for the OS preference and once for the explicit choice and kept
identical; U12 a web-app manifest and an icon so the local name installs as an app; U4
the command palette's items are the pages, tools and teams of the CURRENT view; U14 the
shortcut help; U5 a player detail route and the hover-card hook on every player cell;
U10 the Decisions awards (best and worst move of the season and of the latest week) and
per-team timelines; U13 durations per tool on the Jobs page; page transitions; and B13:
the count-up starts near its target. Scripts are pinned by presence, not executed.
"""
import re
import tempfile
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.glance import decisions_report
    from webui.jobs import OK
    from webui.live import LiveBoard
    from webui.settings import Settings, THEMES
    from tests.test_webui_fourth import plant as plant_fourth
    from tests.test_webui_home import enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import TEAMS, build_tree
    from tests.test_webui_sixth import TEXT_TOKENS, contrast, tokens
    from webui.paths import Root
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

BASE = "webui/templates/base.html"


def css():
    return open(BASE, encoding="utf-8").read().split("</style>")[0]


def script():
    return open(BASE, encoding="utf-8").read().split("</style>")[-1]


class TestStylesheet(unittest.TestCase):
    def test_dark_tokens_once_for_the_os_and_once_for_the_choice_and_identical(self):
        s = css()
        self.assertIn('@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {', s)
        media = tokens(s.split("@media (prefers-color-scheme: dark)", 1)[1].split("} }", 1)[0])
        chosen = tokens(s.split(':root[data-theme="dark"] {', 1)[1].split("}", 1)[0])
        self.assertEqual(media, chosen, "the two dark blocks must carry the same tokens")
        self.assertGreater(len(chosen), 10)
        for name in TEXT_TOKENS:
            for ground in ("plane", "surface"):
                self.assertGreaterEqual(contrast(chosen[name], chosen[ground]), 4.5, name)
        self.assertIn(':root[data-theme="dark"] { color-scheme: dark;', s)
        self.assertIn(':root[data-theme="light"] { color-scheme: light;', s)

    def test_page_transitions_honour_reduced_motion(self):
        s = css()
        self.assertIn("@view-transition { navigation: auto; }", s)
        self.assertIn("@view-transition { navigation: none; }", s.split("prefers-reduced-motion", 1)[1])

    def test_the_hidden_attribute_beats_any_display_a_class_gives(self):
        """Scripts here hide rows by setting `hidden`, and the browser's own rule for that
        is weaker than any class that sets a display -- so a filtered row stayed on screen.
        One global rule instead of the four per-component patches that preceded it."""
        self.assertIn("[hidden] { display: none !important; }", css())

    def test_count_up_starts_near_its_target(self):                                  # B13
        js = script()
        self.assertIn("0.9 * target", js)
        self.assertIn("dur = 400", js)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        plant_fourth(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def client(self, mode="dev", runner=None, theme=None):
        st = Settings(self.root)
        st.set_mode(mode)
        st.set_theme(theme or "system")
        app = create_app(self.root, runner=runner or FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def get(self, path, **kw):
        r = self.client(**kw).get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)

    # ---- U11 theme
    def test_theme_setting_and_route(self):
        st = Settings(self.root)
        self.assertEqual(THEMES, ("system", "light", "dark"))
        self.assertEqual(st.theme, "system")
        self.assertEqual(st.set_theme("dark"), "dark")
        self.assertEqual(Settings(self.root).theme, "dark")
        with self.assertRaises(ValueError):
            st.set_theme("sepia")
        c = self.client()
        self.assertEqual(c.post("/theme", data={"theme": "light"}).status_code, 403)
        r = c.post("/theme", data={"_csrf": "tok", "theme": "light", "back": "/league"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/league"))
        self.assertEqual(Settings(self.root).theme, "light")
        self.assertEqual(c.post("/theme", data={"_csrf": "tok", "theme": "sepia"}).status_code, 400)
        for mode in ("dev", "simple"):
            self.assertIn('<html lang="en" data-theme="dark">', self.get("/", mode=mode, theme="dark"))
            self.assertIn('<html lang="en">', self.get("/", mode=mode, theme="system"))
            self.assertIn('action="/theme"', self.get("/league", mode=mode))

    # ---- U12 manifest
    def test_manifest_and_icon_in_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                c = self.client(mode=mode)
                r = c.get("/manifest.webmanifest")
                self.assertEqual(r.status_code, 200)
                self.assertIn("manifest", r.headers["Content-Type"])
                m = r.get_json(force=True)
                self.assertEqual((m["start_url"], m["display"]), ("/", "standalone"))
                self.assertTrue(m["name"] and m["short_name"])
                self.assertEqual(m["icons"][0]["src"], "/icon.svg")
                icon = c.get("/icon.svg")
                self.assertEqual(icon.status_code, 200)
                self.assertIn("image/svg+xml", icon.headers["Content-Type"])
                self.assertIn("<svg", icon.get_data(as_text=True))
                body = self.get("/", mode=mode)
                self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', body)
                self.assertIn('<meta name="theme-color"', body)

    # ---- U4 + U14 palette and shortcuts
    def test_palette_lists_only_what_the_view_has(self):
        dev = self.get("/", mode="dev")
        self.assertIn('id="pal"', dev)
        self.assertIn('id="keys"', dev)
        self.assertIn("g h", dev)
        items = re.search(r"var PALETTE = (\[.*?\]);", dev).group(1)
        self.assertIn('"/system"', items)
        self.assertIn('"/tools/compare_players"', items)
        self.assertIn('"/tools/run_simulation"', items)
        self.assertIn('"/team/', items)                                            # the teams, to their pages (UI-A1)
        simple = self.get("/", mode="simple")
        items = re.search(r"var PALETTE = (\[.*?\]);", simple).group(1)
        self.assertNotIn('"/system"', items)
        self.assertNotIn('"/tools/run_simulation"', items)
        self.assertIn('"/tools/compare_players"', items)
        self.assertIn('"/gameday"', items)
        self.assertIn("compare_players?a=", script())                              # "compare A vs B"

    # ---- U5 player detail and hover cards
    def test_player_detail_route_and_hover_hook(self):
        c = self.client()
        j = c.get("/api/player?name=Player%202%20O'Neil").get_json()
        self.assertEqual((j["name"], j["pos"], j["nfl"], j["owner"]), ("Player 2 O'Neil", "QB", "GB", TEAMS[2]))
        self.assertEqual(j["mean"], 19.2)
        self.assertEqual(j["bye"], 11)
        self.assertEqual(c.get("/api/player?name=Nobody%20Here").status_code, 404)
        self.assertEqual(c.get("/api/player").status_code, 404)
        for mode in ("dev", "simple"):
            body = self.get("/league", mode=mode)
            self.assertIn('data-player="Player 0 O\'Neil"', body.replace("&#39;", "'"))
            self.assertIn('id="pcard"', body)
            self.assertIn("/api/player?name=", body)

    # ---- U10 awards and timelines
    def test_decisions_awards_and_timelines(self):
        rep = decisions_report(self.root, MY_TEAM)
        aw = rep["awards"]
        self.assertEqual((aw["best"]["team"], aw["best"]["playoff"]), (TEAMS[2], 5.0))
        self.assertEqual((aw["worst"]["team"], aw["worst"]["playoff"]), (TEAMS[1], -4.0))
        self.assertEqual(aw["week"], 3)
        self.assertEqual(aw["best_week"]["team"], TEAMS[2])
        self.assertEqual(rep["timelines"][TEAMS[1]], [2.0, -2.0])
        self.assertEqual(rep["timelines"][MY_TEAM], [3.0])
        for mode in ("dev", "simple"):
            body = self.get("/decisions", mode=mode)
            self.assertIn("Move of the season", body)
            self.assertIn("Ouch of the season", body)
            self.assertIn('class="spark"', body)                                    # the ledger's timelines

    # ---- U13 durations
    def test_every_decisions_filter_matches_something_it_can_filter_on(self):
        """A filter button whose key names no row is a dead control. Each button is either
        one of the three computed keys or a transaction type the rows actually carry."""
        import re as _re
        body = self.get("/decisions")
        keys = _re.findall(r'<button type="button" data-f="([a-z_]+)"', body)
        types = set(_re.findall(r'class="dec[^"]*" data-type="([a-z_]+)"', body))
        self.assertGreaterEqual(len(keys), 4)
        self.assertTrue(types, "the fixture must render some moves for this to mean anything")
        for k in keys:
            with self.subTest(filter=k):
                self.assertTrue(k in ("all", "mine", "moved") or k in types,
                                f"the {k!r} filter matches no row's data-type ({sorted(types)})")
        self.assertIn('data-mine="1"', body)
        self.assertIn('data-moved="1"', body)

    def test_jobs_page_charts_durations_per_tool(self):
        runner = FakeRunner()
        for i, secs in enumerate((60, 90, 75)):
            jid = runner.launch(["py", "-m", "scripts.optimize_lineup"], "optimize_lineup", label="Optimal lineup")
            runner.metas[jid].update(state=OK, started_at=f"2026-09-2{i}T00:00:00Z", finished_at=f"2026-09-2{i}T00:0{secs // 60}:{secs % 60:02d}Z", rc=0)
        body = self.get("/jobs", runner=runner)
        self.assertIn("How long each tool takes", body)
        self.assertIn('class="spark"', body)
        self.assertIn("Optimal lineup", body)


if __name__ == "__main__":
    unittest.main()

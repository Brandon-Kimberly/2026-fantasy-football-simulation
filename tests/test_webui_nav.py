"""
tests.test_webui_nav -- the season grid and the navigation (docs/WEB_UI_ROADMAP.md UI-A4, A5).

UI-A4  League gains the season on one grid: fourteen weeks by eight teams, each cell the
       opponent with the result (and the median result) once played, or the chance to win
       before; every cell links to its game on the Matchups page; the owner's row marked, the
       run-in weeks 12 to 14 shaded. Symmetric by construction: a team's opponent lists that
       team back in the same week.
UI-A5  The developer view's top bar keeps the object pages and moves the machinery -- Records,
       Jobs, Logs, System, Sync -- into one menu (Decision 6). The simple view's bar is
       unchanged. Every tool that answers a question is linked from a page where that
       question comes up, as well as from Tools. The three operations tools have no such page
       (they are about the pipeline, not the league) and live in the developer menu's System.
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
    from webui.tools import SIMPLE_TOOLS, TOOLS
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import CB, QF, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

OPS_TOOLS = ("check_freshness", "run_windows", "data_health")
OBJECT_PAGES = ("/league", "/team/quantum-ferrets", "/team/neon-walruses", "/player/100", "/matchups", "/waivers",
                "/trade?with=neon-walruses", "/playoffs", "/luck", "/decisions")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def get(self, path, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)


class TestSeasonGrid(Case):
    def test_the_grid_is_complete_and_symmetric(self):
        from webui.objects import season_grid
        g = season_grid(self.root, MY_TEAM)
        self.assertEqual(len(g["teams"]), 8)
        self.assertEqual(g["weeks"], list(range(1, 15)))
        for t in g["teams"]:
            for w in g["weeks"]:
                c = g["cells"][t][w]
                self.assertIsNotNone(c["opponent"], (t, w))
                self.assertEqual(g["cells"][c["opponent"]][w]["opponent"], t, (t, w))

    def test_a_played_cell_and_an_unplayed_one(self):
        from webui.objects import season_grid
        c = season_grid(self.root, MY_TEAM)["cells"]
        self.assertEqual((c[QF][2]["opponent"], c[QF][2]["result"], c[QF][2]["median"]), (CB, "L", "L"), "week 2 as played")
        self.assertEqual(c[QF][2]["href"], "/matchups/week-2#t-quantum-ferrets")
        self.assertIsNone(c[QF][5]["result"])

    def test_league_draws_it_and_each_cell_opens_its_game(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/league", mode)
                self.assertIn("The season, week by week", visible_text(body))
                self.assertEqual(len(re.findall(r'<td class="sg[^"]*"><a href="/matchups/week-\d+#t-', body)), 8 * 14)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in visible_text(body)], [])

    def test_the_game_the_cell_links_to_is_anchored(self):
        self.assertIn('id="t-quantum-ferrets"', self.get("/matchups/week-2"))


class TestNavigation(Case):
    def nav(self, body):
        top = re.search(r'<nav class="tabs"[^>]*>(.*?)</nav>', body, re.S).group(1)
        return re.findall(r'<a href="(/[^"]*)"', re.sub(r"<details.*?</details>", "", top, flags=re.S))

    def test_the_developer_bar_keeps_the_objects_and_menus_the_machinery(self):
        body = self.get("/", "dev")
        self.assertEqual(self.nav(body), ["/", "/matchups", "/league", "/playoffs", "/players", "/history",
                                          "/forecasts", "/accuracy", "/decisions", "/tools"])
        menu = re.search(r'<details class="devmenu".*?</details>', body, re.S).group(0)
        self.assertEqual(re.findall(r'<a href="(/[^"]*)"', menu), ["/records", "/jobs", "/logs", "/system", "/sync"])

    def test_the_simple_bar_is_unchanged(self):
        body = self.get("/", "simple")
        self.assertEqual(self.nav(body), ["/", "/matchups", "/league", "/playoffs", "/players", "/history",
                                          "/forecasts", "/decisions", "/tools"])
        self.assertNotIn('class="devmenu"', body)

    def test_every_question_tool_is_linked_where_its_question_arises(self):
        for mode, tools in (("dev", [t for t in TOOLS if t not in OPS_TOOLS]), ("simple", list(SIMPLE_TOOLS))):
            bodies = "".join(self.get(p, mode) for p in OBJECT_PAGES)
            missing = [t for t in tools if f'href="/tools/{t}' not in bodies]
            with self.subTest(mode=mode):
                self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()

"""
tests.test_webui_modes -- the two views (docs/WEB_UI.md W8): dev, the owner's, and simple,
the one anyone could use.

The guard that keeps "account for both modes in every change" true: every simple-view
page is rendered on a full fixture tree and scanned for the developer vocabulary --
file names, job ids, verdict codes, audit numbers, sync, the command line -- and any
hit fails. Also pinned: the setting is server-side and toggles from one POST route; the
simple view serves no dev-only page and no dev-only tool; a tool's answer (the job page)
is for everyone but shows no command, log or exit code; the simple tool form asks who
and when only; the nav differs; and dev mode is unchanged (every other test runs in it).
"""
import json
import os
import re
import tempfile
import unittest

from webui import render
from webui.paths import Root
from webui.settings import Settings

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app, DEV_ONLY_PREFIXES
    from webui.jobs import OK
    from webui.live import LiveBoard
    from webui.tools import SIMPLE_TOOLS, TOOLS
    from tests.test_webui_fourth import plant as plant_fourth
    from tests.test_webui_home import enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_render import LINEUP
    from tests.test_webui_routes import build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

# Words a manager never needs to see. Case-sensitive on purpose: 'Sync' the noun and
# 'sync' the verb are both the owner's; 'engine' too. A term is checked against the
# page's visible text, not its markup (ids, classes and script stay).
DEV_TERMS = ("data/", ".json", ".jsonl", "canonical", "VOID", "DEGRADED", "STALE", "argv", "exit code", "pid ",
             "--skip-sync", "--json", "engine", "subprocess", "localhost", "pseudonym", "overlay", "manifest",
             "backup", "sync", "Sync", "py -3.10", "scripts.", "F84", "F51", "F50", "F61", "H5", "C3", "R1", "B11",
             "job id", "raw", "the record on disk", "LOCAL VIEW", "run windows", "digest", "provenance", "roster_grades",
             "optimize_lineup", "evaluate_move", "weekly_report", "audit")
SIMPLE_PAGES = ("/", "/gameday", "/league", "/forecasts", "/forecasts/week-3", "/decisions", "/tools", "/tools/compare_players",
                "/tools/optimize_lineup", "/tools/live_matchup",
                "/records/week-3/optimal-lineup/2026-09-24-165331", "/file/decisions/week_03/lineup_20260924T165331Z_week3.json")


def visible_text(html):
    """The page as a reader sees it: no scripts, styles, tags or attributes."""
    s = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return s


class TestSettings(unittest.TestCase):
    def test_mode_is_stored_locally_and_toggles(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "data"))
            root = Root(td)
            st = Settings(root)
            self.assertEqual(st.mode, "dev")
            self.assertEqual(Settings(root, default_mode="simple").mode, "simple")
            self.assertEqual(st.toggle(), "simple")
            self.assertEqual(Settings(root).mode, "simple", "the setting is on disk, so every request agrees")
            self.assertTrue(os.path.realpath(st.path).startswith(os.path.realpath(os.path.join(td, "data", "local"))))
            with self.assertRaises(ValueError):
                st.set_mode("expert")

    def test_simplify_strips_audit_codes_only(self):
        self.assertEqual(render.simplify("ranked by P(beat opponent) (F61)"), "ranked by P(beat opponent)")
        self.assertEqual(render.simplify("both legs turn on my own score (B11)"), "both legs turn on my own score")
        self.assertEqual(render.simplify("no availability discount (F51, F84)"), "no availability discount")
        self.assertEqual(render.simplify("a Q4 7:30 clock (2 of 13)"), "a Q4 7:30 clock (2 of 13)")
        self.assertEqual(render.state_label("VOID"), "Didn't finish")
        self.assertEqual(render.state_label("OK"), "Done")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestSimpleView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        plant_fourth(cls.td.name)
        with open(os.path.join(cls.td.name, "data", "decisions", "week_03", "lineup_20260924T165331Z_week3.json"), "w", encoding="utf-8") as fh:
            json.dump(LINEUP, fh)                          # a real-shaped record, with its method note
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def client(self, mode="simple", runner=None):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=runner or FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def test_every_simple_page_is_free_of_developer_vocabulary(self):
        c = self.client("simple")
        for path in SIMPLE_PAGES:
            with self.subTest(page=path):
                r = c.get(path)
                self.assertEqual(r.status_code, 200, path)
                text = visible_text(r.get_data(as_text=True))
                hits = [t for t in DEV_TERMS if t in text]
                self.assertEqual(hits, [], f"{path} shows developer vocabulary: {hits}")

    def test_the_simple_view_serves_no_dev_only_page_and_no_dev_only_tool(self):
        c = self.client("simple")
        for path in ("/system", "/logs", "/logs/decision-log", "/sync", "/records", "/records/week-3", "/results", "/jobs", "/health", "/accuracy"):
            with self.subTest(page=path):
                r = c.get(path)
                self.assertEqual(r.status_code, 404, path)
                self.assertIn("developer view", r.get_data(as_text=True))
        for name in TOOLS:
            r = c.get(f"/tools/{name}")
            self.assertEqual(r.status_code, 200 if name in SIMPLE_TOOLS else 404, name)
        self.assertEqual(c.get("/file/decisions/week_03/lineup_20260924T165331Z_week3.json?raw=1").status_code, 404)
        self.assertEqual(c.get("/file/weeks/week_03/Power_Rankings.png").status_code, 200, "charts still serve")
        self.assertEqual(c.get("/api/live").status_code, 200, "the live panel still works")
        for pfx in DEV_ONLY_PREFIXES:
            self.assertIn(pfx, ("/system", "/status", "/logs", "/sync", "/records", "/results", "/health", "/jobs", "/accuracy"))

    def test_the_simple_nav_and_footer_toggle(self):
        body = self.client("simple").get("/").get_data(as_text=True)
        for href in ("/league", "/forecasts", "/decisions", "/tools"):
            self.assertIn(f'href="{href}"', body)
        for href in ("/records", "/jobs", "/logs", "/system", "/sync"):
            self.assertNotIn(f'<a href="{href}"', body)
        self.assertIn("Switch to the developer view", body)
        self.assertNotIn("LOCAL VIEW", body)
        dev = self.client("dev").get("/").get_data(as_text=True)
        self.assertIn('href="/sync"', dev)
        self.assertIn("Switch to the simple view", dev)

    def test_mode_toggles_from_one_post_route_with_csrf(self):
        c = self.client("simple")
        self.assertEqual(c.post("/mode", data={"mode": "dev"}).status_code, 403)
        r = c.post("/mode", data={"_csrf": "tok", "mode": "dev", "back": "/league"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/league"))
        self.assertEqual(Settings(self.root).mode, "dev")
        self.assertEqual(c.get("/system").status_code, 200, "now the developer view")
        c.post("/mode", data={"_csrf": "tok"})                 # no mode named: toggle
        self.assertEqual(Settings(self.root).mode, "simple")
        r = c.post("/mode", data={"_csrf": "tok", "mode": "dev", "back": "//evil.example"})
        self.assertTrue(r.headers["Location"].endswith("/"), "an off-site back is ignored")

    def test_the_simple_tool_form_asks_who_and_when_only(self):
        body = self.client("simple").get("/tools/compare_players").get_data(as_text=True)
        self.assertIn('name="a"', body)
        self.assertIn('name="week"', body)
        self.assertNotIn('name="sims"', body)
        self.assertNotIn('name="seed"', body)
        self.assertNotIn('name="light"', body)
        self.assertNotIn('id="cmd"', body)
        self.assertIn("> Ask</button>", body)
        dev = self.client("dev").get("/tools/compare_players").get_data(as_text=True)
        self.assertIn('name="sims"', dev)
        self.assertIn('id="cmd"', dev)

    def test_a_tools_answer_is_for_everyone_but_shows_no_machinery(self):
        runner = FakeRunner()
        jid = runner.launch(["py", "-m", "scripts.optimize_lineup", "--team", MY_TEAM], "optimize_lineup")
        runner.metas[jid].update(state=OK, finished_at="2026-09-26T00:00:03Z", rc=0,
                                 record="/file/decisions/week_03/lineup_20260924T165331Z_week3.json")
        body = self.client("simple", runner=runner).get(render.job_url({"id": jid})).get_data(as_text=True)
        self.assertEqual(self.client("simple", runner=runner).get(render.job_url({"id": jid})).status_code, 200)
        self.assertIn("Done", body)
        self.assertIn("Optimal lineup", body)
        self.assertIn("Patrick Mahomes", body)
        text = visible_text(body)
        hits = [t for t in DEV_TERMS if t in text]
        self.assertEqual(hits, [], hits)
        self.assertNotIn("What the tool printed", body)
        self.assertNotIn("exit code", body)
        dev = self.client("dev", runner=runner).get(render.job_url({"id": jid})).get_data(as_text=True)
        self.assertIn("The command, and where it ran", dev)

    def test_the_vibrant_layer_renders_in_both_views_and_respects_reduced_motion(self):
        for mode in ("dev", "simple"):
            body = self.client(mode).get("/").get_data(as_text=True)
            self.assertIn('id="vizg-turf"', body, "the chart gradient defs every page carries")
            self.assertIn('class="rank r1"', body, "medal ranks in the standings")
            self.assertIn("--g-brand:", body)
            self.assertIn("@keyframes draw", body)
            self.assertIn("prefers-reduced-motion: reduce", body)
            self.assertIn('id="ringg"', body)
        league = self.client("simple").get("/league").get_data(as_text=True)
        self.assertIn('class="rank r3"', league)

    def test_dev_mode_is_the_default_and_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            build_tree(td)
            app = create_app(Root(td), runner=FakeRunner(), csrf_token="tok",
                             live=LiveBoard(Root(td), MY_TEAM, league_id=None, fetch=None))
            app.testing = True
            c = app.test_client()
            self.assertEqual(c.get("/system").status_code, 200)
            self.assertIn("dev", c.get("/").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

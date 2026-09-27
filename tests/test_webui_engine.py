"""
tests.test_webui_engine -- the two engine entry points through the launcher (W3).

The runner and the lock are pinned in tests.test_webui_jobs; here what is pinned is
W3's contract: the report ALWAYS carries --skip-sync (the UI never syncs), --canonical only
when ticked, run_simulation takes no options, run_sync is not launchable, a STALE tree is
refused before any launch, the launch form shows the freshness verdict, and reloading a
job page never re-launches. Skips cleanly without Flask.
"""
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui.paths import Root
from webui.tools import ENGINE, TOOLS, get

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


class TestEngineArgv(unittest.TestCase):
    def test_the_engine_registry_is_exactly_the_two_entry_points_and_sync_is_absent(self):
        self.assertEqual(sorted(ENGINE), ["run_simulation", "weekly_report"])
        self.assertNotIn("run_sync", ENGINE)
        self.assertNotIn("run_sync", TOOLS)
        with self.assertRaises(KeyError):
            get("run_sync")
        for t in ENGINE.values():
            self.assertTrue(t.engine and t.heavy)

    def test_the_report_always_skips_sync_and_is_canonical_only_when_ticked(self):
        t = get("weekly_report")
        plain = t.argv({"team": MY_TEAM, "sims": "5000"}, python="PY")
        self.assertEqual(plain[:4], ["PY", "-m", "scripts.weekly_report", "--skip-sync"])
        self.assertNotIn("--canonical", plain)
        ticked = t.argv({"team": MY_TEAM, "canonical": "1", "embed": "on", "full": "1", "evaluate": "3"}, python="PY")
        for flag in ("--skip-sync", "--canonical", "--embed", "--full"):
            self.assertIn(flag, ticked)
        self.assertEqual(ticked.count("--skip-sync"), 1)
        self.assertEqual(ticked[ticked.index("--evaluate") + 1], "3")

    def test_run_simulation_takes_no_options(self):
        self.assertEqual(get("run_simulation").argv({}, python="PY"), ["PY", "-m", "scripts.run_simulation"])
        self.assertEqual(get("run_simulation").argv({"anything": "ignored"}, python="PY"),
                         ["PY", "-m", "scripts.run_simulation"])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestEngineRoutes(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)                       # a fresh, OK week-3 tree
        self.runner = FakeRunner()
        app = create_app(Root(self.td.name), runner=self.runner, csrf_token="tok")
        app.testing = True
        self.c = app.test_client()

    def tearDown(self):
        self.td.cleanup()

    def test_engine_tools_are_listed_under_their_own_heading(self):
        body = self.c.get("/tools").get_data(as_text=True)
        self.assertIn("Engine runs", body)
        self.assertIn("/tools/weekly_report", body)
        self.assertIn("/tools/run_simulation", body)
        self.assertNotIn("/tools/run_sync", body)

    def test_the_form_shows_the_freshness_verdict_and_windows(self):
        body = self.c.get("/tools/weekly_report").get_data(as_text=True)
        self.assertIn("Data on disk", body)
        self.assertIn('pill DEGRADED', body)      # the fixture manifest carries a tolerated failure: DEGRADED, launchable
        self.assertIn("run a sync to persist them", body)     # no kickoffs in the fixture schedule
        self.assertNotIn('name="canonical" value="1" checked', body)

    def test_a_non_stale_tree_launches_the_report_with_skip_sync(self):
        r = self.c.post("/tools/weekly_report", data={"_csrf": "tok", "team": MY_TEAM, "sims": "5000"})
        self.assertEqual(r.status_code, 302)
        argv, tool, _label = self.runner.launches[0]
        self.assertEqual(tool, "weekly_report")
        self.assertIn("--skip-sync", argv)
        self.assertNotIn("--canonical", argv)
        r = self.c.post("/tools/run_simulation", data={"_csrf": "tok"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.runner.launches[1][0][1:], ["-m", "scripts.run_simulation"])

    def test_a_stale_tree_is_refused_before_any_launch(self):
        with tempfile.TemporaryDirectory() as stale:
            os.makedirs(os.path.join(stale, "data", "current"))
            with open(os.path.join(stale, "data", "current", "league_state.json"), "w", encoding="utf-8") as fh:
                fh.write('{"current_week": 3}')
            runner = FakeRunner()
            app = create_app(Root(stale), runner=runner, csrf_token="tok")
            app.testing = True
            c = app.test_client()
            self.assertIn("pill STALE", c.get("/tools/weekly_report").get_data(as_text=True))
            r = c.post("/tools/weekly_report", data={"_csrf": "tok", "team": MY_TEAM})
            self.assertEqual(r.status_code, 409)
            self.assertIn("STALE", r.get_data(as_text=True))
            self.assertIn("scripts.run_sync", r.get_data(as_text=True))
            self.assertEqual(runner.launches, [])
            # a non-engine tool is not gated on freshness
            self.assertEqual(c.post("/tools/roster_calendar", data={"_csrf": "tok", "team": MY_TEAM}).status_code, 302)

    def test_reloading_a_job_page_never_relaunches(self):
        r = self.c.post("/tools/run_simulation", data={"_csrf": "tok"})
        loc = r.headers["Location"]
        for _ in range(3):
            self.assertEqual(self.c.get(loc).status_code, 200)
        self.assertEqual(len(self.runner.launches), 1)

"""
tests.test_webui_launch -- the launcher routes over a fake runner (W2).

The runner's own behaviour is pinned in tests.test_webui_jobs; here a FakeRunner records
what the routes ask of it, so what is pinned is the HTTP contract: the CSRF token on every
POST, a form error re-rendered rather than launched, a refused launch as 409 with the
running job named, unknown tools as 404, pseudonyms in every argv, and a read-only tree.
Skips cleanly without Flask.
"""
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui.jobs import OK, RUNNING, JobRefused
from webui.names import Overlay
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


class FakeRunner:
    def __init__(self, busy=None):
        self.launches, self.metas, self.busy = [], {}, busy
        self.cancelled = []

    def launch(self, argv, tool, label=None, extra=None):
        if self.busy:
            raise JobRefused(f"busy: job {self.busy['id']} is still running")
        self.launches.append((argv, tool, label))
        jid = f"20260926T000000Z_{len(self.launches):06x}_{tool}"
        self.metas[jid] = {"id": jid, "tool": tool, "label": label or tool, "state": RUNNING,
                           "started_at": "2026-09-26T00:00:00Z", "finished_at": None, "rc": None,
                           "pid": 1, "python": argv[0], "args": argv[1:], "cwd": "root", "record": None, "note": None,
                           **(extra or {})}
        return jid

    def typical_seconds(self, tool):
        return None

    def average_seconds(self, tool, n=5):
        return None, 0

    def read(self, jid):
        return self.metas.get(jid)

    def list(self):
        return list(self.metas.values())

    def current(self):
        return self.busy

    def tail(self, jid, chars=6000):
        return "fake output for " + jid, 20

    def log_text(self, jid):
        return "fake log " + jid

    def cancel(self, jid):
        self.cancelled.append(jid)
        return True


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestLauncher(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.td.name, "data", "current"))
        self.root = Root(self.td.name)
        self.runner = FakeRunner()
        app = create_app(self.root, runner=self.runner, csrf_token="tok-123")
        app.testing = True
        self.c = app.test_client()

    def tearDown(self):
        self.td.cleanup()

    def test_tools_index_lists_every_allowlisted_tool_and_nothing_else(self):
        body = self.c.get("/tools").get_data(as_text=True)
        for name in ("optimize_lineup", "compare_players", "evaluate_trade", "check_freshness"):
            self.assertIn(f"/tools/{name}", body)
        for name in ("run_sync", "gameday", "migrate_identity", "localize_reports", "scan_real_names"):
            self.assertNotIn(f"/tools/{name}", body)     # the engine pair is W3's, listed separately
        self.assertEqual(self.c.get("/tools/run_sync").status_code, 404)

    def test_form_carries_the_token_and_an_unchecked_canonical_box(self):
        body = self.c.get("/tools/optimize_lineup").get_data(as_text=True)
        self.assertIn('name="_csrf" value="tok-123"', body)
        self.assertIn('name="canonical"', body)
        self.assertNotIn('name="canonical" value="1" checked', body)
        self.assertIn(f'<option value="{MY_TEAM}" selected', body)

    def test_post_without_the_token_is_refused_and_launches_nothing(self):
        r = self.c.post("/tools/optimize_lineup", data={"team": MY_TEAM})
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/tools/optimize_lineup", data={"team": MY_TEAM, "_csrf": "wrong"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.runner.launches, [])

    def test_a_valid_post_launches_the_exact_argv_and_redirects_to_the_job(self):
        r = self.c.post("/tools/compare_players",
                        data={"_csrf": "tok-123", "a": "Player O'Neil", "b": "Some Body", "week": "5", "light": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertRegex(r.headers["Location"], r"/jobs/20260926T000000Z_[0-9a-f]{6}_compare_players$")
        argv, tool, label = self.runner.launches[0]
        self.assertEqual(tool, "compare_players")
        self.assertEqual(argv[1:], ["-m", "scripts.compare_players", "--week", "5", "--light", "Player O'Neil", "Some Body"])
        page = self.c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("RUNNING", page)
        self.assertIn("fake log", page)
        self.assertNotIn('http-equiv="refresh"', page)          # polled in place, never reloaded
        self.assertIn(r.headers["Location"].split("/jobs/")[-1] + ".json", page)

    def test_a_form_error_is_rendered_not_launched(self):
        r = self.c.post("/tools/evaluate_move", data={"_csrf": "tok-123", "team": MY_TEAM, "add": "--evaluate-unevaluated"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Not launched", r.get_data(as_text=True))
        self.assertEqual(self.runner.launches, [])

    def test_a_refused_launch_is_409_and_names_the_running_job(self):
        self.runner.busy = {"id": "20260926T000000Z_aaaaaa_slow", "label": "slow thing", "started_at": "x"}
        r = self.c.post("/tools/optimize_lineup", data={"_csrf": "tok-123", "team": MY_TEAM})
        self.assertEqual(r.status_code, 409)
        body = r.get_data(as_text=True)
        self.assertIn("busy", body)
        self.assertIn("/jobs/20260926T000000Z_aaaaaa_slow", body)

    def test_real_names_never_enter_an_argv_even_with_the_overlay_on(self):
        app = create_app(self.root, runner=self.runner, csrf_token="tok-123",
                         overlay=Overlay({MY_TEAM: "CANARY Real Club"}))
        app.testing = True
        c = app.test_client()
        form = c.get("/tools/optimize_lineup").get_data(as_text=True)
        self.assertIn(f'<option value="{MY_TEAM}" selected>CANARY Real Club</option>', form)
        c.post("/tools/optimize_lineup", data={"_csrf": "tok-123", "team": MY_TEAM})
        argv = self.runner.launches[-1][0]
        self.assertIn(MY_TEAM, argv)
        self.assertNotIn("CANARY Real Club", " ".join(argv))
        jid = list(self.runner.metas)[-1]
        page = c.get("/jobs/" + jid).get_data(as_text=True)
        self.assertIn("CANARY Real Club", page)     # displayed with the overlay
        self.assertNotIn(MY_TEAM, page)             # and no pseudonym leaks beside it

    def test_jobs_pages_and_cancel(self):
        self.c.post("/tools/roster_calendar", data={"_csrf": "tok-123", "team": MY_TEAM})
        jid = list(self.runner.metas)[-1]
        self.assertIn(jid, self.c.get("/jobs").get_data(as_text=True))
        self.assertEqual(self.c.get(f"/jobs/{jid}/log").get_data(as_text=True), "fake log " + jid)
        self.assertEqual(self.c.post(f"/jobs/{jid}/cancel", data={}).status_code, 403)
        self.assertEqual(self.c.post(f"/jobs/{jid}/cancel", data={"_csrf": "tok-123"}).status_code, 302)
        self.assertEqual(self.runner.cancelled, [jid])
        self.assertEqual(self.c.get("/jobs/nope").status_code, 404)
        self.runner.metas[jid]["state"] = OK
        self.assertNotIn('http-equiv="refresh"', self.c.get(f"/jobs/{jid}").get_data(as_text=True))

    def test_launching_writes_nothing_under_the_tree(self):
        before = self.root.tree_digest()
        self.c.post("/tools/optimize_lineup", data={"_csrf": "tok-123", "team": MY_TEAM})
        self.c.get("/tools")
        self.c.get("/jobs")
        self.assertEqual(self.root.tree_digest(), before)

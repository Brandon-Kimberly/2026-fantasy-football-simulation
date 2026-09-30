"""
tests.test_webui_sse -- server-sent events for running jobs (docs/WEB_UI_ROADMAP.md UI-E8; the
unbuilt half of U13).

The job page polled /jobs/<id>.json every two seconds and the job bar every three.
/jobs/<id>/events now streams the same status as server-sent events: an event whenever it
changes, a heartbeat comment while nothing does, and a last event once the job has ended, after
which the stream closes. The pages open the stream where the browser has EventSource, and fall
back to their polling when it drops. tests.test_webui_browser drives both in a real browser.
"""
import json
import threading
import time
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.jobs import OK, RUNNING
    from webui.live import LiveBoard
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheStream(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        self.runner = FakeRunner()
        self.app = create_app(self.td.name, runner=self.runner, csrf_token="tok",
                              live=LiveBoard(self.td.name, MY_TEAM, league_id=None, fetch=None))
        self.app.testing = True
        self.app.config["SSE_INTERVAL"] = 0.05

    def tearDown(self):
        self.td.cleanup()

    def test_events_while_running_then_the_end_then_close(self):
        jid = self.runner.launch(["py", "-m", "scripts.weekly_report"], "weekly_report")

        def finish():
            time.sleep(0.3)
            self.runner.metas[jid].update(state=OK, rc=0, finished_at="2026-09-26T00:01:00Z")
        threading.Thread(target=finish, daemon=True).start()
        r = self.app.test_client().get(f"/jobs/{jid}/events")
        self.assertEqual(r.mimetype, "text/event-stream")
        body = r.get_data(as_text=True)                # returns because the stream closes at the end
        events = [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[0]["state"], RUNNING)
        self.assertEqual(events[-1]["state"], OK)
        self.assertEqual(len([e for e in events if e["state"] == RUNNING]), 1, "an event only when something changed")
        self.assertEqual(r.headers.get("Cache-Control"), "no-cache")

    def test_an_unknown_job_is_404(self):
        self.assertEqual(self.app.test_client().get("/jobs/20260926T000000Z_ffffff_nope/events").status_code, 404)


if __name__ == "__main__":
    unittest.main()

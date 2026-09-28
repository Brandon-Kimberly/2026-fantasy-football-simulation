"""
tests.test_webui_twelfth -- the web UI roadmap's first wave (docs/WEB_UI_ROADMAP.md),
server side. The browser half lives in tests.test_webui_browser.

UI-F2: the System page said "30 sources fell back" when one source had failed and the
sync had raised thirty warnings. Sources and warnings are counted apart.

UI-F1: whether the week's games have started is the sync's kickoffs against the clock,
decided once on the server, so Home can read live scores on load instead of leading with
a pre-game number after the games began.
"""
import datetime as _dt
import json
import os
import tempfile
import unittest

from webui.glance import freshness_report, kickoff_report, sync_phrase
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def _plant_kickoffs(root, week, times):
    os.makedirs(os.path.join(root, "data", "current"), exist_ok=True)
    with open(os.path.join(root, "data", "current", "nfl_schedule.json"), "w", encoding="utf-8") as fh:
        json.dump({"_meta": {"kickoffs": {str(week): times}}}, fh)


def _at(iso):
    return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


class TestStarted(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        _plant_kickoffs(self.td.name, 3, ["2026-09-25T00:15Z", "2026-09-27T17:00Z", "2026-09-29T00:15Z"])
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_not_started_before_the_first_kickoff(self):
        self.assertFalse(kickoff_report(self.root, 3, now=_at("2026-09-24T12:00:00Z"))["started"])

    def test_started_between_kickoffs(self):
        self.assertTrue(kickoff_report(self.root, 3, now=_at("2026-09-27T20:00:00Z"))["started"])

    def test_started_after_the_last(self):
        k = kickoff_report(self.root, 3, now=_at("2026-09-30T12:00:00Z"))
        self.assertTrue(k["started"])
        self.assertTrue(k["done"])

    def test_no_kickoffs_synced_is_not_started(self):
        self.assertFalse(kickoff_report(self.root, 4)["started"])


class TestSyncPhrase(unittest.TestCase):
    def test_a_failed_source_and_many_warnings_are_two_numbers(self):
        self.assertEqual(sync_phrase(1, 0, 30), "1 source failed, and the sync raised 30 warnings")

    def test_fallbacks_and_failures_together(self):
        self.assertEqual(sync_phrase(1, 2, 0), "1 source failed and 2 fell back to an older copy")
        self.assertEqual(sync_phrase(0, 2, 1), "2 sources fell back to an older copy, and the sync raised 1 warning")

    def test_every_source_through_with_warnings(self):
        self.assertEqual(sync_phrase(0, 0, 3), "every source came through, and the sync raised 3 warnings")

    def test_nothing_to_say(self):
        self.assertEqual(sync_phrase(0, 0, 0), "every source came through")


def _manifest(root, sources, warnings):
    with open(os.path.join(root, "data", "current", "sync_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"started_at": "2026-09-25T17:12:06Z", "finished_at": "2026-09-25T17:13:20Z", "current_week": 3,
                   "season": "2026", "ok": True, "sources": sources,
                   "degraded": [f"WARNING | name collision {i}" for i in range(warnings)], "files": {}}, fh)


SOURCES = dict({f"src_{i}": {"ok": True, "rows": 10} for i in range(11)},
               vegas_totals={"ok": False, "rows": 0})


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestSystemCountsSourcesAndWarningsApart(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        _manifest(self.td.name, SOURCES, 30)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def get(self, path):
        st = Settings(self.root)
        st.set_mode("dev")
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)

    def test_the_report_counts_warnings(self):
        fr = freshness_report(self.root)
        self.assertEqual((fr["n_ok"], fr["n_bad"], fr["n_fell"], fr["n_warn"]), (11, 1, 0, 30))

    def test_no_page_calls_thirty_warnings_thirty_sources(self):
        for path in ("/system", "/current", "/tools/optimize_lineup"):
            with self.subTest(path=path):
                body = self.get(path)
                self.assertNotIn("30 sources", body)
                self.assertNotIn("30 source ", body)
                self.assertNotIn("What fell back", body)
                self.assertNotIn("Sources that fell back", body)

    def test_the_system_lede_says_what_happened(self):
        self.assertIn("1 source failed, and the sync raised 30 warnings", self.get("/system"))


if __name__ == "__main__":
    unittest.main()

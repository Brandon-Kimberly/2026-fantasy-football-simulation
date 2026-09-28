"""
tests.test_webui_twelfth -- the web UI roadmap's first wave (docs/WEB_UI_ROADMAP.md),
server side. The browser half lives in tests.test_webui_browser.

UI-F1: whether the week's games have started is the sync's kickoffs against the clock,
decided once on the server, so Home can read live scores on load instead of leading with
a pre-game number after the games began.
"""
import datetime as _dt
import json
import os
import tempfile
import unittest

from webui.glance import kickoff_report
from webui.paths import Root


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


if __name__ == "__main__":
    unittest.main()

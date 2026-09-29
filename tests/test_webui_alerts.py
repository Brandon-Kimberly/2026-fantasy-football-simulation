"""
tests.test_webui_alerts -- more alert triggers (docs/WEB_UI_ROADMAP.md UI-R5).

Beside the kickoff alert, each opt-in, each saying why and how much, and like it working only
while a page is open:
  - a designation changed for one of the owner's players (the sync's designations log: the
    player's two newest statuses differ, recorded this week);
  - the owner's playoff odds moved by more than two standard errors between the two newest
    forecasts;
  - the waiver run is thirty minutes away (the page times it);
  - the model's lineup would move the chance of winning by at least two points (the live
    panel's plan; the page decides, from the snapshot).
Every alert has a stable key -- the same change gives the same key on every read -- so the page
fires it once and never again on reload.
"""
import datetime as _dt
import json
import os
import tempfile
import unittest

from webui import alerts                           # outside the probe

NOW = _dt.datetime(2026, 9, 25, 15, 0, tzinfo=_dt.timezone.utc)

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.paths import Root
    from tests.test_webui_objects import QF, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestAlerts(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)
        rows = [{"player_id": "100", "name": "Player 0 O'Neil", "team": QF, "week": 3, "injury_status": None,
                 "recorded_at": "2026-09-23T10:00:00Z"},
                {"player_id": "100", "name": "Player 0 O'Neil", "team": QF, "week": 3, "injury_status": "Questionable",
                 "recorded_at": "2026-09-25T10:00:00Z"},
                {"player_id": "101", "name": "Player 1 O'Neil", "team": "Neon Walruses", "week": 3,
                 "injury_status": "Out", "recorded_at": "2026-09-25T10:00:00Z"}]
        with open(os.path.join(self.td.name, "data", "logs", "designations.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
        d = os.path.join(self.td.name, "data", "weeks", "week_02")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "live_season_forecast_week_2.json"), "w", encoding="utf-8") as fh:
            json.dump({QF: {"current_state": {"actual_wins_banked": 1.0},
                            "forecast": {"playoff_probability_pct": 70.0, "playoff_standard_error": 0.25}}}, fh)

    def tearDown(self):
        self.td.cleanup()

    def kinds(self):
        return {a["kind"]: a for a in alerts.alerts(self.root, MY_TEAM, now=NOW)}

    def test_a_designation_change_for_my_player(self):
        a = self.kinds()["designation"]
        self.assertIn("Player 0 O'Neil", a["title"])
        self.assertIn("Questionable", a["title"])
        self.assertEqual(sum(1 for x in alerts.alerts(self.root, MY_TEAM, now=NOW) if x["kind"] == "designation"), 1,
                         "another team's player is not mine")

    def test_the_playoff_odds_moved_past_two_standard_errors(self):
        a = self.kinds()["odds"]
        self.assertIn("93.5", a["body"])
        self.assertIn("70.0", a["body"])

    def test_the_waiver_run_is_timed_by_the_page(self):
        a = self.kinds()["waiver"]
        self.assertEqual(a["at"], "2026-09-25T16:00:00Z")

    def test_the_same_changes_give_the_same_keys(self):
        first = sorted(a["key"] for a in alerts.alerts(self.root, MY_TEAM, now=NOW))
        self.assertGreaterEqual(len(first), 3, "the designation, the odds and the waiver run")
        self.assertEqual(first, sorted(a["key"] for a in alerts.alerts(self.root, MY_TEAM, now=NOW)))
        self.assertEqual(len(set(first)), len(first))


if __name__ == "__main__":
    unittest.main()

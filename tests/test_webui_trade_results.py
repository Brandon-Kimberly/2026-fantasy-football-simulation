"""
tests.test_webui_trade_results -- process against result for completed trades
(docs/WEB_UI_ROADMAP.md UI-T4).

The decision log grades every trade before the fact (the paired simulation). Beside it now:
what happened -- what each side's incoming players scored while STARTED for their new team,
against what its outgoing players scored while started for theirs (TheDadHut's two nets).
Two columns, never merged into one grade. The replay of each week with the trade undone is
not built: it needs counterfactual lineups, and the page says so.

The fixture's trade t3 (week 3): Josh Jacobs (5850) to Rocket Pandas, Tyrone Tracy (11655) to
Neon Walruses. Planted: week 3, Rocket Pandas started Jacobs (20.0 points); Neon Walruses
benched Tracy (12.0 points, not counted). By hand:
  Rocket Pandas   got 20.0 while started, gave 0.0  -> net +20.0; decision +5.0 +- 1.1
  Neon Walruses   got 0.0, gave 20.0                -> net -20.0; decision -4.0 +- 1.2
"""
import json
import os
import tempfile
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import plant
    from tests.test_webui_routes import TEAMS
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTradeResults(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        d = os.path.join(self.td.name, "data")
        with open(os.path.join(d, "current", "weekly_lineups.json"), "w", encoding="utf-8") as fh:
            json.dump({"week_3": {TEAMS[2]: {"starters": ["5850"], "players": ["5850"]},
                                  TEAMS[1]: {"starters": [], "players": ["11655"]}}}, fh)
        with open(os.path.join(d, "logs", "first_recorded_scores.jsonl"), "a", encoding="utf-8") as fh:
            for pid, name, pts in (("5850", "Josh Jacobs", 20.0), ("11655", "Tyrone Tracy", 12.0)):
                fh.write(json.dumps({"player_id": pid, "name": name, "week": 3, "points": pts, "recorded_at": "2026-09-29T10:00:00Z"}) + "\n")
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_the_two_nets_and_the_decision_side_by_side(self):
        from webui.trade_results import trade_results
        (t,) = trade_results(self.root)
        rp, nw = t["sides"][TEAMS[2]], t["sides"][TEAMS[1]]
        self.assertEqual((rp["got_pts"], rp["gave_pts"], rp["net"]), (20.0, 0.0, 20.0))
        self.assertEqual((nw["got_pts"], nw["gave_pts"], nw["net"]), (0.0, 20.0, -20.0))
        self.assertEqual((rp["decision"], rp["decision_se"]), (5.0, 1.1))
        self.assertEqual((nw["decision"], nw["decision_se"]), (-4.0, 1.2))
        self.assertEqual(rp["got"][0]["weeks_started"], 1)
        self.assertEqual(nw["got"][0]["weeks_started"], 0, "benched is not counted")

    def test_the_decisions_page_shows_both_columns_never_one_grade(self):
        for mode in ("dev", "simple"):
            st = Settings(self.root)
            st.set_mode(mode)
            app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                             live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
            app.testing = True
            text = visible_text(app.test_client().get("/decisions").get_data(as_text=True))
            with self.subTest(mode=mode):
                self.assertIn("Trades: the decision and what happened", text)
                self.assertIn("+20.0", text)
                self.assertIn("with the trade undone", text, "the unbuilt replay is named, not hidden")
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

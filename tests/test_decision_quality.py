"""
tests.test_decision_quality -- lineup decisions judged before the games (docs/WEB_UI_ROADMAP.md
UI-L4).

The roadmap listed this as "on disk". It was not: the lineups each team actually STARTED in
2026 are nowhere on disk. The sync fetched every completed week's matchups -- starters
included -- and kept only the points. So the sync now keeps them too, at no extra request:
data/current/weekly_lineups.json, {"week_N": {team: {"starters": [ids], "players": [ids]}}},
current state rewritten each sync like weekly_actuals.json, and read by nothing in the engine.

Then, for each completed week:
  before the games   expected points of the lineup started, over the expected points of the
                     best lineup that roster could have started, both priced on the last
                     projection logged before the week's first kickoff
  in hindsight       the same with the points actually scored -- labelled hindsight
  not a decision     the hindsight gap minus the expected gap: what the dice did
Worked by hand on the fixture week below (one QB slot): A was projected 20 and scored 10;
B was projected 15 and scored 25; A started. Before the games: 20 of 20, full marks, 0 left.
In hindsight: 25 was available and 10 was scored, 15 left. Not a decision: 15 - 0 = 15.
Written before the file, the extractor or the measure existed.
"""
import json
import os
import tempfile
import unittest

MATCHUPS = [{"roster_id": 1, "matchup_id": 1, "points": 120.0, "starters": ["11", "0", "12"], "players": ["11", "12", "13"]},
            {"roster_id": 2, "matchup_id": 1, "points": 110.0, "starters": ["21"], "players": ["21", "22"]}]


class TestTheSyncKeepsTheLineups(unittest.TestCase):
    def test_each_teams_starters_and_roster(self):
        from fantasy_sim.sync import _extract_weekly_lineups
        got = _extract_weekly_lineups(MATCHUPS, {1: "Quantum Ferrets", 2: "Neon Walruses"})
        self.assertEqual(got["Quantum Ferrets"], {"starters": ["11", "12"], "players": ["11", "12", "13"]},
                         "an empty slot ('0') is not a starter")
        self.assertEqual(got["Neon Walruses"]["starters"], ["21"])

    def test_the_sync_writes_them_beside_the_actuals(self):
        """Structural, stated as one: the full sync is not run by the suite."""
        import inspect
        from fantasy_sim import sync
        from fantasy_sim.storage import WEEKLY_LINEUPS_FILE
        src = inspect.getsource(sync._sync_body)
        self.assertIn("_extract_weekly_lineups(wk_matchups", src)
        self.assertIn("save_json(WEEKLY_LINEUPS_FILE", src)
        self.assertEqual(os.path.normpath(WEEKLY_LINEUPS_FILE), os.path.normpath(os.path.join("data", "current", "weekly_lineups.json")))


class TestTheMeasure(unittest.TestCase):
    def test_the_right_call_that_lost(self):
        from webui.decision_quality import judge
        roster = {"A": {"pos": "QB", "slots": ["QB"]}, "B": {"pos": "QB", "slots": ["QB"]}}
        q = judge(started=["A"], roster=roster, projected={"A": 20.0, "B": 15.0}, scored={"A": 10.0, "B": 25.0}, slots=("QB",))
        self.assertEqual((q["started_exp"], q["best_exp"], q["before"]), (20.0, 20.0, 1.0))
        self.assertEqual((q["expected_left"], q["hindsight_left"], q["not_a_decision"]), (0.0, 15.0, 15.0))

    def test_the_wrong_call(self):
        from webui.decision_quality import judge
        roster = {"A": {"pos": "QB", "slots": ["QB"]}, "B": {"pos": "QB", "slots": ["QB"]}}
        q = judge(started=["B"], roster=roster, projected={"A": 20.0, "B": 15.0}, scored={"A": 10.0, "B": 25.0}, slots=("QB",))
        self.assertEqual((q["before"], q["expected_left"]), (0.75, 5.0))
        self.assertEqual((q["hindsight_left"], q["not_a_decision"]), (0.0, -5.0), "the wrong call happened to win")

    def test_a_starter_with_no_projection_leaves_the_week_unjudged(self):
        from webui.decision_quality import judge
        roster = {"A": {"pos": "QB", "slots": ["QB"]}, "B": {"pos": "QB", "slots": ["QB"]}}
        q = judge(started=["A"], roster=roster, projected={"B": 15.0}, scored={"A": 10.0, "B": 25.0}, slots=("QB",))
        self.assertIsNone(q["before"])
        self.assertEqual(q["unpriced"], ["A"])


try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.render import slug
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import QF, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheTeamPage(unittest.TestCase):
    """The fixture: Quantum Ferrets started Player 0 (pid 100) in week 1 over Player 1 (101).
    The fixture's projection log prices Player 0 at 18.0 or more before kickoff; Player 1 is
    planted at 12.0. The page says which part was the decision and which the dice, in both views."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        with open(os.path.join(self.td.name, "data", "current", "weekly_lineups.json"), "w", encoding="utf-8") as fh:
            json.dump({"week_1": {QF: {"starters": ["100"], "players": ["100", "101"]}}}, fh)
        with open(os.path.join(self.td.name, "data", "logs", "projection_log.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"player_id": "101", "name": "Player 1 O'Neil", "week": 1, "sleeper_mean": 12.0,
                                 "synced_at": "2026-09-09T10:00:00Z"}) + "\n")
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_the_section_in_both_views(self):
        from webui.decision_quality import season
        rows = season(self.root, QF, slots=("QB",))
        self.assertEqual([r["week"] for r in rows], [1])
        self.assertEqual(rows[0]["before"], 1.0)
        for mode in ("dev", "simple"):
            st = Settings(self.root)
            st.set_mode(mode)
            app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                             live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
            app.testing = True
            body = app.test_client().get(f"/team/{slug(QF)}").get_data(as_text=True)
            with self.subTest(mode=mode):
                text = visible_text(body)
                self.assertIn("Lineup calls, judged before the games", text)
                self.assertIn("in hindsight", text)
                self.assertIn("not a decision", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

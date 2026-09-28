"""
tests.test_webui_eighth -- the audit's Phase 5 (docs/WEB_UI_AUDIT.md): live and game day.

U6 `glance.kickoff_report` reads the synced kickoffs and says what the next one is, how
many games start then, how many are still ahead, and when the week's games are all under
way. U8 `glance.h2h_report` finds every meeting with this week's opponent in this season's
actuals and last season's archive. U7 the live snapshot carries every NFL game's clock
and score, the board keeps an in-memory history of the win probability through the day
(capped, reset on a new week, never written), and the panel's rows carry game-clock
chips. U3 every page shows a job bar while a job runs and the script that turns its end
into a toast. The gameday page is a full-screen scoreboard served in both views. Nothing
here reaches the network or writes under data/.

What the tests do NOT do: execute the page scripts. The countdown tick, the toast, the
swing animation and the confetti are pinned by their presence in the served page only.
"""
import datetime as _dt
import json
import os
import tempfile
import unittest

from webui import live

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.glance import h2h_report, kickoff_report
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_home import OPP, enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_live import MY_RID, OPP_RID, plant as plant_live
    from tests.test_webui_routes import TEAMS, build_tree
    from webui.paths import Root
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

KICKS = ["2026-09-25T00:15Z"] + ["2026-09-27T17:00Z"] * 9 + ["2026-09-27T20:05Z", "2026-09-27T20:25Z", "2026-09-28T00:20Z", "2026-09-29T00:15Z"]


def utc(s):
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def plant_gameday(root):
    """Kickoffs for week 3, this season's actuals for weeks 1-2, and a 2025 archive in
    which the two teams met twice."""
    with open(os.path.join(root, "data", "current", "nfl_schedule.json"), "w", encoding="utf-8") as fh:
        json.dump({"3": {"KC": "CAR", "CAR": "KC"}, "_meta": {"kickoffs": {"3": KICKS}}}, fh)
    with open(os.path.join(root, "data", "current", "weekly_actuals.json"), "w", encoding="utf-8") as fh:
        json.dump({"week_1": {"team_results": {MY_TEAM: {"points_scored": 150.5, "h2h_win": 1.0}, OPP: {"points_scored": 140.0, "h2h_win": 0.0}}},
                   "week_2": {"team_results": {MY_TEAM: {"points_scored": 120.0, "h2h_win": 0.0}, OPP: {"points_scored": 131.2, "h2h_win": 1.0}}}}, fh)
    with open(os.path.join(root, "data", "current", "league_state.json"), "w", encoding="utf-8") as fh:
        json.dump({"current_week": 3, "season": "2026"}, fh)
    with open(os.path.join(root, "data", "logs", "season_2025.json"), "w", encoding="utf-8") as fh:
        json.dump({"season": "2025", "roster_map": {"1": MY_TEAM, "2": OPP, "3": TEAMS[1]},
                   "matchups": {"5": [{"roster_id": 1, "matchup_id": 2, "points": 120.0}, {"roster_id": 2, "matchup_id": 2, "points": 130.0},
                                      {"roster_id": 3, "matchup_id": 1, "points": 99.0}],
                                "9": [{"roster_id": 1, "matchup_id": 1, "points": 172.4}, {"roster_id": 2, "matchup_id": 1, "points": 150.1}],
                                "11": [{"roster_id": 1, "matchup_id": 1, "points": 100.0}, {"roster_id": 3, "matchup_id": 1, "points": 90.0}]}}, fh)


def fetch_with_scores(url):
    if "/matchups/" in url:
        return [{"roster_id": int(MY_RID), "matchup_id": 1, "points": 31.5, "starters": ["100", "101"], "players_points": {"100": 21.5, "101": 10.0}},
                {"roster_id": int(OPP_RID), "matchup_id": 1, "points": 0.0, "starters": ["200", "201"], "players_points": {}}]
    if "scoreboard" in url:
        return {"events": [
            {"competitions": [{"status": {"type": {"state": "post", "completed": True}, "period": 4, "displayClock": "0:00"},
                               "competitors": [{"team": {"abbreviation": "KC"}, "homeAway": "home", "score": "27"},
                                               {"team": {"abbreviation": "CAR"}, "homeAway": "away", "score": "13"}]}]},
            {"competitions": [{"status": {"type": {"state": "in", "completed": False}, "period": 2, "displayClock": "7:30"},
                               "competitors": [{"team": {"abbreviation": "GB"}, "homeAway": "away", "score": "3"},
                                               {"team": {"abbreviation": "CHI"}, "homeAway": "home", "score": "10"}]}]}]}
    raise AssertionError("unexpected url " + url)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestKickoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        plant_gameday(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_before_the_first_game(self):
        k = kickoff_report(self.root, 3, now=utc("2026-09-24T22:15:00Z"))
        self.assertEqual(k["next"], "2026-09-25T00:15Z")
        self.assertEqual(k["in_seconds"], 7200)
        self.assertEqual((k["games"], k["remaining"], k["at_next"]), (14, 14, 1))
        self.assertTrue(k["first"])
        self.assertFalse(k["done"])

    def test_sunday_morning(self):
        k = kickoff_report(self.root, 3, now=utc("2026-09-27T14:46:00Z"))
        self.assertEqual(k["next"], "2026-09-27T17:00Z")
        self.assertEqual(k["in_seconds"], 134 * 60)
        self.assertEqual((k["remaining"], k["at_next"]), (13, 9))
        self.assertFalse(k["first"])

    def test_after_the_last_kickoff_and_without_kickoffs(self):
        k = kickoff_report(self.root, 3, now=utc("2026-09-29T03:00:00Z"))
        self.assertIsNone(k["next"])
        self.assertTrue(k["done"])
        self.assertEqual(k["remaining"], 0)
        self.assertIsNone(kickoff_report(self.root, 4, now=utc("2026-09-29T03:00:00Z"))["next"])
        self.assertEqual(kickoff_report(self.root, 4)["games"], 0)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestH2H(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)                  # the schedule pairs me with OPP every week
        plant_gameday(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_meetings_this_season_and_last_newest_first(self):
        h = h2h_report(self.root, MY_TEAM, OPP)
        self.assertEqual([(r["season"], r["week"]) for r in h["rows"]], [("2026", 2), ("2026", 1), ("2025", 9), ("2025", 5)])
        self.assertEqual((h["wins"], h["losses"], h["ties"]), (2, 2, 0))
        self.assertEqual(h["last"]["week"], 2)
        self.assertFalse(h["last"]["won"])
        self.assertEqual(h["rows"][2]["mine"], 172.4)
        self.assertTrue(h["rows"][2]["won"])
        self.assertEqual(h["since"], "2025")

    def test_no_meetings_and_no_opponent(self):
        h = h2h_report(self.root, MY_TEAM, TEAMS[2])
        self.assertEqual(h["rows"], [])
        self.assertIsNone(h["last"])
        self.assertEqual(h2h_report(self.root, MY_TEAM, None)["rows"], [])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestLiveV2(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        plant_live(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_snapshot_carries_every_game_with_its_clock_and_score(self):
        s = live.snapshot(self.root, 3, MY_TEAM, "L", fetch_with_scores)
        self.assertTrue(s["ok"])
        games = {(g["away"], g["home"]): g for g in s["games"]}
        self.assertEqual(games[("CAR", "KC")]["label"], "final")
        self.assertEqual((games[("CAR", "KC")]["away_score"], games[("CAR", "KC")]["home_score"]), (13, 27))
        self.assertEqual(games[("GB", "CHI")]["label"], "Q2 7:30")
        self.assertEqual(games[("GB", "CHI")]["state"], "in")
        self.assertEqual(s["mine"]["rows"][0]["status"], "final")

    def test_board_history_grows_with_refreshes_is_capped_and_resets_on_a_new_week(self):
        t = [0.0]
        board = LiveBoard(self.root, MY_TEAM, league_id="L", fetch=fetch_with_scores, min_interval=45, clock=lambda: t[0], max_history=3)
        board.get(3)
        self.assertEqual(len(board.peek()["history"]), 1)
        for _ in range(4):
            t[0] += 60
            board.get(3, refresh=True)
        h = board.peek()["history"]
        self.assertEqual(len(h), 3, "capped")
        self.assertEqual(set(h[0]), {"at", "p", "mine", "theirs"})
        self.assertAlmostEqual(h[-1]["mine"], board.peek()["snapshot"]["mine"]["projected"])
        t[0] += 60
        board.get(4, refresh=True)
        self.assertEqual(len(board.peek()["history"]), 1, "a new week starts a new day")
        before = self.root.tree_digest()
        self.assertEqual(self.root.tree_digest(), before)

    def test_a_disabled_board_has_an_empty_history(self):
        self.assertEqual(LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None).peek()["history"], [])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        plant_live(cls.td.name)
        plant_gameday(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def client(self, mode="dev", runner=None, board=None):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=runner or FakeRunner(), csrf_token="tok", settings=st,
                         live=board or LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def get(self, path, **kw):
        r = self.client(**kw).get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)

    def test_home_countdown_and_head_to_head_in_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/", mode=mode)
                self.assertIn('id="kick"', body)                                   # U6
                self.assertIn('data-at="2026-09-', body)
                self.assertIn("head-to-head", body)                                # U8
                self.assertIn("2–2", body)
                self.assertIn("since 2025", body)
                self.assertIn('href="/gameday"', body)
                self.assertIn("@keyframes clash", body)                            # the vs clash
                self.assertIn("confetti", body.split("</style>")[-1])              # the script, final win only

    def test_gameday_page_in_both_views_from_the_cached_snapshot_only(self):
        calls = []

        def counting(url):
            calls.append(url)
            return fetch_with_scores(url)
        board = LiveBoard(self.root, MY_TEAM, league_id="L", fetch=counting)
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/gameday", mode=mode, board=board)
                self.assertIn('id="gd"', body)
                self.assertIn("auto-refresh", body.lower())
                self.assertIn("/api/live", body)
                self.assertEqual(calls, [], "a page render never fetches")

    def test_job_bar_only_while_a_job_runs(self):
        busy = {"id": "20260926T000000Z_000001_optimize_lineup", "tool": "optimize_lineup", "label": "Optimal lineup",
                "state": "RUNNING", "started_at": "2026-09-26T00:00:00Z", "finished_at": None, "rc": None}
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/league", mode=mode, runner=FakeRunner(busy=busy))
                self.assertIn('id="jobbar"', body)                                 # U3
                self.assertIn('data-url="/jobs/optimal-lineup/2026-09-26-000000-000001"', body)
                self.assertIn('id="toast"', body)
                self.assertIn("Open the answer", body.split("</style>")[-1])
                quiet = self.get("/league", mode=mode)
                self.assertNotIn('id="jobbar"', quiet)
                self.assertIn('id="toast"', quiet)


if __name__ == "__main__":
    unittest.main()

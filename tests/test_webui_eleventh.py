"""
tests.test_webui_eleventh -- what the model would change about the lineup I am ACTUALLY
starting (docs/WEB_UI.md W14).

Everything else in this UI reports. This is the first thing that intervenes: the live read
already knows the starters Sleeper has me fielding, and the newest optimal-lineup record
already knows the ones the tool would field, and until now nobody compared them. The
comparison is a set difference with the points at stake attached, and one piece of
honesty on top -- a man whose game has already kicked off cannot be swapped, so he is
reported and marked locked rather than advised.

`lineup_plan` reads the record; `lineup_diff` is pure. Neither reaches the network, and
the diff rides along on a snapshot that still writes nothing.
"""
import json
import os
import tempfile
import unittest

from webui import live

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_home import enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_live import MY_RID, OPP_RID, plant as plant_live
    from tests.test_webui_routes import build_tree
    from webui.paths import Root
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

# the tool's slate: a QB it would start, and a receiver it would start who is on my bench
PLAN = {"starts": {"Patrick Mahomes": {"slot": "QB", "pos": "QB", "expected": 24.0, "flag": ""},
                   "Deebo Bench": {"slot": "WR", "pos": "WR", "expected": 14.0, "flag": ""}},
        "bench": {"Jalen Coker": 10.0},
        "stamp": "2026-09-27T21:42:48Z", "total": 216.1, "week": 3}

# what Sleeper says I am fielding
MINE = [{"pid": "100", "name": "Patrick Mahomes", "pos": "QB", "nfl": "KC", "expected": 24.0, "frac": 1.0, "status": "pregame"},
        {"pid": "101", "name": "Jalen Coker", "pos": "WR", "nfl": "CAR", "expected": 9.1, "frac": 1.0, "status": "pregame"}]


def plant_plan(root, lineup=None, stamp="20260927T214248Z"):
    """A newer optimal-lineup record than the one tests.test_webui_live plants."""
    rel = os.path.join(root, "data", "decisions", "week_03", f"lineup_{stamp}_week3.json")
    os.makedirs(os.path.dirname(rel), exist_ok=True)
    with open(rel, "w", encoding="utf-8") as fh:
        json.dump({"tool": "optimize_lineup", "team": MY_TEAM, "week": 3, "timestamp_utc": stamp,
                   "expected_total": 216.1,
                   "lineup": lineup if lineup is not None else [
                       {"slot": "QB", "name": "Patrick Mahomes", "pos": "QB", "expected": 24.0, "flag": ""},
                       {"slot": "WR", "name": "Deebo Bench", "pos": "WR", "expected": 14.0, "flag": ""}],
                   "bench": [{"name": "Jalen Coker", "pos": "WR", "expected": 10.0, "available": True}]}, fh)


class TestDiff(unittest.TestCase):
    def test_a_swap_is_named_with_the_points_at_stake(self):
        d = live.lineup_diff(PLAN, MINE)
        self.assertEqual([(x["name"], x["expected"], x["slot"]) for x in d["start"]], [("Deebo Bench", 14.0, "WR")])
        self.assertEqual([(x["name"], x["expected"]) for x in d["bench"]], [("Jalen Coker", 10.0)])
        self.assertEqual(d["delta"], 4.0)
        self.assertTrue(d["actionable"])
        self.assertEqual(d["stamp"], "2026-09-27T21:42:48Z")

    def test_the_benched_mans_value_is_the_models_own_number_for_him(self):
        """His live row says 9.1 (the season baseline); the record priced him at 10.0 for
        THIS week, and the record is what the advice is being measured against."""
        self.assertEqual(live.lineup_diff(PLAN, MINE)["bench"][0]["expected"], 10.0)
        thin = dict(PLAN, bench={})
        self.assertEqual(live.lineup_diff(thin, MINE)["bench"][0]["expected"], 9.1)

    def test_agreement_is_silence(self):
        agreed = {"starts": {r["name"]: {"slot": "X", "pos": r["pos"], "expected": r["expected"]} for r in MINE},
                  "bench": {}, "stamp": "s", "total": 1.0, "week": 3}
        d = live.lineup_diff(agreed, MINE)
        self.assertEqual((d["start"], d["bench"]), ([], []))
        self.assertEqual(d["delta"], 0.0)
        self.assertFalse(d["actionable"])

    def test_a_man_whose_game_has_kicked_off_is_reported_but_not_advised(self):
        started = [dict(MINE[0]), dict(MINE[1], frac=0.4, status="Q3 7:12")]
        d = live.lineup_diff(PLAN, started)
        self.assertTrue(d["bench"][0]["locked"])
        self.assertFalse(d["actionable"], "there is nobody left to bench")
        self.assertEqual(d["locked"], 1)
        # and the same for the man it wants started
        d2 = live.lineup_diff(PLAN, MINE, clock_by_name={"Deebo Bench": 0.0})
        self.assertTrue(d2["start"][0]["locked"])
        self.assertFalse(d2["actionable"])

    def test_no_record_no_advice(self):
        for empty in (None, {}, {"starts": {}, "bench": {}, "stamp": None, "total": None, "week": 3}):
            d = live.lineup_diff(empty, MINE)
            self.assertEqual((d["start"], d["bench"], d["delta"], d["actionable"]), ([], [], 0.0, False))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPlanFromDisk(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        plant_live(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_the_newest_record_wins_and_carries_what_the_diff_needs(self):
        plant_plan(self.td.name)
        p = live.lineup_plan(self.root, 3)
        self.assertEqual(sorted(p["starts"]), ["Deebo Bench", "Patrick Mahomes"])
        self.assertEqual(p["starts"]["Deebo Bench"], {"slot": "WR", "pos": "WR", "expected": 14.0, "flag": ""})
        self.assertEqual(p["bench"], {"Jalen Coker": 10.0})
        self.assertEqual(p["total"], 216.1)
        self.assertEqual(p["stamp"], "20260927T214248Z")

    def test_no_record_for_the_week(self):
        self.assertEqual(live.lineup_plan(self.root, 9)["starts"], {})


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestOnThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        plant_live(cls.td.name)
        plant_plan(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def fetch(self, url):
        if "/matchups/" in url:
            return [{"roster_id": int(MY_RID), "matchup_id": 1, "points": 0.0, "starters": ["100", "101"], "players_points": {}},
                    {"roster_id": int(OPP_RID), "matchup_id": 1, "points": 0.0, "starters": ["200"], "players_points": {}}]
        if "scoreboard" in url:
            return {"events": []}
        raise RuntimeError("no other source")

    def test_the_snapshot_carries_the_diff(self):
        s = live.snapshot(self.root, 3, MY_TEAM, "L", self.fetch)
        self.assertEqual([x["name"] for x in s["plan"]["start"]], ["Deebo Bench"])
        self.assertEqual([x["name"] for x in s["plan"]["bench"]], ["Jalen Coker"])
        self.assertTrue(s["plan"]["actionable"])
        before = self.root.tree_digest()
        live.snapshot(self.root, 3, MY_TEAM, "L", self.fetch)
        self.assertEqual(self.root.tree_digest(), before, "still writes nothing")

    def test_the_page_draws_it_in_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                st = Settings(self.root)
                st.set_mode(mode)
                app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                                 live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
                app.testing = True
                body = app.test_client().get("/").get_data(as_text=True)
                script = next(s for s in body.split("<script>") if "getElementById('live-body')" in s)
                css = body.split("</style>", 1)[1].split("</style>", 1)[0]
                self.assertIn("function plan(", script)
                self.assertIn("Start", script)
                self.assertIn("Bench", script)
                self.assertIn("too late", script, "a locked swap says so rather than advising it")
                self.assertIn(".plan", css)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestKickoffAlert(unittest.TestCase):
    """W16: the UI's one piece of speech. Everything else waits to be opened; this asks the
    browser to interrupt you before your lineup locks. Opt-in, and only while a page is
    open -- there is no server-side push and the page says so."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        plant_live(cls.td.name)
        plant_plan(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def page(self, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        body = app.test_client().get("/").get_data(as_text=True)
        return body, next(s for s in body.split("<script>") if "getElementById('live-body')" in s)

    def test_the_control_is_offered_in_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body, _script = self.page(mode)
                self.assertIn('id="live-alert"', body)
                self.assertIn('type="checkbox"', body.split('id="live-alert"', 1)[0].rsplit("<input", 1)[-1] + "<input")
                self.assertIn("before kickoff", body)

    def test_permission_is_asked_for_only_when_the_box_is_ticked(self):
        # the request lives in base.html's window.askNotify (shared with the other alerts since
        # 2026-09-30); Home's script calls it once, inside the change handler, never on load
        body, script = self.page()
        self.assertNotIn("requestPermission", script)
        self.assertEqual(script.count("window.askNotify()"), 1)
        asked = script.index("window.askNotify()")
        handler = script.index("alerts.addEventListener('change'")
        self.assertLess(handler, asked, "the request must sit inside the change handler, not run on load")
        self.assertEqual(body.count("requestPermission"), 1, "one place asks: window.askNotify")

    def test_it_fires_once_per_kickoff_thirty_minutes_out(self):
        _body, script = self.page()
        self.assertIn("var ALERT_AT = 30 * 60", script)
        self.assertIn("function alertCheck(", script)
        self.assertIn("localStorage.setItem('live-alert'", script)
        self.assertIn("'alerted:'", script, "a fired alert is remembered against its own kickoff")

    def test_the_message_says_what_to_do_about_it(self):
        _body, script = self.page()
        self.assertIn("questionable", script)
        self.assertIn("would field a different lineup", script)

    def test_the_page_admits_it_only_works_while_open(self):
        for mode in ("dev", "simple"):
            body, _script = self.page(mode)
            self.assertIn("while this page is open", body)


if __name__ == "__main__":
    unittest.main()

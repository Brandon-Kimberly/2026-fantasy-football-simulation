"""
tests.test_webui_live -- the live scoreboard (docs/WEB_UI.md W6).

What is pinned: the three formulas match scripts.live_matchup's (copied, since that
module imports the engine); a snapshot is built from a fake Sleeper matchups payload and
a fake ESPN scoreboard with the right banked points, remaining expectation and win
probability; expectations prefer this week's lineup record over the baseline mean; a
lost scoreboard is a caveat, not a failure; the board caches, honours its minimum
interval, keeps the last good snapshot through a failed refresh, and never fetches when
disabled; /api/live serves the payload with the overlay applied; and -- the caveat the
owner set -- NOTHING under data/ changes across any number of refreshes.
"""
import json
import math
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM, TEAM_NAME_MAP
from webui import live
from webui.names import Overlay
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from tests.test_webui_home import enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

MY_RID = next(k for k, v in TEAM_NAME_MAP.items() if v == MY_TEAM)
OPP_RID = next(k for k, v in TEAM_NAME_MAP.items() if v != MY_TEAM)
OPP = TEAM_NAME_MAP[OPP_RID]


def _w(root, rel, obj):
    p = os.path.join(root, "data", *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def plant(root):
    """Baselines with player ids, a lineup record for week 3 that re-prices one starter."""
    _w(root, "current/player_baselines.json", {
        "Patrick Mahomes": {"player_id": "100", "pos": "QB", "team": "KC", "mean": 20.0, "std_aleatoric": 6.0, "std_epistemic": 2.0},
        "Jalen Coker": {"player_id": "101", "pos": "WR", "team": "CAR", "mean": 10.0, "std_aleatoric": 5.0, "std_epistemic": 1.0},
        "Xavier Worthy": {"player_id": "200", "pos": "WR", "team": "KC", "mean": 12.0, "std_aleatoric": 5.0, "std_epistemic": 1.0},
        "Jordan Love": {"player_id": "201", "pos": "QB", "team": "GB", "mean": 18.0, "std_aleatoric": 6.0, "std_epistemic": 2.0}})
    _w(root, "decisions/week_03/lineup_20260924T165331Z_week3.json", {
        "tool": "optimize_lineup", "team": MY_TEAM, "week": 3,
        "lineup": [{"slot": "QB", "name": "Patrick Mahomes", "expected": 24.0, "p10": 10.0, "p50": 23.0, "p90": 40.0}],
        "bench": []})


def fake_fetch(url):
    if "/matchups/3" in url:
        return [{"roster_id": int(MY_RID), "matchup_id": 1, "points": 31.5, "starters": ["100", "101"],
                 "players_points": {"100": 21.5, "101": 10.0}},
                {"roster_id": int(OPP_RID), "matchup_id": 1, "points": 0.0, "starters": ["200", "201"],
                 "players_points": {}}]
    if "scoreboard" in url:
        return {"events": [
            {"competitions": [{"status": {"type": {"state": "post", "completed": True}, "period": 4, "displayClock": "0:00"},
                               "competitors": [{"team": {"abbreviation": "KC"}}, {"team": {"abbreviation": "CAR"}}]}]},
            {"competitions": [{"status": {"type": {"state": "pre", "completed": False}, "period": 0, "displayClock": "15:00"},
                               "competitors": [{"team": {"abbreviation": "GB"}}, {"team": {"abbreviation": "CHI"}}]}]}]}
    raise AssertionError("unexpected url " + url)


class TestFormulas(unittest.TestCase):
    def test_clock_fraction_pregame_final_and_mid_game(self):
        self.assertEqual(live.clock_fraction(1, "15:00", "pre", False), 1.0)
        self.assertEqual(live.clock_fraction(4, "0:00", "post", True), 0.0)
        self.assertAlmostEqual(live.clock_fraction(3, "7:30", "in", False), (450 + 900) / 3600.0)
        self.assertAlmostEqual(live.clock_fraction(5, "9:00", "in", False), 540 / 3600.0)   # OT capped at ten minutes

    def test_remaining_scales_mean_linearly_and_sd_by_root(self):
        mu, sd = live.remaining(20.0, 6.0, 0.25)
        self.assertEqual(mu, 5.0)
        self.assertAlmostEqual(sd, 3.0)

    def test_win_probability_is_symmetric_and_degenerate_without_spread(self):
        self.assertAlmostEqual(live.win_probability(10, 2, 10, 2), 0.5)
        self.assertAlmostEqual(live.win_probability(12, 2, 10, 2) + live.win_probability(10, 2, 12, 2), 1.0)
        self.assertEqual(live.win_probability(11, 0, 10, 0), 1.0)
        self.assertLess(live.win_probability(12, 2, 10, 2, inflate=2.0), live.win_probability(12, 2, 10, 2))

    def test_game_clocks_labels_and_aliases(self):
        clocks = live.game_clocks(3, fake_fetch)
        self.assertEqual(clocks["KC"], (0.0, "final"))
        self.assertEqual(clocks["GB"], (1.0, "pregame"))


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_expectations_prefer_this_weeks_lineup_record(self):
        exp = live.expectations(self.root, 3)
        self.assertEqual(exp["100"]["mean"], 24.0)
        self.assertEqual(exp["100"]["source"], "lineup record")
        self.assertAlmostEqual(exp["100"]["sd"], 30.0 / live.Z80)
        self.assertEqual(exp["101"]["source"], "baseline")
        self.assertAlmostEqual(exp["101"]["sd"], math.hypot(5.0, 1.0))

    def test_snapshot_banks_played_points_and_prices_the_rest(self):
        before = self.root.tree_digest()
        s = live.snapshot(self.root, 3, MY_TEAM, "L", fake_fetch)
        self.assertTrue(s["ok"])
        self.assertEqual((s["team"], s["opponent"]), (MY_TEAM, OPP))
        self.assertEqual(s["mine"]["banked"], 31.5)
        self.assertEqual(s["mine"]["left_mu"], 0.0, "both of my starters' games are final")
        self.assertEqual(s["mine"]["to_play"], 0)
        self.assertEqual(s["theirs"]["banked"], 0.0)
        self.assertEqual(s["theirs"]["left_mu"], 18.0, "KC is final (Worthy scores nothing more), GB is pregame (Love's full 18)")
        self.assertEqual(s["theirs"]["to_play"], 1)
        self.assertEqual(s["theirs"]["rows"][0]["status"], "final")
        self.assertEqual(s["theirs"]["rows"][1]["status"], "pregame")
        expect = live.win_probability(31.5, 0.0, 18.0, math.hypot(6.0, 2.0))
        self.assertAlmostEqual(s["p_win"], round(expect, 4))
        self.assertTrue(s["clocks_ok"])
        self.assertEqual(s["sources"], ["lineup record"])
        self.assertEqual(self.root.tree_digest(), before, "a snapshot must write nothing under data/")

    def test_a_lost_scoreboard_is_a_caveat_not_a_failure(self):
        def no_clocks(url):
            if "scoreboard" in url:
                raise OSError("espn down")
            return fake_fetch(url)
        s = live.snapshot(self.root, 3, MY_TEAM, "L", no_clocks)
        self.assertTrue(s["ok"])
        self.assertFalse(s["clocks_ok"])
        self.assertEqual(s["mine"]["rows"][0]["status"], "pregame")

    def test_my_roster_missing_is_reported_not_raised(self):
        s = live.snapshot(self.root, 3, MY_TEAM, "L", lambda url: [] if "matchups" in url else fake_fetch(url))
        self.assertFalse(s["ok"])
        self.assertIn("isn't in this week's matchups", s["error"])


class TestBoard(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)
        self.t = [1000.0]
        self.calls = []

    def tearDown(self):
        self.td.cleanup()

    def counting(self, url):
        self.calls.append(url)
        return fake_fetch(url)

    def board(self, fetch=None):
        return live.LiveBoard(self.root, MY_TEAM, league_id="L", fetch=fetch or self.counting, min_interval=45, clock=lambda: self.t[0])

    def test_disabled_board_never_fetches(self):
        b = live.LiveBoard(self.root, MY_TEAM, league_id=None, fetch=self.counting)
        self.assertFalse(b.enabled)
        p = b.get(3, refresh=True)
        self.assertEqual(p["snapshot"], None)
        self.assertEqual(self.calls, [])

    def test_first_get_fetches_then_caches_until_a_refresh_after_the_interval(self):
        b = self.board()
        p = b.get(3)
        self.assertTrue(p["snapshot"]["ok"])
        n = len(self.calls)
        self.assertEqual(len(b.get(3)["snapshot"]["mine"]["rows"]), 2)
        self.assertEqual(len(self.calls), n, "a plain get serves the cache")
        b.get(3, refresh=True)
        self.assertEqual(len(self.calls), n, "a refresh inside the minimum interval is ignored")
        self.t[0] += 46
        b.get(3, refresh=True)
        self.assertGreater(len(self.calls), n)
        self.assertEqual(b.peek()["age_seconds"], 0)

    def test_a_failed_refresh_keeps_the_last_snapshot_and_names_the_error(self):
        b = self.board()
        b.get(3)
        b.fetch = lambda url: (_ for _ in ()).throw(OSError("offline"))
        self.t[0] += 100
        p = b.get(3, refresh=True)
        self.assertTrue(p["snapshot"]["ok"])
        self.assertIn("offline", p["error"])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestRoute(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        enrich(self.td.name)                 # the schedule, so the landing page has a matchup to hang the panel on
        plant(self.td.name)
        self.root = Root(self.td.name)
        self.calls = []

    def tearDown(self):
        self.td.cleanup()

    def counting(self, url):
        self.calls.append(url)
        return fake_fetch(url)

    def client(self, board, overlay=None):
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", overlay=overlay, live=board)
        app.testing = True
        return app.test_client()

    def test_home_renders_the_panel_without_touching_the_network(self):
        c = self.client(live.LiveBoard(self.root, MY_TEAM, league_id="L", fetch=self.counting))
        body = c.get("/").get_data(as_text=True)
        self.assertIn('id="live"', body)
        self.assertIn('data-enabled="1"', body)
        self.assertIn("auto-refresh", body)
        self.assertEqual(self.calls, [], "a page render never fetches; only /api/live does")

    def test_api_live_fetches_applies_the_overlay_and_writes_nothing(self):
        before = self.root.tree_digest()
        ov = Overlay({MY_TEAM: "CANARY-REAL-ME", OPP: "CANARY-REAL-OPP"})
        c = self.client(live.LiveBoard(self.root, MY_TEAM, league_id="L", fetch=self.counting), overlay=ov)
        j = c.get("/api/live?refresh=1").get_json()
        self.assertTrue(j["enabled"])
        self.assertEqual(j["snapshot"]["team"], "CANARY-REAL-ME")
        self.assertEqual(j["snapshot"]["opponent"], "CANARY-REAL-OPP")
        self.assertEqual(j["snapshot"]["mine"]["banked"], 31.5)
        for _ in range(5):
            c.get("/api/live?refresh=1")
        self.assertEqual(self.root.tree_digest(), before, "refreshes must leave every file under data/ untouched")

    def test_the_default_board_is_disabled_on_a_runner(self):
        old = os.environ.get("GITHUB_ACTIONS")
        os.environ["GITHUB_ACTIONS"] = "true"
        try:
            self.assertFalse(live.LiveBoard.default(self.root, MY_TEAM).enabled)
        finally:
            if old is None:
                del os.environ["GITHUB_ACTIONS"]
            else:
                os.environ["GITHUB_ACTIONS"] = old


if __name__ == "__main__":
    unittest.main()

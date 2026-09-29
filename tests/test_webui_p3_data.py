"""
tests.test_webui_p3_data -- three P3 items built from data on disk (docs/WEB_UI_ROADMAP.md
UI-W6, T6, H4).

UI-W6  FAAB standings and pace: each team's budget left (the league's 100, WAIVER_MECHANICS),
       what it has spent, its spend per completed week, and who can outbid the owner --
       every team with more budget left.
UI-T6  The trade finder ordered by acceptability: by the SMALLER of the two sides' gains (a
       deal the other side loses on will not happen), with the other side's positional need
       -- the starting slot where its best lineup falls furthest below the league's average.
UI-H4  Records only a model can keep, this season so far: the least likely win (by the quoted
       pre-game chance), the biggest comeback (the lowest playoff odds at any forecast for a
       team now in a playoff place), the biggest collapse (the reverse), and the most
       improbable champion once there is one.
"""
import json
import os
import tempfile
import unittest

from webui import render

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import CB, CM, NW, QF, plant
    from tests.test_webui_routes import TEAMS
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


class TestTradeOrder(unittest.TestCase):
    def test_ordered_by_the_smaller_gain_with_their_need(self):
        rec = {"tool": "find_trades", "team": "Me", "week": 3, "_needs": {"B": "QB", "C": "RB", "D": "WR"},
               "buy": [{"with": "B", "target": "x", "my_gain": 5.0, "their_gain": -1.0},
                       {"with": "C", "target": "y", "my_gain": 3.0, "their_gain": 2.0},
                       {"with": "D", "target": "z", "my_gain": 1.0, "their_gain": 4.0}]}
        view = render.record_view(rec)
        buy = next(s for s in view["sections"] if s and s.get("title") == "Buy")
        keys = [c["key"] for c in buy["columns"]]

        def column(key):
            return [r["cells"][keys.index(key)]["text"] for r in buy["rows"]]
        self.assertEqual(column("with"), ["C", "D", "B"])
        self.assertEqual(column("their_need"), ["RB", "WR", "QB"])
        self.assertEqual(column("both_gain"), ["+2.0", "+1.0", "-1.0"])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def write(self, rel, data):
        p = os.path.join(self.td.name, "data", *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def get(self, path, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)


class TestFaab(Case):
    """The fixture's budgets are 90 - i in fixture order; Cosmic Badgers raised to 95 here. Week 3,
    two weeks done: the owner has spent 10, 5.0 a week."""

    def setUp(self):
        super().setUp()
        p = os.path.join(self.td.name, "data", "current", "league_standings.json")
        with open(p, encoding="utf-8") as fh:
            st = json.load(fh)
        st[CB]["remaining_faab"] = 95
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(st, fh)

    def test_pace_and_who_can_outbid_me(self):
        from webui.players_page import faab_table
        t = faab_table(self.root, MY_TEAM)
        rows = {r["team"]: r for r in t["rows"]}
        self.assertEqual((rows[QF]["left"], rows[QF]["spent"], rows[QF]["per_week"]), (90.0, 10.0, 5.0))
        self.assertEqual(t["outbid"], [CB])

    def test_the_waiver_board_shows_it_in_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                text = visible_text(self.get("/waivers", mode))
                self.assertIn("Budgets", text)
                self.assertIn("can outbid you", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


class TestNeeds(Case):
    def test_the_slot_furthest_below_the_league(self):
        from webui import trade
        self.write("current/player_baselines.json", {"A QB": {"pos": "QB", "mean": 20.0}, "A RB": {"pos": "RB", "mean": 15.0},
                                                     "B QB": {"pos": "QB", "mean": 10.0}, "B RB": {"pos": "RB", "mean": 18.0}})
        self.write("current/live_rosters.json", {QF: [{"name": "A QB"}, {"name": "A RB"}], NW: [{"name": "B QB"}, {"name": "B RB"}]})
        n = trade.needs(self.root)
        self.assertEqual((n[QF], n[NW]), ("RB", "QB"))


class TestModelRecords(Case):
    """Week 2: Cosmic Badgers won as played at a quoted 29%. A planted week-2 forecast: Crimson
    Marmots at 5.0% then 86.5% now, and in a playoff place; Neon Walruses at 99.0% then 92.5%
    now, fifth."""

    def setUp(self):
        super().setUp()
        self.write("weeks/week_02/live_season_forecast_week_2.json",
                   {t: {"current_state": {"actual_wins_banked": 1.0},
                        "forecast": {"playoff_probability_pct": {CM: 5.0, NW: 99.0}.get(t, 50.0), "playoff_standard_error": 0.5}}
                    for t in TEAMS})

    def test_the_planted_records(self):
        from webui.history import model_records
        r = model_records(self.root)
        self.assertEqual((r["least_likely_win"]["team"], r["least_likely_win"]["p"]), (CB, 0.29))
        self.assertEqual((r["comeback"]["team"], r["comeback"]["low"], r["comeback"]["week"]), (CM, 5.0, 2))
        self.assertEqual((r["collapse"]["team"], r["collapse"]["high"]), (NW, 99.0))
        self.assertIsNone(r["champion"])

    def test_history_shows_them(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                text = visible_text(self.get("/history", mode))
                self.assertIn("Records only a model can keep", text)
                self.assertIn("29%", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

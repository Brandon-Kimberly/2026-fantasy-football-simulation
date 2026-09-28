"""
tests.test_webui_wave4 -- the roadmap's fourth wave: game day and the big screen
(docs/WEB_UI_ROADMAP.md).

UI-T1, the trade builder. "Is this trade good?" was a form and a six-minute wait. The builder
picks players from both rosters and answers at once with an estimate: each side's best
starting lineup, week by week over the rest of the regular season, before and after (an
exact assignment to the league's 13 starting slots, byes respected), the bye weeks the deal
leaves a slot empty, and the drop a two-for-one forces at the 19-man active limit. It says
it is an estimate, and hands the deal to the paired simulation for the real answer.

League, second pass. UI-O2 a power rating from the simulation itself: the mean of a team's
row in the head-to-head matrix, its chance to beat an average league opponent (the matrix
carries no standard errors, so no rank interval is invented). UI-O4 the schedule left: the
average chance to win the games still to play, beside that rating. UI-O11 the playoff
bracket, "if it ended today", with each seed's chance of that seed. UI-F5 a pending trade
as its two sides, not a key/value dump.

UI-M1, Home knows what day of the week it is. A fantasy week has phases and Home behaved
the same through all of them. Before the first kickoff it previews the game and says how
last week ended (as the league counted it -- F83); from the first kickoff the live number
leads (UI-F1); once nobody on either side has a game left the hero states the RESULT, not a
probability.
"""
import json
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui.glance import home_report
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import CB, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def kickoffs(root, when):
    p = os.path.join(root, "data", "current", "nfl_schedule.json")
    with open(p, encoding="utf-8") as fh:
        s = json.load(fh)
    s.setdefault("_meta", {}).setdefault("kickoffs", {})["3"] = [when]
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(s, fh)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPhase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)                       # week 3 current; week 2 lost as played, won re-scored
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def home(self, mode="simple"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client().get("/").get_data(as_text=True)

    def test_before_the_first_kickoff_is_the_preview(self):
        kickoffs(self.td.name, "2099-01-01T17:00:00Z")
        self.assertEqual(home_report(self.root, MY_TEAM)["phase"], "before")

    def test_after_a_kickoff_is_live(self):
        kickoffs(self.td.name, "2026-01-01T17:00:00Z")
        self.assertEqual(home_report(self.root, MY_TEAM)["phase"], "live")

    def test_last_week_as_the_league_counted_it(self):
        last = home_report(self.root, MY_TEAM)["last_result"]
        self.assertEqual((last["week"], last["opponent"], last["result"], last["rescored"]), (2, CB, "L", True))
        self.assertEqual((last["mine"], last["theirs"], last["quote"]), (148.52, 144.19, 0.71))

    def test_home_says_how_last_week_ended_in_both_views(self):
        kickoffs(self.td.name, "2099-01-01T17:00:00Z")
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                text = visible_text(self.home(mode))
                self.assertIn("Last week", text)
                self.assertIn("lost to", text)
                self.assertIn("re-scored", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


def _player(pos, mean, bye, slots=None, on_ir=False, pid="1"):
    return {"pos": pos, "mean": mean, "bye": bye, "slots": slots or [pos], "on_ir": on_ir, "player_id": pid,
            "team": "GB", "injury_status": None}


class TestLineup(unittest.TestCase):
    def test_the_limit_is_the_engines(self):
        from fantasy_sim.decisions import ACTIVE_ROSTER_LIMIT
        from webui import trade
        self.assertEqual(trade.ACTIVE_ROSTER_LIMIT, ACTIVE_ROSTER_LIMIT)

    def test_the_best_lineup_fills_flex_with_the_best_leftover(self):
        from webui import trade
        players = {"Q": _player("QB", 20, 5), "R1": _player("RB", 15, 5), "R2": _player("RB", 14, 5),
                   "R3": _player("RB", 13, 5), "W1": _player("WR", 12, 5), "W2": _player("WR", 11, 5),
                   "W3": _player("WR", 10, 5), "T": _player("TE", 9, 5), "K": _player("K", 8, 5),
                   "D": _player("DL", 7, 5), "L": _player("LB", 6, 5), "B": _player("DB", 5, 5), "X": _player("WR", 1, 5)}
        total, used, empty = trade.best_lineup(players)
        self.assertEqual(empty, 0)
        self.assertEqual(total, 20 + 15 + 14 + 12 + 11 + 9 + 8 + 7 + 6 + 5 + 13 + 10 + 1)   # FLEX: R3, W3 and X
        self.assertIn("X", used, "the third FLEX goes to the best leftover, however poor")

    def test_a_bye_or_ir_leaves_a_slot_empty(self):
        from webui import trade
        players = {"Q": _player("QB", 20, 6), "K": _player("K", 8, 5, on_ir=True)}
        total, _used, empty = trade.best_lineup(players, week=6)
        self.assertEqual((total, empty), (0.0, 13))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTradeEstimate(unittest.TestCase):
    """Mine gives a 12-point WR for their 14-point WR and a 3-point TE: I get better at WR,
    they lose their only TE, and I go to 20 active and must drop my weakest bench player."""

    def setUp(self):
        from tests.test_webui_routes import build_tree
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        base, rosters = {}, {MY_TEAM: [], CB: []}
        mine = [("QB", 20), ("RB", 15), ("RB", 14), ("WR", 12), ("WR", 11), ("TE", 9), ("K", 8), ("DL", 7),
                ("LB", 6), ("DB", 5)] + [("RB", 4)] * 9
        theirs = [("QB", 18), ("RB", 13), ("RB", 12), ("WR", 14), ("WR", 10), ("TE", 3), ("K", 7), ("DL", 6),
                  ("LB", 5), ("DB", 4), ("WR", 2)]
        for team, spec, tag in ((MY_TEAM, mine, "m"), (CB, theirs, "t")):
            for i, (pos, mean) in enumerate(spec):
                name = f"{tag}-{pos}-p{i}"          # not "mRB11": that spells the audit codes R1 and B11
                base[name] = _player(pos, float(mean), None if name != "t-TE-p5" else 6, pid=f"{tag}{i}")   # only their TE has a bye
                rosters[team].append({"name": name, "pos": pos, "team": "GB", "on_ir": False})
        with open(os.path.join(self.td.name, "data", "current", "player_baselines.json"), "w", encoding="utf-8") as fh:
            json.dump(base, fh)
        with open(os.path.join(self.td.name, "data", "current", "live_rosters.json"), "w", encoding="utf-8") as fh:
            json.dump(rosters, fh)
        with open(os.path.join(self.td.name, "data", "current", "league_schedule.json"), "w", encoding="utf-8") as fh:
            json.dump([[[MY_TEAM, CB]]] * 14, fh)
        self.root = Root(self.td.name)

    def test_each_side_week_by_week_and_the_forced_drop(self):
        from webui import trade
        e = trade.estimate(self.root, MY_TEAM, ["m-WR-p3"], CB, ["t-WR-p3", "t-TE-p5"])
        me, them = e["sides"][MY_TEAM], e["sides"][CB]
        self.assertEqual(me["per_week"], 2.0, "WR 12 out, WR 14 in; the 3-point TE sits")
        self.assertLess(them["per_week"], 0)
        self.assertEqual(them["new_holes"], [w for w in range(3, 15) if w != 6],
                         "no TE left -- every remaining week except 6, their TE's bye, which was already empty")
        self.assertEqual((me["active_after"], me["over"]), (20, 1))
        self.assertEqual(me["drops"][0]["mean"], 3.0, "the likely cut is the weakest non-starter")
        self.assertIn("/tools/evaluate_trade?", e["simulate"])

    def test_the_page_in_both_views(self):
        from webui.app import create_app
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                st = Settings(self.root)
                st.set_mode(mode)
                app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                                 live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
                app.testing = True
                body = app.test_client().get("/trade?with=cosmic-badgers&give=m3&get=t3&get=t5").get_data(as_text=True)
                text = visible_text(body)
                self.assertIn("Trade builder", text)
                self.assertIn("+2.0", text)
                self.assertIn("estimate", text)
                self.assertIn("/tools/evaluate_trade?", body)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestLeagueSecondPass(unittest.TestCase):
    def setUp(self):
        from tests.test_webui_objects import IW, NW, QF
        self.QF, self.IW, self.NW = QF, IW, NW
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)                 # week 3 current; QF plays IW in weeks 3-14
        m = os.path.join(self.td.name, "data", "weeks", "week_03", "syndicate_comprehensive_matrix_week_3.json")
        with open(m, encoding="utf-8") as fh:
            mx = json.load(fh)
        mx["h2h_win_probability_matrix"] = {QF: {QF: None, IW: 64.0, CB: 70.0, NW: 50.0},
                                            IW: {QF: 36.0, IW: None, CB: 45.0, NW: 40.0}}
        with open(m, "w", encoding="utf-8") as fh:
            json.dump(mx, fh)
        d = os.path.join(self.td.name, "data", "current")
        with open(os.path.join(d, "playoff_bracket.json"), "w", encoding="utf-8") as fh:
            json.dump({"playoff_week_start": 15, "playoff_teams": 4, "seeds": [QF, IW, CB, NW],
                       "rounds": [{"round": 1, "match": 1, "t1": QF, "t2": NW, "winner": None},
                                  {"round": 1, "match": 2, "t1": IW, "t2": CB, "winner": None},
                                  {"round": 2, "match": 3, "t1": None, "t2": None, "winner": None, "place": 1}]}, fh)
        with open(os.path.join(d, "pending_trades.json"), "w", encoding="utf-8") as fh:
            json.dump({"_meta": {"week": 3, "n": 1}, "trades": [{"transaction_id": "p1", "week": 3, "status": "pending",
                       "teams": [QF, IW], "players": [{"player_id": "100", "name": "Player 0 O'Neil", "to_team": IW, "from_team": QF},
                                                      {"player_id": "106", "name": "Player 6 O'Neil", "to_team": QF, "from_team": IW}]}]}, fh)
        self.root = Root(self.td.name)

    def get(self, path, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client().get(path).get_data(as_text=True)

    def test_power_is_the_mean_chance_to_beat_a_league_opponent(self):
        from webui.objects import league_extras
        p = {r["team"]: r for r in league_extras(self.root, MY_TEAM)["power"]}
        self.assertAlmostEqual(p[self.QF]["rating"], (0.64 + 0.70 + 0.50) / 3, places=4)
        self.assertEqual(p[self.QF]["rank"], 1)

    def test_the_schedule_left_is_the_mean_chance_in_the_games_to_come(self):
        from webui.objects import league_extras
        p = {r["team"]: r for r in league_extras(self.root, MY_TEAM)["power"]}
        self.assertAlmostEqual(p[self.QF]["remaining"], 0.64, places=4, msg="Iron Wombats every week from 3 to 14")
        self.assertEqual(p[self.QF]["games_left"], 12)

    def test_the_bracket_if_it_ended_today(self):
        from webui.objects import league_extras
        b = league_extras(self.root, MY_TEAM)["bracket"]
        self.assertEqual([s["team"] for s in b["seeds"]], [self.QF, self.IW, CB, self.NW])
        self.assertTrue(b["projected"])
        self.assertAlmostEqual(b["seeds"][0]["p_seed"], 12.5, places=1, msg="the fixture's Seed 1 chance")

    def test_league_shows_all_three_and_the_pending_trade_as_two_sides(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/league", mode)
                text = visible_text(body)
                for s in ("Power", "If the season ended today", "in review"):
                    self.assertIn(s, text)
                self.assertNotIn("transaction id", text)
                self.assertIn("Player 6 O'Neil", body.replace("&#39;", "'"))
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

"""
tests.test_webui_ninth_batch -- three small roadmap items (docs/WEB_UI_ROADMAP.md).

UI-W3  The waiver results card: the most recent daily run (09:00 Pacific), every claim
       that won it -- team, player, drop, winning bid -- and each claim's paired-simulation
       grade from the decision log, or "not graded yet". Only winning claims are on file
       (a losing bid is not logged: Decision 4), and the card says so.
UI-T3  A trade's contributing factors, computed: which starters go out and come in, the
       starting line slot by slot, the weeks two or more starters share a bye that did
       not before, and each position's depth after the deal.
UI-R6  Luck, pulled not pushed: a Luck page reached from team pages and the palette, never
       from Home, showing the pre-registered ledger's five measures (fantasy_sim.luck_ledger,
       a pure module) with standard errors -- and z and p withheld below the pre-registered
       six weeks. Built from the files on disk. No sixth measure and no combined score.
"""
import json
import os
import re
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
    from tests.test_webui_objects import NW, RP, TL, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


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


class TestWaiverRun(Case):
    """The fixture's one claim ran 2026-09-16; two more planted here won the 2026-09-25 run
    (16:00Z is 09:00 Pacific), one graded +2.5 +- 1.0, one not yet."""

    def setUp(self):
        super().setUp()
        rows = [{"transaction_id": "t5", "type": "waiver", "week": 3, "created": "2026-09-25T16:00:05Z", "is_mine": False,
                 "teams": [RP], "faab_bid": 12,
                 "adds": [{"name": "Jordyn Brooks", "player_id": "5", "to_team": RP, "projection": {"pos": "LB", "mean": 10.4}}],
                 "drops": [{"name": "Barrett Carter", "player_id": "6", "to_team": RP, "projection": {"pos": "LB", "mean": 7.9}}]},
                {"transaction_id": "t6", "type": "waiver", "week": 3, "created": "2026-09-25T16:00:07Z", "is_mine": False,
                 "teams": [TL], "faab_bid": 3,
                 "adds": [{"name": "Zach Charbonnet", "player_id": "7", "to_team": TL, "projection": {"pos": "RB", "mean": 8.8}}],
                 "drops": []},
                {"record_type": "evaluation", "transaction_id": "t5", "evaluated_at": "2026-09-26T00:00:00Z", "n_sims": 3000,
                 "batches": 10, "post_execution_reversed": False,
                 "teams": {RP: {"playoff_pct": {"delta": 2.5, "se": 1.0, "with": 40, "without": 37.5},
                                "champ_pct": {"delta": 0.5, "se": 0.4, "with": 6, "without": 5.5}}}}]
        with open(os.path.join(self.td.name, "data", "logs", "decision_log.jsonl"), "a", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))

    def test_the_latest_run_with_every_winner_bid_and_grade(self):
        from webui.players_page import waiver_run
        run = waiver_run(self.root)
        self.assertEqual(run["date"], "2026-09-25")
        self.assertEqual([(c["team"], c["bid"]) for c in run["claims"]], [(RP, 12), (TL, 3)])
        g = run["claims"][0]["grade"]
        self.assertEqual((g["delta"], g["se"], g["verdict"]["tier"]), (2.5, 1.0, "modest"))
        self.assertIsNone(run["claims"][1]["grade"])

    def test_the_waivers_page_shows_the_card_in_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                text = visible_text(self.get("/waivers", mode))
                for s in ("The last waiver run", "Jordyn Brooks", "bid 12", "a modest gain", "not graded yet",
                          "Losing bids are not recorded"):
                    self.assertIn(s, text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


class TestTradeFactors(Case):
    """Quantum Ferrets swap their WR (bye 9) for Neon Walruses' WR (bye 7), who shares week 7
    with their RB -- a bye collision the deal creates."""

    def setUp(self):
        super().setUp()
        base = {"A QB": {"pos": "QB", "mean": 20.0, "bye": 5, "player_id": "a1"},
                "A RB": {"pos": "RB", "mean": 15.0, "bye": 7, "player_id": "a2"},
                "A WR": {"pos": "WR", "mean": 12.0, "bye": 9, "player_id": "a3"},
                "B WR": {"pos": "WR", "mean": 11.0, "bye": 7, "player_id": "b1"},
                "B TE": {"pos": "TE", "mean": 8.0, "bye": 6, "player_id": "b2"}}
        self.write("current/player_baselines.json", base)
        self.write("current/live_rosters.json", {MY_TEAM: [{"name": n} for n in ("A QB", "A RB", "A WR")],
                                                 NW: [{"name": n} for n in ("B WR", "B TE")]})

    def test_the_collision_the_starters_and_the_depth(self):
        from webui import trade
        f = trade.estimate(self.root, MY_TEAM, ["A WR"], NW, ["B WR"])["sides"][MY_TEAM]["factors"]
        self.assertEqual((f["out"], f["in"]), (["A WR"], ["B WR"]))
        self.assertEqual(f["byes"], [{"week": 7, "players": ["A RB", "B WR"]}])
        self.assertEqual(f["depth"]["WR"], {"before": 1, "after": 1})
        wr = next(s for s in f["slots"] if s["after"] == "B WR")
        self.assertEqual((wr["before"], wr["delta"]), ("A WR", -1.0))

    def test_the_trade_page_lists_it(self):
        text = visible_text(self.get("/trade?with=neon-walruses&give=a3&get=b1", "simple"))
        self.assertIn("Week 7", text)
        self.assertIn("share a bye", text)
        self.assertEqual([t for t in DEV_TERMS if t in text], [])


MEASURES = ("Schedule luck", "Opponent luck", "Close games", "Starters who did not play", "Scoring luck")


class TestLuck(Case):
    def test_exactly_the_five_measures_and_no_combined_score(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/luck", mode)
                rows = re.findall(r'<tr class="measure"', body)
                self.assertEqual(len(rows), 5, "five pre-registered measures, no sixth")
                text = visible_text(body)
                for m in MEASURES:
                    self.assertIn(m, text)
                for combined in ("luck score", "overall luck", "total luck", "luck index"):
                    self.assertNotIn(combined, text.lower())
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])

    def test_below_six_weeks_z_and_p_are_withheld(self):
        text = visible_text(self.get("/luck", "simple"))
        self.assertIn("2 weeks", text)
        self.assertIn("too early", text)
        self.assertNotRegex(text, r"p\s*0\.\d{3}")

    def test_reached_from_team_pages_and_the_palette_never_from_home(self):
        self.assertIn('href="/luck', self.get("/team/quantum-ferrets", "simple"))
        self.assertNotIn('href="/luck', self.get("/", "simple"))
        self.assertIn('"/luck"', self.get("/", "simple"), "the palette's items")

    def test_another_team(self):
        self.assertIn("Neon Walruses", visible_text(self.get("/luck?team=neon-walruses", "simple")))


if __name__ == "__main__":
    unittest.main()

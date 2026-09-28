"""
tests.test_webui_history -- the league's history (docs/WEB_UI_ROADMAP.md UI-H1, H2, H3).

Seasons 2025 (archived, data/logs/season_2025.json) and 2026 are on disk and no page showed
them as history. The record book, the rivalries and the draft board follow the rules the
open-source almanacs learned the hard way: a median game is never a head-to-head result, and
only the championship path counts as playoffs -- which the 2025 archive cannot separate from
consolation games, so both count the REGULAR SEASON only and say so. 2026 results come as the
league counted them (webui.results; F83), and a re-scored box score is marked.
"""
import json
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui import history                     # outside the probe: a missing module must fail, not skip
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import CB, IW, NW, QF, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def plant_2025(root):
    """Two regular weeks and one playoff week; roster 6 is Quantum Ferrets as in the real map."""
    rmap = {"1": "Neon Walruses", "4": "Cosmic Badgers", "6": "Quantum Ferrets", "7": "Iron Wombats"}
    m = lambda rid, mid, pts: {"roster_id": rid, "matchup_id": mid, "points": pts, "starters": [], "players": []}  # noqa: E731
    doc = {"season": "2025", "settings": {"playoff_week_start": 15}, "roster_map": rmap,
           "final_standings": {"Quantum Ferrets": {"wins": 2, "losses": 0, "ties": 0, "points_scored": 390.0}},
           "matchups": {"1": [m(6, 1, 201.5), m(4, 1, 120.0), m(1, 2, 150.0), m(7, 2, 150.0)],
                        "2": [m(6, 1, 188.5), m(1, 1, 99.25), m(4, 2, 140.0), m(7, 2, 139.0)],
                        "15": [m(6, 1, 250.0), m(4, 1, 90.0), m(1, None, 10.0), m(7, None, 20.0)]}}
    p = os.path.join(root, "data", "logs", "season_2025.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)                       # 2026: QF beat NW wk1, lost to CB wk2 as played (re-scored W)
        plant_2025(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def get(self, path, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)


class TestGames(Case):
    def test_regular_season_only_and_2026_as_played(self):
        g = history.games(self.root)
        self.assertFalse(any(x["season"] == "2025" and x["week"] >= 15 for x in g), "no playoff or consolation game")
        w2 = next(x for x in g if x["season"] == "2026" and x["week"] == 2 and QF in (x["a"], x["b"]))
        self.assertEqual((w2["winner"], w2["rescored"]), (CB, True))
        tie = next(x for x in g if x["season"] == "2025" and x["week"] == 1 and NW in (x["a"], x["b"]))
        self.assertIsNone(tie["winner"], "150-150 is a tie")


class TestRecordBook(Case):
    def test_the_records(self):
        r = {x["key"]: x for x in history.record_book(history.games(self.root))}
        self.assertEqual((r["high"]["team"], r["high"]["value"], r["high"]["season"]), (QF, 201.5, "2025"),
                         "the 250 was a playoff game and does not count")
        self.assertEqual((r["low"]["team"], r["low"]["value"]), (NW, 99.25))
        self.assertEqual((r["margin"]["team"], r["margin"]["value"]), (QF, 89.25), "188.5 - 99.25")
        self.assertEqual(r["close"]["value"], 0.0, "the tie")


class TestRivalries(Case):
    def test_a_pair_across_seasons(self):
        rv = history.rivalries(history.games(self.root))
        qc = rv[tuple(sorted((QF, CB)))]
        wins = {QF: qc["wins"][QF], CB: qc["wins"][CB]}
        self.assertEqual(wins, {QF: 1, CB: 1}, "2025 week 1 to QF; 2026 week 2 to CB as the league played it")
        self.assertEqual(qc["games"], 2)


class TestDraft(Case):
    def test_the_board(self):
        p = os.path.join(self.td.name, "data", "logs", "draft_2026.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"season": "2026", "settings": {"rounds": 2, "teams": 2}, "picks": [
                {"pick_no": 1, "round": 1, "draft_slot": 1, "team": QF, "player_id": "100", "name": "Player 0 O'Neil", "pos": "QB", "nfl_team": "GB"},
                {"pick_no": 2, "round": 1, "draft_slot": 2, "team": IW, "player_id": "106", "name": "Player 6 O'Neil", "pos": "QB", "nfl_team": "GB"},
                {"pick_no": 3, "round": 2, "draft_slot": 2, "team": IW, "player_id": "107", "name": "Player 7 O'Neil", "pos": "QB", "nfl_team": "GB"}]}, fh)
        b = history.draft_board(self.root, "2026")
        self.assertEqual((b["rounds"], b["slots"]), (2, 2))
        cell = b["grid"][0][0]
        self.assertEqual((cell["name"], cell["team"], cell["mean"], cell["kept"]), ("Player 0 O'Neil", QF, 17.2, True))
        self.assertIsNone(b["grid"][1][0], "round 2, slot 1 was never made")


class TestPages(Case):
    def test_both_pages_in_both_views(self):
        for mode in ("dev", "simple"):
            for path in ("/history", "/draft"):
                with self.subTest(mode=mode, path=path):
                    text = visible_text(self.get(path, mode))
                    if mode == "simple":
                        self.assertEqual([t for t in DEV_TERMS if t in text], [])
        text = visible_text(self.get("/history", "simple"))
        for s in ("Record book", "regular season", "Rivalries", "201.5"):
            self.assertIn(s, text)
        self.assertIn('href="/history"', self.get("/", "simple"))


if __name__ == "__main__":
    unittest.main()

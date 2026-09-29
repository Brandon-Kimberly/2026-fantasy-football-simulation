"""
tests.test_webui_tenth_batch -- three roadmap items (docs/WEB_UI_ROADMAP.md UI-M4, E3, M3).

UI-M4  Every category the league scores has a readable label in the live feed, checked
       against the league's own scoring settings pinned in tests/fixtures/golden_sync; and a
       starter's live row carries its category totals, priced with the league's weights, for
       a tooltip.
UI-E3  A read-only JSON API -- /api/team/<slug>, /api/player/<id>, /api/week/<n>, /api/odds --
       each built on the helper its page uses, returning the same numbers, writing nothing
       and fetching nothing.
UI-M3  Where the matchup is decided: the two lineups paired slot by slot, each pair's chance
       that the owner's player outscores the other (normal curves from this week's means and
       the simulation's spread -- the page says so) and each slot's share of the variance of
       the final margin, which sum to one. The pairing is a presentation choice and the page
       says that too.
"""
import hashlib
import json
import math
import os
import tempfile
import unittest
from unittest.mock import patch

from webui import live, matchup_split           # outside the probe: a missing module must fail, not skip

HERE = os.path.dirname(os.path.abspath(__file__))


class TestEveryScoredCategoryHasALabel(unittest.TestCase):
    def test_the_leagues_own_settings(self):
        with open(os.path.join(HERE, "fixtures", "golden_sync", "scoring_settings.json"), encoding="utf-8") as fh:
            scoring = json.load(fh)
        missing = sorted(k for k, v in scoring.items() if v and k not in live.STAT_LABELS)
        self.assertEqual(missing, [])

    def test_a_starters_row_carries_its_categories(self):
        snap = {"ok": True, "stats": {"1": {"idp_tkl_solo": 4, "idp_sack": 1, "idp_tkl_ast": 0}},
                "mine": {"rows": [{"pid": "1"}]}, "theirs": {"rows": [{"pid": "2"}]}}
        live.add_categories(snap, {"idp_tkl_solo": 1.5, "idp_sack": 4.0, "idp_tkl_ast": 0.75})
        self.assertEqual(snap["mine"]["rows"][0]["cats"], [{"text": "4 solo tackles", "pts": 6.0}, {"text": "sack", "pts": 4.0}])
        self.assertEqual(snap["theirs"]["rows"][0]["cats"], [])


def phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


class TestTheSplit(unittest.TestCase):
    """Two slots with known curves: QB 20 +- 6 against 16 +- 8; RB 12 +- 5 and 9 +- 4 against
    14 +- 6 and 7 +- 3 (paired by rank within the slot: 12 with 14, 9 with 7)."""

    MINE = [{"slot": "QB", "name": "q1", "expected": 20.0}, {"slot": "RB", "name": "r2", "expected": 9.0},
            {"slot": "RB", "name": "r1", "expected": 12.0}]
    THEIRS = [{"slot": "QB", "name": "Q1", "expected": 16.0}, {"slot": "RB", "name": "R1", "expected": 14.0},
              {"slot": "RB", "name": "R2", "expected": 7.0}]
    SD = {"q1": 6.0, "r1": 5.0, "r2": 4.0, "Q1": 8.0, "R1": 6.0, "R2": 3.0}

    def setUp(self):
        self.s = matchup_split.split(self.MINE, self.THEIRS, self.SD)

    def test_each_pair_matches_a_direct_calculation(self):
        rows = {(r["mine"], r["theirs"]): r for r in self.s["rows"]}
        self.assertAlmostEqual(rows[("q1", "Q1")]["p"], phi(4.0 / math.sqrt(36 + 64)), places=9)
        self.assertAlmostEqual(rows[("r1", "R1")]["p"], phi(-2.0 / math.sqrt(25 + 36)), places=9)
        self.assertAlmostEqual(rows[("r2", "R2")]["p"], phi(2.0 / math.sqrt(16 + 9)), places=9)

    def test_the_variance_shares_sum_to_one(self):
        self.assertAlmostEqual(sum(r["share"] for r in self.s["rows"]), 1.0, places=9)
        top = max(self.s["rows"], key=lambda r: r["share"])
        self.assertEqual(top["mine"], "q1", "the QB pair carries 100 of the 195 units of variance")

    def test_a_player_without_a_spread_is_left_out_and_counted(self):
        s = matchup_split.split(self.MINE, self.THEIRS, dict(self.SD, R2=None))
        self.assertEqual(len(s["rows"]), 2)
        self.assertEqual(s["left_out"], 1)


try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui import objects
    from webui.app import create_app
    from webui.glance import odds_now
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import QF, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def tree_digest(root):
    h = hashlib.sha256()
    for d, _dirs, files in sorted(os.walk(os.path.join(root, "data"))):
        for f in sorted(files):
            p = os.path.join(d, f)
            h.update(p.encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)
        st = Settings(self.root)
        st.set_mode("simple")
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        self.c = app.test_client()

    def tearDown(self):
        self.td.cleanup()


class TestJsonApi(Case):
    def test_each_endpoint_mirrors_its_page_helper(self):
        t = self.c.get("/api/team/quantum-ferrets").get_json()
        ref = objects.team_report(self.root, QF, MY_TEAM)
        self.assertEqual((t["rank"], t["odds"], t["row"]["all_play"]), (ref["rank"], ref["odds"], ref["row"]["all_play"]))
        p = self.c.get("/api/player/100").get_json()
        self.assertEqual((p["name"], p["owner"]), ("Player 0 O'Neil", QF))
        w = self.c.get("/api/week/2").get_json()
        self.assertEqual(len(w["games"]), 4)
        self.assertEqual(self.c.get("/api/odds").get_json()["week"], odds_now(self.root)["week"])

    def test_unknown_objects_are_404(self):
        for path in ("/api/team/nobody", "/api/player/999999", "/api/week/40"):
            self.assertEqual(self.c.get(path).status_code, 404, path)

    def test_nothing_is_written_and_nothing_is_fetched(self):
        before = tree_digest(self.td.name)

        def refuse(*a, **k):
            raise AssertionError("the API reached the network")
        with patch("urllib.request.urlopen", refuse), patch("requests.get", refuse), patch("requests.Session.get", refuse):
            for path in ("/api/team/quantum-ferrets", "/api/player/100", "/api/week/2", "/api/odds"):
                self.assertEqual(self.c.get(path).status_code, 200, path)
        self.assertEqual(tree_digest(self.td.name), before)


class TestSplitOnMatchups(Case):
    def test_the_current_week_shows_where_it_is_decided(self):
        rec = {"tool": "matchup_lineup", "week": 3, "team": MY_TEAM, "opponent": "Iron Wombats",
               "constructions": {"max_mean": {"lineup": [{"slot": "QB", "name": "Player 0 O'Neil", "expected": 17.0}],
                                              "mean": 17.0, "p_beat_opponent": 0.6, "p_beat_median": 0.6}},
               "opponent_lineup": [{"slot": "QB", "name": "Player 6 O'Neil", "expected": 15.0}]}
        with open(os.path.join(self.td.name, "data", "decisions", "week_03", "matchup_20260927T170804Z_week3.json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        with open(os.path.join(self.td.name, "data", "weeks", "week_03", "player_variance.json"), "w", encoding="utf-8") as fh:
            json.dump({QF: [{"name": "Player 0 O'Neil", "std": 6.0}], "Iron Wombats": [{"name": "Player 6 O'Neil", "std": 8.0}]}, fh)
        text = visible_text(self.c.get("/matchups/week-3").get_data(as_text=True))
        self.assertIn("Where this game is decided", text)
        self.assertIn(f"{100 * phi(2.0 / 10.0):.0f}%", text)
        self.assertIn("presentation", text)
        self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

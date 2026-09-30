"""
tests.test_webui_standings -- one standings helper for every page (docs/WEB_UI_ROADMAP.md UI-O1).

In this league half the wins come from the median game, and the standings fold them into one
number. `webui.standings.table` is the one place a standings row is built, and League, Home
and a team page all render from it: the combined record and its two halves (shown only when
the results reconcile with the league's total, UI-F4), all-play, points for and against, the
streak (both games a week, as Sleeper counts it -- audit 2026-09-29), games back of the last playoff place (or the cushion over the first team
out), playoff and title odds, and a clinch mark when one is proven (UI-O10).

The fixture season (tests.test_webui_objects.plant): weeks 1 and 2, week 2 counted as played
(F83), standings points 300 + i in fixture order. Planted here: Iron Wombats TIE the week-1
median. Worked by hand:
  order          TL 4 wins, CM 3, then the 2-win teams by points CB 304, RP 302, NW 301, QF 300,
                 then PY 1, IW 0
  Quantum Ferrets  head-to-head 1-1, median 1-1, combined 2-2; all-play 7-0 then 3-4 = 10-4;
                 against 140 + 144.19 = 284.19; streak L2 (week 2's two losses as played;
                 head-to-head only it read L1 until 2026-09-29)
  Cosmic Badgers   streak L1 (W L / W L; head-to-head only it read W2)
  Iron Wombats     median 0-1-1; all-play 0-14
  games back     fourth is RP on 2: NW and QF 0 back (out on points), PY 1, IW 2;
                 cushion over fifth (NW, 2): TL +2, CM +1
Points against and all-play come from Sleeper's box scores, which re-score weeks 1-2 (F83); the
page says so while such a week is counted.
"""
import json
import os
import tempfile
import unittest

from webui import standings                      # outside the probe: a missing module must fail, not skip
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.glance import home_report
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import CB, CM, IW, NW, PY, QF, RP, TL, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        p = os.path.join(self.td.name, "data", "current", "weekly_actuals.json")
        with open(p, encoding="utf-8") as fh:
            wa = json.load(fh)
        wa["week_1"]["team_results"][IW]["median_win"] = 0.5
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(wa, fh)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def rows(self):
        return {r["team"]: r for r in standings.table(self.root)}

    def get(self, path, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)


class TestTheHelper(Case):
    def test_order_and_rank(self):
        self.assertEqual([r["team"] for r in standings.table(self.root)], [TL, CM, CB, RP, NW, QF, PY, IW])
        self.assertEqual(self.rows()[QF]["rank"], 6)

    def test_the_records(self):
        q = self.rows()[QF]
        self.assertEqual((q["h2h"]["text"], q["median"]["text"], q["combined"]["text"]), ("1–1", "1–1", "2–2"))
        self.assertEqual(q["all_play"]["text"], "10–4")
        self.assertEqual(self.rows()[IW]["all_play"]["text"], "0–14")

    def test_a_tie_against_the_median(self):
        self.assertEqual(self.rows()[IW]["median"]["text"], "0–1–1")

    def test_points_against_and_the_streak(self):
        r = self.rows()
        self.assertAlmostEqual(r[QF]["points_against"], 284.19, places=2)
        self.assertEqual((r[QF]["streak"], r[CB]["streak"]), ("L2", "L1"))

    def test_games_back_and_the_cushion(self):
        r = self.rows()
        self.assertEqual([r[t]["gb"] for t in (NW, QF, PY, IW)], [0, 0, 1, 2])
        self.assertEqual([r[t]["cushion"] for t in (TL, CM, CB, RP)], [2, 1, 0, 0])
        self.assertIsNone(r[TL]["gb"])
        self.assertIsNone(r[IW]["cushion"])

    def test_odds_and_the_rescored_weeks(self):
        r = self.rows()
        self.assertEqual(r[QF]["playoff"], 93.5)
        self.assertTrue(r[QF]["rescored"], "weeks 1-2 are re-scored box scores")


class TestPages(Case):
    def test_league_shows_every_column_in_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                text = visible_text(self.get("/league", mode))
                for s in ("All-play", "10–4", "284.19", "L2", "Games back", "0–1–1", "re-scored"):
                    self.assertIn(s, text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])

    def test_a_team_page_shows_its_row(self):
        text = visible_text(self.get("/team/quantum-ferrets", "simple"))
        for s in ("10–4", "all-play", "streak L2", "284.19"):
            self.assertIn(s, text)

    def test_home_takes_its_numbers_from_the_same_helper(self):
        home = {r["team"]: r for r in home_report(self.root, MY_TEAM)["standings"]}
        self.assertEqual(len(self.rows()), 8)
        for t, r in self.rows().items():
            self.assertEqual((home[t]["rank"], home[t]["wins"], home[t]["points"], home[t]["playoff"]),
                             (r["rank"], r["wins"], r["points_for"], r["playoff"]), t)
            self.assertEqual(home[t]["all_play"], r["all_play"], t)

    def test_a_proven_clinch_is_marked(self):
        from tests.test_webui_outcomes import plant_export
        plant_export(self.td.name)
        p = os.path.join(self.td.name, "data", "weeks", "week_03", "live_season_forecast_week_3.json")
        with open(p, encoding="utf-8") as fh:
            f = json.load(fh)
        for t, v in f.items():
            v["current_state"]["actual_wins_banked"] = 40.0 if t == QF else 0.0
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(f, fh)
        self.assertEqual(self.rows()[QF]["mark"]["label"], "clinched")
        self.assertIn("clinched", visible_text(self.get("/league", "simple")))


if __name__ == "__main__":
    unittest.main()

"""
tests.test_webui_decisions -- the items the owner's rulings of 2026-09-29 unlocked
(docs/WEB_UI_ROADMAP.md Decisions 2 and 7; UI-R7, H6). Decision 8's picks feature was built and
then removed at the owner's request the same day.

Decision 2: forecast luck, median luck and the schedule-swap matrix, as pre-registered in
docs/LUCK_LEDGER.md's addendum (committed before this code). On the fixture season -- week 2
quoted, week 1 not -- worked by hand:
  forecast_luck  Quantum Ferrets lost week 2 as played at a quoted 0.71: 0 - 0.71 = -0.71,
                 SE sqrt(0.71 x 0.29); Cosmic Badgers +0.71; one week left out (week 1)
  median_luck    with every team quoted 0.5 to beat the week-2 median, each team's raw is its
                 median result - 0.5; the league average is 0, so Quantum Ferrets -0.5
  swap matrix    Quantum Ferrets on Neon Walruses' schedule: week 1 (Walruses played the
                 Ferrets) 180 v 140 W, week 2 v Rocket Pandas 148.52 v 160 L -> 1-1; the
                 diagonal on box scores 2-0 (the league counted week 2 a loss: said on the page)
They sit on the Luck page in their own table, marked registered later; the original five
stay five, and nothing is combined.

Decision 7 (with H6): a page per archived season -- final standings and every regular-season
week's scores; the archives do not record the bracket, and the page says so.
"""
import json
import os
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
    from tests.test_webui_objects import CB, NW, QF, plant
    from tests.test_webui_routes import TEAMS
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

    def app(self, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def get(self, path, mode="dev"):
        r = self.app(mode).get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)


class TestLateLuck(Case):
    def test_forecast_luck(self):
        from webui.luck import late_measures
        m = late_measures(self.root, QF)["forecast_luck"]
        self.assertAlmostEqual(m["delta"], -0.71)
        self.assertAlmostEqual(m["se"], (0.71 * 0.29) ** 0.5, places=6)
        self.assertEqual(m["left_out"], 1)
        self.assertAlmostEqual(late_measures(self.root, CB)["forecast_luck"]["delta"], 0.71)

    def test_median_luck_differenced_against_the_league(self):
        p = os.path.join(self.td.name, "data", "logs", "predictions_2026.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"record_type": "week_predictions", "week": 2, "logged_at": "2026-09-17T10:00:00Z", "canonical": True,
                                 "matchups": [{"a": QF, "b": CB, "p_a": 0.71, "p_b": 0.29}],
                                 "median": {t: {"p_beat_median": 0.5, "expected_total": 150.0} for t in TEAMS}}) + "\n")
        from webui.luck import late_measures
        self.assertAlmostEqual(late_measures(self.root, QF)["median_luck"]["delta"], -0.5)

    def test_the_swap_matrix(self):
        from webui.luck import swap_matrix
        m = swap_matrix(self.root)
        self.assertEqual(m["cells"][QF][NW], (1, 1, 0))
        self.assertEqual(m["cells"][QF][QF], (2, 0, 0))
        self.assertTrue(m["rescored"])

    def test_the_luck_page_keeps_the_five_and_adds_the_later_three(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/luck", mode)
                self.assertEqual(body.count('<tr class="measure"'), 5, "the original five stay five")
                self.assertEqual(body.count('<tr class="measure late"'), 2)
                text = visible_text(body)
                for s in ("Registered later", "Forecast luck", "Median luck", "On each other's schedule"):
                    self.assertIn(s, text)
                self.assertNotIn("luck score", text.lower())
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


class TestSeasonPages(Case):
    def setUp(self):
        super().setUp()
        weeks = {str(w): [{"roster_id": i + 1, "matchup_id": i // 2 + 1, "points": 100.0 + i + w} for i in range(8)] for w in range(1, 17)}
        bundle = {"league_id": "", "season": "2024", "status": "complete", "settings": {"playoff_week_start": 15},
                  "roster_map": {str(i + 1): t for i, t in enumerate(TEAMS)},
                  "final_standings": {t: {"wins": 14 - i, "losses": i, "ties": 0, "points_scored": 2000.0 - i} for i, t in enumerate(TEAMS)},
                  "matchups": weeks}
        with open(os.path.join(self.td.name, "data", "logs", "season_2024.json"), "w", encoding="utf-8") as fh:
            json.dump(bundle, fh)

    def test_a_complete_season_page(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/history/2024", mode)
                text = visible_text(body)
                self.assertIn("2024 season", text)
                self.assertEqual(body.count('<tr class="fs'), 8, "final standings, every team")
                self.assertIn("Week 14", text)
                self.assertNotIn("Week 15", text, "regular season only")
                self.assertIn("bracket", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])

    def test_history_links_each_season(self):
        self.assertIn('href="/history/2024"', self.get("/history", "simple"))

    def test_an_unknown_season_is_404(self):
        self.assertEqual(self.app().get("/history/1999").status_code, 404)


if __name__ == "__main__":
    unittest.main()

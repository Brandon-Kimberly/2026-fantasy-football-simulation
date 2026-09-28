"""
tests.test_webui_charts -- native charts in place of the week's images (docs/WEB_UI_ROADMAP.md
UI-O5, V2).

UI-O5, the strength-of-schedule grid. strength_of_schedule.json holds every NFL team's implied
points (and opponent, or a bye) for every remaining week, and each fantasy roster's average of
those; the forecast page showed it only as three static images that ignore the theme, cannot be
hovered and cannot be read aloud. The grid: 32 NFL teams and the 8 rosters, one row each, one
column per week, the NUMBER in every cell (colour is never the only carrier), byes marked, the
fantasy playoff weeks set apart, sorted by a chosen window's average. The three images step
aside where the grid renders.
"""
import json
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui import charts                        # outside the probe: a missing module must fail, not skip
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_routes import PNG, build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

SOS = {"week": 3, "weeks_covered": [3, 4, 5, 15, 16],
       "by_nfl_team": {"KC": {"3": {"total": 27.0, "opponent": "LV", "is_bye": False}, "4": {"total": 25.0, "opponent": "DEN", "is_bye": False},
                              "5": {"total": None, "opponent": None, "is_bye": True}, "15": {"total": 28.0, "opponent": "LAC", "is_bye": False},
                              "16": {"total": 26.0, "opponent": "HOU", "is_bye": False}},
                       "CAR": {"3": {"total": 18.0, "opponent": "NO", "is_bye": False}, "4": {"total": 19.0, "opponent": "TB", "is_bye": False},
                               "5": {"total": 20.0, "opponent": "ATL", "is_bye": False}, "15": {"total": 30.0, "opponent": "NYJ", "is_bye": False},
                               "16": {"total": 29.0, "opponent": "NO", "is_bye": False}}},
       "by_fantasy_team": {"Quantum Ferrets": {"3": 22.0, "4": 22.5, "5": 21.0, "15": 23.0, "16": 22.0}}}


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        d = os.path.join(self.td.name, "data", "weeks", "week_03")
        with open(os.path.join(d, "strength_of_schedule.json"), "w", encoding="utf-8") as fh:
            json.dump(SOS, fh)
        for n in ("Strength_of_Schedule_By_Team.png", "Strength_of_Schedule_By_Roster.png", "Strength_of_Schedule_Team_Ranking.png"):
            with open(os.path.join(d, n), "wb") as fh:
                fh.write(PNG)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()


class TestGrid(Case):
    def test_rows_cells_byes_and_the_playoff_weeks(self):
        g = charts.sos_grid(self.root, 3)
        self.assertEqual(g["weeks"], [3, 4, 5, 15, 16])
        self.assertEqual(g["playoff_weeks"], [15, 16])
        kc = next(r for r in g["nfl"] if r["team"] == "KC")
        self.assertEqual([c["total"] for c in kc["cells"]], [27.0, 25.0, None, 28.0, 26.0])
        self.assertTrue(kc["cells"][2]["bye"])
        self.assertEqual(kc["cells"][0]["opponent"], "LV")
        self.assertEqual(g["fantasy"][0]["team"], MY_TEAM)

    def test_the_window_decides_the_order(self):
        rest = charts.sos_grid(self.root, 3, window="rest")
        self.assertEqual([r["team"] for r in rest["nfl"]], ["KC", "CAR"], "KC 26.5 on average, CAR 23.2")
        po = charts.sos_grid(self.root, 3, window="playoffs")
        self.assertEqual([r["team"] for r in po["nfl"]], ["CAR", "KC"], "CAR 29.5 in weeks 15-16, KC 27.0")
        self.assertEqual(po["nfl"][0]["avg"], 29.5)

    def test_every_cell_carries_a_shade_on_one_scale(self):
        g = charts.sos_grid(self.root, 3)
        shades = [c["shade"] for r in g["nfl"] for c in r["cells"] if not c["bye"]]
        self.assertEqual((min(shades), max(shades)), (0.0, 1.0))


class TestPage(Case):
    def get(self, path, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)

    def test_the_grid_replaces_the_three_images(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/forecasts/week-3", mode)
                self.assertIn('class="sos"', body)
                self.assertNotIn("Strength_of_Schedule_By_Team.png", body)
                text = visible_text(body)
                for s in ("Strength of schedule", "BYE", "27.0", "vs LV"):
                    self.assertIn(s, text) if s != "vs LV" else self.assertIn('title="vs LV', body)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

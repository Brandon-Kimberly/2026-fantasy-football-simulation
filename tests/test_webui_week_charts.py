"""
tests.test_webui_week_charts -- native charts in place of a forecast week's images
(docs/WEB_UI_ROADMAP.md UI-V2).

Each simulated week writes chart images that ignore the theme and cannot be hovered or read
aloud. The week page already draws most of them natively; it now drops each image where its
native section renders, and the sections use the validated primitives (UI-V3):
  Expected_Wins, All_Teams_Trajectories, Win_Trajectory  -> the expected-wins line chart
  Seeding_Distribution                                     -> the seed grid, a heat grid
  H2H_Heatmap                                              -> the head-to-head grid
  Season_Outcomes                                          -> the playoff and title odds
  Weekly_Scoring_Density                                   -> floors and ceilings
Power_Rankings -- the roster value baseline (best lineup plus 10% of the bench) -- is drawn
as the same bar list as the odds (the owner's request, 2026-09-29). The playoff and title bars stop giving
each team a generated hue (the dataviz rule for eight series): the owner's bar in --c-me,
the rest in --c-other, the names doing the identifying. The digest keeps its images.
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
    from tests.test_webui_objects import plant
    from tests.test_webui_routes import TEAMS
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

IMAGES = ("Expected_Wins", "All_Teams_Trajectories", "Win_Trajectory", "Seeding_Distribution", "H2H_Heatmap",
          "Season_Outcomes", "Weekly_Scoring_Density", "Power_Rankings")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestWeekPage(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)
        d = os.path.join(self.td.name, "data", "weeks", "week_03")
        for name in IMAGES:
            with open(os.path.join(d, f"{name}.png"), "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")
        m = os.path.join(d, "syndicate_comprehensive_matrix_week_3.json")
        with open(m, encoding="utf-8") as fh:
            mx = json.load(fh)
        mx["weekly_trajectories"] = {t: {"expected_cumulative_wins_by_week": [0, 1 + i % 2, 2 + i % 3]} for i, t in enumerate(TEAMS)}
        mx["finishing_seed_probabilities"] = {t: {f"Seed {k}": (12.5 + (i - k) * 1.5) for k in range(1, 9)} for i, t in enumerate(TEAMS)}
        mx["win_distributions"] = {t: {"p10_floor": 8, "p50_median": 12, "p90_ceiling": 16} for t in TEAMS}
        mx["weekly_score_percentiles"] = {t: {"mean": 150, "p10_floor": 115, "p90_ceiling": 190} for t in TEAMS}
        mx["roster_value_baseline_pts"] = {t: 160.0 + 5 * i for i, t in enumerate(TEAMS)}
        with open(m, "w", encoding="utf-8") as fh:
            json.dump(mx, fh)

    def tearDown(self):
        self.td.cleanup()

    def get(self, mode):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get("/forecasts/week-3")
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def test_each_image_with_a_native_section_is_dropped(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                shown = re.findall(r'<img src="[^"]*/([A-Za-z_]+)\.png"', self.get(mode))
                self.assertEqual(shown, [], "Power_Rankings too, once the roster value is drawn natively")

    def test_the_seed_grid_is_the_validated_heat_grid(self):
        body = self.get("simple")
        seeds = body[body.index("Where each team finishes"):]
        self.assertIn('class="heat2"', seeds[:4000])
        self.assertEqual(len(re.findall(r'<td class="hc h\d"', seeds[:20000])), 64)

    def test_the_roster_value_is_drawn_natively(self):
        body = self.get("simple")
        sec = body[body.index("Roster value"):]
        sec = sec[:sec.index("</section>")]
        self.assertEqual(sec.count('class="b"'), 8, "a bar per team")
        self.assertIn("195.0 pts", visible_text(sec), "the best roster's value written")
        self.assertNotIn("hsl(", re.sub(r'<span class="mark[^"]*"[^>]*>[^<]*</span>', "", sec))

    def test_no_generated_hue_for_eight_teams(self):
        body = self.get("dev")
        odds = body[body.index("Playoff odds"):body.index("Every team")]
        bars = re.sub(r'<span class="mark[^"]*"[^>]*>[^<]*</span>', "", odds.split("</h2>", 1)[1])   # the team chips are identity marks
        self.assertNotIn("hsl(", bars, "no per-team hue in the bars")
        self.assertIn("pbar", body[body.index("Every team"):], "the playoff column carries a percentage bar")
        self.assertEqual([t for t in DEV_TERMS if t in visible_text(self.get("simple"))], [])


if __name__ == "__main__":
    unittest.main()

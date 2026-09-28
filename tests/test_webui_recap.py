"""
tests.test_webui_recap -- the week in review (docs/WEB_UI_ROADMAP.md UI-R1, R2).

A played week's Matchups page gains its review: the awards (upset of the week, the week's
lowest winning score, its best losing score, the closest miss against the median), the teams
whose odds moved most after the week's results, the best-graded move made that week, and the
players who most beat their own pre-kickoff projection. A player's surprise is DIFFERENCED
AGAINST THE LEAGUE -- the week's mean surprise across every projected player is subtracted --
because the model has a known bias and an absolute surprise would crown it. Nothing is called
luck and nothing is combined into one score. A week with no results has no awards.
"""
import json
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui import recap                        # outside the probe: a missing module must fail, not skip
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import CB, CM, QF, RP, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    """The planted week 2 (as played): QF 148.52 L to CB 144.19 (the model had QF 71%), NW 170 W
    RP 160, TL 182 W PY 159, CM 142 W IW 126; median cut 155.0."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def get(self, path, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client().get(path).get_data(as_text=True)


class TestAwards(Case):
    def test_the_awards(self):
        a = {x["key"]: x for x in recap.week_recap(self.root, 2, MY_TEAM)["awards"]}
        self.assertEqual((a["upset"]["team"], a["upset"]["quote"]), (CB, 0.29))
        self.assertEqual((a["low_win"]["team"], a["low_win"]["points"]), (CM, 142.0))
        self.assertEqual((a["high_loss"]["team"], a["high_loss"]["points"]), (RP, 160.0), "160 in a loss to Neon Walruses' 170")
        self.assertEqual(a["median_miss"]["team"], QF, "148.52 is the closest score under the 155.0 cut")

    def test_no_results_no_awards(self):
        self.assertEqual(recap.week_recap(self.root, 5, MY_TEAM)["awards"], [])


class TestSurprises(Case):
    def test_each_players_surprise_is_differenced_against_the_league(self):
        p = os.path.join(self.td.name, "data", "logs")
        rows = [("100", "Player 0 O'Neil", 17.0, 9.8), ("101", "Player 1 O'Neil", 10.0, 30.0),
                ("102", "Player 2 O'Neil", 10.0, 10.0)]
        with open(os.path.join(p, "projection_log.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps({"player_id": pid, "name": n, "week": 2, "sleeper_mean": m,
                                         "synced_at": "2026-09-16T10:00:00Z"}) + "\n" for pid, n, m, _p in rows))
        with open(os.path.join(p, "first_recorded_scores.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps({"player_id": pid, "name": n, "week": 2, "points": pts}) + "\n"
                             for pid, n, _m, pts in rows))
        s = recap.week_recap(self.root, 2, MY_TEAM)
        # raw surprises -7.2, +20.0, 0.0; league mean +4.27; the top one is +15.73 over the league
        self.assertAlmostEqual(s["league_surprise"], (-7.2 + 20.0 + 0.0) / 3, places=2)
        top = s["surprises"][0]
        self.assertEqual(top["name"], "Player 1 O'Neil")
        self.assertAlmostEqual(top["over_league"], 20.0 - (-7.2 + 20.0 + 0.0) / 3, places=2)


class TestPage(Case):
    def test_a_played_week_reviews_itself_in_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                text = visible_text(self.get("/matchups/week-2", mode))
                for s in ("The week in review", "Upset of the week", "Cosmic Badgers"):
                    self.assertIn(s, text)
                self.assertNotIn("luck", text.lower())
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])

    def test_an_unplayed_week_has_no_review(self):
        self.assertNotIn("The week in review", visible_text(self.get("/matchups/week-5")))


if __name__ == "__main__":
    unittest.main()

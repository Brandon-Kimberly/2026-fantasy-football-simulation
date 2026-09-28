"""
tests.test_webui_wave3 -- the roadmap's third wave: answers without waiting
(docs/WEB_UI_ROADMAP.md).

UI-L1, lineup advice priced in the chance to win. The callout "the model would field a
different lineup" priced a swap only in points. The game-plan record (matchup_lineup) holds
the model lineup's margin against this opponent (mean and sd) and its chance to beat the
median; shifting that margin by the points the swap is worth gives, approximately, what the
lineup Sleeper has now costs in chance to win the game and to beat the median. Labelled as
the estimate it is -- one record's Normal margin, not a fresh simulation.

UI-P4, instant compare. "Start A or B?" was a job averaging 1.2 minutes, because it always
runs the joint simulation. The exported distributions answer the common case at once: each
player priced exactly as the live panel prices him (this week's lineup or matchup record,
else the season baseline, sd from the aleatoric and epistemic parts), P(A > B) for two
players and each one's chance of the top score for three or four. It is an ESTIMATE and
says so -- it ignores same-game links and the chance a player sits, which the joint
simulation prices -- and where a joint answer for the same pair and week is on file, that
answer is shown instead.
"""
import json
import math
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui import compare         # outside the probe: a missing module must fail, not skip
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_live import plant as plant_live
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_routes import build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        plant_live(self.td.name)       # Mahomes priced 24.0 this week by a lineup record; Coker, Worthy, Love baseline
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def client(self, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def record(self, a, b, week, p_a, name="compare_20260928T040643Z_x.json"):
        d = os.path.join(self.td.name, "data", "decisions", "adhoc")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            json.dump({"timestamp_utc": "20260928T040643Z", "tool": "compare_players", "a_name": a, "b_name": b,
                       "week": week, "p_a": p_a, "p_b": round(1 - p_a, 4), "se_p": 0.011, "n": 2000, "path": "joint"}, fh)


class TestEstimate(Case):
    def test_two_players_is_the_normal_difference(self):
        r = compare.estimate(self.root, ["Jalen Coker", "Xavier Worthy"], week=3)
        a, b = r["players"]
        self.assertEqual((a["mean"], b["mean"]), (10.0, 12.0))
        want = phi((10.0 - 12.0) / math.sqrt(a["sd"] ** 2 + b["sd"] ** 2))
        self.assertAlmostEqual(r["p_first_beats_second"], want, places=3)
        self.assertAlmostEqual(sum(p["p_top"] for p in r["players"]), 1.0, places=3)
        self.assertAlmostEqual(a["p_top"], want, places=2)

    def test_this_weeks_price_wins_over_the_season_baseline(self):
        r = compare.estimate(self.root, ["Patrick Mahomes", "Jordan Love"], week=3)
        m = r["players"][0]
        self.assertEqual((m["mean"], m["source"]), (24.0, "lineup record"))
        self.assertEqual(r["players"][1]["source"], "baseline")

    def test_three_or_four_players_share_the_top_score(self):
        r = compare.estimate(self.root, ["Jalen Coker", "Xavier Worthy", "Jordan Love", "Patrick Mahomes"], week=3)
        self.assertAlmostEqual(sum(p["p_top"] for p in r["players"]), 1.0, places=3)
        self.assertEqual(max(r["players"], key=lambda p: p["p_top"])["name"], "Patrick Mahomes")
        self.assertIsNone(r["p_first_beats_second"], "a pairwise number only for a pair")

    def test_an_unknown_name_is_flagged_not_guessed(self):
        r = compare.estimate(self.root, ["Jalen Coker", "Nobody Atall"], week=3)
        self.assertTrue(r["players"][1]["unknown"])
        self.assertIsNone(r["p_first_beats_second"])

    def test_a_joint_answer_for_the_same_pair_and_week_is_found_either_way_round(self):
        self.record("Xavier Worthy", "Jalen Coker", 3, 0.42)
        j = compare.estimate(self.root, ["Jalen Coker", "Xavier Worthy"], week=3)["joint"]
        self.assertAlmostEqual(j["p_first"], 0.58, places=4, msg="flipped to the order asked")
        self.assertIsNone(compare.estimate(self.root, ["Jalen Coker", "Xavier Worthy"], week=4)["joint"])


class TestPages(Case):
    def test_the_api_answers_two_to_four_and_refuses_otherwise(self):
        c = self.client()
        j = c.get("/api/compare?p=Jalen+Coker&p=Xavier+Worthy&week=3").get_json()
        self.assertEqual(len(j["players"]), 2)
        for bad in ("/api/compare?p=Jalen+Coker", "/api/compare?" + "&".join(["p=Jalen+Coker"] * 5)):
            self.assertEqual(c.get(bad).status_code, 400, bad)

    def test_the_compare_page_answers_at_once_in_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.client(mode).get("/tools/compare_players?a=Jalen+Coker&b=Xavier+Worthy&week=3").get_data(as_text=True)
                self.assertIn('id="instant"', body)
                text = visible_text(body)
                self.assertIn("Quick estimate", text)
                self.assertIn("ignores", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])

    def test_the_joint_answer_replaces_the_estimate_when_on_file(self):
        self.record("Jalen Coker", "Xavier Worthy", 3, 0.5824)      # not a half-way digit: 0.5815 * 100 is 58.1499...
        text = visible_text(self.client("simple").get("/tools/compare_players?a=Jalen+Coker&b=Xavier+Worthy&week=3").get_data(as_text=True))
        self.assertIn("58.2%", text)
        self.assertIn("full comparison", text)


MATCHUP = {"tool": "matchup_lineup", "team": MY_TEAM, "week": 3, "timestamp_utc": "20260926T120000Z",
           "ranking_by_p_beat_opponent": ["max_mean", "safe"],
           "constructions": {"max_mean": {"mean": 186.39, "sd": 37.63, "p_beat_opponent": 0.6946, "se": 0.0065,
                                          "p_beat_median": 0.7578, "margin_mean": 25.18, "margin_sd": 49.94},
                             "safe": {"mean": 180.0, "sd": 30.0, "p_beat_opponent": 0.6, "se": 0.007,
                                      "p_beat_median": 0.7, "margin_mean": 12.0, "margin_sd": 45.0}}}


class TestLineupStakes(Case):
    def plant_record(self):
        d = os.path.join(self.td.name, "data", "decisions", "week_03")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "matchup_20260926T120000Z_week3.json"), "w", encoding="utf-8") as fh:
            json.dump(MATCHUP, fh)

    def test_a_swap_is_priced_in_chance_to_win_from_the_record(self):
        from statistics import NormalDist
        from webui.live import lineup_stakes
        self.plant_record()
        s = lineup_stakes(self.root, 3, 3.2)
        n = NormalDist()
        self.assertAlmostEqual(s["d_h2h"], n.cdf(25.18 / 49.94) - n.cdf((25.18 - 3.2) / 49.94), places=4)
        m = n.inv_cdf(0.7578) * 37.63
        self.assertAlmostEqual(s["d_median"], n.cdf(m / 37.63) - n.cdf((m - 3.2) / 37.63), places=4)
        self.assertEqual((s["p_h2h"], s["se"], s["stamp"]), (0.6946, 0.0065, "20260926T120000Z"))

    def test_no_record_no_price(self):
        from webui.live import lineup_stakes
        self.assertIsNone(lineup_stakes(self.root, 3, 3.2))

    def test_the_live_plan_carries_the_stakes(self):
        from tests.test_webui_live import fake_fetch
        from webui.live import snapshot
        self.plant_record()
        plan = snapshot(self.root, 3, MY_TEAM, "L", fake_fetch)["plan"]
        self.assertTrue(plan["bench"], "Sleeper fields a man the lineup record does not start")
        self.assertIsNotNone(plan["stakes"])
        self.assertLess(plan["stakes"]["d_h2h"], 0, "benching a 10-point man for nothing costs chance")


if __name__ == "__main__":
    unittest.main()

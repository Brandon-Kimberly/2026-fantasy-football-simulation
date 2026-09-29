"""
tests.test_webui_player_calibration -- how the model has done on each player, and Accuracy's second
version (docs/WEB_UI_ROADMAP.md UI-P3, Q1).

UI-P3. Where each week's points landed inside the model's range for that player: a percentile
read off the week's forecast export (weeks/week_NN/player_variance.json: min, p10, p25, p50,
p75, p90, max), linear between those points. The export is the one-week spread pooled over the
weeks that forecast simulated -- the model does not save a spread per player per week -- and
it leaves out weeks a player does not play, so a zero is left out too (more often a player who
did not play than a real zero). The six bins are the quantiles' own: under the 10th, 10th-25th,
25th-50th, 50th-75th, 75th-90th, over the 90th. If the ranges are right, each bin holds its
width's share, so observed over expected is flat at 1.
  Done when: synthetic points drawn from the stated distributions give a flat histogram within
  noise. On the player page each week shows where it landed, with the count, and never a
  verdict on a handful of weeks.

UI-Q1. A reliability diagram (forecast probability against how often it happened, in five bins,
each with its standard error under calibration) over the quoted matchup and beat-the-median
calls, and the Brier score week by week. Drawn only from ENOUGH_WEEKS on; before that the page
says when it will draw.
  Done when: synthetic calibrated forecasts give a diagonal within noise. The fixture's one
  scored week: the quoted 0.71 for Quantum Ferrets, who lost as played -> Brier 0.5041.
"""
import json
import math
import os
import random
import tempfile
import unittest

from webui import calibration
from webui.accuracy import reliability

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_objects import QF, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

Q = {"min": 0.0, "p10": 2.0, "p25": 5.0, "p50": 10.0, "p75": 15.0, "p90": 22.0, "max": 80.0}


class TestLanding(unittest.TestCase):
    def test_linear_between_the_stated_points(self):
        for x, want in ((10.0, 50.0), (3.5, 17.5), (18.5, 82.5), (0.0, 0.0), (90.0, 100.0), (2.0, 10.0)):
            self.assertAlmostEqual(calibration.landing(Q, x), want, msg=x)

    def test_the_bins_are_the_quantiles_own(self):
        self.assertEqual(calibration.BINS, ((0, 10), (10, 25), (25, 50), (50, 75), (75, 90), (90, 100)))
        self.assertEqual(calibration.bin_of(9.99), 0)
        self.assertEqual(calibration.bin_of(10.0), 1)
        self.assertEqual(calibration.bin_of(100.0), 5)

    def test_synthetic_points_from_the_stated_distributions_are_flat(self):
        rng = random.Random(20260929)
        z = {"p10": -1.2815516, "p25": -0.6744898, "p50": 0.0, "p75": 0.6744898, "p90": 1.2815516}
        pcts = []
        for _ in range(600):
            mu, sd = rng.uniform(4, 20), rng.uniform(2, 8)
            q = {k: mu + v * sd for k, v in z.items()}
            q.update({"min": mu - 6 * sd, "max": mu + 6 * sd})
            for _ in range(5):
                pcts.append(calibration.landing(q, rng.gauss(mu, sd)))
        h = calibration.histogram(pcts)
        self.assertEqual(h["n"], 3000)
        for b in h["bins"]:
            with self.subTest(bin=b["label"]):
                se = math.sqrt(b["expected"] * (1 - b["expected"]) / h["n"])
                self.assertLess(abs(b["share"] - b["expected"]), 3 * se)
                self.assertAlmostEqual(b["ratio"], b["share"] / b["expected"])


class TestReliability(unittest.TestCase):
    def test_synthetic_calibrated_forecasts_lie_on_the_diagonal(self):
        rng = random.Random(7)
        pairs = []
        for _ in range(6000):
            p = rng.random()
            pairs.append((p, 1.0 if rng.random() < p else 0.0))
        rows = reliability(pairs)
        self.assertEqual(len(rows), 5)
        for r in rows:
            with self.subTest(bin=r["lo"]):
                self.assertGreater(r["n"], 0)
                self.assertLess(abs(r["observed"] - r["forecast"]), 3 * r["se"])

    def test_an_empty_bin_is_kept_and_empty(self):
        rows = reliability([(0.9, 1.0), (0.95, 1.0)])
        self.assertEqual([r["n"] for r in rows], [0, 0, 0, 0, 2])
        self.assertIsNone(rows[0]["observed"])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)
        spread = dict(Q, name="Player 0 O'Neil", pos="QB", mean=11.0, std=6.0, low_n=False)
        d = os.path.join(self.td.name, "data", "weeks", "week_01")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "player_variance.json"), "w", encoding="utf-8") as fh:
            json.dump({QF: [spread]}, fh)
        with open(os.path.join(self.td.name, "data", "logs", "first_recorded_scores.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"name": "Player 0 O'Neil", "player_id": "100", "points": 18.5, "week": 1,
                                 "recorded_at": "2026-09-15T10:00:00Z"}) + "\n")

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


class TestPlayerPage(Case):
    def test_the_landing_and_its_count(self):
        from tests.test_webui_modes import DEV_TERMS, visible_text
        self.assertEqual(calibration.player_landings(self.root, "Player 0 O'Neil"), {1: 82.5})
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                text = visible_text(self.get("/player/100", mode))
                self.assertIn("83rd", text, "18.5 lands at the 82.5th percentile")
                self.assertIn("1 week", text)
                self.assertIn("not a verdict", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


class TestAccuracyPage(Case):
    def test_the_histogram_the_brier_by_week_and_the_gate(self):
        from webui.accuracy import report
        r = report(self.root)
        self.assertEqual(r["brier_weeks"], [{"week": 2, "matchups": 0.5041, "median": None}])
        body = self.get("/accuracy")
        self.assertIn("Where players' weeks landed", body)
        self.assertIn("draws from week 5", body, "gated: one scored week")
        self.assertNotIn('class="relia"', body)

    def test_past_the_gate_the_diagram_draws(self):
        from webui import accuracy
        old = accuracy.ENOUGH_WEEKS
        accuracy.ENOUGH_WEEKS = 1
        try:
            body = self.get("/accuracy")
        finally:
            accuracy.ENOUGH_WEEKS = old
        self.assertIn('class="relia"', body)
        self.assertIn('class="diag"', body, "the line a calibrated forecast sits on")


if __name__ == "__main__":
    unittest.main()

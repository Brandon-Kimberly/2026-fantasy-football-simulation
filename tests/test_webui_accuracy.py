"""
tests.test_webui_accuracy -- the model's own track record (docs/WEB_UI.md W17).

The project's whole claim is that these probabilities are counted, sourced and
pre-registered, and until now the UI never once showed whether they came true. This is
that page, and the arithmetic under it.

Two rules make it honest rather than flattering:

  * only the QUOTED forecast counts -- the newest committed pre-kickoff row logged BEFORE
    the week's first kickoff. Anything logged after the games began knows too much, and a
    week with no committed row before kickoff is skipped and SAID to be skipped, never
    quietly filled in with a later one.
  * a week with no result yet is not scored at all.

Everything else is counting: Brier and hit rate on the matchup calls, bias, absolute
error and z on the points, and the same on the beat-the-median calls.
"""
import json
import os
import tempfile
import unittest

from webui import accuracy
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

A, B, C, D = "Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Turbo Llamas"


def _row(week, at, canonical, p_a, totals):
    """One predictions row: A vs B and C vs D, with each team's points forecast."""
    return {"record_type": "week_predictions", "week": week, "logged_at": at, "canonical": canonical,
            "matchups": [{"a": A, "b": B, "p_a": p_a, "p_b": round(1 - p_a, 4), "se": 0.007},
                         {"a": C, "b": D, "p_a": 0.5, "p_b": 0.5, "se": 0.007}],
            "median": {t: {"expected_total": e, "sd_total": 20.0, "p_beat_median": pm}
                       for t, (e, pm) in totals.items()}}


def plant(root):
    """Weeks 1 and 2 played; week 3 quoted but not yet played. Week 1 also carries a
    committed row logged AFTER kickoff, which must never be the one scored."""
    d = os.path.join(root, "data")
    os.makedirs(os.path.join(d, "logs"), exist_ok=True)
    os.makedirs(os.path.join(d, "current"), exist_ok=True)
    with open(os.path.join(d, "current", "nfl_schedule.json"), "w", encoding="utf-8") as fh:
        json.dump({"_meta": {"kickoffs": {"1": ["2026-09-10T00:20Z", "2026-09-13T17:00Z"],
                                          "2": ["2026-09-17T00:15Z"], "3": ["2026-09-24T00:15Z"]}}}, fh)
    totals = {A: (150.0, 0.60), B: (140.0, 0.40), C: (130.0, 0.35), D: (160.0, 0.70)}
    rows = [
        _row(1, "2026-09-08T10:00:00Z", True, 0.70, totals),          # committed, before kickoff
        _row(1, "2026-09-09T22:00:00Z", True, 0.62, totals),          # committed, later, still before: this one
        _row(1, "2026-09-11T10:00:00Z", True, 0.99, totals),          # after kickoff: knows too much
        _row(1, "2026-09-09T23:00:00Z", False, 0.51, totals),         # not committed
        _row(2, "2026-09-16T10:00:00Z", True, 0.40, totals),
        _row(3, "2026-09-23T10:00:00Z", True, 0.80, totals),          # no result yet
    ]
    with open(os.path.join(d, "logs", "predictions_2026.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in rows))
    with open(os.path.join(d, "current", "weekly_actuals.json"), "w", encoding="utf-8") as fh:
        json.dump({
            # week 1: A wins (the 0.62 call lands), every team 10 over its forecast
            "week_1": {"median_cutoff": 145.0, "team_results": {
                A: {"points_scored": 160.0, "h2h_win": 1.0, "median_win": 1},
                B: {"points_scored": 150.0, "h2h_win": 0.0, "median_win": 1},
                C: {"points_scored": 140.0, "h2h_win": 1.0, "median_win": 0},
                D: {"points_scored": 170.0, "h2h_win": 0.0, "median_win": 1}}},
            # week 2: A wins again (the 0.40 call misses), every team 10 under
            "week_2": {"median_cutoff": 135.0, "team_results": {
                A: {"points_scored": 140.0, "h2h_win": 1.0, "median_win": 1},
                B: {"points_scored": 130.0, "h2h_win": 0.0, "median_win": 0},
                C: {"points_scored": 120.0, "h2h_win": 0.0, "median_win": 0},
                D: {"points_scored": 150.0, "h2h_win": 1.0, "median_win": 1}}}}, fh)


class TestReport(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_only_played_weeks_are_scored_and_only_from_the_quoted_row(self):
        r = accuracy.report(self.root)
        self.assertEqual([w["week"] for w in r["weeks"]], [1, 2])
        self.assertEqual(r["weeks"][0]["at"], "2026-09-09T22:00:00Z", "the last committed word before kickoff")
        self.assertEqual(r["weeks"][0]["matchups"][0]["p"], 0.62, "not the 0.99 logged after the games began")
        self.assertEqual([s["week"] for s in r["skipped"]], [3])
        self.assertIn("no result", r["skipped"][0]["why"])

    def test_the_matchup_calls_are_counted_and_scored(self):
        m = accuracy.report(self.root)["matchups"]
        # four matchups over two weeks; the two coin-flips are not calls
        self.assertEqual((m["n"], m["calls"], m["hits"]), (4, 2, 1))
        self.assertEqual(m["rate"], 0.5)
        # Brier over all four: (1-.62)^2 + (0-.5)^2 + (1-.40)^2 + (1-.5)^2 all / 4
        self.assertAlmostEqual(m["brier"], (0.38 ** 2 + 0.25 + 0.60 ** 2 + 0.25) / 4, places=6)

    def test_the_points_forecast_is_scored_for_bias_error_and_spread(self):
        p = accuracy.report(self.root)["points"]
        self.assertEqual(p["n"], 8)
        self.assertAlmostEqual(p["bias"], 0.0, places=6, msg="ten over, then ten under")
        self.assertAlmostEqual(p["mae"], 10.0, places=6)
        self.assertAlmostEqual(p["mean_z"], 0.0, places=6)
        self.assertAlmostEqual(p["sd_z"], 0.5345, places=3)          # sample sd of +-0.5
        self.assertEqual(p["within80"], 1.0)

    def test_the_median_calls_are_scored_too(self):
        m = accuracy.report(self.root)["median"]
        self.assertEqual(m["n"], 8)
        # counted by hand: wk1 A, C and D land and B misses (0.40 quoted, he beat it); wk2 all four land
        self.assertEqual((m["calls"], m["hits"]), (8, 7))

    def test_the_sample_is_called_thin_until_it_is_not(self):
        r = accuracy.report(self.root)
        self.assertFalse(r["enough"])
        self.assertGreaterEqual(accuracy.ENOUGH_WEEKS, 5, "F25: first measurable at weeks 5-6")
        self.assertIn("weeks", r["note"])

    def test_a_week_quoted_only_after_kickoff_is_skipped_and_said_to_be(self):
        p = os.path.join(self.td.name, "data", "logs", "predictions_2026.jsonl")
        rows = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
        for x in rows:                       # the only committed week-2 row now lands after kickoff
            if x["week"] == 2 and x["canonical"]:
                x["logged_at"] = "2026-09-18T10:00:00Z"
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(x) + "\n" for x in rows))
        r = accuracy.report(Root(self.td.name))
        self.assertEqual([w["week"] for w in r["weeks"]], [1])
        self.assertIn(2, [s["week"] for s in r["skipped"]])
        self.assertIn("before", [s["why"] for s in r["skipped"] if s["week"] == 2][0])

    def test_nothing_logged_at_all(self):
        with tempfile.TemporaryDirectory() as td:
            build_tree(td)
            r = accuracy.report(Root(td))
            self.assertEqual(r["weeks"], [])
            self.assertEqual(r["points"]["n"], 0)
            self.assertFalse(r["enough"])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPage(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def get(self, mode="dev", path="/accuracy"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)

    def test_the_page_is_in_both_views_and_leads_with_the_sample_size(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get(mode)
                self.assertIn("Accuracy", body)
                self.assertIn('href="/accuracy"', body)                      # it is in the nav
                self.assertIn("2 weeks", body, "the thinness of the sample leads, not the hit rate")
                self.assertIn("week 5", body.lower(), "and when it first means anything")

    def test_it_shows_the_calls_the_points_and_every_scored_week(self):
        body = self.get()
        self.assertIn("Brier", body)
        self.assertIn("Beat the median", body)
        for text in ("Week 1", "Week 2"):
            self.assertIn(text, body)

    def test_the_simple_view_says_it_without_the_jargon(self):
        from tests.test_webui_modes import DEV_TERMS, visible_text
        text = visible_text(self.get("simple"))
        self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

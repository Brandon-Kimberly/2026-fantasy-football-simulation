"""
tests.test_webui_as_played -- the pages decide a result by the league's record AS PLAYED
(owner decision 2026-09-28; F83; scripts.as_played_record).

For weeks 1-2 of 2026 the weekly actuals carry Sleeper's re-scored box scores (the NEW IDP
scale), and one week-2 game has the other winner there. data/logs/as_played_results_2026.json
holds the verified as-played results; webui.results lays them over the actuals, so every
page -- records, what-each-week-did, the head-to-head history, Accuracy -- counts the game
the way the league does. Points stay the box scores (the record claims results only), and a
page that shows an overridden game's score says it is the re-scored one.
"""
import json
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui import accuracy
from webui.glance import h2h_report, odds_moves, records
from webui.paths import Root
from webui.results import as_played, week_results

A, B, C, D = MY_TEAM, "Cosmic Badgers", "Rocket Pandas", "Turbo Llamas"


def _w(root, rel, obj):
    p = os.path.join(root, "data", *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def plant(root, with_record=True):
    """Week 2: the box scores give A the win over B (148.52-144.19); as played, B won
    (the league's standings: A 2 wins, B 2 wins)."""
    _w(root, "current/sync_manifest.json", {"current_week": 3, "season": "2026", "finished_at": "2026-09-28T05:31:12Z"})
    _w(root, "current/league_state.json", {"current_week": 3, "season": "2026"})
    _w(root, "current/league_schedule.json", [[[A, C], [B, D]], [[A, B], [C, D]]])
    _w(root, "current/weekly_actuals.json", {
        "week_1": {"team_results": {A: {"points_scored": 185.86, "h2h_win": 1.0, "median_win": 1},
                                    C: {"points_scored": 172.11, "h2h_win": 0.0, "median_win": 1},
                                    B: {"points_scored": 165.55, "h2h_win": 1.0, "median_win": 0},
                                    D: {"points_scored": 153.02, "h2h_win": 0.0, "median_win": 0}}},
        "week_2": {"team_results": {A: {"points_scored": 148.52, "h2h_win": 1.0, "median_win": 0},
                                    B: {"points_scored": 144.19, "h2h_win": 0.0, "median_win": 0},
                                    C: {"points_scored": 182.15, "h2h_win": 1.0, "median_win": 1},
                                    D: {"points_scored": 126.03, "h2h_win": 0.0, "median_win": 1}}}})
    _w(root, "current/league_standings.json", {A: {"h2h_wins": 2}, B: {"h2h_wins": 2}, C: {"h2h_wins": 3},
                                               D: {"h2h_wins": 1}})
    if with_record:
        _w(root, "logs/as_played_results_2026.json", {
            "_meta": {"basis": "as played (OLD IDP scoring), F83", "weeks": [1, 2]},
            "week_1": {A: {"h2h_win": 1.0, "median_win": 1}, B: {"h2h_win": 1.0, "median_win": 0},
                       C: {"h2h_win": 0.0, "median_win": 1}, D: {"h2h_win": 0.0, "median_win": 0}},
            "week_2": {A: {"h2h_win": 0.0, "median_win": 0}, B: {"h2h_win": 1.0, "median_win": 0},
                       C: {"h2h_win": 1.0, "median_win": 1}, D: {"h2h_win": 0.0, "median_win": 1}}})


class TestOverlay(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_the_record_is_read(self):
        self.assertEqual(as_played(self.root)[2][A], {"h2h_win": 0.0, "median_win": 0})

    def test_results_follow_the_record_and_points_stay_the_box_score(self):
        r = week_results(self.root)[2][A]
        self.assertEqual((r["h2h_win"], r["median_win"], r["points_scored"]), (0.0, 0, 148.52))
        self.assertTrue(r["as_played"])
        self.assertTrue(r["rescored"], "the box score's own result disagreed")
        self.assertFalse(week_results(self.root)[1][A]["rescored"], "week 1 agreed")

    def test_without_the_record_the_actuals_stand(self):
        with tempfile.TemporaryDirectory() as td:
            plant(td, with_record=False)
            r = week_results(Root(td))[2][A]
            self.assertEqual(r["h2h_win"], 1.0)
            self.assertFalse(r["as_played"])


class TestEveryPageCountsTheGameAsTheLeagueDoes(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_records_reconcile_with_the_standings(self):
        r = records(self.root)
        self.assertEqual((r[A]["h2h"]["text"], r[A]["median"]["text"]), ("1–1", "1–1"))
        self.assertTrue(r[A]["agrees"])
        self.assertTrue(r[B]["agrees"])

    def test_head_to_head_history_calls_week_2_a_loss_and_says_the_score_is_rescored(self):
        rows = [x for x in h2h_report(self.root, A, B)["rows"] if x["week"] == 2]
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["won"])
        self.assertTrue(rows[0]["rescored"])

    def test_accuracy_scores_the_call_against_the_record(self):
        _w(self.td.name, "current/nfl_schedule.json", {"_meta": {"kickoffs": {"2": ["2026-09-17T00:15Z"]}}})
        os.makedirs(os.path.join(self.td.name, "data", "logs"), exist_ok=True)
        with open(os.path.join(self.td.name, "data", "logs", "predictions_2026.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"record_type": "week_predictions", "week": 2, "logged_at": "2026-09-16T10:00:00Z",
                                 "canonical": True, "matchups": [{"a": A, "b": B, "p_a": 0.71, "p_b": 0.29}],
                                 "median": {}}) + "\n")
        m = accuracy.report(Root(self.td.name))["matchups"]
        self.assertEqual((m["calls"], m["hits"]), (1, 0), "a 71% call on a game the league scored a loss is a miss")

    def test_what_week_2_did_uses_the_record(self):
        for n, name in ((2, "week_02"), (3, "week_03")):
            _w(self.td.name, f"weeks/{name}/live_season_forecast_week_{n}.json",
               {t: {"forecast": {"playoff_probability_pct": 50.0 + n}, "current_state": {}} for t in (A, B, C, D)})
        r = odds_moves(Root(self.td.name))["teams"][A]
        self.assertTrue(r["reconciled"])
        self.assertEqual((r["results"][0]["h2h"], r["results"][0]["median"]), ("L", "L"))


if __name__ == "__main__":
    unittest.main()

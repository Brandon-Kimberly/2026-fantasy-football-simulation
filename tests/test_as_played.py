"""
tests.test_as_played -- the league's results AS PLAYED where Sleeper's API no longer reports
them (F83; docs/EVALUATION_BOUNDARIES.md boundary 1; owner decision 2026-09-28).

Weeks 1-2 were played, and are banked in the league's standings, under the OLD IDP
scoring. Sleeper's /matchups endpoint recomputes a completed week against the CURRENT
settings, so for those two weeks it reports the NEW scale -- and on the NEW scale one
week-2 game has the other winner. The owner's ruling: the league's record, as played,
decides. scripts.as_played_record rebuilds each team's as-played score from the frozen
pre-change snapshot (first_recorded_scores.jsonl) and the week's starters, derives the
head-to-head and median results, and refuses to write unless every team's wins equal the
wins the league banked.
"""
import unittest

from scripts import as_played_record as apr

MAP = {"1": "A", "2": "B", "3": "C", "4": "D"}


def matchups(starters_points):
    """{roster_id: (matchup_id, {pid: new_scale_points})} -> the /matchups payload shape."""
    return [{"roster_id": int(r), "matchup_id": mid, "starters": list(pts), "points": round(sum(pts.values()), 2),
             "players_points": pts} for r, (mid, pts) in starters_points.items()]


class TestReconstruct(unittest.TestCase):
    def setUp(self):
        # new scale: A 30 beats B 29; as played (old scale, frozen) B's IDP man scored 5 more: B wins
        self.ms = matchups({"1": (1, {"10": 20.0, "11": 10.0}), "2": (1, {"20": 20.0, "21": 9.0}),
                            "3": (2, {"30": 40.0}), "4": (2, {"40": 5.0})})
        self.frozen = {(1, "10"): 20.0, (1, "11"): 10.0, (1, "20"): 20.0, (1, "30"): 40.0, (1, "40"): 5.0}
        self.by_name = {(1, "Linebacker Two"): 14.0}
        self.names = {"21": "Linebacker Two"}

    def test_scores_come_from_the_frozen_snapshot_not_the_recomputed_points(self):
        res, missing = apr.reconstruct(1, self.ms, self.frozen, self.by_name, self.names, MAP)
        self.assertEqual(missing, [])
        self.assertEqual((res["A"]["points"], res["B"]["points"]), (30.0, 34.0), "B's man by name: 14, not 9")
        self.assertEqual((res["A"]["h2h_win"], res["B"]["h2h_win"]), (0.0, 1.0))

    def test_median_is_taken_over_the_as_played_scores(self):
        res, _m = apr.reconstruct(1, self.ms, self.frozen, self.by_name, self.names, MAP)
        # as played: A 30, B 34, C 40, D 5 -> cut (30 + 34) / 2 = 32
        self.assertEqual({t: r["median_win"] for t, r in res.items()}, {"A": 0, "B": 1, "C": 1, "D": 0})

    def test_a_starter_missing_from_the_snapshot_is_reported_not_guessed(self):
        del self.by_name[(1, "Linebacker Two")]
        _res, missing = apr.reconstruct(1, self.ms, self.frozen, self.by_name, self.names, MAP)
        self.assertEqual(missing, [("B", "21", "Linebacker Two")])

    def test_a_tie_is_half_a_win_each(self):
        ms = matchups({"1": (1, {"10": 20.0}), "2": (1, {"20": 20.0}), "3": (2, {"30": 1.0}), "4": (2, {"40": 2.0})})
        frozen = {(1, "10"): 20.0, (1, "20"): 20.0, (1, "30"): 1.0, (1, "40"): 2.0}
        res, _m = apr.reconstruct(1, ms, frozen, {}, {}, MAP)
        self.assertEqual((res["A"]["h2h_win"], res["B"]["h2h_win"]), (0.5, 0.5))


class TestVerify(unittest.TestCase):
    def test_every_team_must_match_the_banked_wins(self):
        by_week = {1: {"A": {"h2h_win": 1.0, "median_win": 1}, "B": {"h2h_win": 0.0, "median_win": 0}}}
        self.assertEqual(apr.verify(by_week, {"A": {"h2h_wins": 2}, "B": {"h2h_wins": 0}}), [])
        self.assertEqual(apr.verify(by_week, {"A": {"h2h_wins": 1}, "B": {"h2h_wins": 0}}), [("A", 2.0, 1)])

    def test_a_team_the_standings_do_not_list_is_a_mismatch(self):
        by_week = {1: {"A": {"h2h_win": 1.0, "median_win": 0}}}
        self.assertEqual(apr.verify(by_week, {}), [("A", 1.0, None)])


if __name__ == "__main__":
    unittest.main()

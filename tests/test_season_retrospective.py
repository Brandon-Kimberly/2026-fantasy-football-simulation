"""Season retrospective (2025): four measurements reported separately, no combined verdict.
Hand-computed on a crafted 4-team, 2-regular-season-week bundle (playoff_week_start = 3; a
week-3 playoff matchup is present and must be EXCLUDED from every measurement):

  wk1 (A-B, C-D): A 15, B 12, C 28, D 10 -> all-play exp A 2/3, B 1/3, C 1, D 0
  wk2 (A-C, B-D): A 20, B 35, C 25, D 11 -> all-play exp A 1/3, B 1, C 2/3, D 0
  H2H: A 1-1, B 1-1, C 2-0, D 0-2. Luck = actual - expected: A 0, B -1/3, C +1/3, D 0.
  Lineups (slots QB+FLEX from the bundle, never hardcoded): A left 2 (wk1: r5 7 > r1 5)
  and 7 (wk2: r5 9 > r1 2) on the bench; B left 3 in wk1 BY STARTING THE ABSENT r2 (0.0)
  over r6 (3.0) -- the starter-zero and the lineup loss are the same event; C and D optimal.
  Zeros: B r2 wk1 (STARTED), B r6 wk2 (bench), D r8 wk2 (bench).
  High-scorer losses: D lost to the week high both weeks (C 28, B 35); A and B lost only
  to non-high scorers. Written before fantasy_sim.season_retrospective existed."""
import unittest

from fantasy_sim.season_retrospective import season_retrospective


def _entry(rid, mid, players, starters):
    return {"roster_id": rid, "matchup_id": mid,
            "points": round(sum(players[p] for p in starters), 2),
            "players": list(players), "starters": list(starters),
            "players_points": dict(players)}


BUNDLE = {
    "league_id": "L0", "season": "2025", "status": "complete",
    "roster_positions": ["QB", "FLEX", "BN"],
    "settings": {"playoff_week_start": 3, "league_average_match": 0},
    "roster_map": {"1": "A", "2": "B", "3": "C", "4": "D"},
    "final_standings": {t: {} for t in "ABCD"},
    "matchups": {
        "1": [_entry(1, 1, {"q1": 10.0, "r1": 5.0, "r5": 7.0}, ["q1", "r1"]),
              _entry(2, 1, {"q2": 12.0, "r2": 0.0, "r6": 3.0}, ["q2", "r2"]),
              _entry(3, 2, {"q3": 20.0, "r3": 8.0, "r7": 2.0}, ["q3", "r3"]),
              _entry(4, 2, {"q4": 6.0, "r4": 4.0, "r8": 1.0}, ["q4", "r4"])],
        "2": [_entry(1, 1, {"q1": 18.0, "r1": 2.0, "r5": 9.0}, ["q1", "r1"]),
              _entry(3, 1, {"q3": 15.0, "r3": 10.0, "r7": 1.0}, ["q3", "r3"]),
              _entry(2, 2, {"q2": 30.0, "r2": 5.0, "r6": 0.0}, ["q2", "r2"]),
              _entry(4, 2, {"q4": 8.0, "r4": 3.0, "r8": 0.0}, ["q4", "r4"])],
        # playoffs -- must not contaminate any regular-season measurement
        "3": [_entry(3, 1, {"q3": 99.0, "r3": 99.0, "r7": 0.0}, ["q3", "r3"]),
              _entry(2, 1, {"q2": 1.0, "r2": 1.0, "r6": 0.0}, ["q2", "r2"])],
    },
}
POSITIONS = {p: ["QB"] for p in ("q1", "q2", "q3", "q4")}
POSITIONS.update({p: ["RB"] for p in ("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8")})


class TestSeasonRetrospective(unittest.TestCase):
    def setUp(self):
        self.r = season_retrospective(BUNDLE, POSITIONS)

    def test_regular_season_cutoff_comes_from_the_bundle(self):
        self.assertEqual(self.r["regular_season_weeks"], [1, 2],
                         "week 3 is playoffs (playoff_week_start=3) and is excluded")

    def test_schedule_luck_is_all_play_expected_vs_actual_and_sums_to_zero(self):
        sl = self.r["schedule_luck"]
        self.assertAlmostEqual(sl["A"]["expected_wins_all_play"], 1.0, places=4)
        self.assertAlmostEqual(sl["B"]["expected_wins_all_play"], 4.0 / 3.0, places=4)
        self.assertAlmostEqual(sl["C"]["expected_wins_all_play"], 5.0 / 3.0, places=4)
        self.assertEqual(sl["A"]["actual_wins"], 1)
        self.assertEqual(sl["C"]["actual_wins"], 2)
        self.assertAlmostEqual(sl["B"]["luck"], -1.0 / 3.0, places=4)
        self.assertAlmostEqual(sl["C"]["luck"], 1.0 / 3.0, places=4)
        self.assertAlmostEqual(sum(d["luck"] for d in sl.values()), 0.0, places=9)
        self.assertEqual(sl["B"]["points_rank"], 2, "B scored 47, second behind C's 53")

    def test_lineup_efficiency_uses_the_bundles_slot_list_and_realized_scores(self):
        le = self.r["lineup_efficiency"]
        self.assertAlmostEqual(le["A"]["actual"], 35.0)
        self.assertAlmostEqual(le["A"]["optimal"], 44.0, msg="QB+FLEX from the bundle: r5 over r1 both weeks")
        self.assertAlmostEqual(le["A"]["points_lost"], 9.0)
        self.assertAlmostEqual(le["A"]["pct"], 100 * 35.0 / 44.0, places=2)
        self.assertAlmostEqual(le["B"]["points_lost"], 3.0,
                               msg="starting the absent r2 over r6 IS the lineup loss")
        for t in ("C", "D"):
            self.assertAlmostEqual(le[t]["points_lost"], 0.0, msg=f"{t} started optimally")
        weeks = {w["week"]: w for w in le["A"]["weeks"]}
        self.assertAlmostEqual(weeks[1]["lost"], 2.0)
        self.assertAlmostEqual(weeks[2]["lost"], 7.0)

    def test_absence_rate_is_the_zero_point_proxy_and_counts_started_zeros(self):
        ab = self.r["absences"]
        self.assertEqual(ab["B"]["zero_point_player_weeks"], 2)
        self.assertEqual(ab["B"]["player_weeks"], 6)
        self.assertAlmostEqual(ab["B"]["rate"], 2.0 / 6.0, places=4)
        self.assertEqual(ab["B"]["starter_zeros"], 1, "r2 was STARTED while absent in week 1")
        self.assertEqual(ab["D"]["zero_point_player_weeks"], 1)
        self.assertEqual(ab["A"]["zero_point_player_weeks"], 0)
        self.assertIn("proxy", self.r["absence_note"].lower())

    def test_high_scorer_losses_count_only_losses_to_the_weekly_top_score(self):
        hs = self.r["high_scorer_losses"]
        self.assertEqual(hs["D"]["vs_week_high_scorer"], 2)
        self.assertEqual(hs["D"]["losses"], 2)
        self.assertEqual(hs["B"]["vs_week_high_scorer"], 0, "B lost wk1 to A, who was not the high scorer")
        self.assertEqual(hs["A"]["vs_week_high_scorer"], 0, "A lost wk2 to C; B was the high scorer")

    def test_the_h2h_format_context_note_is_in_the_result(self):
        note = self.r["context_note"]
        self.assertIn("H2H", note)
        self.assertIn("variance", note.lower())


class TestSolverSlotsParameter(unittest.TestCase):
    """The Hungarian solver gains slots=None (default: REQUIRED_STARTING_SLOTS, behaviour
    preserved -- the golden master is the proof). A custom slot list makes it usable on the
    2025 format (team DEF, no IDP). Written before the parameter existed."""

    def test_custom_slots_assign_a_def_unit_and_the_default_still_stands(self):
        from fantasy_sim.simulation import FantasySimulationEngine
        solve = FantasySimulationEngine._solve_optimal_assignment
        assigned, unfilled = solve([("QB guy", ["QB"], 20.0), ("Broncos", ["DEF"], 8.0),
                                    ("RB guy", ["RB"], 11.0)],
                                   slots=["QB", "FLEX", "DEF"])
        by_slot = {slot: (name, v) for name, v, slot in assigned}
        self.assertEqual(by_slot["QB"][0], "QB guy")
        self.assertEqual(by_slot["DEF"][0], "Broncos")
        self.assertEqual(by_slot["FLEX"][0], "RB guy")
        self.assertEqual(unfilled, [])
        # default path: no slots argument -> the 13-slot 2026 structure, DEF has no home
        assigned, unfilled = solve([("Broncos", ["DEF"], 8.0)])
        self.assertEqual(assigned, [])
        self.assertEqual(len(unfilled), 13)


if __name__ == "__main__":
    unittest.main()

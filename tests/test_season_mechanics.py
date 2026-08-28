"""
tests.test_season_mechanics

AUDIT_PLAN.md Phases 5 and 6 -- season/playoff mechanics and outputs.

    Phase 5 invariant: league rules are implemented as written.
    Phase 6 invariant: what is exported equals what was computed.

The real league (Sleeper settings, fetched 2026-08-28): 8 teams, 4 playoff teams, playoffs
start week 15 (two rounds), league_average_match = 1 (median matchup on -> 2 decisions per
team per week), trade deadline week 11, 13 starters. The engine's constants match; the tests
below pin that the mechanics built on them behave as the rules say.

Observed through `tests.test_invariants.ScenarioRun` (the 17 run_simulation outputs plus the
real save_json payloads) on both committed fixtures. Passing tests lock verified behaviour;
failing tests characterise AUDIT_PHASE_5_6_FINDINGS.md.

WHAT IS NOT COVERED
-------------------
1. Tie handling in the playoff rounds (a tied semi-final or final advances the LOWER seed --
   `w1 = s1 if score(s1) > score(s4) else s4`) and a weekly score landing exactly on the
   8-team median (both middle teams get the median win, so 5 are awarded instead of 4). Both
   are measure-zero with continuous scores and the code is inline in run_simulation, so
   they are reported (findings 4, 5), not tested.
2. Chart rendering. The DATA behind every chart is asserted here against the exports; the
   PNGs themselves are not compared (see golden_master.py for why).
"""
import copy
import json
import logging
import os
import unittest
from unittest.mock import patch

import numpy as np

from fantasy_sim.config import SIM_CONFIG, REGULAR_SEASON_WEEKS
from fantasy_sim.simulation import FantasySimulationEngine
from tests.golden_master import STAGE_A_ARG_NAMES
from tests.test_invariants import ScenarioRun

SCENARIOS = ("week01", "week06")
PLAYOFF_TEAMS = 4          # Sleeper settings.playoff_teams
PLAYOFF_WEEK_START = 15    # Sleeper settings.playoff_week_start
DECISIONS_PER_WEEK = 2     # H2H + median (settings.league_average_match == 1)


# ----------------------------------------------------------------------------- Phase 5
class TestDecisionsPerWeek(unittest.TestCase):
    def test_each_team_earns_between_zero_and_two_decisions_per_week(self):
        """H2H (1, 0.5 on a tie, 0) plus median (1 or 0): every weekly increment in the
        cumulative-win trajectory is in {0, 0.5, 1, 1.5, 2}."""
        for scenario in SCENARIOS:
            run = ScenarioRun.get(scenario)
            w0 = run.current_week - 1
            for t in run.teams:
                inc = np.diff(run.args["trajectories"][t][:, w0:], axis=1)
                self.assertTrue(set(np.unique(inc).tolist()) <= {0.0, 0.5, 1.0, 1.5, 2.0},
                                "%s %s: increments %s" % (scenario, t, np.unique(inc)))

    def test_league_awards_exactly_eight_decisions_every_week(self):
        """4 matchups x 1 H2H decision + 4 median wins = num_teams per week, every week,
        every sim -- the rule as written, not just on average."""
        for scenario in SCENARIOS:
            run = ScenarioRun.get(scenario)
            w0 = run.current_week - 1
            total = sum(np.diff(run.args["trajectories"][t][:, w0:], axis=1) for t in run.teams)
            self.assertEqual(np.unique(total).tolist(), [float(len(run.teams))],
                             "%s: league-wide decisions per week %s" % (scenario, np.unique(total)))


class TestSeedingAndBracket(unittest.TestCase):
    def _recompute(self, run):
        n = len(run.teams)
        seeds = {t: np.zeros(n) for t in run.teams}
        playoffs = {t: 0 for t in run.teams}
        last = {t: 0 for t in run.teams}
        for s in range(run.total_sims):
            order = sorted(run.teams, key=lambda t: (run.args["wins"][t][s], run.args["points"][t][s]), reverse=True)
            for r, t in enumerate(order):
                seeds[t][r] += 1
            for t in order[:PLAYOFF_TEAMS]:
                playoffs[t] += 1
            last[order[-1]] += 1
        return seeds, playoffs, last

    def test_seeding_is_by_wins_then_points_every_simulation(self):
        """seed_matrix must equal the ranking recomputed per sim from the exported wins and
        (regular-season) points, with points as the tiebreak. Ties on wins occur in every
        run (17-28 team-pairs at the golden size), so the tiebreak is exercised."""
        for scenario in SCENARIOS:
            run = ScenarioRun.get(scenario)
            seeds, _, _ = self._recompute(run)
            for t in run.teams:
                np.testing.assert_array_equal(run.args["seed_matrix"][t], seeds[t], err_msg="%s %s" % (scenario, t))

    def test_playoff_berths_are_the_top_four_seeds_and_last_place_is_seed_eight(self):
        for scenario in SCENARIOS:
            run = ScenarioRun.get(scenario)
            _, playoffs, last = self._recompute(run)
            for t in run.teams:
                self.assertAlmostEqual(np.mean(run.args["b_playoffs"][t]) * run.total_sims, playoffs[t], places=9)
                self.assertAlmostEqual(np.mean(run.args["b_toilets"][t]) * run.total_sims, last[t], places=9)

    def test_exactly_one_champion_per_simulation_from_the_playoff_field(self):
        for scenario in SCENARIOS:
            run = ScenarioRun.get(scenario)
            champs = sum(np.mean(run.args["b_champs"][t]) * run.total_sims for t in run.teams)
            self.assertAlmostEqual(champs, run.total_sims, places=9)
            # a team with zero playoff appearances cannot have a championship
            for t in run.teams:
                if np.mean(run.args["b_playoffs"][t]) == 0:
                    self.assertEqual(np.mean(run.args["b_champs"][t]), 0.0)


class TestWeekIndexingEntryPoints(unittest.TestCase):
    """The plan: confirm range(current_week - 1, 16) and the 14-week season line up at
    every entry point. Sleeper's /state/nfl reports week 15-18 during the playoffs, and
    sync writes that straight to league_state.json."""

    def _run_at(self, current_week):
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "golden", "week06")
        files = {n: json.load(open(os.path.join(d, n))) for n in os.listdir(d) if n.endswith(".json")}
        files["league_state.json"] = {"current_week": current_week}
        captured = {}

        def rec(engine, *a):
            captured.update(zip(STAGE_A_ARG_NAMES, a))
        prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.CRITICAL)
        orig = SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"]
        SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = 1, 2
        try:
            with patch("fantasy_sim.simulation.load_json", side_effect=lambda p: copy.deepcopy(files[os.path.basename(p)])), \
                 patch.object(FantasySimulationEngine, "export_and_visualize", rec):
                FantasySimulationEngine().run_simulation()
        finally:
            SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = orig
            logging.getLogger().setLevel(prev)
        return captured

    def test_regular_season_entry_points_simulate_exactly_the_remaining_weeks(self):
        for cw in (13, 14):
            cap = self._run_at(cw)
            team = next(iter(cap["global_weekly_scores"]))
            played = sum(1 for w in range(REGULAR_SEASON_WEEKS) if cap["global_weekly_scores"][team][:, w].any())
            self.assertEqual(played, REGULAR_SEASON_WEEKS - cw + 1)

    def test_playoff_and_post_season_entry_points_fail_loudly_not_with_an_internal_error(self):
        """Regression guard for Phase 5 finding 1 (immediate half). current_week 15 used to
        raise IndexError (top4 is never populated because the week-14 seeding block never
        runs), 16 KeyError: None (w1/w2 are None), 17 UnboundLocalError (the week loop never
        executes, then the post-loop assert reads week_num). Sleeper reports these weeks
        during and after the playoffs, so the first sync in playoff week 1 made the engine
        crash with an internal error rather than a statement of what it cannot do. It now
        refuses with a ValueError that names the limitation and F3, the graceful
        bracket-from-banked-standings version tracked in AUDIT_PLAN.md. When F3 lands this
        test flips: these weeks must RUN, and the ValueError branch below goes."""
        for cw in (PLAYOFF_WEEK_START, PLAYOFF_WEEK_START + 1, PLAYOFF_WEEK_START + 2):
            with self.assertRaises(ValueError) as ctx:
                self._run_at(cw)
            self.assertIn("F3", str(ctx.exception))
            self.assertIn(str(cw), str(ctx.exception))


# ----------------------------------------------------------------------------- Phase 6
class TestExportsMatchComputation(unittest.TestCase):
    def test_season_outcomes_and_win_distributions_are_direct_recomputations(self):
        for scenario in SCENARIOS:
            run = ScenarioRun.get(scenario)
            mx = run.payload("comprehensive_matrix")
            for row in mx["season_outcomes"]:
                t = row["Team"]
                self.assertAlmostEqual(row["Expected_Wins"], float(np.mean(run.args["wins"][t])), places=9)
                self.assertAlmostEqual(row["Expected_Points"], float(np.mean(run.args["points"][t])), places=9)
                self.assertAlmostEqual(row["Playoff_Pct"], float(np.mean(run.args["b_playoffs"][t])) * 100, places=9)
                self.assertAlmostEqual(row["Champ_Pct"], float(np.mean(run.args["b_champs"][t])) * 100, places=9)
            for t in run.teams:
                wd = mx["win_distributions"][t]
                for key, q in (("p01_worst_case", 1), ("p10_floor", 10), ("p25_lower_bound", 25), ("p50_median", 50),
                               ("p75_upper_bound", 75), ("p90_ceiling", 90), ("p99_best_case", 99)):
                    self.assertAlmostEqual(wd[key], round(float(np.percentile(run.args["wins"][t], q)), 2), places=9, msg="%s %s %s" % (scenario, t, key))
                self.assertTrue(np.allclose(mx["weekly_trajectories"][t]["expected_cumulative_wins_by_week"],
                                            run.args["trajectories"][t].mean(axis=0)))

    def test_seed_probabilities_and_h2h_matrix_are_oriented_and_scaled_correctly(self):
        """seed_df is reindexed by team and the H2H matrix is row = winner, divided by
        sims x weeks simulated (Phase 1 fix). Diagonal is NaN."""
        for scenario in SCENARIOS:
            run = ScenarioRun.get(scenario)
            mx = run.payload("comprehensive_matrix")
            for t in run.teams:
                for i in range(len(run.teams)):
                    self.assertAlmostEqual(mx["finishing_seed_probabilities"][t]["Seed %d" % (i + 1)],
                                           run.args["seed_matrix"][t][i] / run.total_sims * 100, places=9)
                row = mx["h2h_win_probability_matrix"][t]
                self.assertTrue(row[t] != row[t] or row[t] is None)   # NaN diagonal
                for o in run.teams:
                    if o != t:
                        self.assertAlmostEqual(row[o], run.args["h2h"][t][o] / (run.total_sims * run.weeks_simulated) * 100, places=9)

    def test_insights_top_line_fields_match_the_engine(self):
        for scenario in SCENARIOS:
            run = ScenarioRun.get(scenario)
            ins = run.payload("syndicate_insights")
            self.assertEqual(ins["engine_simulations_run"], run.total_sims)
            self.assertAlmostEqual(ins["highest_single_week_score_observed"], round(float(run.args["max_score"]), 2), places=9)
            self.assertEqual(ins["team_with_highest_ceiling_game"], run.args["max_team"])
            self.assertEqual(ins["week_of_highest_score"], run.args["max_wk"])


class TestForecastRecordConsistency(unittest.TestCase):
    def _forecast_with_tie(self):
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "golden", "week06")
        files = {n: json.load(open(os.path.join(d, n))) for n in os.listdir(d) if n.endswith(".json")}
        wa = files["weekly_actuals.json"]
        wk = sorted(wa)[0]
        team = sorted(wa[wk]["team_results"])[0]
        wa[wk]["team_results"][team]["h2h_win"] = 0.5      # a real H2H tie
        saved = {}
        prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.CRITICAL)
        orig = SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"]
        SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = 1, 2
        try:
            with patch("fantasy_sim.simulation.load_json", side_effect=lambda p: copy.deepcopy(files[os.path.basename(p)])), \
                 patch("fantasy_sim.simulation.save_json", side_effect=lambda p, data, indent=2: saved.__setitem__(os.path.basename(p), data)), \
                 patch("matplotlib.pyplot.savefig"):
                engine = FantasySimulationEngine()
                engine.run_simulation()
        finally:
            SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = orig
            logging.getLogger().setLevel(prev)
        fc = [v for k, v in saved.items() if "forecast" in k][0]
        return team, engine, fc

    def test_banked_plus_expected_future_equals_expected_final(self):
        """FAILS -- finding 2. actual_wins_banked is int(h2h + median), so a banked H2H tie
        (0.5) is truncated away: banked 2.5 exports as 2 while expected_final_wins (10.5)
        keeps the half, and the record no longer adds up (2 + 8.0 != 10.5). Ties are real
        Sleeper outcomes and sync records them as 0.5."""
        team, engine, fc = self._forecast_with_tie()
        rec = fc[team]
        banked_engine = engine.actual_h2h_wins[team] + engine.actual_median_wins[team]
        self.assertAlmostEqual(rec["current_state"]["actual_wins_banked"], banked_engine, places=9,
                               msg="banked exported %s, engine holds %s" % (rec["current_state"]["actual_wins_banked"], banked_engine))
        self.assertAlmostEqual(rec["current_state"]["actual_wins_banked"] + rec["forecast"]["expected_future_wins"],
                               rec["forecast"]["expected_final_wins"], places=2)


class TestEliminationFlag(unittest.TestCase):
    def test_mathematical_elimination_does_not_depend_on_the_number_of_simulations(self):
        """FAILS -- finding 3. is_mathematically_eliminated = (Playoff_Pct == 0.0). A
        mathematical fact about the season cannot change with how many seasons were
        simulated; a Monte Carlo zero can. On week06 -- 8 regular-season weeks and 16
        decisions still to play for every team -- the flagged set at 2 sims and at 16 sims
        must be identical if the flag means what its name says. It is not: fewer sims,
        more zeros, more 'mathematically eliminated' teams."""
        sixteen = ScenarioRun.get("week06")
        two = ScenarioRun("week06", batches=1, sims=2)
        flags = {}
        for label, run in (("16 sims", sixteen), ("2 sims", two)):
            fc = run.payload("live_season_forecast")
            flags[label] = sorted(t for t in run.teams if fc[t]["forecast"]["is_mathematically_eliminated"])
        self.assertEqual(flags["2 sims"], flags["16 sims"],
                         "flag depends on sample size: %s" % flags)


if __name__ == "__main__":
    unittest.main()

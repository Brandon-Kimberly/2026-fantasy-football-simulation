"""
tests.test_playoffs

F3 (AUDIT_PLAN.md): simulate from inside the playoffs, seeding the bracket from banked
standings instead of crashing (Phase 5 finding 1). Two things the survey found that the
feature depends on:

  1. sync banks weekly_actuals for EVERY week below current_week, and Sleeper's /matchups/15
     and /16 carry all eight teams with matchup_ids (semifinals plus consolation games), so
     from the first week-16 sync the banked "regular season" standings include playoff-week
     wins, median wins and points. The engine must bank standings from weeks <= 14 only.
  2. /winners_bracket is the authoritative record of who played whom and who won; sync did
     not fetch it.

Characterisation first (expectedFailure), on the real engine through the week06 fixture.
"""
import copy
import json
import os
import unittest
from unittest.mock import patch

import numpy as np

from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import load_json

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "golden", "week06")


def _fixture_files():
    return {n: load_json(os.path.join(FIXTURE, n)) for n in os.listdir(FIXTURE) if n.endswith(".json")}


def _engine_with(files):
    """Construct the engine (which banks standings in __init__ via _apply_bayesian_updates)
    on an in-memory copy of the fixture; no simulation is run."""
    import logging
    prev = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.CRITICAL)
    try:
        with patch("fantasy_sim.simulation.load_json", side_effect=lambda p: copy.deepcopy(files.get(os.path.basename(p), {}))):
            return FantasySimulationEngine()
    finally:
        logging.getLogger().setLevel(prev)


class TestBankedStandingsStopAtWeek14(unittest.TestCase):
    def test_a_playoff_week_in_weekly_actuals_is_not_banked_into_standings(self):
        """GUARD (F3; was characterisation). Add a week-15 entry to the fixture's weekly_actuals exactly
        as sync writes one (Sleeper returns matchup_ids for playoff weeks): every team's
        banked wins and points must be unchanged by it."""
        files = _fixture_files()
        before = _engine_with(files)
        wa = files["weekly_actuals.json"]
        teams = list(before.team_names)
        wa["week_15"] = {"team_results": {t: {"points_scored": 100.0 + i, "h2h_win": 1 if i % 2 else 0, "median_win": 1 if i >= 4 else 0}
                                          for i, t in enumerate(teams)},
                         "player_scores": {}}
        after = _engine_with(files)
        for t in teams:
            self.assertEqual(after.actual_h2h_wins[t], before.actual_h2h_wins[t], "%s: week 15 h2h banked" % t)
            self.assertEqual(after.actual_median_wins[t], before.actual_median_wins[t], "%s: week 15 median banked" % t)
            self.assertAlmostEqual(after.actual_points[t], before.actual_points[t], msg="%s: week 15 points banked" % t)

    def test_regular_season_weeks_are_banked(self):
        """GUARD: the same mechanism must still bank weeks <= 14 (the fixture's weeks 1-5)."""
        files = _fixture_files()
        eng = _engine_with(files)
        self.assertGreater(sum(eng.actual_points.values()), 0.0)
        self.assertGreater(sum(eng.actual_h2h_wins.values()) + sum(eng.actual_median_wins.values()), 0.0)


# ------------------------------------------------------------------- seeding from banked standings
from tests.golden_master import _sandbox, STAGE_A_ARG_NAMES, FIXTURE_ROOT


def _run(scenario, current_week=None, bracket=None, batches=1, sims=6):
    """Run a golden scenario with an optional current_week / bracket override; returns the
    engine and the stage-A arguments."""
    captured = {}

    def rec(engine, *a):
        captured.update(zip(STAGE_A_ARG_NAMES, a))
    real_load = None
    with _sandbox(scenario, batches, sims):
        import fantasy_sim.simulation as simmod
        real_load = simmod.load_json

        def load(path):
            name = os.path.basename(path)
            if name == "league_state.json" and current_week is not None:
                return {"current_week": current_week}
            if name == "playoff_bracket.json" and bracket is not None:
                return copy.deepcopy(bracket)
            return real_load(path)
        with patch("fantasy_sim.simulation.load_json", side_effect=load), \
             patch.object(FantasySimulationEngine, "export_and_visualize", rec):
            engine = FantasySimulationEngine()
            engine.run_simulation()
    return engine, captured


class TestSeedingFromBankedStandings(unittest.TestCase):
    """F3 acceptance (AUDIT_PLAN.md): at current_week 15 b_playoffs is exactly 0 or 1 per team
    (banked), the four seeds are the top four by (banked wins, banked points), Playoff_Pct sums
    to 400 and Champ_Pct to 100 (Phase 1 normalisation), and the two regular-season golden
    scenarios stay byte-identical (test_golden_master)."""

    @classmethod
    def setUpClass(cls):
        cls.engine, cls.args = _run("week15")

    def test_playoff_field_is_banked_not_simulated(self):
        seeds = self.engine._seed_from_banked_standings()[0]
        for t in self.engine.team_names:
            v = self.args["b_playoffs"][t]
            self.assertIn(float(v[0]) if hasattr(v, "__len__") else float(v), (0.0, 1.0), "%s: b_playoffs %r" % (t, v))
            self.assertEqual(float(v[0]) if hasattr(v, "__len__") else float(v), 1.0 if t in seeds else 0.0)

    def test_seeds_are_the_top_four_by_banked_wins_then_points(self):
        e = self.engine
        ranked = sorted(e.team_names, key=lambda t: (e.actual_h2h_wins[t] + e.actual_median_wins[t], e.actual_points[t]), reverse=True)
        self.assertEqual(e._seed_from_banked_standings()[0], ranked[:4])
        self.assertEqual(ranked[:4], ["Polar Yetis", "Cosmic Badgers", "Neon Walruses", "Quantum Ferrets"])   # the fixture's banked top four

    def test_probabilities_normalise_and_the_champion_is_a_seed(self):
        seeds = set(self.engine._seed_from_banked_standings()[0])
        playoffs = sum(float(np.mean(self.args["b_playoffs"][t])) for t in self.engine.team_names)
        champs = sum(float(np.mean(self.args["b_champs"][t])) for t in self.engine.team_names)
        self.assertAlmostEqual(playoffs, 4.0, places=9)
        self.assertAlmostEqual(champs, 1.0, places=9)
        for t in self.engine.team_names:
            if t not in seeds:
                self.assertEqual(float(np.mean(self.args["b_champs"][t])), 0.0)

    def test_week_16_uses_the_recorded_semifinal_winners(self):
        """At current_week 16 only the final is simulated: the champion is always one of the
        two round-1 winners the bracket records."""
        with open(os.path.join(FIXTURE_ROOT, "week15", "playoff_bracket.json")) as handle:
            bracket = json.load(handle)
        bracket["rounds"][0]["winner"], bracket["rounds"][0]["loser"] = bracket["rounds"][0]["t2"], bracket["rounds"][0]["t1"]   # the 4 seed upsets the 1
        bracket["rounds"][1]["winner"], bracket["rounds"][1]["loser"] = bracket["rounds"][1]["t1"], bracket["rounds"][1]["t2"]
        engine, args = _run("week15", current_week=16, bracket=bracket)
        winners = {bracket["rounds"][0]["winner"], bracket["rounds"][1]["winner"]}
        for t in engine.team_names:
            c = float(np.mean(args["b_champs"][t]))
            if t not in winners:
                self.assertEqual(c, 0.0, "%s won a title without winning a semifinal" % t)
        self.assertAlmostEqual(sum(float(np.mean(args["b_champs"][t])) for t in engine.team_names), 1.0, places=9)

    def test_week_16_without_recorded_winners_falls_back_to_week_15_actuals_or_refuses(self):
        with self.assertRaises(ValueError) as ctx:
            _run("week15", current_week=16, bracket={})
        self.assertIn("semifinal winners", str(ctx.exception))

    def test_bracket_field_overrides_banked_ranking_with_a_warning(self):
        with open(os.path.join(FIXTURE_ROOT, "week15", "playoff_bracket.json")) as handle:
            bracket = json.load(handle)
        e = self.engine
        ranked = sorted(e.team_names, key=lambda t: (e.actual_h2h_wins[t] + e.actual_median_wins[t], e.actual_points[t]), reverse=True)
        bracket["rounds"][1]["t2"] = ranked[4]          # Sleeper's tiebreak put the 5th team in
        with self.assertLogs(level="WARNING"):
            engine, args = _run("week15", bracket=bracket)
        self.assertIn(ranked[4], engine._seed_from_banked_standings()[0])
        self.assertNotIn(ranked[2], engine._seed_from_banked_standings()[0])   # the team the bracket replaced

    def test_week_17_refuses_as_season_complete(self):
        with self.assertRaises(ValueError) as ctx:
            _run("week15", current_week=17)
        self.assertIn("complete", str(ctx.exception))

class TestWeek16SemifinalFallback(unittest.TestCase):
    """F26 coverage follow-up: simulation lines 931-936 -- the week-16 fallback that
    resolves semifinal winners from REAL week-15 h2h results when the bracket records
    none -- had never executed under any test. A bug there is a silently wrong
    CHAMPIONSHIP PAIRING, reachable only at current_week >= 16: the worst possible time
    to discover it. Pinned on a crafted week-16 state over the existing fixture
    scaffolding (no bracket -> the fallback is the only path)."""

    def _engine16(self, wk15_results):
        files = _fixture_files()
        files["league_state.json"] = {"current_week": 16}
        files["playoff_bracket.json"] = {}
        wa = copy.deepcopy(files.get("weekly_actuals.json") or {})
        wa["week_15"] = {"team_results": wk15_results, "median_cutoff": 0}
        files["weekly_actuals.json"] = wa
        return _engine_with(files)

    def test_both_semifinal_winners_come_from_week15_h2h(self):
        probe = self._engine16({})
        ranked = sorted(probe.team_names,
                        key=lambda t: (probe.actual_h2h_wins[t] + probe.actual_median_wins[t],
                                       probe.actual_points[t]), reverse=True)
        s1, s2, s3, s4 = ranked[:4]
        # semi 1: the 1-seed won; semi 2: the 3-seed won -- exercising BOTH comparison
        # directions in won() (the a-side and b-side branches)
        e = self._engine16({s1: {"h2h_win": 1}, s4: {"h2h_win": 0},
                            s2: {"h2h_win": 0}, s3: {"h2h_win": 1}})
        top4, _ranked, (w1, w2) = e._seed_from_banked_standings()
        self.assertEqual(top4, [s1, s2, s3, s4])
        self.assertEqual(w1, s1, "semi 1 (1v4) goes to the recorded week-15 winner")
        self.assertEqual(w2, s3, "semi 2 (2v3) goes to the recorded week-15 winner")

    def test_missing_week15_results_refuse_loudly_instead_of_guessing_a_final(self):
        e = self._engine16({})
        with self.assertRaises(ValueError) as ctx:
            e._seed_from_banked_standings()
        self.assertIn("Re-run the sync", str(ctx.exception))



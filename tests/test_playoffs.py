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

from fantasy_sim.config import SIM_CONFIG, REGULAR_SEASON_WEEKS
from fantasy_sim.simulation import FantasySimulationEngine

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "golden", "week06")


def _fixture_files():
    return {n: json.load(open(os.path.join(FIXTURE, n))) for n in os.listdir(FIXTURE) if n.endswith(".json")}


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
    @unittest.expectedFailure
    def test_a_playoff_week_in_weekly_actuals_is_not_banked_into_standings(self):
        """CHARACTERISATION (F3). Add a week-15 entry to the fixture's weekly_actuals exactly
        as sync writes one (Sleeper returns matchup_ids for playoff weeks): every team's
        banked wins and points must be unchanged by it. Today they are not."""
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

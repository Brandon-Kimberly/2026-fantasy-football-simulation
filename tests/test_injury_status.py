"""
tests.test_injury_status

Follow-up F4 (AUDIT_PLAN.md): a player who is out NOW must not be drawn at full strength.

Origin: bye-modelling steps 5b/5c measured the engine realising 4.1% injury absence in weeks
6-11 of the real 2025 season against 14.7% real -- 0.0% in the first simulated week, because
every player starts every simulated season healthy: `injury_clocks` is initialised to 0 for
the whole roster and nothing in the pipeline reads Sleeper's per-player `injury_status`
(110 IR / 41 PUP / 8 Out / 451 Questionable in the committed cache) or the league's `reserve`
(IR-slot) list. The trailing zeros in weekly history were, by accident, the only signal, and
Phase 2 finding 5 (correctly) stopped ingesting them.

Characterisation first (expectedFailure), on the real engine through a controlled league.
"""
import logging
import unittest
from unittest.mock import patch

from fantasy_sim.config import SIM_CONFIG, REGULAR_SEASON_WEEKS, NFL_TEAMS
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)

TEAMS = ["A", "B", "C", "D", "E", "F", "G", "H"]
SLOTS = ["QB", "K", "DB", "DL", "LB", "RB", "RB", "RB", "WR", "WR", "WR", "TE", "TE"]
OUT_PLAYER = "A_RB5"


def _full_schedule():
    sched = {}
    for w in range(1, 19):
        sched[str(w)] = {t: NFL_TEAMS[(i + 1) % len(NFL_TEAMS)] for i, t in enumerate(NFL_TEAMS)}
    sched["_meta"] = {"failed_weeks": [], "byes": {}}
    return sched


class InjuryLeague(object):
    """One instrumented run: every player FA, mean 12, no byes, injury rates 0 (so the only
    possible absence is an initial one), one player carrying `status` in both his roster
    entry and his baseline. Records injury_clocks and lineup candidates at the first
    simulated week of every season."""

    def __init__(self, status, current_week=3, sims=3):
        roster, baselines = {}, {}
        for t in TEAMS:
            roster[t] = []
            for i, pos in enumerate(SLOTS):
                name = "%s_%s%d" % (t, pos, i)
                entry = {"name": name, "pos": pos, "team": "FA"}
                base = {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 0.0, "pos": pos, "team": "FA", "bye": 0}
                if name == OUT_PLAYER:
                    entry["injury_status"] = status
                    base["injury_status"] = status
                roster[t].append(entry)
                baselines[name] = base
        fs = {
            LEAGUE_STATE_FILE: {"current_week": current_week},
            LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
            VEGAS_FILE: {"_meta": {"week": current_week, "source": "odds_api", "fetched_at": "x"}},
            LIVE_ROSTERS_FILE: roster, BASELINES_FILE: baselines,
            TEAM_RATINGS_FILE: {}, DEFENSIVE_RATINGS_FILE: {},
            DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
            LEAGUE_SCHEDULE_FILE: [[["A", "B"], ["C", "D"], ["E", "F"], ["G", "H"]]] * REGULAR_SEASON_WEEKS,
            NFL_SCHEDULE_FILE: _full_schedule(),
            WEEKLY_ACTUALS_FILE: {},
        }
        span = 17 - current_week
        self.first_week_clocks, self.first_week_candidates = [], []
        opened = [0]
        real_solve = FantasySimulationEngine._solve_optimal_assignment
        real_apportion = FantasySimulationEngine._apportion_vacated_volume
        me = self

        def apportion(engine, pools, clocks, newly):
            if opened[0] % span == 0:                     # first week of a season
                me.first_week_clocks.append(dict(clocks))
                me.first_week_candidates.append([])
            opened[0] += 1
            return real_apportion(engine, pools, clocks, newly)

        def solve(c):
            if (opened[0] - 1) % span == 0 and me.first_week_candidates:
                me.first_week_candidates[-1].extend(n for n, _, _ in c)
            return real_solve(c)
        rates = {k: 0.0 for k in SIM_CONFIG["INJURY_RATES"]}
        profiles = {t: {"faab_agg": 0.5, "trade_will": 0.0} for t in TEAMS}
        prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        orig = SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"]
        SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = 1, sims
        try:
            with patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]), \
                 patch.dict(SIM_CONFIG["INJURY_RATES"], rates), \
                 patch.dict("fantasy_sim.simulation.MANAGER_PROFILES", profiles, clear=True), \
                 patch.object(FantasySimulationEngine, "_solve_optimal_assignment", staticmethod(solve)), \
                 patch.object(FantasySimulationEngine, "_apportion_vacated_volume", apportion), \
                 patch.object(FantasySimulationEngine, "export_and_visualize", lambda s, *a: None):
                self.engine = FantasySimulationEngine()
                self.engine.run_simulation()
        finally:
            SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = orig
            logging.getLogger().setLevel(prev)


class TestInitialInjuryState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = InjuryLeague("IR")

    @unittest.expectedFailure
    def test_a_player_on_ir_does_not_start_the_season_healthy(self):
        """CHARACTERISATION (F4). A player whose status is IR enters every simulated season
        with injury_clocks == 0 and is a lineup candidate in the first week, i.e. he is drawn
        at full strength. Remove the expectedFailure when F4 lands."""
        for clocks, cands in zip(self.ir.first_week_clocks, self.ir.first_week_candidates):
            self.assertGreater(clocks.get(OUT_PLAYER, 0), 0,
                               msg="IR player entered the season with clock %r" % clocks.get(OUT_PLAYER))
            self.assertNotIn(OUT_PLAYER, cands, msg="IR player was a first-week lineup candidate")

    def test_the_rest_of_the_roster_starts_healthy(self):
        """Guard (passes today, must keep passing): with onset rates at 0, nobody else is on a
        clock in the first week -- the initial state is the only absence source here."""
        for clocks in self.ir.first_week_clocks:
            others = {p: c for p, c in clocks.items() if c > 0 and p != OUT_PLAYER}
            self.assertEqual(others, {})

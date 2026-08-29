"""
tests.test_injury_status

Follow-up F4 (AUDIT_PLAN.md): a player who is out NOW must not be drawn at full strength.

Origin: bye-modelling steps 5b/5c measured the engine realising 4.1% injury absence in weeks
6-11 of the real 2025 season against 14.7% real -- 0.0% in the first simulated week, because
every player started every simulated season healthy: `injury_clocks` was initialised to 0 for
the whole roster and nothing in the pipeline read Sleeper's per-player `injury_status`
(110 IR / 41 PUP / 8 Out / 451 Questionable in the synced cache on 2026-08-28) or the league's
`reserve` (IR-slot) list. The trailing zeros in weekly history were, by accident, the only
signal, and Phase 2 finding 5 (correctly) stopped ingesting them.

Step 2: `_initial_absence_clock` -- one clock, two entry points (see SIM_CONFIG's
INITIAL_ABSENCE comment for the real-2025 derivation). Every assertion is on the real engine;
the harness only instruments it.
"""
import logging
import unittest
from unittest.mock import patch

import numpy as np

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
    possible absence is an initial one), one player (OUT_PLAYER) carrying `status` / `on_ir`
    in his baseline. Records injury_clocks and lineup candidates at the first simulated week
    of every season, and every week's clock for OUT_PLAYER."""

    def __init__(self, status, on_ir=False, current_week=3, sims=3):
        roster, baselines = {}, {}
        for t in TEAMS:
            roster[t] = []
            for i, pos in enumerate(SLOTS):
                name = "%s_%s%d" % (t, pos, i)
                roster[t].append({"name": name, "pos": pos, "team": "FA"})
                base = {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 0.0, "pos": pos, "team": "FA", "bye": 0}
                if name == OUT_PLAYER:
                    base["injury_status"] = status
                    base["on_ir"] = on_ir
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
        self.first_week_clocks, self.first_week_candidates, self.out_player_clocks = [], [], []
        opened = [0]
        real_solve = FantasySimulationEngine._solve_optimal_assignment
        real_apportion = FantasySimulationEngine._apportion_vacated_volume
        me = self

        def apportion(engine, pools, clocks, newly):
            if opened[0] % span == 0:                     # first week of a season
                me.first_week_clocks.append(dict(clocks))
                me.first_week_candidates.append([])
                me.out_player_clocks.append([])
            me.out_player_clocks[-1].append(clocks.get(OUT_PLAYER, 0))
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
        np.random.seed(7)
        cls.ir = InjuryLeague("IR")
        cls.q_off_ir = InjuryLeague("Questionable")
        cls.q_on_ir = InjuryLeague("Questionable", on_ir=True)

    def test_a_player_on_ir_does_not_start_the_season_healthy(self):
        """GUARD (F4 step 2; was characterisation). An IR player enters every simulated season
        on an injury clock and is not a first-week lineup candidate."""
        for clocks, cands in zip(self.ir.first_week_clocks, self.ir.first_week_candidates):
            self.assertGreater(clocks.get(OUT_PLAYER, 0), 0,
                               msg="IR player entered the season with clock %r" % clocks.get(OUT_PLAYER))
            self.assertNotIn(OUT_PLAYER, cands, msg="IR player was a first-week lineup candidate")

    def test_the_clock_runs_down_like_any_other_injury_clock(self):
        """The initial clock is an ordinary injury clock: it decrements by one each week and
        the player is back once it reaches 0 (no re-draw, no special casing downstream)."""
        for seq in self.ir.out_player_clocks:
            self.assertGreater(seq[0], 0)
            for a, b in zip(seq, seq[1:]):
                self.assertEqual(b, max(0, a - 1), msg="clock sequence %r" % (seq,))

    def test_questionable_off_the_ir_slot_starts_healthy(self):
        """No game-time-probability source, so Questionable / Doubtful off the IR slot are
        drawn healthy (AUDIT_PLAN F4: a decision, not an oversight)."""
        for clocks, cands in zip(self.q_off_ir.first_week_clocks, self.q_off_ir.first_week_candidates):
            self.assertEqual(clocks.get(OUT_PLAYER, 0), 0)
            self.assertIn(OUT_PLAYER, cands)

    def test_the_league_ir_slot_is_absent_regardless_of_status(self):
        """on_ir dominates: a Questionable player the manager parked on IR is out (the named,
        accepted cost in AUDIT_PLAN F4)."""
        for clocks, cands in zip(self.q_on_ir.first_week_clocks, self.q_on_ir.first_week_candidates):
            self.assertGreater(clocks.get(OUT_PLAYER, 0), 0)
            self.assertNotIn(OUT_PLAYER, cands)

    def test_the_rest_of_the_roster_starts_healthy(self):
        """With onset rates at 0, nobody else is on a clock in the first week -- the initial
        state is the only absence source here."""
        for league in (self.ir, self.q_off_ir, self.q_on_ir):
            for clocks in league.first_week_clocks:
                others = {p: c for p, c in clocks.items() if c > 0 and p != OUT_PLAYER}
                self.assertEqual(others, {})


class TestInitialAbsenceClock(unittest.TestCase):
    """The draw itself, against the constants it is built from."""

    def _draws(self, status, on_ir, n=20000):
        np.random.seed(11)
        return np.array([FantasySimulationEngine._initial_absence_clock(status, on_ir) for _ in range(n)])

    def test_healthy_statuses_draw_nothing(self):
        state = np.random.get_state()
        for status in (None, "Questionable", "Doubtful", "COV", "NA", "Active"):
            self.assertEqual(FantasySimulationEngine._initial_absence_clock(status, False), 0)
        after = np.random.get_state()
        self.assertEqual(state[2], after[2], "a healthy player consumed RNG draws")
        self.assertTrue(np.array_equal(state[1], after[1]), "a healthy player consumed RNG draws")

    def test_stage_two_entry_returns_at_the_steady_hazard(self):
        """IR / PUP / Sus / DNR / on_ir: P(back after exactly 1 week) = steady hazard 0.16,
        every draw in 1..16, mean near the capped-geometric value."""
        h = SIM_CONFIG["ABSENCE_RETURN_HAZARD_STEADY"]
        for status, on_ir in (("IR", False), ("PUP", False), ("Sus", False), ("DNR", False), ("Questionable", True)):
            d = self._draws(status, on_ir)
            self.assertTrue(((d >= 1) & (d <= 16)).all())
            self.assertAlmostEqual((d == 1).mean(), h, delta=0.01, msg=status)
            expected = sum(k * h * (1 - h) ** (k - 1) for k in range(1, 16)) + 16 * (1 - h) ** 15
            self.assertAlmostEqual(d.mean(), expected, delta=0.1, msg=status)

    def test_a_fresh_out_enters_at_stage_one(self):
        """"Out": P(back after exactly 1 week) = first-week hazard 0.29, then steady."""
        h1, h = SIM_CONFIG["ABSENCE_RETURN_HAZARD_FIRST_WEEK"], SIM_CONFIG["ABSENCE_RETURN_HAZARD_STEADY"]
        d = self._draws("Out", False)
        self.assertAlmostEqual((d == 1).mean(), h1, delta=0.01)
        # conditional on surviving week 1, week 2 returns at the steady hazard
        self.assertAlmostEqual((d == 2).sum() / (d >= 2).sum(), h, delta=0.015)
        # on the IR slot, "Out" is stage two like everyone else
        self.assertAlmostEqual((self._draws("Out", True) == 1).mean(), h, delta=0.01)

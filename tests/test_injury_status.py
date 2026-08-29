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

from tests.golden_master import STAGE_A_ARG_NAMES

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


# ----------------------------------------------------------------------- F5: onset week
class OnsetLeague(object):
    """F5 harness. Same controlled league as InjuryLeague, no initial absences; one exposed
    player (OUT_PLAYER, an RB) with onset rate 1.0 in a league where everyone else has rate 0,
    and the duration draw pinned so every onset yields exactly `n` (np.random.exponential is
    patched to return n - 1 -> int(n - 1) + 1 == n). Records, per simulated week: the
    newly-injured set, the exposed player's clock, and whether he was a lineup candidate."""

    def __init__(self, n, current_week=3, sims=2, p_locked=0.0):
        roster, baselines = {}, {}
        for t in TEAMS:
            roster[t] = []
            for i, pos in enumerate(SLOTS):
                name = "%s_%s%d" % (t, pos, i)
                # the exposed player is the only RB on team A; everyone else is a K, so the
                # rate patch below (RB 1.0, all else 0) exposes exactly one player
                real_pos = pos if name == OUT_PLAYER else ("K" if pos == "RB" else pos)
                roster[t].append({"name": name, "pos": real_pos, "team": "FA"})
                baselines[name] = {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 0.0,
                                   "pos": real_pos, "team": "FA", "bye": 0}
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
        self.span = 17 - current_week
        self.weeks = []          # per simulated week: dict(newly=bool, clock=int, candidate=bool, pool=float)
        real_solve = FantasySimulationEngine._solve_optimal_assignment
        real_apportion = FantasySimulationEngine._apportion_vacated_volume
        me = self

        def apportion(engine, pools, clocks, newly):
            me.weeks.append({"newly": OUT_PLAYER in newly, "clock": clocks.get(OUT_PLAYER, 0),
                             "candidate": False, "pool": sum(v for d in pools.values() for v in d.values())})
            return real_apportion(engine, pools, clocks, newly)

        def solve(c):
            if me.weeks and any(nm == OUT_PLAYER for nm, _, _ in c):
                me.weeks[-1]["candidate"] = True
            return real_solve(c)
        self.args = {}

        def export(engine, *a):
            me.args.update(zip(STAGE_A_ARG_NAMES, a))
        rates = {k: 0.0 for k in SIM_CONFIG["INJURY_RATES"]}
        rates["RB"] = 1.0
        profiles = {t: {"faab_agg": 0.5, "trade_will": 0.0} for t in TEAMS}
        prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        orig = SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"]
        SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = 1, sims
        try:
            with patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]):
                with patch.dict(SIM_CONFIG, {"LOCKED_ONSET_PROBABILITY": p_locked}):
                  with patch.dict(SIM_CONFIG["INJURY_RATES"], rates):
                    with patch.dict("fantasy_sim.simulation.MANAGER_PROFILES", profiles, clear=True):
                        with patch.object(FantasySimulationEngine, "_solve_optimal_assignment", staticmethod(solve)):
                            with patch.object(FantasySimulationEngine, "_apportion_vacated_volume", apportion):
                                with patch.object(FantasySimulationEngine, "export_and_visualize", export):
                                    with patch("fantasy_sim.simulation.np.random.exponential", lambda scale: float(n - 1)):
                                        FantasySimulationEngine().run_simulation()
        finally:
            SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = orig
            logging.getLogger().setLevel(prev)

    def spells(self):
        """(onset week index, weeks fully absent after it) for each onset, per simulated season."""
        out = []
        for s in range(0, len(self.weeks), self.span):
            season = self.weeks[s:s + self.span]
            i = 0
            while i < len(season):
                if season[i]["newly"]:
                    j = i + 1
                    while j < len(season) and not season[j]["candidate"] and not season[j]["newly"]:
                        j += 1
                    out.append((i, j - i - 1, season[i]["candidate"]))
                    i = j
                else:
                    i += 1
        return out


class TestOnsetWeekSemantics(unittest.TestCase):
    """Regime A (known before lock; p_locked pinned to 0 here -- regime B has its own class).

    F5 candidate (b) / the off-by-one. The calibration behind the duration mixture counts
    games MISSED (64% of injuries <= 2 games missed, mean 3.1), and the real-2025 absence
    spells were measured as runs of exact zeros. The engine sets the clock to n in the onset
    week, lets the player PLAY that week at 0.35x, and decrements the clock at the end of the
    same week -- so a drawn n yields n - 1 fully absent weeks, and n = 1 (40% of onsets) is
    never absent at all. Measured in-simulation: out-on-clock / newly-hurt = 2.08 against the
    mixture's mean of 3.11."""

    @classmethod
    def setUpClass(cls):
        np.random.seed(5)
        cls.n1, cls.n3 = OnsetLeague(1), OnsetLeague(3)

    def test_an_onset_week_is_a_missed_game(self):
        """GUARD (F5, was characterisation). The newly injured player must not be a lineup candidate in the
        onset week (a missed game is a zero, as the calibration data counted it)."""
        for league in (self.n1, self.n3):
            for onset_idx, absent_after, played_onset_week in league.spells():
                self.assertFalse(played_onset_week, "newly injured player was a lineup candidate in his onset week")

    def test_a_drawn_n_produces_n_missed_games(self):
        """GUARD (F5, was characterisation). With the duration pinned at n, the spell must be n missed games:
        the onset week plus n - 1 further weeks. Before F5 it was the onset week (played) plus n - 1."""
        for n, league in ((1, self.n1), (3, self.n3)):
            spells = league.spells()
            self.assertGreater(len(spells), 0)
            for onset_idx, absent_after, played in spells:
                if onset_idx + n > league.span:       # spell truncated by the end of the season
                    continue
                missed = absent_after + (0 if played else 1)
                self.assertEqual(missed, n, "n=%d: onset at index %d, %d fully absent weeks after, played onset week: %s"
                                 % (n, onset_idx, absent_after, played))

    def test_vacated_volume_is_recorded_in_the_onset_week(self):
        """Guard that must survive the fix: the onset week opens the teammates' pool."""
        for league in (self.n1, self.n3):
            for w in league.weeks:
                if w["newly"]:
                    self.assertGreater(w["pool"], 0.0)


class TestLockedZero(unittest.TestCase):
    """Regime B (F5 step 2): with LOCKED_ONSET_PROBABILITY pinned to 1, every onset is a
    locked-lineup zero -- the manager did not know before lock. The player stays a lineup
    candidate at his pre-game expectation (no lookahead: the lineup is chosen on expected_pre)
    and realises exactly 0; he opens no roster hole; from the next week he is out like any
    clocked player. With it pinned to 0 (TestOnsetWeekSemantics) he is excluded instead."""

    @classmethod
    def setUpClass(cls):
        np.random.seed(9)
        cls.locked = OnsetLeague(3, p_locked=1.0)

    def _sim0_weeks(self):
        log = self.locked.args["audit_log"]["weeks"]
        return [(idx, (log.get(str(3 + idx)) or log.get(3 + idx))) for idx in range(self.locked.span)]

    def test_a_locked_onset_stays_a_candidate_and_realises_zero(self):
        starts = zeros = 0
        for idx, wd in self._sim0_weeks():
            w = self.locked.weeks[idx]
            if not w["newly"] or not wd:
                continue
            self.assertTrue(w["candidate"], "locked onset was not a lineup candidate in week index %d" % idx)
            for s in wd["teams"]["A"]["starters"]:
                if s["name"] == OUT_PLAYER:
                    starts += 1
                    self.assertGreater(s.get("expected", 0), 0, "locked starter must carry his pre-game expectation")
                    zeros += float(s.get("actual", s.get("score"))) == 0.0
        self.assertGreater(starts, 0, "the locked player never started; the check would be vacuous")
        self.assertEqual(zeros, starts, "a locked starter must realise exactly 0")

    def test_a_locked_onset_is_out_from_the_next_week(self):
        for idx, wd in self._sim0_weeks():
            w = self.locked.weeks[idx]
            if idx + 1 < self.locked.span and w["newly"]:
                nxt = self.locked.weeks[idx + 1]
                self.assertFalse(nxt["candidate"] and not nxt["newly"],
                                 "locked player was a candidate the week after his onset without a new onset")
                self.assertGreater(nxt["clock"], 0)

    def test_locked_and_excluded_regimes_differ_only_in_candidacy(self):
        """Same league, same pinned duration: regime B keeps the player in the candidate list
        in his onset weeks, regime A (p_locked = 0) does not; both give him n missed games."""
        np.random.seed(9)
        excluded = OnsetLeague(3, p_locked=0.0)
        for lg, expect_candidate in ((self.locked, True), (excluded, False)):
            onset_weeks = [w for w in lg.weeks if w["newly"]]
            self.assertGreater(len(onset_weeks), 0)
            self.assertTrue(all(w["candidate"] == expect_candidate for w in onset_weeks))

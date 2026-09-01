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
import fantasy_sim.simulation as simmod
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

        solves_this_week = [0]

        def apportion(engine, pools, clocks, newly):
            if opened[0] % span == 0:                     # first week of a season
                me.first_week_clocks.append(dict(clocks))
                me.first_week_candidates.append([])
                me.out_player_clocks.append([])
            me.out_player_clocks[-1].append(clocks.get(OUT_PLAYER, 0))
            opened[0] += 1
            solves_this_week[0] = 0
            return real_apportion(engine, pools, clocks, newly)

        def solve(c):
            # Only the 8 lineup solves right after the apportion boundary are THIS week's
            # lineups; later solves (trade evaluation, next week's intended lineup -- F6)
            # are not.
            solves_this_week[0] += 1
            if solves_this_week[0] <= len(TEAMS) and (opened[0] - 1) % span == 0 and me.first_week_candidates:
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
        # "NA" was in this list until 2026-09-01: it is Sleeper's reserve / non-football code
        # (Commissioner Exempt included) and now enters the stage-2 clock -- see
        # TestCommissionerExemptIsAnAbsence and config.INITIAL_ABSENCE_STATUSES.
        for status in (None, "Questionable", "Doubtful", "COV", "Active"):
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

        solves_this_week = [0]

        def apportion(engine, pools, clocks, newly):
            me.weeks.append({"newly": OUT_PLAYER in newly, "clock": clocks.get(OUT_PLAYER, 0),
                             "candidate": False, "pool": sum(v for d in pools.values() for v in d.values())})
            solves_this_week[0] = 0
            return real_apportion(engine, pools, clocks, newly)

        def solve(c):
            # first 8 solves after the boundary = this week's lineups (see InjuryLeague)
            solves_this_week[0] += 1
            if me.weeks and solves_this_week[0] <= len(TEAMS) and any(nm == OUT_PLAYER for nm, _, _ in c):
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


# ------------------------------------------------------------------ F6: onset exposure
class _ExposureRun(object):
    """F6 harness on the week01 fixture (2 x 15 seasons, real engine). Per simulated week:
    the assigned lineup of every team (from _solve_optimal_assignment's return, first 8 solves
    after the apportion boundary) and the newly-injured set. An onset is classified by whether
    the player was in his team's lineup the PREVIOUS week -- the same definition the real 2025
    hazard split used ("started the previous week"). Also times the run."""
    _cache = None

    def __init__(self):
        import time
        from tests.golden_master import _sandbox
        weeks = []
        real_solve = FantasySimulationEngine._solve_optimal_assignment
        real_app = FantasySimulationEngine._apportion_vacated_volume

        all_solves = []

        def app(engine, pools, clocks, newly):
            # F6: the 8 solves immediately BEFORE this boundary are this week's INTENDED lineups
            intended = set().union(*[a for a in all_solves[-8:]]) if len(all_solves) >= 8 else set()
            weeks.append({"newly": set(newly), "clocks": {p for p, c in clocks.items() if c > 0}, "lineups": [], "intended": intended})
            return real_app(engine, pools, clocks, newly)

        def solve(c):
            a, u = real_solve(c)
            all_solves.append({n for n, _, _ in a})
            if weeks and len(weeks[-1]["lineups"]) < 8:
                weeks[-1]["lineups"].append({n for n, _, _ in a})
            return a, u
        t0 = time.time()
        with _sandbox("week01", 2, 15):
            with patch.object(FantasySimulationEngine, "_solve_optimal_assignment", staticmethod(solve)):
                with patch.object(FantasySimulationEngine, "_apportion_vacated_volume", app):
                    with patch.object(FantasySimulationEngine, "export_and_visualize", lambda s, *a: None):
                        self.engine = FantasySimulationEngine()
                        self.engine.run_simulation()
        self.seconds = time.time() - t0
        self.span = 17 - self.engine.current_week
        self.teams = list(self.engine.team_names)
        self.rosters = {t: set(self.engine.rosters[t]) for t in self.teams}
        # classify onsets and count exposure (present player-weeks) by previous-week lineup status
        self.onsets = {"starter": 0, "bench": 0}; self.exposure = {"starter": 0, "bench": 0}
        self.base = {"starter": 0.0, "bench": 0.0}      # expected onsets at INJURY_RATES, no exposure factor
        # by INTENDED-lineup membership (what the engine actually scales on), F6 guard
        self.i_onsets = {"starter": 0, "bench": 0}; self.i_exposure = {"starter": 0, "bench": 0}; self.i_base = {"starter": 0.0, "bench": 0.0}
        self.by_pos = {}                                 # pos -> [exposures, onsets] (Phase 7)
        rates = SIM_CONFIG["INJURY_RATES"]
        for i, w in enumerate(weeks):
            if i % self.span == 0:
                continue                                   # no previous week in this season
            prev = weeks[i - 1]
            for ti, t in enumerate(self.teams):
                prev_lineup = prev["lineups"][ti] if ti < len(prev["lineups"]) else set()
                for p in self.rosters[t]:
                    if p in w["clocks"] and p not in w["newly"]:
                        continue                           # already out: not exposed this week
                    bye = (self.engine.baselines.get(p) or {}).get("bye")
                    if bye == self.engine.current_week + (i % self.span):
                        continue
                    grp = "starter" if p in prev_lineup else "bench"
                    self.exposure[grp] += 1
                    self.onsets[grp] += p in w["newly"]
                    self.base[grp] += rates.get(simmod.normalize_position((self.engine.baselines.get(p) or {}).get("pos", "FLEX")), 0.025)
                    ig = "starter" if p in w["intended"] else "bench"
                    self.i_exposure[ig] += 1
                    self.i_onsets[ig] += p in w["newly"]
                    self.i_base[ig] += rates.get(simmod.normalize_position((self.engine.baselines.get(p) or {}).get("pos", "FLEX")), 0.025)
                    pos = simmod.normalize_position((self.engine.baselines.get(p) or {}).get("pos", "FLEX"))
                    self.by_pos.setdefault(pos, [0, 0])
                    self.by_pos[pos][0] += 1
                    self.by_pos[pos][1] += p in w["newly"]

    @classmethod
    def get(cls):
        if cls._cache is None:
            cls._cache = cls()
        return cls._cache

    def hazard(self, grp):
        return self.onsets[grp] / float(self.exposure[grp])

    def relative(self, grp):
        """Observed onsets over the onsets INJURY_RATES alone would give this group -- removes
        the positional slot mix (K and three IDP slots start; RB/WR crowd the bench)."""
        return self.onsets[grp] / self.base[grp]

    def relative_intended(self, grp):
        return self.i_onsets[grp] / self.i_base[grp]


class TestOnsetExposure(unittest.TestCase):
    """F6. Real 2025: 61 of 75 fresh onsets were by previous-week starters over about 73% of
    rostered player-weeks, 14 by bench players over about 27% -- a per-player-week hazard ratio
    of roughly 1.6 (one season, n = 14 bench onsets). The engine draws one hazard for every
    rostered player regardless of role."""

    def test_pooled_onset_hazard_matches_the_roster_weighted_rate(self):
        """GUARD that must survive F6: the roster-weighted onset hazard stays at INJURY_RATES
        (the exposure split must redistribute onsets, not add them). Tolerance: 3 SE on ~2,700
        onsets."""
        run = _ExposureRun.get()
        rates = SIM_CONFIG["INJURY_RATES"]
        expected = 0.0; n = 0
        for t in run.teams:
            for p in run.rosters[t]:
                pos = simmod.normalize_position((run.engine.baselines.get(p) or {}).get("pos", "FLEX"))
                expected += rates.get(pos, 0.025); n += 1
        expected /= n
        total_onsets = run.onsets["starter"] + run.onsets["bench"]
        total_exposure = run.exposure["starter"] + run.exposure["bench"]
        observed = total_onsets / float(total_exposure)
        se = (expected * (1 - expected) / total_exposure) ** 0.5
        self.assertAlmostEqual(observed, expected, delta=3 * se,
                               msg="pooled hazard %.4f vs roster-weighted INJURY_RATES %.4f (SE %.4f)" % (observed, expected, se))

    def test_previous_week_starters_face_a_higher_onset_hazard_than_the_bench(self):
        """GUARD (F6, was characterisation). Real 2025, one consistent definition (previous-week
        status for both exposure and onset): starter hazard 0.0575, bench 0.0462, ratio 1.25
        (n = 14 bench onsets; interval roughly 0.9-1.7). The engine was 0.91 before F6 because
        INJURY_RATES was applied uniformly. With ONSET_EXPOSURE_STARTER / _BENCH = 1.05 / 0.84
        on the intended lineup, the ratio is taken on INTENDED-lineup membership (the 8 solves
        before each apportion boundary) -- the previous-week-lineup proxy the real data had to
        use dilutes as onset churn rises (1.19 at the old rates, 1.03 at Phase 7's) -- and
        the positional slot mix (K and three IDP slots start at low rates; RB/WR
        crowd the bench at high ones) confounds the RAW hazards -- so the ratio is taken on
        observed / expected-at-INJURY_RATES per group. Assert it sits above 1.0 and inside
        the real interval."""
        run = _ExposureRun.get()
        self.assertGreater(run.onsets["bench"], 100, "too few bench onsets to judge")
        ratio = run.relative_intended("starter") / run.relative_intended("bench")
        self.assertTrue(1.05 <= ratio <= 1.5, "intended-starter/bench onset ratio, positional mix removed: %.2f (starter obs/exp %.3f on %d exposures, bench %.3f on %d); previous-week-lineup proxy ratio %.2f"
                        % (ratio, run.relative_intended("starter"), run.i_exposure["starter"], run.relative_intended("bench"), run.i_exposure["bench"], run.relative("starter") / run.relative("bench")))

    def test_wall_clock_baseline_is_recorded(self):
        """Not an assertion on speed -- records the pre-F6 wall clock of the instrumented
        2 x 15 fixture run so the one-extra-Hungarian cost can be measured against it."""
        run = _ExposureRun.get()
        self.assertGreater(run.seconds, 0.0)
        print("\n[F6 wall clock] instrumented week01 2x15 run: %.1f s" % run.seconds)


# ------------------------------------------------------------- Phase 7: INJURY_RATES level
REAL_2025_ALL_CAUSE_HAZARD = {
    # pos: (onsets, exposures, Wilson 95% low, high). Real 2025, weeks 2-14, rostered players who
    # scored > 0 the previous week; hazard = P(zero this week), ANY cause. One season.
    "QB": (8, 149, 0.027, 0.102),
    "RB": (19, 414, 0.030, 0.071),
    "WR": (38, 472, 0.059, 0.109),
    "TE": (7, 142, 0.024, 0.098),
    "K": (0, 94, 0.000, 0.039),
}


class TestInjuryRateLevel(unittest.TestCase):
    """Phase 7, INJURY_RATES. The engine's realised per-position onset hazard on the fixture
    (which is INJURY_RATES[pos] up to the F6 exposure factors and sampling noise) against the
    real 2025 all-cause interval. Decision rule: a rate moves only if the config value lies
    OUTSIDE the interval, and then to the point estimate. QB (0.025 vs 0.027-0.102) and WR
    (0.040 vs 0.059-0.109) are outside; RB, TE and K are inside and stay."""

    def _hazard(self, pos):
        run = _ExposureRun.get()
        n, k = run.by_pos[pos]
        self.assertGreater(n, 2000, "%s: too few exposures on the fixture" % pos)
        return k / float(n), n, k

    def test_wr_onset_hazard_is_inside_the_real_interval(self):
        """GUARD (Phase 7; was characterisation at 0.040). WR re-derived to 0.081 (38/472),
        interval 0.059-0.109; the fixture must realise a hazard inside it."""
        h, n, k = self._hazard("WR")
        lo, hi = REAL_2025_ALL_CAUSE_HAZARD["WR"][2:]
        self.assertTrue(lo <= h <= hi, "WR realised hazard %.4f (%d/%d) outside real interval %.3f-%.3f" % (h, k, n, lo, hi))

    def test_qb_onset_hazard_is_inside_the_real_interval(self):
        """GUARD (Phase 7; was characterisation at 0.025). QB re-derived to 0.054 (8/149, n
        thin), interval 0.027-0.102; the fixture must realise a hazard inside it."""
        h, n, k = self._hazard("QB")
        lo, hi = REAL_2025_ALL_CAUSE_HAZARD["QB"][2:]
        self.assertTrue(lo <= h <= hi, "QB realised hazard %.4f (%d/%d) outside real interval %.3f-%.3f" % (h, k, n, lo, hi))

    def test_rb_te_k_onset_hazards_are_inside_the_real_interval(self):
        """GUARD: these three are inside the real interval today and must stay there -- they
        are deliberately NOT re-derived (RB at the top edge, TE n = 7, K 0/94)."""
        for pos in ("RB", "TE", "K"):
            h, n, k = self._hazard(pos)
            lo, hi = REAL_2025_ALL_CAUSE_HAZARD[pos][2:]
            self.assertTrue(lo <= h <= hi, "%s realised hazard %.4f (%d/%d) outside real interval %.3f-%.3f" % (pos, h, k, n, lo, hi))


class TestCommissionerExemptIsAnAbsence(unittest.TestCase):
    """Sleeper's injury_status "NA" (reserve / non-football, incl. the Commissioner Exempt
    list; a live case: Josh Jacobs, 2026 week 1) is a roster-eligibility absence of unknown
    length. It enters F4's clock at stage 2 (already >= 2 weeks in), like IR/PUP/Sus/DNR. The
    return hazard for NA was NOT isolated in the 2025 measurement -- the steady hazard is
    carried over, unverified, and config.py says so. Written before "NA" was in
    INITIAL_ABSENCE_STATUSES: the first assertion failed (clock 0 = healthy)."""

    def test_na_enters_on_a_stage_two_clock(self):
        np.random.seed(3)
        clocks = [FantasySimulationEngine._initial_absence_clock("NA", False) for _ in range(4000)]
        self.assertTrue(all(c >= 1 for c in clocks), "NA must never enter healthy")
        self.assertNotIn("NA", SIM_CONFIG["INITIAL_ABSENCE_STAGE1_STATUSES"])
        # stage 2: 1 + 0.84 / 0.16 = 6.25 expected weeks (cap 16 pulls it slightly down)
        self.assertGreater(float(np.mean(clocks)), 5.0); self.assertLess(float(np.mean(clocks)), 7.5)
        self.assertEqual(FantasySimulationEngine._initial_absence_clock(None, False), 0)


if __name__ == "__main__":
    unittest.main()

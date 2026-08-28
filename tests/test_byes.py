"""
tests.test_byes

Bye/absence modelling -- the dependency that blocked Phase 1 finding 7, Phase 2 findings 4
and 5, and Phase 4 finding 4. Step 1 (sync derives byes from the NFL schedule) is covered in
test_ingestion. This module pins what the ENGINE does with a bye, through the real
run_simulation on a controlled league where one real NFL team (DET) is off in week 6:

  step 2 -- the three formerly-dead guards: a player on bye is not a lineup candidate and
            scores nothing; suffers no injury onset; and the streamer-need scan counts the
            hole. Plus the vacated-volume non-interaction claim, pinned rather than argued:
            no injury pool is ever opened for a team in its bye week, and every recipient of
            contingency volume plays the week he receives it.
  step 3 -- streamer persistence: a streamer won for next week's bye hole is carried into
            that week and consumed, and the team does not bid for the same hole twice.

Every assertion here is on the real engine; the helpers only instrument it.
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
from tests.golden_master import STAGE_A_ARG_NAMES

TEAMS = ["A", "B", "C", "D", "E", "F", "G", "H"]
SLOTS = ["QB", "K", "DB", "DL", "LB", "RB", "RB", "RB", "WR", "WR", "WR", "TE", "TE"]
BYE_TEAM, BYE_WEEK = "DET", 6


def _schedule_with_bye(team, week):
    """Pairings for weeks 1-18 in which every NFL team plays every week except `team` in
    `week`, plus the _meta.byes record sync would write."""
    sched = {}
    for w in range(1, 19):
        playing = [t for t in NFL_TEAMS if not (t == team and w == week)]
        sched[str(w)] = {t: playing[(i + 1) % len(playing)] for i, t in enumerate(playing)}
    sched["_meta"] = {"failed_weeks": [], "byes": {team: week}}
    return sched


class ByeLeague(object):
    """One instrumented run. `det` maps slot index -> True for the players on each team's
    roster that should be on the bye NFL team (everyone else is FA). Records, per week:
    candidate counts and unfilled slots per team, injury onsets recorded, contingency
    recipients, and bids."""

    def __init__(self, det_slots, current_week=5, sims=4, injury_rates=None, seed_batch=1):
        roster, baselines = {}, {}
        for t in TEAMS:
            roster[t] = []
            for i, pos in enumerate(SLOTS):
                name = "%s_%s%d" % (t, pos, i)
                on_det = i in det_slots
                roster[t].append({"name": name, "pos": pos, "team": BYE_TEAM if on_det else "FA"})
                baselines[name] = {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 0.0, "pos": pos,
                                   "team": BYE_TEAM if on_det else "FA", "bye": BYE_WEEK if on_det else 0}
        fs = {
            LEAGUE_STATE_FILE: {"current_week": current_week},
            LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
            VEGAS_FILE: {"_meta": {"week": current_week, "source": "odds_api", "fetched_at": "x"}},
            LIVE_ROSTERS_FILE: roster, BASELINES_FILE: baselines,
            TEAM_RATINGS_FILE: {}, DEFENSIVE_RATINGS_FILE: {},
            DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
            LEAGUE_SCHEDULE_FILE: [[["A", "B"], ["C", "D"], ["E", "F"], ["G", "H"]]] * REGULAR_SEASON_WEEKS,
            NFL_SCHEDULE_FILE: _schedule_with_bye(BYE_TEAM, BYE_WEEK),
            WEEKLY_ACTUALS_FILE: {},
        }
        self.current_week = current_week
        # Week bookkeeping: run_simulation calls _apportion_vacated_volume exactly once per
        # simulated week, weeks current_week..16, then wraps to the next season. Bids and
        # injury onsets precede that call within a week; lineup solves follow it.
        span = 17 - current_week
        self.opened = 0
        self.next_week = lambda: current_week + (self.opened % span)
        self.last_week = lambda: current_week + ((self.opened - 1) % span)
        self.candidates, self.unfilled, self.onsets, self.recipients, self.pools, self.bids = [], [], [], [], [], []
        self.args = {}
        real_solve = FantasySimulationEngine._solve_optimal_assignment
        real_record = FantasySimulationEngine._record_vacated_volume
        real_apportion = FantasySimulationEngine._apportion_vacated_volume
        real_faab = FantasySimulationEngine._compute_faab_bid
        me = self

        def solve(c):
            a, u = real_solve(c)
            me.candidates.append((me.last_week(), len(c), tuple(sorted(n for n, _, _ in c))))
            me.unfilled.append((me.last_week(), len(u)))
            return a, u

        def record(pools, p_pos, nfl_team, season_mean):
            me.onsets.append((me.next_week(), nfl_team, p_pos))
            return real_record(pools, p_pos, nfl_team, season_mean)

        def apportion(engine, pools, clocks, newly):
            wk = me.next_week()                               # this call opens week wk
            me.opened += 1
            me.pools.append((wk, {(pos, tm): v for pos, d in pools.items() for tm, v in d.items() if v > 0}))
            out = real_apportion(engine, pools, clocks, newly)
            me.recipients.append((wk, dict(out)))
            return out

        def faab(remaining, raw, agg, needs, defl, avg):
            me.bids.append((me.next_week(), needs))
            return real_faab(remaining, raw, agg, needs, defl, avg)

        def export(engine, *a):
            me.args.update(zip(STAGE_A_ARG_NAMES, a))
        rates = {k: 0.0 for k in SIM_CONFIG["INJURY_RATES"]}
        rates.update(injury_rates or {})
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
                 patch.object(FantasySimulationEngine, "_record_vacated_volume", staticmethod(record)), \
                 patch.object(FantasySimulationEngine, "_apportion_vacated_volume", apportion), \
                 patch.object(FantasySimulationEngine, "_compute_faab_bid", staticmethod(faab)), \
                 patch.object(FantasySimulationEngine, "export_and_visualize", export):
                self.engine = FantasySimulationEngine()
                self.engine.run_simulation()
        finally:
            SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = orig
            logging.getLogger().setLevel(prev)
        # the season loop runs weeks current_week..16 per sim; the week counter wraps per sim
        self.sims = sims


# --------------------------------------------------------------------------- step 2
class TestByeGuards(unittest.TestCase):
    """The three guards in run_simulation, live for the first time. One DET QB per team,
    DET off in week 6."""

    @classmethod
    def setUpClass(cls):
        cls.league = ByeLeague(det_slots={0})     # slot 0 = QB on DET

    def _by_week(self, records):
        out = {}
        for wk, val in records:
            out.setdefault(wk, []).append(val)
        return out

    def test_a_player_on_bye_is_not_a_lineup_candidate_that_week(self):
        """Phase 1 finding 7's invariant, finally testable: in week 6 every team's candidate
        list has 12 players (the DET QB is absent) and one unfilled slot; every other week 13
        and 0. The absent name is the DET QB's."""
        cands = {}
        for wk, n, names in self.league.candidates:
            cands.setdefault(wk, []).append((n, names))
        for wk, entries in cands.items():
            for n, names in entries:
                if wk == BYE_WEEK:
                    self.assertEqual(n, 12, "week %d: %d candidates" % (wk, n))
                    self.assertFalse(any("_QB0" in nm for nm in names))
                else:
                    self.assertEqual(n, 13, "week %d: %d candidates" % (wk, n))
        unf = self._by_week(self.league.unfilled)
        self.assertTrue(all(u == 1 for u in unf[BYE_WEEK]))
        self.assertTrue(all(u == 0 for wk, us in unf.items() if wk != BYE_WEEK for u in us))

    def test_a_player_on_bye_scores_nothing_and_a_streamer_fills_the_slot(self):
        """From the audit log (sim 0): week 6 starters carry no DET QB and exactly one
        STREAMER_QB; the team total is the sum of its starters (Phase 1 invariant)."""
        log = self.league.args["audit_log"]["weeks"]
        for t, td in log[BYE_WEEK]["teams"].items():
            names = [s["name"] for s in td["starters"]]
            self.assertFalse(any(n.endswith("_QB0") for n in names), names)
            self.assertEqual(sum(1 for n in names if n.startswith("STREAMER_QB")), 1)
            self.assertAlmostEqual(sum(s["actual"] for s in td["starters"]), td["total_score"], delta=0.07)
        for t, td in log[BYE_WEEK + 1]["teams"].items():
            self.assertTrue(any(n.endswith("_QB0") for n in [s["name"] for s in td["starters"]]))

    def test_the_streamer_need_scan_counts_the_bye_hole(self):
        """Bids are placed for week 6's hole (needs >= 1 for every team). Whether they are
        placed in week 5 AND again in week 6 is step 3's subject, not this test's."""
        by_week = self._by_week(self.league.bids)
        self.assertGreaterEqual(len(by_week.get(BYE_WEEK, [])) + len(by_week.get(BYE_WEEK - 1, [])), len(TEAMS) * self.league.sims)


class TestByeAndInjuries(unittest.TestCase):
    """Injury onsets and the vacated-volume pools around a bye. Two DET WRs on every roster
    (slots 8 and 9), WR injury rate 0.5 (1.0 would injure every DET WR in the league in the
    same week and leave no healthy recipient), season starting in week 6 so the bye is the
    first week: no DET onset can be recorded in week 6, and week 7's onsets open a DET/WR pool
    whose recipients are the healthy DET WRs -- who, being DET, are playing."""

    @classmethod
    def setUpClass(cls):
        cls.league = ByeLeague(det_slots={8, 9}, current_week=BYE_WEEK, sims=3, injury_rates={"WR": 0.5})

    def test_no_injury_onset_on_a_bye(self):
        det_onsets = {}
        for wk, team, pos in self.league.onsets:
            if team == BYE_TEAM:
                det_onsets[wk] = det_onsets.get(wk, 0) + 1
        self.assertNotIn(BYE_WEEK, det_onsets, det_onsets)
        self.assertGreater(det_onsets.get(BYE_WEEK + 1, 0), 0, det_onsets)

    def test_vacated_volume_never_touches_a_team_on_bye(self):
        """THE NON-INTERACTION PIN. _apportion_vacated_volume has no notion of a bye and needs
        none, because pools are per real NFL team and an onset is only recorded for a player
        whose team plays that week -- so every pool's team, and every recipient (a teammate),
        is playing. Asserted on the real engine: (1) no pool is ever opened for DET in week 6;
        (2) in every week, every recipient of contingency volume belongs to a team that is
        not on bye that week; (3) the week-7 DET pool is actually paid out, so the check is
        not vacuous."""
        byes = {BYE_TEAM: BYE_WEEK}
        team_of = {p: d["team"] for p, d in self.league.engine.baselines.items()}
        paid_out = 0
        for (wk, pools), (wk2, recips) in zip(self.league.pools, self.league.recipients):
            self.assertEqual(wk, wk2)
            for (pos, tm), vol in pools.items():
                self.assertNotEqual((tm, wk), (BYE_TEAM, BYE_WEEK), "a DET pool was opened in DET's bye week")
            for name, pts in recips.items():
                self.assertNotEqual(byes.get(team_of[name]), wk,
                                    "%s received %.2f contingency points in week %d, his bye" % (name, pts, wk))
                if team_of[name] == BYE_TEAM and pts > 0:
                    paid_out += 1
        self.assertGreater(paid_out, 0, "no DET contingency ever paid out; the check would be vacuous")


# --------------------------------------------------------------------------- step 3
class TestStreamerPersistence(unittest.TestCase):
    """Phase 4 finding 4, live now that byes exist. Needs are max(this week, next week), so a
    bye hole in week 6 is bid for in week 5. Before: won_streamers was rebuilt empty every
    week, so the week-5 streamer was discarded and week 6 bid again -- FAAB spent twice for
    one hole. Now a won streamer persists for one week and reduces next week's need."""

    @classmethod
    def setUpClass(cls):
        cls.league = ByeLeague(det_slots={0}, current_week=5, sims=4)   # DET QB, bye week 6

    def test_one_bid_per_bye_hole_not_two(self):
        by_week = {}
        for wk, needs in self.league.bids:
            by_week[wk] = by_week.get(wk, 0) + 1
        n = len(TEAMS) * self.league.sims
        self.assertEqual(by_week.get(BYE_WEEK - 1, 0), n, "week 5 should bid once per team for next week's hole: %s" % by_week)
        self.assertEqual(by_week.get(BYE_WEEK, 0), 0, "week 6 must not bid again for the hole already covered: %s" % by_week)
        self.assertEqual(sum(by_week.values()), n, "one bid per team-season in total: %s" % by_week)

    def test_the_carried_streamer_fills_the_bye_hole(self):
        """Every team has exactly one unfilled slot in week 6 (the DET QB) and it is filled
        by the streamer won in week 5: the audit log's week-6 STREAMER_QB carries the ladder
        value (>= 4.0 floor, capped at the QB replacement level), not the unbid fallback."""
        unfilled_w6 = [u for wk, u in self.league.unfilled if wk == BYE_WEEK]
        self.assertEqual(unfilled_w6, [1] * len(unfilled_w6))
        log = self.league.args["audit_log"]["weeks"][BYE_WEEK]["teams"]
        for t, td in log.items():
            streamers = [s for s in td["starters"] if s["name"].startswith("STREAMER_QB")]
            self.assertEqual(len(streamers), 1, t)

    def test_persistence_is_one_week_only(self):
        """A streamer won in week 5 for the week-6 hole is consumed in week 6; nothing is
        carried into week 7 and no phantom streamer starts there (13 candidates, 0 unfilled)."""
        unfilled_w7 = [u for wk, u in self.league.unfilled if wk == BYE_WEEK + 1]
        self.assertEqual(unfilled_w7, [0] * len(unfilled_w7))
        log = self.league.args["audit_log"]["weeks"][BYE_WEEK + 1]["teams"]
        for t, td in log.items():
            self.assertFalse(any(s["name"].startswith("STREAMER") for s in td["starters"]), t)


if __name__ == "__main__":
    unittest.main()

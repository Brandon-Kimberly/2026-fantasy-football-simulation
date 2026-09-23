"""B3: the lineup tools re-solve the whole week as if nothing had kicked off.

On Sunday 2026-09-20 at 08:37 PT the optimizer wanted one player at DB and another in a
FLEX -- but both of the men it wanted to bench had played Thursday and were LOCKED. Its
"expected total 204.6" was a lineup that could not be set. The same weekend the weekly
report said P(win) 72.1% while the live tracker said 57.8%; the gap was Thursday's banked
result plus a lineup no longer reachable.

THE TRAP B3 NAMES, and it shapes the whole design: "the pre-kickoff optimizer is correct
and must not change. Only the mid-week behaviour is wrong. Gate on game_clocks, not on the
day of the week." So every test below that exercises locks passes them in EXPLICITLY, and
`TestPreKickoffIsUntouched` pins that the no-locks path is bit-for-bit the old behaviour.

`game_clocks` hits ESPN, so the lock logic is pure and takes the clocks dict; the network
stays in the script and the suite stays hermetic (F48).

Written before the change and confirmed failing (rule 1).
"""
import logging
import unittest
from unittest.mock import patch

from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)

TEAMS = ['Quantum Ferrets', 'Neon Walruses', 'Rocket Pandas', 'Polar Yetis']
SLOTS = ['QB', 'RB', 'RB', 'WR', 'WR', 'TE', 'FLEX', 'FLEX', 'FLEX', 'K', 'DL', 'LB', 'DB']

# Two NFL teams: THU played Thursday (locked), SUN has not kicked off.
CLOCKS_MIDWEEK = {"THU": (0.0, "final"), "SUN": (1.0, "pregame")}
CLOCKS_PREGAME = {"THU": (1.0, "pregame"), "SUN": (1.0, "pregame")}


def _p(mean, pos, team):
    return {"mean": mean, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
            "team": team, "bye": 0}


def _fs():
    """One roster where the WEAK WR played Thursday and the STRONG WR plays Sunday.

    Pre-kickoff the optimizer rightly starts the strong one. Mid-week that swap is
    unreachable: the weak one is already locked into the lineup and cannot be benched,
    and a locked BENCH player cannot be promoted either.
    """
    base, rosters = {}, {}
    for t in TEAMS:
        entries = []
        for si, slot in enumerate(SLOTS):
            pos = {"FLEX": "WR"}.get(slot, slot)
            n = f"{t[:2]}_{slot}_{si}"
            base[n] = _p(12.0 - si * 0.1, pos, "SUN")
            entries.append({"name": n, "pos": pos, "team": "SUN"})
        rosters[t] = entries
    me = TEAMS[0][:2]
    # the starter who already played (Thursday) and is WEAK -- optimizer wants him out
    base[f"{me}_WR_3"] = _p(4.0, "WR", "THU")
    rosters[TEAMS[0]][3]["team"] = "THU"
    # a strong bench WR who plays Sunday -- optimizer wants him in
    base[f"{me}_BENCH_STRONG"] = _p(20.0, "WR", "SUN")
    rosters[TEAMS[0]].append({"name": f"{me}_BENCH_STRONG", "pos": "WR", "team": "SUN"})
    # a strong bench WR who ALREADY PLAYED -- must never be proposed
    base[f"{me}_BENCH_PLAYED"] = _p(25.0, "WR", "THU")
    rosters[TEAMS[0]].append({"name": f"{me}_BENCH_PLAYED", "pos": "WR", "team": "THU"})
    return {
        LEAGUE_STATE_FILE: {"current_week": 1},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: {"_meta": {"week": 1, "source": "odds_api", "fetched_at": "x"},
                     "THU": {"total": 22.0, "spread": 0.0, "opponent": "SUN"},
                     "SUN": {"total": 22.0, "spread": 0.0, "opponent": "THU"}},
        LIVE_ROSTERS_FILE: rosters,
        BASELINES_FILE: base,
        TEAM_RATINGS_FILE: {"THU": {"off_rating": 22}, "SUN": {"off_rating": 22}},
        DEFENSIVE_RATINGS_FILE: {"THU": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                 "SUN": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[TEAMS[0], TEAMS[1]], [TEAMS[2], TEAMS[3]]]] * 14,
        NFL_SCHEDULE_FILE: {str(w): {"THU": "SUN", "SUN": "THU"} for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: {},
    }


class _Case(unittest.TestCase):
    def setUp(self):
        self.fs = _fs()
        self.me = TEAMS[0]
        self.pre = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        self.p_exists = patch('os.path.exists', side_effect=lambda p: p in self.fs)
        self.p_load = patch('fantasy_sim.simulation.load_json', side_effect=lambda p: self.fs[p])
        self.p_exists.start(); self.p_load.start()
        self.engine = FantasySimulationEngine()
        # what Sleeper says is CURRENTLY started: the 13 slot players, index-aligned to SLOTS
        self.current = {f"{self.me[:2]}_{s}_{i}": s for i, s in enumerate(SLOTS)}

    def tearDown(self):
        self.p_exists.stop(); self.p_load.stop()
        logging.getLogger().setLevel(self.pre)


class TestLockedTeams(unittest.TestCase):
    """Pure. A team is locked once its game leaves 'pregame' -- in progress or final."""

    def test_pregame_is_not_locked(self):
        from fantasy_sim.decisions import locked_nfl_teams
        self.assertEqual(locked_nfl_teams(CLOCKS_PREGAME), frozenset())

    def test_final_and_in_progress_are_locked(self):
        from fantasy_sim.decisions import locked_nfl_teams
        self.assertEqual(locked_nfl_teams(CLOCKS_MIDWEEK), frozenset({"THU"}))
        self.assertEqual(locked_nfl_teams({"A": (0.4, "Q3 05:22")}), frozenset({"A"}))

    def test_unknown_status_is_treated_as_unlocked(self):
        """A team missing from the scoreboard must not be silently frozen out of the
        lineup -- an absent clock is ignorance, not a kickoff."""
        from fantasy_sim.decisions import locked_nfl_teams
        self.assertEqual(locked_nfl_teams({"A": (1.0, "unknown")}), frozenset())

    def test_empty_clocks_lock_nothing(self):
        from fantasy_sim.decisions import locked_nfl_teams
        self.assertEqual(locked_nfl_teams({}), frozenset())
        self.assertEqual(locked_nfl_teams(None), frozenset())


class TestPreKickoffIsUntouched(_Case):
    """B3's trap. With no locks the optimizer must behave exactly as before."""

    def test_no_locks_means_the_old_answer(self):
        from fantasy_sim.decisions import optimize_lineup
        a = optimize_lineup(self.engine, self.me, 1, sims=50, seed=7)
        b = optimize_lineup(self.engine, self.me, 1, sims=50, seed=7,
                            locked_teams=frozenset(), current_starters=self.current)
        self.assertEqual([r["name"] for r in a["lineup"]], [r["name"] for r in b["lineup"]])
        self.assertAlmostEqual(a["expected_total"], b["expected_total"], places=9)

    def test_pre_kickoff_the_strong_bench_player_is_promoted(self):
        """The behaviour that must survive: with nothing locked, the optimizer swaps the
        weak starter out for the strong bench man."""
        from fantasy_sim.decisions import optimize_lineup
        r = optimize_lineup(self.engine, self.me, 1, sims=50, seed=7)
        names = [x["name"] for x in r["lineup"]]
        self.assertIn(f"{self.me[:2]}_BENCH_STRONG", names)


class TestMidWeekLocks(_Case):
    def test_a_locked_starter_cannot_be_benched(self):
        from fantasy_sim.decisions import optimize_lineup
        r = optimize_lineup(self.engine, self.me, 1, sims=50, seed=7,
                            locked_teams=frozenset({"THU"}), current_starters=self.current)
        names = [x["name"] for x in r["lineup"]]
        self.assertIn(f"{self.me[:2]}_WR_3", names,
                      "B3: he already played; the lineup cannot un-start him")

    def test_a_locked_bench_player_is_never_proposed(self):
        from fantasy_sim.decisions import optimize_lineup
        r = optimize_lineup(self.engine, self.me, 1, sims=50, seed=7,
                            locked_teams=frozenset({"THU"}), current_starters=self.current)
        names = [x["name"] for x in r["lineup"]]
        alts = [x.get("alternative") for x in r["lineup"]]
        self.assertNotIn(f"{self.me[:2]}_BENCH_PLAYED", names,
                         "B3: his game is over; he cannot be started")
        self.assertNotIn(f"{self.me[:2]}_BENCH_PLAYED", alts,
                         "B3: nor offered as an alternative")

    def test_the_unlocked_strong_bench_player_is_still_promoted(self):
        """Locks must constrain only the locked. An unlocked upgrade stays available."""
        from fantasy_sim.decisions import optimize_lineup
        r = optimize_lineup(self.engine, self.me, 1, sims=50, seed=7,
                            locked_teams=frozenset({"THU"}), current_starters=self.current)
        self.assertIn(f"{self.me[:2]}_BENCH_STRONG", [x["name"] for x in r["lineup"]])

    def test_the_result_reports_how_many_slots_were_pinned(self):
        from fantasy_sim.decisions import optimize_lineup
        r = optimize_lineup(self.engine, self.me, 1, sims=50, seed=7,
                            locked_teams=frozenset({"THU"}), current_starters=self.current)
        self.assertEqual(r.get("pinned"), 1,
                         "B3 scope: the header says how many slots were pinned")
        self.assertEqual(r.get("locked_excluded"), 1,
                         "and how many bench players were ruled out")

    def test_the_reachable_total_is_not_the_fantasy_total(self):
        """The defect in one number: the unconstrained solve reports a total that cannot
        be achieved once Thursday has played."""
        from fantasy_sim.decisions import optimize_lineup
        free = optimize_lineup(self.engine, self.me, 1, sims=50, seed=7)
        real = optimize_lineup(self.engine, self.me, 1, sims=50, seed=7,
                               locked_teams=frozenset({"THU"}), current_starters=self.current)
        self.assertLess(real["expected_total"], free["expected_total"],
                        "the locked solve must be no better than the fantasy one")


class TestTheGate(unittest.TestCase):
    """B3's trap again: gate on game_clocks, not on the day of the week."""

    def test_locks_apply_only_to_the_current_week(self):
        from fantasy_sim.decisions import should_respect_locks
        self.assertFalse(should_respect_locks(week=5, current_week=3, clocks=CLOCKS_MIDWEEK))

    def test_locks_apply_when_any_game_has_started_this_week(self):
        from fantasy_sim.decisions import should_respect_locks
        self.assertTrue(should_respect_locks(week=3, current_week=3, clocks=CLOCKS_MIDWEEK))

    def test_no_locks_before_the_first_kickoff(self):
        from fantasy_sim.decisions import should_respect_locks
        self.assertFalse(should_respect_locks(week=3, current_week=3, clocks=CLOCKS_PREGAME))

    def test_unreachable_clocks_do_not_silently_enable_locks(self):
        """If the scoreboard fetch fails we know nothing; the pre-kickoff answer is the
        safe one, and it is what the tool printed all last season."""
        from fantasy_sim.decisions import should_respect_locks
        self.assertFalse(should_respect_locks(week=3, current_week=3, clocks=None))


if __name__ == "__main__":
    unittest.main()

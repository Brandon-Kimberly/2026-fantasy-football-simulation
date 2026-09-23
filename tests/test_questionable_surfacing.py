"""B4: `Questionable` is invisible to every tool, and no tool says so.

`INITIAL_ABSENCE_STATUSES = ('IR','PUP','Out','Sus','DNR','NA')`. `Questionable` is in no
absence set anywhere, so `_initial_absence_clock` returns 0 for a Questionable player
exactly as for a healthy one, and `_unavailable_now` reports him available. Every
projection, every VORP and every win probability treats a flak-jacketed receiver with a
broken rib as fully fit. Confirmed live on 2026-09-20 (the F50/F51 work): a Questionable
designation changed nothing in the sim because it never reached it.

THIS IS A SURFACING CHANGE, NOT A MODELLING ONE. `Questionable` is deliberately NOT added
to `INITIAL_ABSENCE_STATUSES`, and no haircut is applied:

  - the Sleeper projection the baseline derives from ALREADY reflects expected usage for a
    Questionable player, so a second discount would double-count;
  - in-week availability is a call the owner hedges by hand off the Saturday designations
    (F51, and CLAUDE.md's live_matchup note).

The defect is that the tools never TELL the owner which numbers carry that unpriced risk.
So these tests assert that the designation appears in the output, and -- just as
importantly -- that the numbers do not move. A test below pins the trap directly.

Written before the change and confirmed failing (rule 1).
"""
import logging
import unittest
from unittest.mock import patch

from fantasy_sim.simulation import FantasySimulationEngine, SIM_CONFIG
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)

TEAMS = ['Quantum Ferrets', 'Neon Walruses', 'Rocket Pandas', 'Polar Yetis']
SLOTS = ['QB', 'RB', 'RB', 'WR', 'WR', 'TE', 'FLEX', 'FLEX', 'FLEX', 'K', 'DL', 'LB', 'DB']


def _p(mean, pos, team="DET", **kw):
    d = {"mean": mean, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
         "team": team, "bye": 0}
    d.update(kw)
    return d


def _fs():
    """One roster with a QUESTIONABLE starting WR who has a healthy bench alternative."""
    base = {}
    rosters = {}
    for ti, t in enumerate(TEAMS):
        entries = []
        for si, slot in enumerate(SLOTS):
            pos = {"FLEX": "WR"}.get(slot, slot)
            n = f"{t[:2]}_{slot}_{si}"
            base[n] = _p(12.0 - si * 0.1, pos)
            entries.append({"name": n, "pos": pos, "team": "DET"})
        # bench WR, healthy, clearly worse
        b = f"{t[:2]}_BENCH_WR"
        base[b] = _p(8.0, "WR")
        entries.append({"name": b, "pos": "WR", "team": "DET"})
        rosters[t] = entries
    # make ONE starter on the first team Questionable
    global QUESTIONABLE
    QUESTIONABLE = f"{TEAMS[0][:2]}_WR_3"
    base[QUESTIONABLE] = _p(12.0 - 3 * 0.1, "WR", injury_status="Questionable")
    return {
        LEAGUE_STATE_FILE: {"current_week": 1},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: {"_meta": {"week": 1, "source": "odds_api", "fetched_at": "x"},
                     "DET": {"total": 24.0, "spread": -4.0, "opponent": "CHI"},
                     "CHI": {"total": 20.0, "spread": 4.0, "opponent": "DET"}},
        LIVE_ROSTERS_FILE: rosters,
        BASELINES_FILE: base,
        TEAM_RATINGS_FILE: {"DET": {"off_rating": 25}, "CHI": {"off_rating": 20}},
        DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                 "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[TEAMS[0], TEAMS[1]], [TEAMS[2], TEAMS[3]]]] * 14,
        NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: {},
    }


QUESTIONABLE = ""


class _Case(unittest.TestCase):
    def setUp(self):
        self.fs = _fs()
        self.prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        self.p_exists = patch('os.path.exists', side_effect=lambda p: p in self.fs)
        self.p_load = patch('fantasy_sim.simulation.load_json', side_effect=lambda p: self.fs[p])
        self.p_exists.start(); self.p_load.start()
        self.engine = FantasySimulationEngine()

    def tearDown(self):
        self.p_exists.stop(); self.p_load.stop()
        logging.getLogger().setLevel(self.prev)


class TestTheTrapStaysShut(_Case):
    """B4's explicit trap: 'Do not add Questionable to INITIAL_ABSENCE_STATUSES. That is a
    modelling change with no evidence base and it would move every prediction.'"""

    def test_questionable_is_not_an_absence_status(self):
        self.assertNotIn("Questionable", SIM_CONFIG["INITIAL_ABSENCE_STATUSES"])

    def test_a_questionable_player_draws_no_absence_clock(self):
        self.assertFalse(self.engine._initial_absence_clock("Questionable", False))

    def test_a_questionable_player_is_still_startable(self):
        from fantasy_sim.decisions import _unavailable_now, _entry
        self.assertFalse(_unavailable_now(_entry(self.engine, QUESTIONABLE)))

    def test_his_week_expectation_is_not_discounted(self):
        """The surfacing must not sneak in a haircut: the Sleeper projection already
        prices expected usage, so a second discount would double-count."""
        from fantasy_sim.decisions import week_expectation
        healthy_twin = f"{TEAMS[0][:2]}_WR_4"
        q = week_expectation(self.engine, QUESTIONABLE, 1)
        h = week_expectation(self.engine, healthy_twin, 1)
        self.assertAlmostEqual(q / self.engine.baselines[QUESTIONABLE]["mean"],
                               h / self.engine.baselines[healthy_twin]["mean"], places=6,
                               msg="a Questionable player must be scaled exactly like a "
                                   "healthy one -- surfacing only")


class TestTheDesignationReachesTheTools(_Case):
    def test_optimize_lineup_rows_carry_the_designation(self):
        from fantasy_sim.decisions import optimize_lineup
        r = optimize_lineup(self.engine, TEAMS[0], 1, sims=50, seed=1)
        row = next((x for x in r["lineup"] if x["name"] == QUESTIONABLE), None)
        self.assertIsNotNone(row, "the Questionable player should still be STARTING")
        self.assertEqual(row.get("flag"), "Questionable",
                         "B4: every tool that prints a starter must print his designation")

    def test_healthy_starters_carry_an_empty_flag_not_none(self):
        from fantasy_sim.decisions import optimize_lineup
        r = optimize_lineup(self.engine, TEAMS[0], 1, sims=50, seed=1)
        healthy = [x for x in r["lineup"] if x["name"] != QUESTIONABLE]
        self.assertTrue(healthy)
        self.assertTrue(all(x.get("flag") == "" for x in healthy),
                        "a healthy starter's flag must be '' so rendering needs no None check")

    def test_bench_rows_carry_it_too(self):
        from fantasy_sim.decisions import optimize_lineup
        r = optimize_lineup(self.engine, TEAMS[0], 1, sims=50, seed=1)
        self.assertTrue(all("flag" in b for b in r["bench"]))

    def test_the_report_summarises_questionable_starters(self):
        """B4 scope 2: a block listing each Questionable starter with his best bench
        fallback by week expectation."""
        from fantasy_sim.decisions import optimize_lineup
        r = optimize_lineup(self.engine, TEAMS[0], 1, sims=50, seed=1)
        q = r.get("questionable_starters")
        self.assertIsInstance(q, list, "B4: the report needs a Questionable-starters block")
        self.assertEqual([x["name"] for x in q], [QUESTIONABLE])
        self.assertIn("fallback", q[0])
        self.assertTrue(q[0]["fallback"], "each entry names the best bench fallback")


class TestTheHelper(_Case):
    def test_injury_flag_reads_the_designation(self):
        from fantasy_sim.decisions import injury_flag, _entry
        self.assertEqual(injury_flag(_entry(self.engine, QUESTIONABLE)), "Questionable")

    def test_injury_flag_is_empty_for_a_healthy_player(self):
        from fantasy_sim.decisions import injury_flag, _entry
        self.assertEqual(injury_flag(_entry(self.engine, f"{TEAMS[0][:2]}_WR_4")), "")

    def test_injury_flag_reports_ir_too(self):
        """Not only Questionable: any non-null designation is worth printing."""
        from fantasy_sim.decisions import injury_flag
        self.assertEqual(injury_flag({"injury_status": "Out"}), "Out")
        self.assertEqual(injury_flag({"on_ir": True}), "IR")


class TestCountsForLiveMatchup(_Case):
    """B4 scope 3: live_matchup prints the Questionable count for BOTH rosters, because
    F51's optimism only cancels when they are comparable."""

    def test_a_team_can_be_counted(self):
        from fantasy_sim.decisions import questionable_count
        self.assertEqual(questionable_count(self.engine, TEAMS[0]), 1)
        self.assertEqual(questionable_count(self.engine, TEAMS[1]), 0)


if __name__ == "__main__":
    unittest.main()

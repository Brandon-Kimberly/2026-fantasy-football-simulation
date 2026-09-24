"""C5: measuring `BASE_STREAMER_MEANS` against the free-agent pool that could fill the hole.

An unfillable slot is scored `max(0, N(m_str, 2.2))` with
`m_str = max(replacement_level[pos] * 0.8, BASE_STREAMER_MEANS[pos])`, and every evaluation
of a hole is priced against it. If `m_str` is below what is claimable for nothing, filling
the hole looks better than it is.

MEASUREMENT ONLY, AND THE TESTS PIN THAT. `BASE_STREAMER_MEANS` is read at engine init by
every hole evaluation, so changing it is a MAJOR release and the owner's decision. This
module reports a gap and a what-if; it moves no constant and mutates no engine.

TWO THINGS THE BACKLOG ITEM GOT WRONG, both pinned below:

  * **FLEX is a slot, not a position.** No free agent normalises to "FLEX", so measuring it
    against its own pool reports a meaningless gap against nothing. The pool that can
    legally fill a FLEX is RB, WR and TE together.
  * **`m_str` is not `BASE_STREAMER_MEANS[pos]`.** It is the max of that and
    `replacement * 0.8`, which for QB on the live roster is 14.66, not 14.0. Comparing the
    pool to the raw constant overstates the gap.

PHASE 4'S CAP IS LOAD-BEARING. A streamer may never out-project the replacement level, or
a HOLE becomes worth more than a player and the tools start recommending you empty a slot.
`derived_capped` is therefore `min(pool_top_mean, replacement)`, and a test pins it.

New capability, so no red characterisation exists and none is claimed; these passed on the
first run and were verified by mutation (see the commit message).
"""
import json
import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)
from fantasy_sim.streamer_study import free_agent_pool, render_lines, streamer_gap

TEAMS = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis"]
WEEK = 3
# QB is rich on purpose: the rostered QBs put the replacement level above 17.5, so
# `replacement * 0.8` (14.0+) BEATS the 14.0 constant and m_str comes from the floor, not
# the constant. Without a position in that branch, a mutation replacing the whole formula
# with the bare constant passes -- which is exactly what happened on the first run of this
# file, and it is the branch the live roster is in (QB replacement 18.32 -> m_str 14.66).
SLOTS = [("QB", 24.0), ("RB", 17.0), ("RB", 15.0), ("WR", 16.0), ("WR", 13.0),
         ("TE", 12.0), ("K", 9.0), ("DL", 9.0), ("LB", 9.0), ("DB", 9.0),
         ("RB", 11.0), ("WR", 10.5), ("WR", 10.0)]
# Unrostered, so they are the pool. QB deliberately rich (a claimable 18-19 behind a 14.0
# constant is the case C5 was written about) and K deliberately empty.
#
# The QB numbers also set the replacement level: `_calc_replacement_levels` takes the Nth
# best mean in the WHOLE baseline pool (rostered and free alike; N = 10 for QB, clamped to
# the pool size), so with four 24.0 starters and these three the level lands at 18.0 and
# `replacement * 0.8` = 14.4 beats the 14.0 constant. That is the branch the live roster is
# in, and the branch the first version of this fixture never reached.
FREE = [("FA_QB_1", "QB", 19.0), ("FA_QB_2", "QB", 18.5), ("FA_QB_3", "QB", 18.0),
        ("FA_RB_1", "RB", 6.0), ("FA_RB_2", "RB", 5.0),
        ("FA_WR_1", "WR", 8.0), ("FA_WR_2", "WR", 7.0),
        ("FA_TE_1", "TE", 9.5), ("FA_TE_2", "TE", 4.0),
        ("FA_DL_1", "DL", 7.0), ("FA_LB_1", "LB", 7.0), ("FA_DB_1", "DB", 7.0)]


def _fs():
    base, rosters, pid = {}, {}, 6000
    for t in TEAMS:
        entries = []
        for i, (pos, mu) in enumerate(SLOTS):
            pid += 1
            nm = f"{t[:2]}_{pos}_{i}"
            base[nm] = {"mean": mu, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
                        "team": "DET", "bye": 0, "player_id": str(pid)}
            entries.append({"name": nm, "pos": pos, "team": "DET"})
        rosters[t] = entries
    for nm, pos, mu in FREE:
        pid += 1
        base[nm] = {"mean": mu, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
                    "team": "DET", "bye": 0, "player_id": str(pid)}
    return {
        LEAGUE_STATE_FILE: {"current_week": WEEK},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: {"_meta": {"week": WEEK, "source": "odds_api", "fetched_at": "x"},
                     "DET": {"total": 22.0, "spread": 0.0, "opponent": "CHI"},
                     "CHI": {"total": 22.0, "spread": 0.0, "opponent": "DET"}},
        LIVE_ROSTERS_FILE: rosters,
        BASELINES_FILE: base,
        TEAM_RATINGS_FILE: {"DET": {"off_rating": 22}, "CHI": {"off_rating": 22}},
        DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                 "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[TEAMS[0], TEAMS[1]], [TEAMS[2], TEAMS[3]]]] * 14,
        NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: {},
    }


def _engine():
    fs = _fs()
    pre = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.ERROR)
    with patch("os.path.exists", side_effect=lambda p: p in fs), \
         patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]):
        e = FantasySimulationEngine()
    logging.getLogger().setLevel(pre)
    return e


def _row(study, pos):
    return next(r for r in study["rows"] if r["pos"] == pos)


class TestThePool(unittest.TestCase):
    def test_only_unrostered_players_are_in_it(self):
        pool = free_agent_pool(_engine())
        self.assertEqual([n for _v, n in pool["QB"]], ["FA_QB_1", "FA_QB_2", "FA_QB_3"])

    def test_FLEX_is_the_union_of_RB_WR_and_TE(self):
        """A slot, not a position. Nobody normalises to FLEX, so its own pool is empty and
        measuring against it would report a meaningless gap against nothing."""
        pool = free_agent_pool(_engine())
        self.assertEqual(sorted(n for _v, n in pool["FLEX"]),
                         sorted(n for n, p, _m in FREE if p in ("RB", "WR", "TE")))

    def test_a_position_with_no_free_agent_is_absent_not_zero(self):
        pool = free_agent_pool(_engine())
        self.assertNotIn("K", pool, "zero would read as a pool worth nothing")


class TestTheGapTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = _engine()
        cls.study = streamer_gap(cls.e, top_n=3)

    def test_m_str_is_the_engines_own_formula_not_the_bare_constant(self):
        """`max(replacement * 0.8, BASE)`. Comparing a pool against the raw constant
        overstates the gap, which is what the backlog item did (it read QB's streamer as
        14.0 when the live floor makes it 14.66)."""
        from fantasy_sim.config import BASE_STREAMER_MEANS
        for r in self.study["rows"]:
            rep = float(self.e.replacement_levels.get(r["pos"], 4.0))
            self.assertAlmostEqual(r["m_str"],
                                   round(max(rep * 0.8, BASE_STREAMER_MEANS[r["pos"]]), 2),
                                   places=2, msg=r["pos"])

    def test_at_least_one_position_takes_the_replacement_FLOOR(self):
        """The control for the test above. Every position in the first version of this
        fixture had `replacement * 0.8` BELOW the constant, so the max never mattered and a
        mutation deleting it passed. QB is now in the floor branch, as it is live."""
        from fantasy_sim.config import BASE_STREAMER_MEANS
        r = _row(self.study, "QB")
        self.assertGreater(r["m_str"], BASE_STREAMER_MEANS["QB"],
                           "the floor must actually bind somewhere in this fixture")
        self.assertAlmostEqual(r["m_str"], round(r["replacement"] * 0.8, 2), places=2)

    def test_the_QB_gap_is_measured_against_the_top_three(self):
        r = _row(self.study, "QB")
        self.assertAlmostEqual(r["top_mean"], 18.5, places=2)      # (19 + 18.5 + 18) / 3
        self.assertAlmostEqual(r["gap"], round(18.5 - r["m_str"], 2), places=2)

    def test_a_position_with_no_pool_reports_None_rather_than_a_gap(self):
        r = _row(self.study, "K")
        self.assertEqual((r["top_mean"], r["gap"], r["derived_capped"]), (None, None, None))
        self.assertEqual(r["pool_n"], 0)

    def test_the_derived_streamer_never_exceeds_replacement(self):
        """Phase 4's cap. Without it a hole becomes worth more than a rostered starter and
        the tools start recommending you empty a slot."""
        for r in self.study["rows"]:
            if r["derived_capped"] is not None:
                self.assertLessEqual(r["derived_capped"], r["replacement"] + 1e-9, r["pos"])

    def test_where_the_cap_binds_it_is_flagged(self):
        for r in self.study["rows"]:
            if r["top_mean"] is None:
                continue
            self.assertEqual(r["cap_binds"], r["top_mean"] > r["replacement"], r["pos"])

    def test_a_pool_below_the_streamer_reports_a_NEGATIVE_gap(self):
        """Not every position is understated, and a study that could only find gaps in one
        direction would not be a measurement. The fixture's RB pool is deliberately poor."""
        self.assertLess(_row(self.study, "RB")["gap"], 0)

    def test_it_says_it_changes_nothing_and_that_the_change_is_MAJOR(self):
        self.assertIn("MEASUREMENT ONLY", self.study["note"])
        self.assertIn("MAJOR", self.study["note"])

    def test_no_constant_and_no_engine_state_is_touched(self):
        from fantasy_sim.config import BASE_STREAMER_MEANS
        before_const = dict(BASE_STREAMER_MEANS)
        e = _engine()
        before_rep = dict(e.replacement_levels)
        streamer_gap(e)
        self.assertEqual(dict(BASE_STREAMER_MEANS), before_const)
        self.assertEqual(dict(e.replacement_levels), before_rep)


class TestRenderingAndRecording(unittest.TestCase):
    def test_every_position_reaches_the_text(self):
        from fantasy_sim.config import BASE_STREAMER_MEANS
        text = "\n".join(render_lines(streamer_gap(_engine())))
        for pos in BASE_STREAMER_MEANS:
            self.assertIn(pos, text, pos)
        self.assertIn("no free agent at this position", text, "K has an empty pool")

    def test_recording_appends_one_row_and_never_raises(self):
        from scripts.streamer_study import record
        d = tempfile.mkdtemp()
        p = os.path.join(d, "streamer_levels.jsonl")
        study = streamer_gap(_engine())
        self.assertEqual(record(study, WEEK, path=p), 1)
        self.assertEqual(record(study, WEEK, path=p), 1)
        with open(p, encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh if x.strip()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["week"], WEEK)
        self.assertTrue(any(r["pos"] == "QB" for r in rows[0]["rows"]))

    def test_an_unwritable_path_is_reported_not_raised(self):
        """A log is a record, not a dependency -- the contract every other log here follows."""
        from scripts.streamer_study import record
        self.assertEqual(record(streamer_gap(_engine()), WEEK, path=os.devnull + "/x/y.jsonl"), 0)


if __name__ == "__main__":
    unittest.main()

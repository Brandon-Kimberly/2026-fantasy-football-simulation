"""B15: the need-driven finder misses best-available; add an exhaustive scan.

`find_trade_targets` returned "no buy-side candidates" three times in week 3 while an
ad-hoc exhaustive scan found the Coker deal, the DL flip and the Roquan leverage. The
finder only looks at *their bench player who starts at my weakest slot*; it never sees
*their starter I could displace with a piece they need more*.

THE TRAP B15 NAMES. The first exhaustive scan silently skipped a rival because a
hardcoded 19-man roster cap rejected their 20-man roster -- a team with a player on IR
carries 20 (B16: reserve slots sit ON TOP of the 19 active). Compare against
`len(their_roster)`, never a literal, and a test pins it.

A TENSION WITH B2, RESOLVED IN B2'S FAVOUR. B15 says to "filter where both sides gain on
the screen". B2 -- written later, off four measured reversals -- says `their_screen_gain`
is the least reliable number the tool computes and must never decide which candidates the
simulation sees, because a screen that misranks filters the good ones out first. Those
cannot both hold. So:

  * ranking is by MY screen gain only, always;
  * their screen gain is reported, labelled, and never used to rank;
  * the mutual-gain filter exists as an explicit OPT-IN (`require_mutual=True`) for when
    the candidate list needs cutting, and its cost is stated where it is offered.

Written before the module existed and confirmed failing (rule 1).
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
ME, RIVAL = TEAMS[0], TEAMS[1]


def _p(mean, pos, **kw):
    return dict({"mean": mean, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
                 "team": "DET", "bye": 0}, **kw)


def _fs():
    """A swap the NEED-DRIVEN finder cannot see.

    I am deep at RB and thin at LB. The rival is the mirror. The piece I want is their
    STARTING LB, not a buried bench player -- so `find_trade_targets`, which only looks
    at their bench, never proposes it.
    """
    base, rosters = {}, {}
    filler = [("QB", 18.0), ("RB", 17.0), ("RB", 15.0), ("WR", 16.0), ("WR", 13.0),
              ("TE", 12.0), ("K", 9.0), ("DL", 9.0), ("LB", 9.0), ("DB", 9.0),
              ("RB", 11.0), ("WR", 10.5), ("WR", 10.0)]
    for t in TEAMS:
        entries = []
        for i, (pos, mu) in enumerate(filler):
            n = f"{t[:2]}_{pos}_{i}"
            base[n] = _p(mu, pos)
            entries.append({"name": n, "pos": pos, "team": "DET"})
        rosters[t] = entries

    base["My_Spare_RB"] = _p(14.0, "RB")          # surplus: I already start three RBs
    rosters[ME].append({"name": "My_Spare_RB", "pos": "RB", "team": "DET"})
    for e in rosters[ME]:
        if e["pos"] == "LB":
            e["name"] = "My_Weak_LB"
    base["My_Weak_LB"] = _p(2.0, "LB")            # my hole

    for e in rosters[RIVAL]:
        if e["pos"] == "LB":
            e["name"] = "Their_Star_LB"
        if e["pos"] == "RB" and e["name"].endswith("_1"):
            e["name"] = "Their_Weak_RB"
    base["Their_Star_LB"] = _p(16.0, "LB")        # a STARTER, not buried
    base["Their_Weak_RB"] = _p(4.0, "RB")         # their hole

    for i in range(26):
        base[f"POOL_WR_{i:02d}"] = _p(9.5 - 0.15 * i, "WR")
    for i in range(12):
        base[f"POOL_LB_{i:02d}"] = _p(9.0 - 0.2 * i, "LB")
    return {
        LEAGUE_STATE_FILE: {"current_week": 1},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: {"_meta": {"week": 1, "source": "odds_api", "fetched_at": "x"},
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


class _Case(unittest.TestCase):
    def setUp(self):
        self.fs = _fs()
        self.pre = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        self.p_exists = patch('os.path.exists', side_effect=lambda p: p in self.fs)
        self.p_load = patch('fantasy_sim.simulation.load_json', side_effect=lambda p: self.fs[p])
        self.p_exists.start(); self.p_load.start()
        self.engine = FantasySimulationEngine()

    def tearDown(self):
        self.p_exists.stop(); self.p_load.stop()
        logging.getLogger().setLevel(self.pre)


class TestItFindsWhatTheNeedDrivenFinderMisses(_Case):
    def test_the_need_driven_finder_does_not_propose_their_starter(self):
        """Establishes the gap rather than assuming it."""
        from fantasy_sim.decisions import find_trade_targets
        got = find_trade_targets(self.engine, ME, top_n=20)["buy"]
        self.assertNotIn("Their_Star_LB", [b["target"] for b in got])

    def test_the_exhaustive_scan_finds_it(self):
        from fantasy_sim.swaps import exhaustive_swaps
        got = exhaustive_swaps(self.engine, ME, week=1)
        targets = {tuple(s["i_get"]) for s in got}
        self.assertIn(("Their_Star_LB",), targets,
                      "B15: their STARTER, displaced by a piece they need more")

    def test_the_swap_that_fixes_both_holes_ranks_first(self):
        from fantasy_sim.swaps import exhaustive_swaps
        best = exhaustive_swaps(self.engine, ME, week=1)[0]
        self.assertIn("Their_Star_LB", best["i_get"])


class TestTheRosterCapTrap(_Case):
    """B15's named trap: a hardcoded 19 silently skipped a 20-man roster."""

    def test_a_rival_with_twenty_players_is_not_skipped(self):
        from fantasy_sim.swaps import exhaustive_swaps
        # 20 players, one on IR -- exactly the shape that was skipped (B16: reserve slots
        # sit on top of the 19 active spots).
        self.engine.baselines["Their_IR_Guy"] = _p(6.0, "WR", on_ir=True)
        self.engine.rosters[RIVAL].append("Their_IR_Guy")
        self.engine.meta[RIVAL]["Their_IR_Guy"] = {"pos": "WR", "team": "DET"}
        got = exhaustive_swaps(self.engine, ME, week=1)
        self.assertTrue(any(s["with"] == RIVAL for s in got),
                        "B15: compare against len(their_roster), never a literal 19")

    def test_no_literal_nineteen_in_the_module(self):
        import inspect
        from fantasy_sim import swaps
        code = "".join(ln.split("#", 1)[0] for ln in inspect.getsource(swaps).splitlines())
        self.assertNotIn("19", code)


class TestRankingFollowsB2(_Case):
    def test_ranking_is_by_my_screen_gain_only(self):
        from fantasy_sim.swaps import exhaustive_swaps
        got = exhaustive_swaps(self.engine, ME, week=1)
        gains = [s["my_screen_gain"] for s in got]
        self.assertEqual(gains, sorted(gains, reverse=True))

    def test_their_screen_gain_is_reported_but_never_ranks(self):
        from fantasy_sim.swaps import exhaustive_swaps
        got = exhaustive_swaps(self.engine, ME, week=1)
        self.assertTrue(all("their_screen_gain" in s for s in got))
        self.assertTrue(all(s["simulated"] is False for s in got),
                        "B2: a screen number is not a finding")

    def test_the_mutual_filter_is_opt_in_not_the_default(self):
        from fantasy_sim.swaps import exhaustive_swaps
        loose = exhaustive_swaps(self.engine, ME, week=1)
        strict = exhaustive_swaps(self.engine, ME, week=1, require_mutual=True)
        self.assertLessEqual(len(strict), len(loose))
        self.assertTrue(all(s["their_screen_gain"] > 0 for s in strict))


class TestBoundsAndHygiene(_Case):
    def test_it_caps_at_two_for_two(self):
        from fantasy_sim.swaps import exhaustive_swaps
        for s in exhaustive_swaps(self.engine, ME, week=1):
            self.assertLessEqual(len(s["i_give"]), 2)
            self.assertLessEqual(len(s["i_get"]), 2)

    def test_sides_are_size_matched_so_no_roster_overflows(self):
        from fantasy_sim.swaps import exhaustive_swaps
        for s in exhaustive_swaps(self.engine, ME, week=1):
            self.assertEqual(len(s["i_give"]), len(s["i_get"]))

    def test_candidates_are_deduplicated(self):
        from fantasy_sim.swaps import exhaustive_swaps
        got = exhaustive_swaps(self.engine, ME, week=1)
        keys = [(s["with"], tuple(sorted(s["i_give"])), tuple(sorted(s["i_get"]))) for s in got]
        self.assertEqual(len(keys), len(set(keys)))

    def test_my_own_team_is_never_a_counterparty(self):
        from fantasy_sim.swaps import exhaustive_swaps
        self.assertNotIn(ME, {s["with"] for s in exhaustive_swaps(self.engine, ME, week=1)})

    def test_an_unknown_team_is_refused(self):
        from fantasy_sim.swaps import exhaustive_swaps
        with self.assertRaises(KeyError):
            exhaustive_swaps(self.engine, "Nobody FC", week=1)


if __name__ == "__main__":
    unittest.main()

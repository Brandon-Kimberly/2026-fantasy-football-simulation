"""C1: a dual-eligible player filling one slot is reported as starting at another.

On 2026-09-23 `scripts.trade_leverage` named a rival's LB slot as **3.33 below
replacement** and offered him as the best buyer in the league for this roster's LB
surplus. Two trades were constructed and sent on that basis. The paired simulation then
said every one of them COST that rival 0.4-0.7 expected wins, which is the opposite of
what a below-replacement slot implies.

**THE BACKLOG'S HYPOTHESIS WAS WRONG.** C1 guessed a week-vs-season basis mismatch:
`starters_by_position` solves on week expectation while `replacement_levels` is a season
mean. That is true and it is not the cause -- the numbers move by about a point, not by
three and a half.

**The actual mechanism**, reproduced from the live rosters:

    the rival's optimal assignment          DL slot  <- T.J. Watt   (LB, 7.52)
                                            LB slot  <- Nakobe Dean (LB, 14.49)

Watt is DL-eligible via `config.DUAL_ELIGIBILITY` (`'T.J. Watt': ['LB', 'DL']`), so the
solver legally put him in the DL slot -- the rival owns no actual DL. Then
`starters_by_position` resolved him to his OWN position, LB, so `leverage` compared
7.52 against the **LB** replacement of 10.86 and reported a 3.33-point hole.

Measured against the slot he actually fills, Watt is **+0.69 ABOVE** the DL replacement
of 6.83. The rival had no LB hole and no DL hole. The entire signal was an artefact.

**WHY RESOLVING TO THE PLAYER'S POSITION IS RIGHT FOR FLEX AND WRONG HERE.**
`starters_by_position`'s docstring says *"FLEX is resolved to the player's own position,
which is the whole point: a third WR starting at FLEX is a WR starter."* That reasoning is
correct and must survive: asking "is my WR room deep enough" has to count the WR playing
FLEX. But a dedicated positional slot is the opposite case -- what matters is the slot
being filled, not the filler's primary listing. So: FLEX resolves to the player, every
other slot resolves to itself.

**This is Phase 3 finding 3's family, running the other way.** That finding was about
looking players up by their RAW Sleeper position instead of the normalised one. This is
about normalising when the SLOT was the right answer.

**Both callers are affected**, which is C1's stated trap: `market_sweep` uses the same
helper to pick each position's "slot-losing starter", so a dual-eligible man covering a
hole at another position is compared against the wrong pool of free agents there too.

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

TEAMS = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis"]
ME, RIVAL = TEAMS[0], TEAMS[1]

# The rival's two linebackers. EDGE is DL-eligible and weak; TRUE_LB is LB-only and good.
# The rival owns NO actual DL, so the solver must put EDGE in the DL slot.
EDGE, TRUE_LB = "Rival Edge", "Rival Backer"


def _p(mean, pos, pid):
    return {"mean": mean, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
            "team": "DET", "bye": 0, "player_id": pid}


def _fs():
    base, rosters = {}, {}
    filler = [("QB", 18.0), ("RB", 17.0), ("RB", 15.0), ("WR", 16.0), ("WR", 13.0),
              ("TE", 12.0), ("K", 9.0), ("DL", 9.0), ("LB", 9.0), ("DB", 9.0),
              ("RB", 11.0), ("WR", 10.5), ("WR", 10.0)]
    pid = 2000
    for t in TEAMS:
        entries = []
        for i, (pos, mu) in enumerate(filler):
            pid += 1
            n = f"{t[:2]}_{pos}_{i}"
            base[n] = _p(mu, pos, str(pid))
            entries.append({"name": n, "pos": pos, "team": "DET"})
        rosters[t] = entries

    # Strip the rival's real DL so his DL slot can only be filled by the edge player,
    # and give him the two linebackers above.
    rosters[RIVAL] = [x for x in rosters[RIVAL] if not x["name"].endswith("_DL_7")]
    base[EDGE] = _p(7.5, "LB", "9001")
    base[TRUE_LB] = _p(14.5, "LB", "9002")
    rosters[RIVAL].append({"name": EDGE, "pos": "LB", "team": "DET"})
    rosters[RIVAL].append({"name": TRUE_LB, "pos": "LB", "team": "DET"})

    # My surplus LB, so `leverage` would have something to offer if it saw a hole.
    base["My Spare LB"] = _p(12.0, "LB", "9003")
    rosters[ME].append({"name": "My Spare LB", "pos": "LB", "team": "DET"})

    return {
        LEAGUE_STATE_FILE: {"current_week": 3},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: {"_meta": {"week": 3, "source": "odds_api", "fetched_at": "x"},
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
        self.p_exists = patch("os.path.exists", side_effect=lambda p: p in self.fs)
        self.p_load = patch("fantasy_sim.simulation.load_json",
                            side_effect=lambda p: self.fs[p])
        # Slot eligibility comes from config.DUAL_ELIGIBILITY -- a hand-maintained
        # {name: [positions]} map -- not from the baselines entry. `_opts` imports it
        # inside the call, so patching the config attribute is what reaches it.
        self.p_dual = patch("fantasy_sim.config.DUAL_ELIGIBILITY", {EDGE: ["LB", "DL"]})
        self.p_exists.start()
        self.p_load.start()
        self.p_dual.start()
        self.engine = FantasySimulationEngine()

    def tearDown(self):
        self.p_exists.stop()
        self.p_load.stop()
        self.p_dual.stop()
        logging.getLogger().setLevel(self.pre)


class TestTheFixtureReproducesTheLiveShape(_Case):
    """Sanity. If the solver does not actually put the edge man in the DL slot, the rest
    of this file is testing nothing."""

    def test_the_edge_player_fills_the_DL_slot(self):
        from fantasy_sim.market import roster_gaps
        g = roster_gaps(self.engine, RIVAL, weeks=(3,))[3]
        dl = [n for n, _v in g["starters"].get("DL", [])]
        self.assertEqual(dl, [EDGE],
                         "the rival owns no DL; his DL-eligible LB must cover the slot")

    def test_the_real_linebacker_fills_the_LB_slot(self):
        from fantasy_sim.market import roster_gaps
        g = roster_gaps(self.engine, RIVAL, weeks=(3,))[3]
        lb = [n for n, _v in g["starters"].get("LB", [])]
        self.assertEqual(lb, [TRUE_LB])


class TestStartersAreKeyedBySlotNotByPrimaryPosition(_Case):
    def test_the_edge_player_is_reported_under_DL(self):
        from fantasy_sim.market import starters_by_position
        s = starters_by_position(self.engine, RIVAL, 3)
        self.assertIn(EDGE, s.get("DL", []),
                      "he is filling the DL slot; that is the position he starts at")

    def test_he_is_NOT_also_reported_under_LB(self):
        from fantasy_sim.market import starters_by_position
        s = starters_by_position(self.engine, RIVAL, 3)
        self.assertNotIn(EDGE, s.get("LB", []),
                         "one man cannot start at two positions; reporting him under his "
                         "primary listing is what created a phantom LB hole")

    def test_exactly_one_LB_starter_for_a_one_LB_league(self):
        from fantasy_sim.market import starters_by_position
        self.assertEqual(starters_by_position(self.engine, RIVAL, 3).get("LB"), [TRUE_LB])


class TestFlexStillResolvesToThePlayer(_Case):
    """The trap in reverse. FLEX resolving to the player's own position is CORRECT and the
    docstring's reasoning stands: a third WR starting at FLEX is a WR starter, and a fix
    that made FLEX report 'FLEX' would break every depth question that asks about WRs."""

    def test_a_flex_started_player_counts_at_his_own_position(self):
        from fantasy_sim.market import roster_gaps, starters_by_position
        g = roster_gaps(self.engine, ME, weeks=(3,))[3]
        flex = [n for n, _v in g["starters"].get("FLEX", [])]
        self.assertTrue(flex, "fixture must start someone at FLEX")
        s = starters_by_position(self.engine, ME, 3)
        for name in flex:
            own = self.engine.baselines[name]["pos"]
            self.assertIn(name, s.get(own, []),
                          f"{name} starts at FLEX and must still count as a {own} starter")
        self.assertNotIn("FLEX", s, "FLEX is not a position anyone plays")


class TestLeverageReportsNoPhantomHole(_Case):
    """The defect as the owner saw it."""

    def test_the_rival_has_no_LB_hole(self):
        from fantasy_sim.leverage import leverage
        rival = next(g for g in leverage(self.engine, ME, 3) if g["team"] == RIVAL)
        lb_holes = [h for h in rival["holes"] if h["pos"] == "LB"]
        self.assertEqual(lb_holes, [],
                         "their LB slot is filled by a 14.5 starter; the 7.5 man is at DL")

    def test_the_edge_player_is_judged_against_the_DL_bar_if_judged_at_all(self):
        """He may still be a hole -- but only if he is below the DL replacement, which is
        the bar for the slot he is actually filling."""
        from fantasy_sim.leverage import leverage
        rival = next(g for g in leverage(self.engine, ME, 3) if g["team"] == RIVAL)
        for h in rival["holes"]:
            if h["their_starter"] == EDGE:
                self.assertEqual(h["pos"], "DL")
                self.assertAlmostEqual(h["replacement"],
                                       self.engine.replacement_levels["DL"], places=6)

    def test_my_surplus_LB_is_not_offered_into_a_phantom_hole(self):
        from fantasy_sim.leverage import leverage
        rival = next(g for g in leverage(self.engine, ME, 3) if g["team"] == RIVAL)
        self.assertFalse(
            any(h["i_could_send"] == "My Spare LB" and h["pos"] == "LB"
                for h in rival["holes"]),
            "this is the offer that was actually sent, twice, on a hole that did not exist")


if __name__ == "__main__":
    unittest.main()

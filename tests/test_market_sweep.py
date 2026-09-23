"""B12: promote the scratchpad market sweep into a real tool.

`sweep3.py` was written in a session scratchpad, run repeatedly, and produced real
decisions. Porting it is not transcription -- B12 names three traps, and the scratchpad
version FAILS one of them:

    print(f"  {pos:3s} +{gain:5.2f}   add {best[1]...}   drop {cut[1] ...}")

`cut` is the SLOT-LOSING starter (`ms[ns-1]`, the Nth-best at a position with N slots).
B12: "the *drop* it names must be the worst player at the position, which are different
people." With three WRs and two WR slots, the scratchpad told the owner to drop his WR2.

The second trap it gets right and the port must keep: values come from
`engine.baselines[...]['mean']`, the corrected post-F54 number. No count-weighted
re-blending anywhere -- that arithmetic over-credits volatile producers and reversed four
recommendations in one week.

The third trap -- "never key by name" -- does NOT apply inside the engine and the port
must not "fix" it: `sync.resolve_player_keys` has already disambiguated
`engine.baselines`, so a colliding name is either the rostered player or is keyed
"Name (pid)". The scratchpad bug that scored the wrong DeVonta Smith was in a version
reading the RAW player cache. `fantasy_sim.player_ids.resolve_pid` (B17) is the helper
for that path; this one stays on engine keys, and a test pins that it never touches the
cache.

A FOURTH thing the scratchpad got wrong that B12 does not mention: it computed the
slot-losing starter from a FIXED per-position count (`SLOTS = {"RB": 2, "WR": 2, ...}`),
ignoring the three FLEX slots. A third WR who starts at FLEX was counted as a bench
player, so his position's "slot-losing starter" was the wrong man entirely. The port
reads the engine's own optimal assignment instead.

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
ME = TEAMS[0]


def _p(mean, pos):
    return {"mean": mean, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
            "team": "DET", "bye": 0}


def _fs():
    """My roster holds THREE WRs. Two fill the WR slots, the third starts at FLEX, and a
    FOURTH is genuinely buried. So:
        WR starters        = WR_A (20), WR_B (14), WR_C (11, via FLEX)
        slot-losing WR     = WR_C at 11   (the weakest STARTER)
        worst WR / the drop= WR_D at 3    (a different man)
    A free-agent WR at 13 therefore upgrades nobody's slot... but one at 16 upgrades WR_C.
    """
    base, rosters = {}, {}
    filler = [("QB", 18.0), ("RB", 17.0), ("RB", 15.0), ("TE", 12.0), ("K", 9.0),
              ("DL", 9.0), ("LB", 9.0), ("DB", 9.0)]
    for t in TEAMS:
        entries = []
        for i, (pos, mu) in enumerate(filler):
            n = f"{t[:2]}_{pos}_{i}"
            base[n] = _p(mu, pos)
            entries.append({"name": n, "pos": pos, "team": "DET"})
        rosters[t] = entries
    for nm, mu in (("WR_A", 20.0), ("WR_B", 14.0), ("WR_C", 11.0), ("WR_D", 3.0)):
        base[nm] = _p(mu, "WR")
        rosters[ME].append({"name": nm, "pos": "WR", "team": "DET"})
    # free agents (on nobody's roster)
    base["FA_WR_GOOD"] = _p(16.0, "WR")
    base["FA_WR_MEH"] = _p(6.0, "WR")
    base["FA_DL_GOOD"] = _p(14.0, "DL")
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

    def _rows(self):
        from fantasy_sim.market import market_sweep
        r = market_sweep(self.engine, ME, 1)
        return {row["pos"]: row for row in r["rows"]}, r


class TestTheSlotLosingStarter(_Case):
    def test_flex_is_counted_so_the_third_wr_is_a_starter(self):
        """The scratchpad's fixed SLOTS dict said WR:2 and called WR_C a bench player."""
        rows, _ = self._rows()
        self.assertEqual(rows["WR"]["slot_loser"], "WR_C",
                         "the weakest WR who actually STARTS (via FLEX), not the 2nd-best")
        self.assertAlmostEqual(rows["WR"]["slot_loser_mean"], 11.0)

    def test_the_gain_is_measured_against_that_man(self):
        rows, _ = self._rows()
        self.assertAlmostEqual(rows["WR"]["gain"], 16.0 - 11.0)
        self.assertEqual(rows["WR"]["best_available"], "FA_WR_GOOD")


class TestTheDropIsNotTheSlotLoser(_Case):
    """B12's named trap, and the bug the scratchpad shipped."""

    def test_the_drop_is_the_worst_player_at_the_position(self):
        rows, _ = self._rows()
        self.assertEqual(rows["WR"]["drop"], "WR_D",
                         "B12: the drop is the WORST at the position")
        self.assertAlmostEqual(rows["WR"]["drop_mean"], 3.0)

    def test_the_drop_and_the_slot_loser_are_reported_separately(self):
        rows, _ = self._rows()
        self.assertNotEqual(rows["WR"]["drop"], rows["WR"]["slot_loser"],
                            "conflating them is exactly the scratchpad bug")


class TestUpgradesAndDeadWeight(_Case):
    def test_an_upgrade_is_ranked_by_gain(self):
        _, r = self._rows()
        ups = r["upgrades"]
        self.assertTrue(ups)
        self.assertEqual([u["gain"] for u in ups], sorted((u["gain"] for u in ups), reverse=True))
        self.assertTrue(all(u["gain"] > 0 for u in ups))

    def test_a_free_agent_worse_than_my_slot_loser_is_no_upgrade(self):
        _, r = self._rows()
        self.assertNotIn("FA_WR_MEH", [u["best_available"] for u in r["upgrades"]])

    def test_dead_weight_is_named_against_replacement(self):
        _, r = self._rows()
        dead = {d["name"]: d for d in r["dead_weight"]}
        self.assertIn("WR_D", dead, "3.0 is far below any WR replacement level")
        self.assertLess(dead["WR_D"]["vorp"], 0.0)
        self.assertNotIn("WR_A", dead)


class TestItUsesEngineValuesOnly(_Case):
    """B12's first trap. The whole point is to stop second-guessing the engine."""

    def test_the_reported_mean_is_the_engines_own(self):
        rows, _ = self._rows()
        self.assertAlmostEqual(rows["WR"]["slot_loser_mean"],
                               float(self.engine.baselines["WR_C"]["mean"]))

    def test_it_never_reads_the_raw_player_cache(self):
        """The raw cache is where the name collisions live (B17). This tool works on
        engine keys, which resolve_player_keys has already disambiguated."""
        import inspect
        from fantasy_sim import market
        src = inspect.getsource(market)
        self.assertNotIn("sleeper_players_cache", src)
        self.assertNotIn("PLAYER_CACHE_FILE", src)

    def test_replacement_levels_come_from_the_engine(self):
        _, r = self._rows()
        self.assertEqual(r["replacement_levels"], self.engine.replacement_levels)


class TestEdges(_Case):
    def test_a_position_with_no_free_agent_is_reported_not_skipped(self):
        rows, _ = self._rows()
        self.assertIn("QB", rows)
        self.assertIn("best_available", rows["QB"])

    def test_an_unknown_team_is_refused(self):
        from fantasy_sim.market import market_sweep
        with self.assertRaises(KeyError):
            market_sweep(self.engine, "Nobody FC", 1)


if __name__ == "__main__":
    unittest.main()

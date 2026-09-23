"""B12: promote the scratchpad trade-bait script into a real tool.

`bait.py` answers two questions that produced real offers in week 3:

1. **Sell high** -- whose PUBLIC price sits above the model's? The proxies are where these
   eight managers actually drafted him (a revealed preference, not a national ADP) and his
   PRESEASON Sleeper projection. A player taken early, or still projected high, whom the
   corrected blend now marks down, is a name worth more than its production.
2. **Leverage** -- which rival starts someone BELOW replacement, and do I have surplus
   there? That is what made a WR-for-QB swap cheap: the other side needed the position
   badly enough to overpay in kind.

WHERE THE SCRATCHPAD IS WRONG, and this one is the trap B12 names:

    nm = lambda p: f"{cache[p]['first_name']} {cache[p]['last_name']}"
    picks[nm(p["player_id"])] = p["pick_no"]

It builds a NAME-keyed map straight off the raw player cache -- 220 colliding names, seven
of them involving a player rostered in this league. The draft pick of a CB lands on a WR
with the same name. The preseason map has the same defect, keyed off the projection log's
`name` field. Both joins must go through `player_id`.

It also repeats sweep3.py's FLEX blindness: `SLOTS = {"RB": 2, "WR": 2, ...}` decides who
"starts" for a rival, so a third WR starting at FLEX is invisible and a below-replacement
FLEX starter is never reported. The port reads the engine's own optimal assignment.

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

# The collision, mirroring the live cache: one WR and one CB called the same thing.
COLLIDE = "Jordan Case"
WR_PID, CB_PID = "7001", "13001"


def _p(mean, pos, pid):
    return {"mean": mean, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
            "team": "DET", "bye": 0, "player_id": pid}


def _fs():
    base, rosters = {}, {}
    filler = [("QB", 18.0), ("RB", 17.0), ("RB", 15.0), ("WR", 16.0), ("WR", 13.0),
              ("TE", 12.0), ("K", 9.0), ("DL", 9.0), ("LB", 9.0), ("DB", 9.0),
              ("RB", 11.0), ("WR", 10.5)]
    pid = 1000
    for t in TEAMS:
        entries = []
        for i, (pos, mu) in enumerate(filler):
            pid += 1
            n = f"{t[:2]}_{pos}_{i}"
            base[n] = _p(mu, pos, str(pid))
            entries.append({"name": n, "pos": pos, "team": "DET"})
        rosters[t] = entries

    # MY surplus WR, and the colliding WR I own (engine key is the plain name because he
    # is the only rostered one of the pair).
    base[COLLIDE] = _p(12.0, "WR", WR_PID)
    rosters[ME].append({"name": COLLIDE, "pos": "WR", "team": "DET"})
    # the CB of the same name is UNROSTERED, so sync keys him "Name (pid)"
    base[f"{COLLIDE} ({CB_PID})"] = _p(4.0, "DB", CB_PID)

    # The RIVAL starts a genuinely awful DB -- a below-replacement starting slot.
    base["Rival_Bad_DB"] = _p(2.0, "DB", "9901")
    rosters[RIVAL].append({"name": "Rival_Bad_DB", "pos": "DB", "team": "DET"})
    # ...and I own a spare DB better than him.
    base["My_Spare_DB"] = _p(11.0, "DB", "9902")
    rosters[ME].append({"name": "My_Spare_DB", "pos": "DB", "team": "DET"})

    # depth so replacement levels land on real players rather than the worst man
    for i in range(26):
        base[f"POOL_WR_{i:02d}"] = _p(9.5 - 0.15 * i, "WR", f"2{i:03d}")
    for i in range(12):
        base[f"POOL_DB_{i:02d}"] = _p(9.0 - 0.2 * i, "DB", f"3{i:03d}")
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


class TestSellHighJoinsByPlayerId(_Case):
    """B12's named trap. The draft and preseason maps are built from the RAW cache, where
    the collisions live -- the join must go through player_id."""

    def test_the_colliding_cbs_draft_pick_does_not_land_on_my_wr(self):
        from fantasy_sim.leverage import sell_high
        # The CB went early; MY WR was never drafted.
        rows = {r["name"]: r for r in sell_high(
            self.engine, ME, draft_picks={CB_PID: 3}, preseason={WR_PID: 15.0})}
        self.assertIsNone(rows[COLLIDE]["pick"],
                          "B12: a name-keyed join would have given my WR the CB's pick 3")
        self.assertAlmostEqual(rows[COLLIDE]["preseason"], 15.0)

    def test_the_wrs_own_pick_is_used_when_it_is_his(self):
        from fantasy_sim.leverage import sell_high
        rows = {r["name"]: r for r in sell_high(
            self.engine, ME, draft_picks={WR_PID: 9}, preseason={WR_PID: 15.0})}
        self.assertEqual(rows[COLLIDE]["pick"], 9)

    def test_the_markdown_is_preseason_minus_now(self):
        from fantasy_sim.leverage import sell_high
        rows = {r["name"]: r for r in sell_high(
            self.engine, ME, draft_picks={}, preseason={WR_PID: 15.0})}
        self.assertAlmostEqual(rows[COLLIDE]["markdown"], 15.0 - 12.0)
        self.assertAlmostEqual(rows[COLLIDE]["now"], 12.0)

    def test_a_player_with_no_preseason_row_is_reported_with_none_not_dropped(self):
        """Silently omitting him is how a roster hole becomes invisible."""
        from fantasy_sim.leverage import sell_high
        rows = {r["name"]: r for r in sell_high(self.engine, ME, {}, {})}
        self.assertEqual(len(rows), len(self.engine.rosters[ME]))
        self.assertIsNone(rows[COLLIDE]["preseason"])
        self.assertIsNone(rows[COLLIDE]["markdown"])

    def test_sell_high_candidates_are_marked_down_and_drafted_early(self):
        from fantasy_sim.leverage import sell_high
        rows = {r["name"]: r for r in sell_high(
            self.engine, ME, draft_picks={WR_PID: 9}, preseason={WR_PID: 18.0})}
        self.assertTrue(rows[COLLIDE]["sell_high"],
                        "marked down 6.0 and taken at pick 9 is the whole pattern")

    def test_a_marked_down_player_nobody_drafted_early_is_not_a_sell_high(self):
        from fantasy_sim.leverage import sell_high
        rows = {r["name"]: r for r in sell_high(
            self.engine, ME, draft_picks={WR_PID: 150}, preseason={WR_PID: 18.0})}
        self.assertFalse(rows[COLLIDE]["sell_high"])
        self.assertTrue(rows[COLLIDE]["name_over_production"],
                        "still worth flagging, just not as a premium asset")


class TestLeverage(_Case):
    def test_a_rivals_below_replacement_starter_is_found(self):
        from fantasy_sim.leverage import leverage
        gaps = {g["team"]: g for g in leverage(self.engine, ME, week=1)}
        self.assertIn(RIVAL, gaps)
        holes = {h["pos"]: h for h in gaps[RIVAL]["holes"]}
        self.assertIn("DB", holes)
        self.assertEqual(holes["DB"]["their_starter"], "Rival_Bad_DB")
        self.assertGreater(holes["DB"]["deficit"], 0.0)

    def test_my_surplus_is_offered_only_when_it_beats_their_man(self):
        from fantasy_sim.leverage import leverage
        gaps = {g["team"]: g for g in leverage(self.engine, ME, week=1)}
        holes = {h["pos"]: h for h in gaps[RIVAL]["holes"]}
        self.assertEqual(holes["DB"]["i_could_send"], "My_Spare_DB")
        self.assertAlmostEqual(holes["DB"]["upgrade_for_them"], 11.0 - 2.0)

    def test_surplus_means_a_player_i_do_not_start(self):
        """Offering my own starter is not leverage, it is a downgrade."""
        from fantasy_sim.leverage import leverage
        from fantasy_sim.market import starters_by_position
        mine = starters_by_position(self.engine, ME, 1)
        gaps = {g["team"]: g for g in leverage(self.engine, ME, week=1)}
        for g in gaps.values():
            for h in g["holes"]:
                if h["i_could_send"]:
                    self.assertNotIn(h["i_could_send"], mine.get(h["pos"], []))

    def test_my_own_team_is_not_listed(self):
        from fantasy_sim.leverage import leverage
        self.assertNotIn(ME, [g["team"] for g in leverage(self.engine, ME, week=1)])

    def test_flex_is_respected_so_starters_come_from_the_assignment(self):
        """sweep3/bait both used a fixed per-position count and missed FLEX starters."""
        import inspect
        from fantasy_sim import leverage as mod
        src = inspect.getsource(mod)
        self.assertIn("starters_by_position", src)
        self.assertNotIn('"RB": 2', src)


if __name__ == "__main__":
    unittest.main()

"""B2: the cheap trade screen's acceptance rule disagrees badly with the paired simulation.

`find_trade_targets` scores a swap by the engine's acceptance rule -- optimal starting
lineup plus 0.1 x bench (`get_optimal_score`). The paired simulation measures actual
championship odds. In week 3 they disagreed four times, always the same way:

    Warner + Bolton -> Walker + Coker   screen +1.54 for them   sim -16.07 playoff%
    Hooker for Bishop                   screen +0.82            sim  -1.87 champ%
    Lloyd for Bolton                    screen +3.66            sim  Lloyd worse
    Van Ness for Tuipulotu              screen +1.63            sim  3.70 below

Four recommendations reached the owner on screen numbers and were reversed by the sim.

THE TRAP B2 NAMES, and the fix respects it: the screen is NOT wrong for a single-week
question. It is wrong as a proxy for a SEASON, because a 0.1x bench weight cannot express
injury cover or bye cover. Do not re-tune the weight -- replace the USE. So nothing here
changes `get_optimal_score`, and a test pins that.

What changes is what the screen is allowed to CLAIM:

  * `their_gain` is a screen number about someone else's roster, the least reliable thing
    the tool computes and the one that decided `acceptable`. It may not be presented as a
    finding without a paired sim behind it.
  * `--evaluate N` picks which N to simulate. Picking them with a rule that misranks
    filters the good candidates out before the sim ever sees them, so selection uses MY
    screen gain only and the sim decides the counterparty's side.
  * When screen and sim disagree in SIGN, that is the interesting fact and must be said
    out loud rather than resolved silently.

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
ME = TEAMS[0]


def _p(mean, pos):
    return {"mean": mean, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
            "team": "DET", "bye": 0}


def _fs():
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
    # ASYMMETRY, or the offer constructor finds nothing: it looks for THEIR buried bench
    # player who would start at MY weakest fillable slot. With four identical rosters
    # nobody is buried and nobody is desperate.
    for e in rosters[ME]:
        if e["pos"] == "LB":
            e["name"] = "My_Weak_LB"
    base["My_Weak_LB"] = _p(3.0, "LB")
    base["Their_Buried_LB"] = _p(8.5, "LB")     # behind their 9.0 starter, ahead of my 3.0
    rosters[TEAMS[1]].append({"name": "Their_Buried_LB", "pos": "LB", "team": "DET"})
    # _construct_trade_offers returns nothing unless the RICH side has at least two bench
    # players (simulation.py: `if len(r_bench) < 2: return []`), so a second buried body
    # is required for the constructor to run at all.
    base["Their_Buried_DB"] = _p(8.0, "DB")
    rosters[TEAMS[1]].append({"name": "Their_Buried_DB", "pos": "DB", "team": "DET"})

    for i in range(26):
        base[f"POOL_WR_{i:02d}"] = _p(9.5 - 0.15 * i, "WR")
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


class TestTheScreenIsNotRetuned(_Case):
    """B2's trap. get_optimal_score keeps its 0.1x bench weight; only its USE changes."""

    def test_the_acceptance_rule_still_includes_a_tenth_of_the_bench(self):
        import inspect
        src = inspect.getsource(self.engine.get_optimal_score)
        self.assertIn("0.1", src, "B2: do not re-tune the weight; replace the use")


class TestTheScreenLabelsItself(_Case):
    def test_every_candidate_is_marked_unsimulated_until_a_sim_runs(self):
        from fantasy_sim.decisions import find_trade_targets
        r = find_trade_targets(self.engine, ME, top_n=4)
        self.assertTrue(r["buy"])
        for b in r["buy"]:
            self.assertFalse(b["simulated"],
                             "B2: a screen number may not pass as a finding")
            self.assertIsNone(b["sim_champ_delta"])

    def test_their_gain_is_renamed_to_say_it_is_a_screen_number(self):
        """`their_gain` decided `acceptable` and is the least reliable thing the tool
        computes -- a guess about someone else's roster from a one-week proxy."""
        from fantasy_sim.decisions import find_trade_targets
        b = find_trade_targets(self.engine, ME, top_n=2)["buy"][0]
        self.assertIn("their_screen_gain", b)
        self.assertIn("my_screen_gain", b)

    def test_acceptable_no_longer_claims_the_other_side_gains(self):
        """It may say the offer is worth PROPOSING (I gain); whether they accept is
        theirs to decide and the sim's to measure."""
        from fantasy_sim.decisions import find_trade_targets
        for b in find_trade_targets(self.engine, ME, top_n=6)["buy"]:
            self.assertEqual(b["worth_proposing"], b["my_screen_gain"] > 0)


class TestSelectionUsesMyGainOnly(_Case):
    """B2 scope 2(b). The screen chooses which candidates the sim ever sees; ranking them
    with a rule that misranks filters the good ones out first."""

    def test_the_evaluation_order_ignores_their_screen_gain(self):
        from fantasy_sim.decisions import find_trade_targets, selection_order
        buy = find_trade_targets(self.engine, ME, top_n=8)["buy"]
        order = selection_order(buy)
        self.assertEqual([b["my_screen_gain"] for b in order],
                         sorted((b["my_screen_gain"] for b in buy), reverse=True))

    def test_a_candidate_the_screen_thinks_they_hate_is_still_evaluated(self):
        from fantasy_sim.decisions import selection_order
        cands = [{"my_screen_gain": 5.0, "their_screen_gain": -9.0},
                 {"my_screen_gain": 1.0, "their_screen_gain": +9.0}]
        self.assertEqual(selection_order(cands)[0]["my_screen_gain"], 5.0,
                         "the sim decides the counterparty's side, not the screen")


class TestDisagreementIsLabelled(unittest.TestCase):
    """B2 scope 3, and the whole point of the item."""

    def test_opposite_signs_are_flagged(self):
        from fantasy_sim.decisions import screen_sim_disagreement
        d = screen_sim_disagreement(my_screen_gain=1.54, sim_champ_delta=-16.07)
        self.assertTrue(d["disagree"])
        self.assertIn("screen", d["note"].lower())
        self.assertIn("sim", d["note"].lower())

    def test_agreement_is_not_flagged(self):
        from fantasy_sim.decisions import screen_sim_disagreement
        self.assertFalse(screen_sim_disagreement(2.0, 3.0)["disagree"])
        self.assertFalse(screen_sim_disagreement(-2.0, -3.0)["disagree"])

    def test_an_unsimulated_candidate_is_not_a_disagreement(self):
        from fantasy_sim.decisions import screen_sim_disagreement
        d = screen_sim_disagreement(2.0, None)
        self.assertFalse(d["disagree"])
        self.assertIn("unsimulated", d["note"].lower())

    def test_the_sim_wins_when_they_differ(self):
        from fantasy_sim.decisions import screen_sim_disagreement
        d = screen_sim_disagreement(1.54, -16.07)
        self.assertIn("sim", d["verdict"].lower())
        self.assertNotIn("screen", d["verdict"].lower().split("wins")[0])


if __name__ == "__main__":
    unittest.main()

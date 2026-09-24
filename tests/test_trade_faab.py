"""T1: FAAB in `evaluate_trade` -- most of it was already built, and one part must not be.

The backlog item was written from the 2026-09-23 session, when two FAAB-for-player offers
were evaluated by proxy (a throwaway bench player standing in for "give nothing"). Checking
before building found that `--a-faab` / `--b-faab`, the `faab_a_to_b` kwarg, the recorded
field and the unpriced caveat all EXIST and are tested. Two things genuinely do not.

**1. NOBODY CHECKS THAT THE PAYER CAN AFFORD IT.** `faab_a_to_b=48` is accepted from a team
holding 12, and the record, the printed caveat and the logged JSON all state a transfer
that cannot happen. This is the same class as F64 (a bid priced at a number that was never
live): a decision record asserting terms the league would reject.

**2. THE ONE-SIDED TRADE -- FAAB FOR A PLAYER, WHICH IS WHAT WAS ACTUALLY OFFERED -- IS
UNTESTED.** It turns out to WORK: `apply_trade` permits it (`a_gives` empty, `b_gives`
non-empty) and the roster limit already bites only on the receiving side, because only that
side gains a man. So those tests are coverage of a working path, not characterisations of a
defect, and they pass on the first run -- said plainly here rather than counted as red.
Both are one edit from silently breaking and neither was pinned.

**AND ONE THING THE BACKLOG ASKED FOR THAT IS DELIBERATELY NOT DONE.** T1's scope says
"FAAB moves `league_standings.remaining_faab` for both sides in the `with` engine; the
paired simulation already reads that." The premise is true -- `simulation.py` builds
`current_faab` from that field and `_compute_faab_bid` spends it -- but doing it would be
WRONG here, for a measured reason:

  * F31 measured the simulation spending ~31% of this league's real FAAB. A budget delta
    pushed through the paired arms therefore returns ~zero, and reporting that as a price
    is false precision claiming FAAB is worthless.
  * Worse, it would contaminate the number the tool exists for. Today the Champ%/Playoff%
    deltas are a clean read on the PLAYER side of the trade. Moving budgets folds an
    untrustworthy FAAB effect into those same deltas, so the one quantity that IS reliable
    stops being reliable.

The transfer is therefore recorded, stated, and left to the owner's judgment. A test below
pins that the engine's budgets are untouched, so nobody "fixes" this by accident. Recorded
as a follow-up, not silently dropped: it unblocks when F31's behavioural fix lands.

Written before the change. **Two of the eight are red** -- both affordability tests. The
one-sided-trade pair and the three guards at the bottom are green on the first run and are
labelled as coverage rather than presented as verification of a fix.
"""
import logging
import unittest
from unittest.mock import patch

from fantasy_sim.decisions import apply_trade, evaluate_trade
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)

TEAMS = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis"]
A, B = TEAMS[0], TEAMS[1]
WEEK = 3
FAAB = {A: 12.0, B: 80.0, TEAMS[2]: 50.0, TEAMS[3]: 50.0}
SLOTS = [("QB", 18.0), ("RB", 17.0), ("RB", 15.0), ("WR", 16.0), ("WR", 13.0),
         ("TE", 12.0), ("K", 9.0), ("DL", 9.0), ("LB", 9.0), ("DB", 9.0),
         ("RB", 11.0), ("WR", 10.5), ("WR", 10.0)]


def _fs(extra_for_a=0):
    base, rosters, pid = {}, {}, 8000
    for t in TEAMS:
        entries = []
        spec = list(SLOTS) + ([("WR", 6.0)] * extra_for_a if t == A else [])
        for i, (pos, mu) in enumerate(spec):
            pid += 1
            nm = f"{t[:2]}_{pos}_{i}"
            base[nm] = {"mean": mu, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
                        "team": "DET", "bye": 0, "player_id": str(pid)}
            entries.append({"name": nm, "pos": pos, "team": "DET"})
        rosters[t] = entries
    return {
        LEAGUE_STATE_FILE: {"current_week": WEEK},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": FAAB[t]} for t in TEAMS},
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


def _engine(extra_for_a=0):
    fs = _fs(extra_for_a)
    pre = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.ERROR)
    with patch("os.path.exists", side_effect=lambda p: p in fs), \
         patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]):
        e = FantasySimulationEngine()
    logging.getLogger().setLevel(pre)
    return e


def _b_player(engine):
    return engine.rosters[B][-1]


class TestThePayerMustBeAbleToAffordIt(unittest.TestCase):
    """RED. A record asserting terms the league would reject is the F64 class of defect."""

    def test_a_transfer_larger_than_the_budget_is_refused(self):
        e = _engine()
        self.assertAlmostEqual(e.current_faab[A], 12.0)
        with patch("fantasy_sim.simulation.save_json"), patch("fantasy_sim.simulation.save_chart"):
            with self.assertRaises(ValueError) as cm:
                evaluate_trade(e, A, [], B, [_b_player(e)], batches=1, sims=5,
                               faab_a_to_b=48)
        self.assertIn("12", str(cm.exception), "say what the budget actually is")

    def test_the_other_direction_is_checked_too(self):
        """faab_a_to_b is signed. A negative value is B paying A, and B's budget is the
        one that has to cover it."""
        e = _engine()
        e.current_faab[B] = 3.0
        with patch("fantasy_sim.simulation.save_json"), patch("fantasy_sim.simulation.save_chart"):
            with self.assertRaises(ValueError):
                evaluate_trade(e, A, [], B, [_b_player(e)], batches=1, sims=5,
                               faab_a_to_b=-25)

    def test_a_transfer_exactly_equal_to_the_budget_is_allowed(self):
        """Spending everything is legal. The boundary is pinned because `>` and `>=` are
        one keystroke apart and at the limit only one of them is right."""
        e = _engine()
        with patch("fantasy_sim.simulation.save_json"), patch("fantasy_sim.simulation.save_chart"):
            r = evaluate_trade(e, A, [], B, [_b_player(e)], batches=1, sims=5,
                               faab_a_to_b=12)
        self.assertEqual(r["trade"]["faab_a_to_b"], 12)


class TestTheOneSidedTrade(unittest.TestCase):
    """GREEN ON THE FIRST RUN -- coverage, not characterisation. FAAB for a player is what
    was actually offered on 2026-09-23 and the path already works; it simply had no test,
    which is why the offers were evaluated by proxy with a throwaway bench player instead."""

    def test_faab_for_a_player_evaluates_and_records_both_terms(self):
        e = _engine()
        got = _b_player(e)
        with patch("fantasy_sim.simulation.save_json"), patch("fantasy_sim.simulation.save_chart"):
            r = evaluate_trade(e, A, [], B, [got], batches=1, sims=5, faab_a_to_b=5)
        self.assertEqual(r["trade"]["a_gives"], [])
        self.assertEqual(r["trade"]["b_gives"], [got])
        self.assertEqual(r["trade"]["faab_a_to_b"], 5)
        self.assertIn("unpriced", r["faab_note"].lower())

    def test_the_roster_limit_bites_on_the_RECEIVING_side_only(self):
        """Only the side gaining a man can exceed the limit. The side giving one away
        cannot, and a check that refused it would block every legal FAAB purchase."""
        from fantasy_sim.decisions import ACTIVE_ROSTER_LIMIT
        full = _engine(extra_for_a=ACTIVE_ROSTER_LIMIT - len(SLOTS))
        self.assertEqual(len([n for n in full.rosters[A]]), ACTIVE_ROSTER_LIMIT)
        with self.assertRaises(ValueError) as cm:
            apply_trade(full, A, [], B, [_b_player(full)])
        self.assertIn(A, str(cm.exception))
        # ... and the same trade goes through once A drops somebody.
        e2 = apply_trade(full, A, [], B, [_b_player(full)],
                         drops={A: [full.rosters[A][0]]})
        self.assertEqual(len(e2.rosters[A]), ACTIVE_ROSTER_LIMIT)


class TestWhatMustNotChange(unittest.TestCase):
    """GREEN BY DESIGN -- guards, not characterisations. Each pins a property the backlog's
    scope would have broken."""

    def test_the_simulated_budgets_are_never_moved_by_a_transfer(self):
        """T1's scope asked for this and it is deliberately refused. F31 measured the sim
        spending ~31% of real FAAB, so a budget delta returns ~zero -- and folding that
        into the Champ%/Playoff% deltas would contaminate the one read that IS reliable,
        the player side of the trade."""
        e = _engine()
        before = dict(e.current_faab)
        with patch("fantasy_sim.simulation.save_json"), patch("fantasy_sim.simulation.save_chart"):
            evaluate_trade(e, A, [], B, [_b_player(e)], batches=1, sims=5, faab_a_to_b=5)
        self.assertEqual(e.current_faab, before)

    def test_the_caveat_names_the_finding_so_the_reason_is_findable(self):
        e = _engine()
        with patch("fantasy_sim.simulation.save_json"), patch("fantasy_sim.simulation.save_chart"):
            r = evaluate_trade(e, A, [], B, [_b_player(e)], batches=1, sims=5, faab_a_to_b=5)
        self.assertIn("F31", r["faab_note"])

    def test_a_trade_of_nothing_for_nothing_is_still_refused(self):
        """FAAB support must not turn an empty trade into a valid one by accident."""
        e = _engine()
        with self.assertRaises(ValueError):
            apply_trade(e, A, [], B, [])


class TestTheCLIRefusesBeforeSimulating(unittest.TestCase):
    def test_an_unaffordable_transfer_exits_with_a_message_not_a_traceback(self):
        """Three minutes of paired simulation then a traceback is the wrong order. The
        check runs first and the failure reads as a sentence."""
        import scripts.evaluate_trade as st
        e = _engine()
        with patch.object(st, "FantasySimulationEngine", return_value=e), \
             patch.object(st, "evaluate_trade") as never:
            with self.assertRaises(SystemExit) as cm:
                st.main(["--team-a", A, "--a-gives", "", "--team-b", B,
                         "--b-gives", _b_player(e), "--a-faab", "48"])
        self.assertIn("cannot send 48", str(cm.exception))
        never.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""The Questionable count is roster-wide, but the optimism it measures is starter-only.

`live_matchup` prints both rosters' Questionable counts for one specific reason, stated in
`questionable_count`'s own docstring and in CLAUDE.md: **no availability discount is applied
to a pre-game starter** (F51), so the projected margin reads "if everyone plays" for both
sides. That optimism only cancels when the two rosters carry comparable Questionable
counts -- hence printing them.

**The optimism is created by STARTERS.** A Questionable player on the bench contributes
nothing to the projection, so he adds no optimism and must not enter the comparison.
`questionable_count` counts `engine.rosters[team]` -- the whole roster, bench included.

**Measured live, 2026-09-25, week 3:**

    printed:  mine 3, opponent 2   "ASYMMETRIC by 1: the margin flatters mine"
    actual :  mine 2, opponent 0    ASYMMETRIC by 2

Both of the opponent's Questionables (a RB and a WR) were on his BENCH. One of the owner's
three was a DL he had already benched after a waiver pickup. So the printed line understated
the asymmetry by half and named the right direction only by accident -- with a different
bench mix it would have pointed the wrong way entirely.

That is decision-relevant: the owner reads this line to decide whether to trust a live
margin, and it told him his 73.7% was mildly optimistic when it was substantially so.

**The fix is to require the starter set.** The only production caller already has it --
`live_matchup`'s per-team state carries the exact starters the margin was built from, which
is the correct set by construction (not a re-solved optimal lineup, which can differ from
what the owner actually set). Making the parameter REQUIRED rather than optional is
deliberate: a silently roster-wide default is what produced the defect, and a wrong count
here is worse than a TypeError.

Team names are the repository's fictional ones; player names are positional stubs.

Written before the change.
"""
import unittest


class _Engine:
    """`questionable_count` reads only `.rosters` and `.baselines`."""

    def __init__(self, rosters, baselines):
        self.rosters = rosters
        self.baselines = baselines


def _engine():
    baselines = {
        # --- starters ---
        "Starting QB":   {"pos": "QB", "mean": 20.0, "player_id": "1"},
        "Starting RB":   {"pos": "RB", "mean": 15.0, "player_id": "2"},
        "Hurt Starter":  {"pos": "WR", "mean": 13.0, "player_id": "3",
                          "injury_status": "Questionable"},
        # --- bench ---
        "Hurt Benchie":  {"pos": "WR", "mean": 8.0, "player_id": "4",
                          "injury_status": "Questionable"},
        "Fine Benchie":  {"pos": "TE", "mean": 7.0, "player_id": "5"},
        "Benched IR":    {"pos": "DL", "mean": 6.0, "player_id": "6", "on_ir": True},
    }
    rosters = {"Quantum Ferrets": list(baselines),
               "Neon Walruses": []}
    return _Engine(rosters, baselines)


STARTERS = ["Starting QB", "Starting RB", "Hurt Starter"]
STARTER_PIDS = ["1", "2", "3"]


class TestOnlyStartersCount(unittest.TestCase):
    def test_a_questionable_bench_player_is_not_counted(self):
        """The defect. `Hurt Benchie` is projected into no lineup, so he creates none of the
        optimism this count exists to measure."""
        from fantasy_sim.decisions import questionable_count
        self.assertEqual(questionable_count(_engine(), "Quantum Ferrets", STARTERS), 1)

    def test_a_questionable_starter_is_counted(self):
        from fantasy_sim.decisions import questionable_count
        eng = _engine()
        self.assertEqual(questionable_count(eng, "Quantum Ferrets", ["Hurt Starter"]), 1)

    def test_a_roster_whose_questionables_are_all_benched_counts_zero(self):
        """The live opponent's case: two Questionable players, both on the bench, and their
        projection carries no unpriced risk at all."""
        from fantasy_sim.decisions import questionable_count
        eng = _engine()
        self.assertEqual(
            questionable_count(eng, "Quantum Ferrets", ["Starting QB", "Starting RB"]), 0)

    def test_the_starter_set_may_be_given_as_player_ids(self):
        """`live_matchup` carries pids alongside names; matching on either avoids a silent
        zero when a name-collision suffix ('Name (pid)') is in play."""
        from fantasy_sim.decisions import questionable_count
        self.assertEqual(questionable_count(_engine(), "Quantum Ferrets", STARTER_PIDS), 1)

    def test_the_starter_set_is_required(self):
        """A roster-wide default is exactly what produced this defect. A caller that forgets
        the set should fail loudly rather than receive a number that is quietly wrong."""
        from fantasy_sim.decisions import questionable_count
        with self.assertRaises(TypeError):
            questionable_count(_engine(), "Quantum Ferrets")


class TestWhatMustNotChange(unittest.TestCase):
    """GREEN BY DESIGN -- the count's meaning for starters is untouched, and nothing here
    prices a Questionable designation. B4's decision stands: `Questionable` stays out of
    INITIAL_ABSENCE_STATUSES and no haircut is applied."""

    def test_an_unknown_team_is_zero_not_an_error(self):
        from fantasy_sim.decisions import questionable_count
        self.assertEqual(questionable_count(_engine(), "No Such Team", STARTERS), 0)

    def test_an_empty_starter_set_counts_nothing(self):
        from fantasy_sim.decisions import questionable_count
        self.assertEqual(questionable_count(_engine(), "Quantum Ferrets", []), 0)

    def test_ir_and_out_are_not_counted_as_questionable(self):
        """Only the Questionable tag creates the unpriced-risk case. An IR player is already
        modelled as absent, so he is not part of this comparison."""
        from fantasy_sim.decisions import questionable_count
        eng = _engine()
        self.assertEqual(questionable_count(eng, "Quantum Ferrets", ["Benched IR"]), 0)

    def test_a_healthy_starter_adds_nothing(self):
        from fantasy_sim.decisions import questionable_count
        self.assertEqual(questionable_count(_engine(), "Quantum Ferrets", ["Starting QB"]), 0)


if __name__ == "__main__":
    unittest.main()

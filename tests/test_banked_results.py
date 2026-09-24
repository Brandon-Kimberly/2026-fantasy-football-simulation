"""C4: completed results are RECOMPUTED from re-scored points, so history gets rewritten.

The symptom the owner saw. On 2026-09-23 `scripts.luck_ledger` reported
`actual_wins=2` and `close games wins=2 losses=0` for a team that is **2-2** in Sleeper's
standings and remembers losing week 2 by 1.39 points. A pre-registered measurement
disagreed with the league table.

**THE CAUSE IS NOT IN THE LEDGER.** `sync._extract_weekly_h2h_results` decides each
completed week's head-to-head from `entry["points"]` as Sleeper serves it **today**, and
`median_win` likewise from a median recomputed from those same live points. Sleeper
re-scores completed weeks under the league's CURRENT settings. So when F49's IDP scoring
change went live on 2026-09-23 (`docs/EVALUATION_BOUNDARIES.md`, boundary 1), every
finished week was silently re-scored and week 2 flipped from a loss to a win:

    as banked     ~150.65  vs  150.41   LOSS   (what the league table still records)
    re-scored      148.52  vs  144.19   WIN    (what weekly_actuals says now)

The opponent lost 6.22 points to the IDP re-scoring and this roster lost 2.13, which
reversed a 0.24-point margin. `scripts.stat_corrections` shows the movement is entirely
IDP players -- Rousseau, T.J. Watt, Nakobe Dean, Van Ginkel, Hutchinson, Crosby -- so it
is the scoring change, not a stat correction.

**WHY THIS IS WORSE THAN AN ORDINARY BUG.** The luck ledger's whole value is that its
definitions were pre-registered before the data (F53, `docs/LUCK_LEDGER.md`). A
pre-registered measurement that misreads its inputs carries the credibility of
pre-registration while being wrong. And the failure is silent: the recomputed record is
internally consistent -- h2h wins still sum to 4.0 across the league every week -- so
nothing looks broken.

**THE BANKED RECORD IS AVAILABLE AND IS THE TRUTH.**
`league_standings.json` carries Sleeper's own `settings.wins`. That number is not
re-scored: the league banked it when the week closed. It disagrees with the recomputed
record, and that disagreement is the detector.

**A NAMING TRAP, recorded because it is load-bearing.** That field is called `h2h_wins`
and it is NOT head-to-head wins -- it is Sleeper's TOTAL wins, both legs of a
median-scoring week included. A test pins the arithmetic so the name cannot mislead the
next reader: recomputed H2H wins plus recomputed median wins is what must be compared
against it.

**Scope.** C4 asks the ledger to cross-check and refuse rather than print a wrong number.
Re-banking every completed result at source is a larger change that touches the engine's
`actual_wins_banked`, and is recorded in the finding rather than improvised here.

Written before the change and confirmed failing (rule 1).
"""
import unittest

# Two completed weeks as the LEDGER sees them -- {week: {team: points}} and the H2H
# pairings -- after F49's re-scoring. Week 2's 148.52 vs 144.19 is the flipped result:
# as banked it was ~150.65 vs 150.41, a 0.24-point LOSS.
SCORES = {
    1: {"Quantum Ferrets": 185.86, "Neon Walruses": 177.03,
        "Rocket Pandas": 172.11, "Polar Yetis": 164.36},
    2: {"Quantum Ferrets": 148.52, "Neon Walruses": 144.19,
        "Rocket Pandas": 183.65, "Polar Yetis": 155.95},
}
PAIRS = {
    1: [("Quantum Ferrets", "Neon Walruses"), ("Rocket Pandas", "Polar Yetis")],
    2: [("Quantum Ferrets", "Neon Walruses"), ("Rocket Pandas", "Polar Yetis")],
}
ME = "Quantum Ferrets"
# Recomputed: wk1 h2h W + median W, wk2 h2h W + median L  -> 3.
# Sleeper banked 2, because week 2 was a loss when it was played.
BANKED_TRUE, BANKED_REWRITTEN = 3, 2


class TestTheDisagreementIsDetected(unittest.TestCase):
    def test_recomputed_wins_are_compared_against_the_banked_total(self):
        from fantasy_sim.luck_ledger import banked_disagreement
        d = banked_disagreement(SCORES, PAIRS, ME, BANKED_REWRITTEN)
        self.assertIsNotNone(d, "3 recomputed wins against 2 banked must be flagged")
        self.assertEqual(d["recomputed"], 3)
        self.assertEqual(d["banked"], 2)

    def test_both_legs_are_counted_because_the_banked_field_counts_both(self):
        """The naming trap: `league_standings.h2h_wins` is Sleeper's TOTAL wins, both legs
        of a median-scoring week. Comparing h2h-only against it would report a false
        disagreement every week a team wins its median leg."""
        from fantasy_sim.luck_ledger import banked_disagreement
        self.assertIsNone(banked_disagreement(SCORES, PAIRS, ME, BANKED_TRUE))

    def test_a_missing_banked_record_is_not_a_disagreement(self):
        """Absence is unknown, not proof of a rewrite -- the rule the bid ledger and the
        designation log already follow."""
        from fantasy_sim.luck_ledger import banked_disagreement
        self.assertIsNone(banked_disagreement(SCORES, PAIRS, ME, None))

    def test_it_names_the_weeks_it_counted(self):
        from fantasy_sim.luck_ledger import banked_disagreement
        d = banked_disagreement(SCORES, PAIRS, ME, BANKED_REWRITTEN)
        self.assertEqual(d["weeks"], 2)
        self.assertEqual(d["h2h_wins"], 2)
        self.assertEqual(d["median_wins"], 1)


class TestTheLedgerRefusesRatherThanPrintingAWrongNumber(unittest.TestCase):
    """C4's requirement. A pre-registered measurement that cannot trust its inputs must
    say so; printing a plausible number is the failure this finding is about."""

    def _run(self, banked):
        from fantasy_sim.luck_ledger import ledger
        return ledger(SCORES, PAIRS, ME, banked_wins=banked)

    def test_the_result_carries_the_disagreement(self):
        self.assertIsNotNone(self._run(BANKED_REWRITTEN).get("banked_disagreement"))

    def test_schedule_luck_is_withheld_when_the_record_was_rewritten(self):
        """Schedule luck is `actual H2H wins - all-play expected`, so a rewritten win
        corrupts it directly."""
        self.assertIsNone(self._run(BANKED_REWRITTEN)["schedule_luck"])

    def test_close_games_is_withheld_too(self):
        """Close games is decided by the H2H margin, which is exactly what the re-scoring
        moved: a 0.24-point loss became a 4.33-point win."""
        self.assertIsNone(self._run(BANKED_REWRITTEN)["close_games"])

    def test_measurements_that_do_NOT_depend_on_the_result_still_report(self):
        """Opponent luck is points-against. It does not depend on who won, so withholding
        it would throw away good evidence."""
        self.assertIsNotNone(self._run(BANKED_REWRITTEN)["opponent_luck"])

    def test_a_clean_season_reports_everything(self):
        got = self._run(BANKED_TRUE)
        self.assertIsNone(got.get("banked_disagreement"))
        self.assertIsNotNone(got["schedule_luck"])
        self.assertIsNotNone(got["close_games"])

    def test_omitting_the_banked_record_keeps_the_old_behaviour(self):
        """Every existing caller passes no banked record; they must be unaffected."""
        from fantasy_sim.luck_ledger import ledger
        got = ledger(SCORES, PAIRS, ME)
        self.assertIsNone(got.get("banked_disagreement"))
        self.assertIsNotNone(got["schedule_luck"])


class TestTheScriptReadsTheBankedFieldAndNotARecomputedOne(unittest.TestCase):
    """Plumbing. Written AFTER the wiring, not before it -- rule 1 is about the defect,
    and the defect is in the library above. Stated plainly rather than dressed up as a
    regression test; each assertion below was confirmed by mutating the source (reading
    `losses` instead of `wins`, and dropping the roster_id lookup) and watching it fail.

    It is here because the failure mode is silent in the worst way: if `_banked_wins`
    returned {} the cross-check would simply never fire and the tool would go back to
    printing a wrong number with no warning at all."""

    ROSTERS = [
        {"roster_id": 1, "settings": {"wins": 2, "losses": 2, "fpts": 900}},
        {"roster_id": 2, "settings": {"wins": 3, "losses": 1}},
        {"roster_id": 9, "settings": {"wins": 1, "losses": 3}},   # not in `names`
        {"roster_id": 3, "settings": {}},                          # no record yet
    ]

    def _run(self, names):
        from unittest.mock import patch
        import scripts.luck_ledger as sl
        with patch.object(sl, "_get", return_value=self.ROSTERS):
            return sl._banked_wins("L", names)

    def test_it_maps_team_names_to_sleepers_own_win_total(self):
        got = self._run({1: ME, 2: "Neon Walruses"})
        self.assertEqual(got, {ME: 2, "Neon Walruses": 3})

    def test_a_roster_with_no_name_or_no_record_is_omitted_not_zeroed(self):
        """A zero would read as 'banked 0 wins' and fire a false disagreement."""
        got = self._run({1: ME, 3: "Rocket Pandas"})
        self.assertEqual(got, {ME: 2})

    def test_an_unreachable_endpoint_yields_no_record_rather_than_raising(self):
        """No banked record is 'unknown', which banked_disagreement treats as silence.
        The ledger must not fall over because the roster endpoint blipped."""
        from unittest.mock import patch
        import scripts.luck_ledger as sl
        with patch.object(sl, "_get", side_effect=RuntimeError("503")):
            self.assertEqual(sl._banked_wins("L", {1: ME}), {})


if __name__ == "__main__":
    unittest.main()

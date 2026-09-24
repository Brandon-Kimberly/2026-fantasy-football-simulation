"""F70's follow-up, now live: the engine banks a record the league does not recognise.

`actual_wins_banked` and `actual_points` are summed from `weekly_actuals.json`, which sync
writes from Sleeper's `/matchups` endpoint. **That endpoint DERIVES a completed week's
points rather than storing them** — it recomputes stat lines against the league's CURRENT
scoring settings every time it is called.

F83 made this permanent rather than incidental. Weeks 1 and 2 were played and banked under
the old IDP settings; the league now runs the new ones; and `/matchups` will therefore
report those two weeks on the new scale for the rest of the season while the standings hold
them on the old one. No commissioner action can change that, because there is nothing
stored to correct.

Measured on the live league: the recomputed record gives **3 wins** where the league banked
**2**, and `scripts.luck_ledger` has been saying so since C4.

**THE TWO BASES ARE EACH CORRECT FOR A DIFFERENT CONSUMER, and only one of them is wrong
here.** The Bayesian blend wants the RECOMPUTED scale — it asks how good a player is under
the rules that apply in future weeks, and the re-scored weeks answer exactly that. Nothing
about the blend changes. Only the *standings* quantities move: total wins and points, which
feed the week-15 playoff seeding and the exported `actual_wins_banked`.

**THE BANKED RECORD IS NOT BLINDLY TRUSTED, and that is the whole design.** The golden
fixtures' `league_standings.json` is not a coherent banked record — week15's is byte-for-byte
week06's, so it claims 3 wins against 14 completed weeks. Reading it blindly would move
every golden onto fixture data that is itself wrong. So the banked record is used only when
it ACCOUNTS FOR the completed weeks: this league awards two decisions per team per week
(head-to-head plus median), so league-wide wins must equal `teams x weeks`. Measured:

    week01 fixture   0 banked vs 0 expected     -> credible (and identical to recomputed)
    week06 fixture  20 banked vs 40 expected    -> STALE, fall back
    week15 fixture  20 banked vs 112 expected   -> STALE, fall back
    live league     16 banked vs 16 expected    -> credible, and it DISAGREES

That criterion is what keeps the goldens byte-identical while fixing the live case.

**The field is misnamed and it is load-bearing.** `league_standings.h2h_wins` is Sleeper's
`settings.wins` — TOTAL wins, both legs of a median-scoring week. F70 recorded the trap;
this file depends on it, so it is pinned again here.

Written before the change.
"""
import unittest

TEAMS = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis",
         "Turbo Llamas", "Cosmic Badgers", "Iron Wombats", "Crimson Marmots"]


def _standings(wins, points=None):
    return {t: {"h2h_wins": wins.get(t, 0),
                "points_scored": (points or {}).get(t, 100.0 * (i + 1)),
                "remaining_faab": 100.0}
            for i, t in enumerate(TEAMS)}


def _credible(weeks=2):
    """A banked record that accounts for `weeks`: teams x weeks decisions league-wide."""
    total = len(TEAMS) * weeks
    per = total // len(TEAMS)
    return _standings({t: per for t in TEAMS})


class TestTheCredibilityCriterion(unittest.TestCase):
    def test_a_record_accounting_for_every_week_is_used(self):
        from fantasy_sim.simulation import banked_league_record
        got = banked_league_record(_credible(2), TEAMS, 2, median_enabled=True)
        self.assertIsNotNone(got)
        wins, _pts = got
        self.assertEqual(wins[TEAMS[0]], 2)

    def test_a_stale_record_is_refused(self):
        """The golden week15 fixture: 20 banked wins against 14 completed weeks, because
        its standings file is byte-for-byte the week06 one. Trusting it would move every
        golden onto fixture data that is itself wrong."""
        from fantasy_sim.simulation import banked_league_record
        stale = _standings({t: (20 // len(TEAMS)) for t in TEAMS})
        self.assertIsNone(banked_league_record(stale, TEAMS, 14, median_enabled=True))

    def test_zero_weeks_with_a_zero_record_is_credible(self):
        """week01: nothing banked, nothing completed. Trivially consistent, and identical
        to the recomputed sum, so it changes nothing."""
        from fantasy_sim.simulation import banked_league_record
        got = banked_league_record(_standings({t: 0 for t in TEAMS}), TEAMS, 0,
                                   median_enabled=True)
        self.assertIsNotNone(got)
        self.assertEqual(got[0][TEAMS[0]], 0)

    def test_median_disabled_halves_the_expected_count(self):
        """The 2025 backtest runs pure H2H (`MEDIAN_SCORING_ENABLED = False`), so a week
        awards one decision per team, not two. Using the median-on expectation there would
        refuse every correct record in the backtest."""
        from fantasy_sim.simulation import banked_league_record
        half = _standings({t: 1 for t in TEAMS})          # 8 wins over 2 weeks
        self.assertIsNotNone(banked_league_record(half, TEAMS, 2, median_enabled=False))
        self.assertIsNone(banked_league_record(half, TEAMS, 2, median_enabled=True))

    def test_a_missing_team_refuses_the_whole_record(self):
        """A partial record is not a banked record. Half-applying it would mix two bases
        inside one standings table, which is the defect being fixed."""
        from fantasy_sim.simulation import banked_league_record
        partial = _credible(2)
        del partial[TEAMS[-1]]
        self.assertIsNone(banked_league_record(partial, TEAMS, 2, median_enabled=True))

    def test_an_empty_or_missing_standings_file_refuses(self):
        from fantasy_sim.simulation import banked_league_record
        self.assertIsNone(banked_league_record({}, TEAMS, 2, median_enabled=True))
        self.assertIsNone(banked_league_record(None, TEAMS, 2, median_enabled=True))

    def test_ties_still_sum_to_the_expected_total(self):
        """A tie awards 0.5 to each side, so the league-wide sum is unchanged and the
        criterion must not reject it."""
        from fantasy_sim.simulation import banked_league_record
        wins = {t: 2 for t in TEAMS}
        wins[TEAMS[0]], wins[TEAMS[1]] = 1.5, 2.5
        self.assertIsNotNone(banked_league_record(_standings(wins), TEAMS, 2,
                                                  median_enabled=True))


class TestTheEngineUsesTheBankedRecordWhenItIsCredible(unittest.TestCase):
    """The defect: with a credible banked record that DISAGREES, the engine still seeds
    from the recomputed one."""

    def test_total_wins_come_from_the_banked_record(self):
        from tests.test_banked_record_fixture import engine_with
        e = engine_with(banked_wins=2, recomputed_wins=3)
        self.assertEqual(e.actual_total_wins["Quantum Ferrets"], 2,
                         "the league banked 2; the recompute says 3 because /matchups "
                         "re-prices completed weeks under current settings (F83)")

    def test_banked_points_are_used_too(self):
        from tests.test_banked_record_fixture import engine_with
        e = engine_with(banked_wins=2, recomputed_wins=3, banked_points=336.38)
        self.assertAlmostEqual(e.actual_total_points["Quantum Ferrets"], 336.38, places=2)

    def test_the_recomputed_record_is_still_kept_for_diagnosis(self):
        """Not discarded: the disagreement between the two is the signal C4 reports, and
        throwing one away would make it unmeasurable."""
        from tests.test_banked_record_fixture import engine_with
        e = engine_with(banked_wins=2, recomputed_wins=3)
        recomputed = (e.actual_h2h_wins["Quantum Ferrets"]
                      + e.actual_median_wins["Quantum Ferrets"])
        self.assertEqual(recomputed, 3)

    def test_it_says_which_source_it_used(self):
        from tests.test_banked_record_fixture import engine_with
        self.assertEqual(engine_with(banked_wins=2, recomputed_wins=3).banked_record_source,
                         "league")

    def test_a_stale_record_falls_back_and_says_so(self):
        from tests.test_banked_record_fixture import engine_with
        e = engine_with(banked_wins=99, recomputed_wins=3, credible=False)
        self.assertEqual(e.banked_record_source, "recomputed")
        self.assertEqual(e.actual_total_wins["Quantum Ferrets"], 3,
                         "a stale banked record must never be used")


class TestTheBlendIsUntouched(unittest.TestCase):
    """GREEN BY DESIGN. Only the standings quantities move."""

    def test_the_posterior_still_reads_the_recomputed_weekly_scores(self):
        """The blend asks how good a player is under the rules that apply GOING FORWARD,
        and the re-scored weeks answer exactly that. Feeding it banked totals would be
        both wrong and impossible -- the banked record has no per-player detail."""
        from tests.test_banked_record_fixture import engine_with
        e = engine_with(banked_wins=2, recomputed_wins=3)
        self.assertGreater(len(e.baselines), 0)
        self.assertNotIn("actual_total_wins", str(type(e.baselines)))


if __name__ == "__main__":
    unittest.main()

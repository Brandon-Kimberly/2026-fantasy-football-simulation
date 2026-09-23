"""B1 step 2: the variance-components estimator, and what it must not get wrong.

**SAME INSTRUMENT AS PHASE 7, deliberately.** The offensive rates in
`EPISTEMIC_ERROR_RATES` were produced by "survey measurement 2" in
`docs/audit/AUDIT_PHASE_7_FINDINGS.md`: *"between-player variance of season means minus
the within-player sampling term -> sd_true / rostered mean"*, giving QB 0.07, RB 0.28,
WR 0.22, TE 0.20, K 0.25. If IDP is measured with a DIFFERENT instrument the resulting
number cannot be compared to the ones already in the config, and B1's question is
precisely a comparison. So this is that instrument, written down and tested.

**WHAT IT ESTIMATES.** `std_epistemic = rate * mean` is the prior sd on a player's TRUE
weekly mean. Observed season means vary for two reasons: players really do differ, and a
finite sample of games is noisy. Only the first is epistemic. So:

    Var(observed season means)  =  Var(true means)  +  E[ within-player var / games ]

and the estimator subtracts the second term off the first. Skipping that subtraction --
just taking the spread of season means -- OVERSTATES the rate, by exactly the amount of
sampling noise, which is largest for the positions with the fewest games. That is the
single mistake this file exists to prevent.

**A NEGATIVE ESTIMATE IS A REAL OUTCOME, NOT AN ERROR.** When sampling noise exceeds the
observed spread the difference goes below zero, which means the data cannot distinguish
these players from each other at all. It is clamped to zero and FLAGGED, never silently
returned as a small positive number.

Written before `fantasy_sim.epistemic_fit` existed and confirmed failing (rule 1).
"""
import unittest


class TestItSeparatesTrueSpreadFromSamplingNoise(unittest.TestCase):
    def test_with_no_within_player_noise_the_spread_is_all_real(self):
        """Each player scores his true mean every week. Nothing to subtract.

        4.0, not 3.27: the identity `Var(observed) = Var(true) + E[within/n]` is about
        the POPULATION variance, and the unbiased estimator of that from a sample is the
        n-1 form. My first version of this test asserted the biased n form; the code was
        right and the test was wrong.
        """
        from fantasy_sim.epistemic_fit import variance_components
        hist = {"a": [10.0] * 8, "b": [14.0] * 8, "c": [6.0] * 8}
        got = variance_components(hist, min_games=4)
        self.assertAlmostEqual(got["sd_true"], 4.0, places=6)
        self.assertAlmostEqual(got["mean"], 10.0, places=6)
        self.assertAlmostEqual(got["rate"], got["sd_true"] / 10.0, places=6)

    def test_identical_players_with_pure_noise_estimate_near_zero_spread(self):
        """THE POINT OF THE ESTIMATOR. Three players with the SAME true mean, differing
        only by sampling noise, must not be reported as genuinely different."""
        from fantasy_sim.epistemic_fit import variance_components
        hist = {"a": [8.0, 12.0] * 6, "b": [6.0, 14.0] * 6, "c": [9.0, 11.0] * 6}
        got = variance_components(hist, min_games=4)
        self.assertLess(got["sd_true"], 0.5,
                        "these players are identical; a large sd_true means the sampling "
                        "term was not subtracted")

    def test_the_naive_spread_is_reported_alongside_so_the_correction_is_visible(self):
        """The means must differ a LITTLE -- less than sampling noise explains. My first
        fixture gave all three the identical mean, so `sd_observed` was 0 and there was
        no correction to see."""
        from fantasy_sim.epistemic_fit import variance_components
        hist = {"a": [8.0, 12.0] * 6, "b": [7.0, 15.0] * 6, "c": [8.0, 10.0] * 6}
        got = variance_components(hist, min_games=4)
        self.assertAlmostEqual(got["sd_observed"], 1.0, places=6)
        self.assertGreater(got["sd_observed"], got["sd_true"],
                           "the uncorrected number is bigger; that gap IS the correction")
        self.assertFalse(got["degenerate"])

    def test_a_negative_estimate_is_clamped_and_flagged_not_hidden(self):
        from fantasy_sim.epistemic_fit import variance_components
        hist = {"a": [0.5, 19.5] * 4, "b": [1.0, 19.0] * 4, "c": [0.2, 19.8] * 4}
        got = variance_components(hist, min_games=4)
        self.assertEqual(got["sd_true"], 0.0)
        self.assertTrue(got["degenerate"],
                        "sampling noise swamped the spread; say so rather than return a "
                        "small positive number that looks like a measurement")


class TestThePopulationRules(unittest.TestCase):
    def test_players_below_the_game_threshold_are_excluded(self):
        from fantasy_sim.epistemic_fit import variance_components
        hist = {"a": [10.0] * 8, "b": [14.0] * 8, "thin": [99.0]}
        got = variance_components(hist, min_games=4)
        self.assertEqual(got["n_players"], 2)
        self.assertNotIn("thin", got["included"])

    def test_zero_weeks_are_skipped_the_way_the_engine_skips_them(self):
        """`_apply_bayesian_updates` does `if pts == 0: continue`. An estimator fitted on
        a different sample than the blend consumes is measuring a different quantity."""
        from fantasy_sim.epistemic_fit import variance_components
        with_zeros = {"a": [10.0, 0.0, 10.0, 0.0, 10.0, 10.0],
                      "b": [14.0, 0.0, 14.0, 0.0, 14.0, 14.0]}
        clean = {"a": [10.0] * 4, "b": [14.0] * 4}
        self.assertAlmostEqual(variance_components(with_zeros, min_games=4)["sd_true"],
                               variance_components(clean, min_games=4)["sd_true"], places=9)

    def test_too_few_players_refuses_rather_than_reporting(self):
        from fantasy_sim.epistemic_fit import variance_components
        got = variance_components({"a": [10.0] * 8}, min_games=4)
        self.assertIsNone(got["rate"])
        self.assertTrue(got["degenerate"])

    def test_top_k_keeps_the_highest_scoring_players(self):
        """The population must be the fantasy-relevant one, not every man who took a
        snap -- 300 LBs a week are mostly special-teamers with no startable mean."""
        from fantasy_sim.epistemic_fit import top_k_by_total
        hist = {"star": [20.0] * 5, "mid": [10.0] * 5, "scrub": [1.0] * 5}
        self.assertEqual(sorted(top_k_by_total(hist, 2)), ["mid", "star"])

    def test_top_k_larger_than_the_pool_keeps_everyone(self):
        from fantasy_sim.epistemic_fit import top_k_by_total
        self.assertEqual(len(top_k_by_total({"a": [1.0], "b": [2.0]}, 10)), 2)


class TestScoringUsesTheLeaguesOwnSettings(unittest.TestCase):
    """B1's data has to be scored under THIS league's rules, and after F49's sack cut
    landed that means the CURRENT settings -- the ones the engine will use going
    forward."""

    def test_a_stat_line_scores_under_the_supplied_settings(self):
        from fantasy_sim.epistemic_fit import score_stat_line
        settings = {"idp_tkl_solo": 1.5, "idp_sack": 2.0, "idp_qb_hit": 0.5}
        self.assertAlmostEqual(
            score_stat_line({"idp_tkl_solo": 4, "idp_sack": 1, "idp_qb_hit": 2}, settings),
            4 * 1.5 + 2.0 + 2 * 0.5)

    def test_unscored_categories_contribute_nothing(self):
        from fantasy_sim.epistemic_fit import score_stat_line
        self.assertAlmostEqual(
            score_stat_line({"idp_tkl": 9, "def_snp": 60}, {"idp_tkl": 0.0}), 0.0)

    def test_the_sack_cut_is_visible_in_the_score(self):
        """Guard on the boundary itself: the same line is worth less under the new rules,
        so a fit run against the old settings would be measuring a different game."""
        from fantasy_sim.epistemic_fit import score_stat_line
        line = {"idp_sack": 1, "idp_tkl_loss": 1, "idp_tkl_solo": 1, "idp_qb_hit": 1}
        old = {"idp_sack": 4.0, "idp_tkl_loss": 2.0, "idp_tkl_solo": 1.5, "idp_qb_hit": 1.0}
        new = {"idp_sack": 2.0, "idp_tkl_loss": 2.0, "idp_tkl_solo": 1.5, "idp_qb_hit": 0.5}
        self.assertAlmostEqual(score_stat_line(line, old), 8.5)
        self.assertAlmostEqual(score_stat_line(line, new), 6.0)


class TestItReproducesThePhase7OffensiveNumbers(unittest.TestCase):
    """Rule 2, and the only thing that makes the IDP number comparable: if this estimator
    cannot land near Phase 7's published offensive figures on offensive data, then any
    IDP number it produces cannot be set beside QB 0.07 / RB 0.28 / WR 0.22.

    This is a structural check on the estimator, not on the 2025 numbers -- it asserts the
    formula behaves as Phase 7 describes, with the real comparison left to the study
    script, which runs it on live data and prints both.
    """

    def test_a_known_construction_returns_its_own_rate(self):
        from fantasy_sim.epistemic_fit import variance_components
        import random
        rng = random.Random(11)
        true = [12.0, 9.0, 15.0, 10.5, 13.5, 8.0, 11.0, 14.0] * 4
        hist = {f"p{i}": [t + rng.gauss(0, 6.0) for _ in range(16)]
                for i, t in enumerate(true)}
        got = variance_components(hist, min_games=8)
        implied = (sum((t - sum(true) / len(true)) ** 2 for t in true) / len(true)) ** 0.5
        self.assertLess(abs(got["sd_true"] - implied), 1.0,
                        f"recovered {got['sd_true']:.2f} against a built-in {implied:.2f}")


if __name__ == "__main__":
    unittest.main()


class TestTheHoldoutScan(unittest.TestCase):
    """B1's stated acceptance is a HELD-OUT score, which variance components do not give:
    a rate that merely fits the observed half better is not evidence for anything."""

    def test_the_transcribed_blend_matches_the_engines_published_numbers(self):
        """The same arithmetic B1 reproduced from simulation.py:545-547."""
        from fantasy_sim.epistemic_fit import blend
        self.assertAlmostEqual(blend(11.7, 0.55, [16.5, 41.5]), 13.72, places=2)
        self.assertAlmostEqual(blend(11.7, 0.15, [16.5, 41.5]), 11.87, places=2)

    def test_a_population_with_real_spread_prefers_a_looser_prior(self):
        """Players genuinely differ, so the data should be trusted: the scan must not
        pick the stiffest rate on offer."""
        from fantasy_sim.epistemic_fit import holdout_rate_scan
        hist = {f"p{i}": [float(m)] * 14 for i, m in enumerate([4, 8, 12, 16, 20, 24])}
        got = holdout_rate_scan(hist, [0.05, 0.15, 0.30, 0.60], split=7)
        self.assertEqual(got["n_players"], 6)
        self.assertGreater(got["best_rate"], 0.05)

    def test_a_population_with_no_spread_prefers_a_stiffer_prior(self):
        """Identical true means plus noise: trusting the sample is the wrong move, and
        the scan should say so."""
        from fantasy_sim.epistemic_fit import holdout_rate_scan
        import random
        rng = random.Random(5)
        hist = {f"p{i}": [10.0 + rng.gauss(0, 7.0) for _ in range(14)] for i in range(12)}
        got = holdout_rate_scan(hist, [0.05, 0.15, 0.30, 0.60], split=7)
        self.assertLessEqual(got["best_rate"], 0.15)

    def test_too_few_players_refuses(self):
        from fantasy_sim.epistemic_fit import holdout_rate_scan
        got = holdout_rate_scan({"a": [1.0] * 14}, [0.15], split=7)
        self.assertTrue(got["degenerate"])
        self.assertIsNone(got["best_rate"])

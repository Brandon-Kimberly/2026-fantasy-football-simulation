"""
tests.test_calibration

Phase 7 step 2/3 (AUDIT_PHASE_7_FINDINGS.md): EPISTEMIC_ERROR_RATES and the posterior form are
a matched pair. The n_0 = 4 form multiplies prior precision by four, so the rates were tuned to
about twice the true between-player spread; the conjugate form needs the true spread.

Measured on real 2025 (weeks 1-14, rostered players with >= 4 active weeks, per game played):
between-player variance of season means minus the within-player sampling term gives the
epistemic sd of a POSITIONAL prior; divided by the rostered positional mean it is the rate.
"""
import unittest

from fantasy_sim.config import EPISTEMIC_ERROR_RATES

# pos: (rate = sd_true / rostered mean, n players, sd_true, rostered mean). Real 2025, one season.
MEASURED_POSITIONAL_EPISTEMIC_RATE = {
    "QB": (0.07, 20, 1.28, 18.59),
    "RB": (0.28, 46, 3.30, 11.95),
    "WR": (0.22, 60, 2.32, 10.55),
    "TE": (0.20, 19, 1.85, 9.27),
    "K": (0.25, 12, 2.36, 9.63),
}


class TestEpistemicRateLevel(unittest.TestCase):
    @unittest.expectedFailure
    def test_epistemic_rates_match_the_measured_between_player_spread(self):
        """CHARACTERISATION. Config rates (QB 0.30, RB 0.63, WR 0.55, TE 0.50, K 0.40) are about
        twice the measured between-player spread -- correct only in combination with the
        n_0 = 4 form's quadrupled prior precision. Remove the expectedFailure with the joint
        rates + conjugate-form change."""
        for pos, (rate, n, sd_true, mean) in MEASURED_POSITIONAL_EPISTEMIC_RATE.items():
            self.assertAlmostEqual(EPISTEMIC_ERROR_RATES[pos], rate, delta=0.05,
                                   msg="%s: config %.2f vs measured %.2f (sd_true %.2f / mean %.2f, n=%d)"
                                       % (pos, EPISTEMIC_ERROR_RATES[pos], rate, sd_true, mean, n))

    def test_idp_rates_are_untouched(self):
        """GUARD: DL / LB / DB have no 2025 data (no IDP rostered that season) and stay at 0.15;
        FLEX stays at its 0.18 fallback."""
        for pos in ("DL", "LB", "DB"):
            self.assertEqual(EPISTEMIC_ERROR_RATES[pos], 0.15)
        self.assertEqual(EPISTEMIC_ERROR_RATES["FLEX"], 0.18)

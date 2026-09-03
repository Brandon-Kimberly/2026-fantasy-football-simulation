"""The behavioral-plausibility harness's own logic (fast -- no engine runs here), plus
the regression pin on the golden sandbox's live-log severance.

The harness's engine-measuring path is exercised by scripts.run_behavior_check at
milestones, not by the suite: the real-2025 comparison reports known filed gaps every
run by design, and a suite test failing on a known gap becomes wallpaper (the design
decision recorded in fantasy_sim/behavior_check.py's docstring).
"""
import unittest
from unittest.mock import patch

from fantasy_sim.behavior_check import REAL_2025, classify, compare_to_baseline
from fantasy_sim.config import MANAGER_PROFILES
from fantasy_sim.simulation import FantasySimulationEngine, blend_faab_profiles


class TestClassify(unittest.TestCase):
    def test_three_way_verdicts(self):
        lo, hi = REAL_2025["faab_spent"]["band"]
        self.assertEqual(classify("faab_spent", (lo + hi) / 2), "IN-BAND")
        self.assertTrue(classify("faab_spent", lo - 1).startswith("UNDER"))
        self.assertTrue(classify("faab_spent", hi + 1).startswith("OVER"))

    def test_filed_gaps_carry_their_f_numbers_instead_of_reading_as_regressions(self):
        v = classify("trade_completions", 0.0)
        self.assertTrue(v.startswith("UNDER"))
        self.assertIn("F2/F34", v, "a known, tracked gap must say so in the verdict")

    def test_every_reference_has_a_band_and_a_note(self):
        for k, ref in REAL_2025.items():
            self.assertIn("band", ref); self.assertIn("note", ref)
            self.assertLess(ref["band"][0], ref["band"][1])


class TestBaselineDrift(unittest.TestCase):
    METRICS = {"scenario": "week01", "n_sims": 30, "faab_spent": 684.0, "waiver_claims": 110.0}

    def test_identical_rates_report_no_drift(self):
        self.assertEqual(compare_to_baseline(self.METRICS, dict(self.METRICS)), [])

    def test_a_moved_rate_is_drift_and_a_missing_metric_is_loud(self):
        base = {"faab_spent": 600.0}   # moved AND missing waiver_claims
        drifted = compare_to_baseline(self.METRICS, base)
        keys = {d[0] for d in drifted}
        self.assertIn("faab_spent", keys)
        self.assertIn("waiver_claims", keys)

    def test_float_formatting_noise_is_not_drift(self):
        base = dict(self.METRICS); base["faab_spent"] = 684.005
        self.assertEqual(compare_to_baseline(self.METRICS, base), [])


class TestSandboxSeversLiveLogRead(unittest.TestCase):
    def test_engine_built_inside_the_golden_sandbox_never_reads_the_live_decision_log(self):
        """Regression pin on the hermeticity hole the 2026-09-03 audit flagged: engine
        init reads the growing live decision log (the F31 profile updater), and the
        golden sandbox must sever that. A sentinel reader is installed OUTSIDE the
        sandbox; the sandbox's own patch must shadow it. If anyone removes the sandbox's
        read_faab_observations patch, the sentinel fires and this test fails loudly."""
        from tests import golden_master as gm

        def sentinel(*a, **k):
            raise AssertionError("the golden sandbox no longer severs the live "
                                 "decision-log read -- F11-class contamination")

        with patch("fantasy_sim.simulation.read_faab_observations", sentinel):
            with gm._sandbox("week01", 1, 1):
                engine = FantasySimulationEngine()   # sentinel must NOT fire
        self.assertEqual(engine.faab_profiles,
                         blend_faab_profiles(MANAGER_PROFILES, {}),
                         "sandboxed engines run on priors exactly, never live observations")

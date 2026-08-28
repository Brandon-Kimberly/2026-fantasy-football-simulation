"""
tests.test_golden_master

Phase 0 deliverable: the golden-master regression test for `run_simulation` and
`export_and_visualize`.

Read tests/golden_master.py first -- it documents what is hashed, what is deliberately not,
and the coverage gaps that remain. This file is only the assertions.

A note on what this test is and is not. It is a CHARACTERISATION test: it pins what the engine
currently does so that a refactor which changes the numbers cannot pass silently. It asserts
nothing about whether those numbers are right. Do not read a passing golden master as evidence
of correctness -- that is what the later audit phases are for.
"""
import copy
import unittest

import numpy as np

from fantasy_sim.config import SIM_CONFIG
from tests import golden_master as gm


def _describe(stage_name, key, expected_entry, actual_entry):
    """Renders a hash mismatch so the reader can tell last-ulp float noise from a genuine
    shift in the distribution, without having to re-run anything by hand."""
    exp_s = expected_entry.get("summary", {})
    act_s = actual_entry.get("summary", {})
    lines = [
        "",
        "GOLDEN MASTER MISMATCH",
        "  stage : " + stage_name,
        "  output: " + key,
        "  expected hash: " + expected_entry["hash"],
        "  actual   hash: " + actual_entry["hash"],
        "  {:<8} {:>18} {:>18} {:>14}".format("moment", "expected", "actual", "abs delta"),
    ]
    for moment in ("n", "sum", "mean", "std", "min", "max"):
        if moment not in exp_s and moment not in act_s:
            continue
        e = exp_s.get(moment)
        a = act_s.get(moment)
        if isinstance(e, (int, float)) and isinstance(a, (int, float)):
            delta = "{:.6g}".format(abs(a - e))
        else:
            delta = "n/a"
        lines.append("  {:<8} {:>18} {:>18} {:>14}".format(moment, str(e), str(a), delta))
    lines += [
        "",
        "  If every delta is 0 or ~1e-15, this is almost certainly last-ulp float noise from a",
        "  different platform/numpy build, not a behaviour change -- see FLOAT EXACTNESS in",
        "  tests/golden_master.py. If the deltas are material, run_simulation or",
        "  export_and_visualize now produces a different distribution. Diagnose before",
        "  regenerating; `python -m tests.golden_master --regenerate` is not a fix.",
    ]
    return "\n".join(lines)


class TestGoldenMaster(unittest.TestCase):
    """Locks the aggregate behaviour of the two large, otherwise-untested engine methods."""

    @classmethod
    def setUpClass(cls):
        # Each scenario is a full engine run, so run each exactly once and share the record
        # across the comparison tests.
        cls.records = dict((s, gm.run_scenario(s)) for s in gm.SCENARIOS)
        cls.expected = dict((s, gm.load_expected(s)) for s in gm.SCENARIOS)

    def _compare(self, scenario, stage):
        expected = self.expected[scenario][stage]
        actual = self.records[scenario][stage]
        self.assertEqual(
            sorted(expected), sorted(actual),
            "The set of outputs in " + stage + " changed for " + scenario + ". An output was "
            "added or removed, which is a behaviour change in its own right -- regenerate "
            "deliberately if intended.")
        for key in sorted(expected):
            self.assertEqual(expected[key]["hash"], actual[key]["hash"],
                             _describe(stage, scenario + "/" + key, expected[key], actual[key]))

    def test_run_simulation_output_matches_golden_week01(self):
        """Preseason fixture (current_week=1, no completed weeks): run_simulation's full output."""
        self._compare("week01", "stage_a__run_simulation")

    def test_export_output_matches_golden_week01(self):
        self._compare("week01", "stage_b__export_and_visualize")

    def test_export_champ_ranking_matches_golden_week01(self):
        self._compare("week01", "stage_c__export_champ_ranking")

    def test_run_simulation_output_matches_golden_week06(self):
        """Mid-season fixture (current_week=6, five completed weeks). Distinct from week01 in
        that it drives _apply_bayesian_updates' posterior update, the banked-wins entry state,
        and a week loop that does not start at index 0."""
        self._compare("week06", "stage_a__run_simulation")

    def test_export_output_matches_golden_week06(self):
        self._compare("week06", "stage_b__export_and_visualize")

    def test_export_champ_ranking_matches_golden_week06(self):
        self._compare("week06", "stage_c__export_champ_ranking")

    def test_stage_c_actually_exercises_the_champion_ranking_block(self):
        """Guards the guard. stage_c exists only to reach the championship-share ranking, which
        MIN_CHAMP_APPEARANCES_FOR_RANKING = 50 puts out of reach of a short run. If the
        appearance scaling ever stopped clearing that threshold, stage_c would silently
        degenerate into a duplicate of stage_b and the gap would reopen unnoticed -- so assert
        the two differ, and that stage_c's insights payload is genuinely non-empty."""
        for scenario in gm.SCENARIOS:
            with self.subTest(scenario=scenario):
                b = self.records[scenario]["stage_b__export_and_visualize"]
                c = self.records[scenario]["stage_c__export_champ_ranking"]
                key = [k for k in b if "syndicate_insights" in k][0]
                self.assertNotEqual(
                    b[key]["hash"], c[key]["hash"],
                    "stage_c produced the same insights payload as stage_b, so the "
                    "championship-share ranking block is still running its empty branch and "
                    "remains untested.")

    def test_output_does_not_depend_on_ambient_rng_state(self):
        """run_simulation seeds the global stream itself (np.random.seed(1000 + batch)) before
        any draw, so two runs must agree even when the ambient RNG state differs beforehand.

        This is the property the whole harness rests on: if it did not hold, every hash here
        would be a hostage to test execution order. Kept small -- it is a determinism check,
        not a coverage check."""
        np.random.seed(424242)
        np.random.random(1000)
        first = gm.run_scenario("week01", batches=2, sims_per_batch=3)

        np.random.seed(7)
        np.random.random(31337)
        second = gm.run_scenario("week01", batches=2, sims_per_batch=3)

        for stage in first:
            for key in first[stage]:
                self.assertEqual(
                    first[stage][key]["hash"], second[stage][key]["hash"],
                    "Output " + stage + "/" + key + " changed with the ambient RNG state. "
                    "Something now draws from the global stream before the per-batch "
                    "np.random.seed(), which makes every result in this suite "
                    "order-dependent.")

    def test_golden_master_detects_a_change_in_the_model(self):
        """Proves the hashes are load-bearing rather than vacuous.

        A golden master that cannot fail is worthless, and there are several ways this one
        could silently become vacuous -- capture recording nothing, canonical() flattening
        distinct values to the same bytes, the sandbox swallowing the run. So perturb one
        real model constant and require the hashes to move. INJURY_RATES is used because it
        feeds run_simulation directly and its effect propagates all the way to the exports.

        Note the direction of the assertion: this test fails if the harness is INSENSITIVE.
        It says nothing about the perturbed model being wrong."""
        original = copy.deepcopy(SIM_CONFIG["INJURY_RATES"])
        try:
            SIM_CONFIG["INJURY_RATES"] = dict((k, min(1.0, v * 3.0)) for k, v in original.items())
            perturbed = gm.run_scenario("week01")
        finally:
            SIM_CONFIG["INJURY_RATES"] = original

        golden = self.expected["week01"]
        moved = []
        for stage in ("stage_a__run_simulation", "stage_b__export_and_visualize"):
            for key in golden[stage]:
                if golden[stage][key]["hash"] != perturbed[stage][key]["hash"]:
                    moved.append(stage + "/" + key)

        self.assertTrue(
            moved,
            "Tripling every INJURY_RATES entry changed NOTHING in the hashed outputs. The "
            "golden master is not actually observing the model -- fix the harness before "
            "trusting any passing result from it.")
        # The headline season outcomes must be among what moved; if only some peripheral
        # payload shifted, the harness is watching the wrong things.
        self.assertIn("stage_a__run_simulation/wins", moved,
                      "Injury rates tripled but the simulated win totals were unchanged. "
                      "Expected outputs did not move: " + repr(sorted(moved)))


class TestGoldenMasterFixtures(unittest.TestCase):
    """Cheap structural checks on the committed fixture set itself."""

    def test_every_required_input_is_committed_for_every_scenario(self):
        import os
        for scenario in gm.SCENARIOS:
            for name in gm.FIXTURE_INPUTS:
                path = os.path.join(gm.FIXTURE_ROOT, scenario, name)
                with self.subTest(scenario=scenario, fixture=name):
                    self.assertTrue(os.path.exists(path), "Missing fixture: " + path)

    def test_the_two_scenarios_are_actually_different_league_states(self):
        """week06 exists to cover paths week01 cannot reach. If it ever collapsed back into a
        copy of week01 -- a plausible outcome of a careless fixture regeneration -- the extra
        runtime would buy nothing, so pin the two distinguishing properties."""
        import json
        import os

        def load(scenario, name):
            with open(os.path.join(gm.FIXTURE_ROOT, scenario, name)) as f:
                return json.load(f)

        self.assertEqual(load("week01", "league_state.json")["current_week"], 1)
        self.assertEqual(load("week06", "league_state.json")["current_week"], 6)
        self.assertEqual(load("week01", "weekly_actuals.json"), {},
                         "week01 is meant to be the preseason state, with no completed weeks.")
        self.assertEqual(len(load("week06", "weekly_actuals.json")), 5,
                         "week06 is meant to carry five completed weeks so the Bayesian "
                         "update and banked-wins paths are exercised.")

    def test_canonicalisation_distinguishes_values_a_naive_encoder_would_not(self):
        """canonical() is the single point of failure for every hash in this file: anything it
        flattens becomes invisible to the golden master. Pin the cases that matter."""
        # float.hex() keeps ulp-level differences that repr()/round() would discard
        self.assertNotEqual(gm.digest(0.1 + 0.2), gm.digest(0.3))
        # int and float that compare equal must not hash the same
        self.assertNotEqual(gm.digest(1), gm.digest(1.0))
        # -0.0 and 0.0 compare equal but are different doubles
        self.assertNotEqual(gm.digest(-0.0), gm.digest(0.0))
        # key ORDER must not matter, but key CONTENT must
        self.assertEqual(gm.digest({"a": 1, "b": 2}), gm.digest({"b": 2, "a": 1}))
        self.assertNotEqual(gm.digest({"a": 1, "b": 2}), gm.digest({"a": 2, "b": 1}))
        # arrays differing only in shape must not collide
        self.assertNotEqual(gm.digest(np.zeros((2, 3))), gm.digest(np.zeros((3, 2))))


if __name__ == "__main__":
    unittest.main()

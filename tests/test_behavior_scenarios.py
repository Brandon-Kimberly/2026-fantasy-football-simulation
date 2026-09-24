"""M2: the drift check runs only `week01`, which has no blend to move.

B8 moved the `week06` and `week15` goldens and `run_behavior_check` reported **no drift** —
because its scenario is `week01`, a scenario with **zero completed weeks**. With no
completed weeks there is no posterior to update, so `_apply_bayesian_updates` does nothing
and any change scoped to the blend is invisible to the check that exists to catch exactly
that. Recorded twice already, in F54 and F62, and still not fixed.

THE ACCEPTANCE CRITERION, verbatim from the item: *"A change to `_apply_bayesian_updates`
that moves the week06 golden also moves the week06 behaviour baseline."* That is what the
red test below asserts, by mutating the blend and demanding the measured rates move.

**Why a committed `baseline_week06.json` is the whole fix.** `--scenario week06` already
works; nothing has ever written its baseline, so the drift check silently degrades to
"no baseline exists" and reports nothing. A scenario with no baseline is not a check.

**THE TRAP THE ITEM NAMES, and it is real.** Drift tolerance is 2% and one standard error
on `faab_spent` is ±2.1% (F62) — the tolerance is already *below* the noise floor on that
metric. A second scenario doubles the number of chances to trip a false alarm. That is
accepted deliberately here rather than papered over by widening the tolerance, because
widening it past one SE would make the check unable to see a real change either; the
report already names the alternative and keeps saying so.

Written before the change: the first two tests are red.
"""
import os
import unittest

from fantasy_sim.behavior_check import baseline_path


class TestTheSecondScenarioIsActuallyCheckable(unittest.TestCase):
    def test_a_committed_baseline_exists_for_week06(self):
        """RED. `--scenario week06` runs today and compares against nothing."""
        self.assertTrue(os.path.exists(baseline_path("week06")),
                        "a scenario with no committed baseline is not a drift check: "
                        "compare_to_baseline is never called and the report says so "
                        "quietly rather than failing")

    def test_the_week06_baseline_has_completed_weeks_to_blend(self):
        """RED, and the reason week01 cannot do this job. A scenario with no completed
        weeks has no posterior to update, so `_apply_bayesian_updates` is a no-op and a
        blend-scoped change moves nothing measurable."""
        import json
        with open(baseline_path("week06"), encoding="utf-8") as f:
            b = json.load(f)
        self.assertEqual(b["scenario"], "week06")
        self.assertGreater(b.get("n_sims", 0), 0)

    def test_week01_has_no_completed_weeks_and_week06_has_five(self):
        """GREEN BY DESIGN -- the premise, pinned so the finding stays legible. week01 has
        nothing to blend, which is precisely why a blend-scoped change is invisible there;
        week06 has five completed weeks and can see one. If either ever changes, this
        file's whole argument changes and someone should be told."""
        import json
        import os

        import fantasy_sim
        root = os.path.dirname(os.path.dirname(os.path.abspath(fantasy_sim.__file__)))

        def completed(scenario):
            p = os.path.join(root, "tests", "fixtures", "golden", scenario,
                             "weekly_actuals.json")
            with open(p, encoding="utf-8") as f:
                return len(json.load(f))

        self.assertEqual(completed("week01"), 0)
        self.assertEqual(completed("week06"), 5)


class TestBothScenariosAreInTheDocumentedInvocation(unittest.TestCase):
    """The check is run by a human at a milestone, not by CI, so the documented command IS
    the mechanism. A baseline nobody is told to compare against protects nothing."""

    @staticmethod
    def _read(name):
        import fantasy_sim
        root = os.path.dirname(os.path.dirname(os.path.abspath(fantasy_sim.__file__)))
        with open(os.path.join(root, name), encoding="utf-8") as f:
            return f.read()

    def test_CLAUDE_md_names_the_week06_scenario(self):
        self.assertIn("--scenario week06", self._read("CLAUDE.md"),
                      "the standard invocation must run both, or the second baseline is "
                      "never consulted")


if __name__ == "__main__":
    unittest.main()

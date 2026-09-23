"""F59 / backlog B9: the model-health verdict threshold is an unsourced literal.

`simulation.py` decided the season's headline self-assessment on a bare number:

    report['model_health_verdict'] = ('Calibrated & Learning' if mae < 18.0
                                      else 'High Variance / Volatile')

18.0 cited nothing (rule 5). B9 asked whether the live reading -- 29.64 with two weeks
banked, verdict "High Variance / Volatile" -- is alarming or expected, and observed that
the question is unanswerable without knowing where 18.0 came from.

It is answerable, and the answer is that the threshold is unreachable by construction.
See config.TEAM_MAE_HEALTH_THRESHOLD for the derivation; the short form is that 18.0 sits
BELOW the full simulation's own 2025 team-week MAE (22.36) while `team_scoring_mae` is a
much cruder estimator of the same quantity, so the favourable verdict cannot be earned.

Written before the fix and confirmed failing (rule 1).
"""
import re
import unittest

from fantasy_sim import config

SIM_SRC = None
CFG_SRC = None


def _read(mod):
    import inspect
    return inspect.getsource(mod)


class TestTheThresholdIsANamedSourcedConstant(unittest.TestCase):
    def test_the_constant_exists(self):
        self.assertTrue(hasattr(config, "TEAM_MAE_HEALTH_THRESHOLD"),
                        "F59: the model-health threshold belongs in config.py (rule 5)")
        self.assertIsInstance(config.TEAM_MAE_HEALTH_THRESHOLD, float)
        self.assertGreater(config.TEAM_MAE_HEALTH_THRESHOLD, 0.0)

    def test_it_carries_a_sourcing_comment(self):
        """Rule 5: a new constant with no sourcing comment is not acceptable;
        'unverified, carried over' IS acceptable and honest."""
        from fantasy_sim import config as _c
        src = _read(_c)
        m = re.search(r"^TEAM_MAE_HEALTH_THRESHOLD\s*=", src, re.M)
        self.assertIsNotNone(m, "F59: constant not found in config source")
        preceding = src[:m.start()].rstrip().splitlines()
        comment_block = []
        for line in reversed(preceding):
            if line.lstrip().startswith("#"):
                comment_block.append(line)
            else:
                break
        self.assertGreaterEqual(
            len(comment_block), 3,
            "F59: the threshold needs a real derivation comment, not a one-liner -- it "
            "decides the season's headline self-assessment")
        text = " ".join(comment_block).lower()
        self.assertTrue("unverified" in text or "22.3" in text or "backtest" in text,
                        "F59: the comment must say where the number came from, or say "
                        "plainly that it is unverified")

    def test_the_verdict_line_no_longer_holds_a_bare_literal(self):
        """B9's acceptance criterion: no bare numeric threshold in the verdict."""
        from fantasy_sim import simulation
        src = _read(simulation)
        verdict_lines = [ln for ln in src.splitlines() if "model_health_verdict" in ln
                         and "Calibrated" in ln]
        self.assertEqual(len(verdict_lines), 1, f"expected one verdict line, got {verdict_lines}")
        line = verdict_lines[0]
        self.assertNotIn("18.0", line, "F59: the bare literal is still there")
        self.assertIn("TEAM_MAE_HEALTH_THRESHOLD", line,
                      "F59: the verdict must read the named constant")


class TestTheThresholdIsHonestAboutBeingUnreachable(unittest.TestCase):
    """The substantive half. B9 treated this as pure config hygiene; the measurement it
    asked for turns out to say the verdict has never been able to fire favourably."""

    def test_the_threshold_is_recorded_against_the_engines_own_backtest_number(self):
        src = _read(config)
        m = re.search(r"^TEAM_MAE_HEALTH_THRESHOLD\s*=", src, re.M)
        block = src[max(0, m.start() - 2500):m.start()]
        self.assertIn("22.3", block,
                      "F59: the derivation must cite the full simulation's own 2025 "
                      "team-week MAE -- that is the number the threshold has to be read "
                      "against, and it is what makes 18.0 unreachable")
        self.assertIn("[:13]", block,
                      "F59: the derivation must record that team_scoring_mae sums the "
                      "first 13 players in ARBITRARY roster order, not a lineup -- that "
                      "is why this estimator is cruder than the backtest's")


if __name__ == "__main__":
    unittest.main()

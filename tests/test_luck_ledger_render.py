"""R2: the luck ledger prints `p 0.000` next to `too early`.

The live output on 2026-09-23 read:

    scoring luck       -0.46  unlucky  +-  0.11  z -4.03  p 0.000  too early

A reader takes the p-value. `too early` is a word; `p 0.000` is a number with four
significant figures, and in every other context a reader has ever met it means the result
is real. The two are side by side and the number wins.

**THE ARITHMETIC IS NOT WRONG — the inference is unavailable.** z and p are correctly
computed from the sample. But the ledger's pre-registration (F53, `docs/LUCK_LEDGER.md`)
fixes a minimum n before any significance claim, precisely so a two-week sample cannot
produce a verdict, and printing p at n = 2 hands the reader the claim the threshold exists
to withhold. `SIGNIFICANT` was already suppressed below the threshold; the number it was
derived from was not.

**NOTHING ABOUT THE DEFINITIONS CHANGES.** The point estimate and its standard error still
print — they are the honest content at any n, and the SE is the thing that actually says
"this is too noisy to read". Only z and p are withheld, and the reason is stated on the
line.

Also fixed here: the threshold was a bare `6` written twice in `scripts/luck_ledger.py`
with no name and no source. A pre-registered constant that lives as a literal in a
renderer is one edit from being changed by someone who does not know it was pre-registered.

Written before the change.
"""
import unittest


def _metric(delta=-0.46, se=0.11, z=-4.03):
    return {"delta": delta, "se": se, "z": z, "my_mean_z": -0.489,
            "league_mean_z": -0.032, "n_weeks": 2}


class TestBelowTheThresholdNoPValueIsPrinted(unittest.TestCase):
    """RED. The number the reader takes."""

    def _line(self, weeks):
        from scripts.luck_ledger import _fmt
        return _fmt(_metric(), ["my_mean_z", "league_mean_z"], "scoring luck",
                    "scoring_luck", weeks)

    def test_no_numeric_p_value_appears_at_two_weeks(self):
        line = self._line(2)
        self.assertNotIn("p 0.000", line)
        self.assertNotIn("p 0.0", line, f"a p-value at n=2 is the claim the threshold "
                                        f"exists to withhold: {line}")

    def test_no_z_score_appears_either(self):
        """z IS the p-value in another form; suppressing one and printing the other just
        makes the reader do the conversion."""
        self.assertNotIn("z -4.03", self._line(2))

    def test_the_point_estimate_and_SE_still_print(self):
        """The honest content at any n. The SE is what actually says 'too noisy to read'."""
        line = self._line(2)
        self.assertIn("-0.46", line)
        self.assertIn("0.11", line)

    def test_the_reason_is_on_the_line(self):
        self.assertIn("too early", self._line(2))

    def test_the_detail_columns_survive(self):
        self.assertIn("my_mean_z=-0.489", self._line(2))


class TestAtOrAboveTheThresholdInferenceReturns(unittest.TestCase):
    def _line(self, weeks):
        from scripts.luck_ledger import _fmt
        return _fmt(_metric(), ["my_mean_z"], "scoring luck", "scoring_luck", weeks)

    def test_z_and_p_print_once_the_sample_is_big_enough(self):
        line = self._line(6)
        self.assertIn("z -4.03", line)
        self.assertIn("p 0.000", line)

    def test_the_verdict_word_returns_too(self):
        self.assertIn("SIGNIFICANT", self._line(6))

    def test_the_boundary_is_inclusive_at_the_threshold(self):
        """`weeks < MIN` withholds; `weeks == MIN` reports. Pinned because `<` and `<=`
        are one keystroke apart and only one of them matches the pre-registration.

        The match is on a NUMERIC p specifically: the withheld line still contains the
        literal "p" (as `p --`), so a bare substring test for "p " passes on both sides
        and proves nothing. That is how the first version of this assertion was wrong."""
        import re
        from fantasy_sim.luck_ledger import MIN_WEEKS_FOR_INFERENCE
        numeric_p = re.compile(r"\bp\s+\d")
        self.assertIsNone(numeric_p.search(self._line(MIN_WEEKS_FOR_INFERENCE - 1)))
        self.assertIsNotNone(numeric_p.search(self._line(MIN_WEEKS_FOR_INFERENCE)))


class TestTheThresholdIsNamedAndPreRegistered(unittest.TestCase):
    def test_it_lives_beside_the_definitions_not_in_the_renderer(self):
        """A pre-registered constant written as a bare literal in a print helper is one
        edit from being changed by someone who does not know it was pre-registered."""
        from fantasy_sim.luck_ledger import MIN_WEEKS_FOR_INFERENCE
        self.assertEqual(MIN_WEEKS_FOR_INFERENCE, 6)

    def test_the_renderer_uses_the_constant_rather_than_a_literal(self):
        import inspect

        import scripts.luck_ledger as sl
        src = inspect.getsource(sl)
        self.assertNotIn("< 6", src, "the bare literal is still in the renderer")
        self.assertIn("MIN_WEEKS_FOR_INFERENCE", src)


class TestNothingElseChanged(unittest.TestCase):
    """GREEN BY DESIGN. R2 says the definitions do not move."""

    def test_a_metric_with_no_spread_still_says_so(self):
        from scripts.luck_ledger import _fmt
        line = _fmt({"delta": 0.0, "se": 0.0, "z": None}, [], "close games",
                    "close_games", 2)
        self.assertIn("no spread to test", line)

    def test_an_absent_metric_still_renders_its_reason(self):
        from scripts.luck_ledger import _fmt
        self.assertIn("WITHHELD", _fmt(None, [], "schedule luck", "schedule_luck", 2,
                                       "-- WITHHELD, the record was rewritten --"))

    def test_the_lucky_unlucky_word_is_unchanged(self):
        from scripts.luck_ledger import _fmt
        self.assertIn("unlucky", _fmt(_metric(delta=-0.46), [], "scoring luck",
                                      "scoring_luck", 2))


if __name__ == "__main__":
    unittest.main()

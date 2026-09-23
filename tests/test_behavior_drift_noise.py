"""F62: the behavioral drift check calls Monte Carlo noise an engine behavior change.

Found while regenerating the baseline for B7/B8. The check reported six drifted
mechanics and its message says, flatly, "an engine behavior change". Two of them:

    faab_spent   665.60 -> 641.14
    bid_mean       6.025 ->  5.895

Neither is a behavior change. `_compute_faab_bid` takes (remaining_faab, an
externally-drawn standard normal, a 2025-derived aggression multiplier, the league
average) and reads NOTHING that B7 or B8 touched -- not a baseline, not a variance, not a
replacement level. What changed is the PHASE of the shared numpy stream: B7 and B8 alter
how many draws the score sampler consumes upstream, so every later draw is a different
sample of the same distribution.

MEASURED, over the same 30 seasons the check itself runs (3,263 bids):

    per-season faab_spent   sd 72.86   SE 13.30   (+/- 2.1% of the mean)
    bid_mean                sd  8.969  SE  0.157

    faab_spent shift 24.46 = 1.84 SE
    bid_mean   shift  0.130 = 0.83 SE

So the check's rel_tol of 0.02 is SMALLER THAN ONE STANDARD ERROR on `faab_spent`. It
will report drift on essentially any MAJOR that re-phases the stream, and the report will
call that drift a behavior change.

WHY THIS MATTERS RATHER THAN BEING PEDANTRY. The docstring's premise -- "the run is
deterministic; the tolerance only absorbs float formatting" -- is true for a FIXED model
and false across the exact event the check exists to police. A check that cries drift on
every MAJOR teaches itself to be ignored, which is the failure mode CLAUDE.md names when
it explains why the release reminder is not a commit-time gate.

WHAT IS NOT CLAIMED. This does not say the six drifts are all noise; it says the check
cannot tell, and currently asserts otherwise. Attributing each one needs a per-metric
noise scale the harness does not compute. The fix here is honesty in the report, not a
re-tuned tolerance -- raising rel_tol past one SE would hide real changes of the size
these constants actually make.

Written before the change and confirmed failing (rule 1).
"""
import unittest

from fantasy_sim.behavior_check import compare_to_baseline, render_report


BASE = {"scenario": "week01", "n_sims": 30, "faab_spent": 665.60, "bid_mean": 6.025,
        "waiver_claims": 108.77, "trade_offer_events": 5.17}
CUR = dict(BASE, faab_spent=641.14, bid_mean=5.895)


def _report():
    drifted = compare_to_baseline(CUR, BASE)
    return render_report(dict(CUR, trade_completions=0.0, bid_median=3.0, bid_p95=21.1,
                              early_claim_share=0.24, lineup_changes_mean=2.17,
                              lineup_zero_share=0.084), drifted, True)


class TestTheDriftMessageDoesNotAssertCausation(unittest.TestCase):
    def test_it_still_detects_the_drift(self):
        """The detection is not what is wrong -- the explanation is."""
        got = {k for k, _, _ in compare_to_baseline(CUR, BASE)}
        self.assertEqual(got, {"faab_spent", "bid_mean"})

    def test_the_report_no_longer_calls_drift_an_engine_behavior_change(self):
        text = _report()
        self.assertNotIn("an engine behavior change", text,
                         "the check cannot distinguish a behavior change from a "
                         "re-phased RNG stream, and must not claim it can")

    def test_the_report_names_the_monte_carlo_alternative(self):
        text = _report().lower()
        self.assertIn("monte carlo", text)
        self.assertIn("standard error", text)

    def test_the_report_gives_the_measured_noise_scale(self):
        """A caveat with no number is a caveat nobody can act on."""
        self.assertIn("2.1%", _report(),
                      "name the measured SE on faab_spent so a reader can size the drift")

    def test_no_such_note_when_there_is_no_drift(self):
        text = render_report(dict(CUR, trade_completions=0.0, bid_median=3.0, bid_p95=21.1,
                                  early_claim_share=0.24, lineup_changes_mean=2.17,
                                  lineup_zero_share=0.084), [], True)
        self.assertIn("No drift", text)
        self.assertNotIn("standard error", text.lower())


class TestTheToleranceIsNotQuietlyRaised(unittest.TestCase):
    """F62's trap. Raising rel_tol past one SE would silence the noise AND any real
    change of the size these constants make. The fix is the report, not the threshold."""

    def test_rel_tol_is_still_two_percent(self):
        import inspect
        sig = inspect.signature(compare_to_baseline)
        self.assertAlmostEqual(sig.parameters["rel_tol"].default, 0.02)

    def test_the_docstring_no_longer_claims_the_tolerance_only_absorbs_formatting(self):
        self.assertNotIn("only absorbs float formatting", compare_to_baseline.__doc__ or "")


if __name__ == "__main__":
    unittest.main()

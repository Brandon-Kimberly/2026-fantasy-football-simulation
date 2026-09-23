"""B1: the IDP epistemic rate makes the Bayesian blend nearly inert for three slots.

`EPISTEMIC_ERROR_RATES` sets DL/LB/DB to **0.15** against QB 0.30, RB 0.63, WR 0.55.
`_apply_bayesian_updates` is precision-weighted -- `prior_var = std_epistemic ** 2` -- so a
small rate makes the prior *very* confident and observed games barely move it.

**THIS PINS A MECHANISM, NOT A BUG.** B1 is explicit that the precision weighting is
CORRECT: two wildly different scores genuinely are weak evidence. The open question is
only whether the prior's stated confidence is calibrated for IDP, and that is a
measurement, not an assertion. These tests exist so that if the constant later moves, the
review shows exactly what moved and by how much -- and so nobody "fixes" the mechanism by
mistake while trying to change the number.

**What the mechanism does, reproduced from the real arithmetic:**

    same two observed games [16.5, 41.5], same prior 11.7
      WR       rate 0.55   posterior 13.72
      RB       rate 0.63   posterior 14.26
      QB       rate 0.30   posterior 12.36
      LB/DL/DB rate 0.15   posterior 11.87    <- about 30% of a WR's movement

Which is why on 2026-09-22 three real IDP players with genuinely loud two-game samples
moved by hundredths while a QB with a QUIETER sample moved by three full points. The model
learns about quarterbacks and does not learn about linebackers.

**NOT ASSERTED HERE, because it is not known:** that 0.15 is wrong. `CLAUDE.md` records
the IDP constants as "less rigorously sourced", which is a statement about provenance, not
about error. B1's own trap: *"Do NOT raise the rate because it feels low. That is exactly
what rule 5 forbids."* The measurement is a separate piece of work and its result governs
whether anything changes.

**One guard is about a neighbouring constant, deliberately.** `actual_var` has a floor of
`0.5 * prior_var`. With a tiny `prior_var` that floor is tiny too, so it is tempting to
raise the floor as a proxy for the rate. B1 forbids that, and a test now says so.

Written against current behaviour and passing: this is a CHARACTERISATION, which is what
B1 scope step 1 asks for, not a regression test.
"""
import math
import unittest

from fantasy_sim.config import EPISTEMIC_ERROR_RATES

N_0 = 4.0          # simulation.py: the prior is worth four games


def posterior(prior_mean, rate, scores):
    """`_apply_bayesian_updates` transcribed, and ONLY for reasoning about the mechanism.

    A transcription verifies the transcription, so the engine test below runs the REAL
    blend on a mock filesystem and requires this to agree with it.
    """
    n = len(scores)
    prior_var = max(0.1, (rate * prior_mean) ** 2)
    raw_actual_var = (sum((s - sum(scores) / n) ** 2 for s in scores) / n) if n > 1 else prior_var
    actual_var = max(raw_actual_var, 0.5 * prior_var)
    post_var = 1.0 / ((N_0 / prior_var) + (n / actual_var))
    return ((N_0 * prior_mean / prior_var) + (n * (sum(scores) / n) / actual_var)) * post_var


LOUD = [16.5, 41.5]        # the real Devin Lloyd two-game sample
PRIOR = 11.7


class TestTheRatesAsConfigured(unittest.TestCase):
    def test_idp_is_a_quarter_of_the_least_trusting_offensive_rate(self):
        for pos in ("DL", "LB", "DB"):
            self.assertAlmostEqual(EPISTEMIC_ERROR_RATES[pos], 0.15)
        self.assertLess(EPISTEMIC_ERROR_RATES["DL"], EPISTEMIC_ERROR_RATES["QB"])
        self.assertAlmostEqual(EPISTEMIC_ERROR_RATES["RB"] / EPISTEMIC_ERROR_RATES["DL"],
                               4.2, places=6)


class TestTheMechanism(unittest.TestCase):
    """The arithmetic B1 reproduced, pinned so a later change is reviewable."""

    def test_an_idp_moves_about_a_third_as_far_as_a_wr_on_identical_evidence(self):
        wr = posterior(PRIOR, EPISTEMIC_ERROR_RATES["WR"], LOUD)
        idp = posterior(PRIOR, EPISTEMIC_ERROR_RATES["DL"], LOUD)
        self.assertAlmostEqual(wr, 13.72, places=2)
        self.assertAlmostEqual(idp, 11.87, places=2)
        self.assertLess((idp - PRIOR) / (wr - PRIOR), 0.10,
                        "the IDP posterior barely leaves its prior on the same evidence")

    def test_a_smaller_rate_always_means_a_stiffer_prior(self):
        """Monotone, so the direction of any future change is unambiguous."""
        moves = [posterior(PRIOR, r, LOUD) - PRIOR for r in (0.10, 0.15, 0.30, 0.55, 0.63)]
        self.assertEqual(moves, sorted(moves))

    def test_the_idp_posterior_is_within_two_percent_of_its_prior(self):
        idp = posterior(PRIOR, EPISTEMIC_ERROR_RATES["LB"], LOUD)
        self.assertLess(abs(idp - PRIOR) / PRIOR, 0.02,
                        "'nearly inert' is the claim; this is the number behind it")

    def test_a_consistent_sample_does_not_rescue_it(self):
        """B1's second block: even a quiet, agreeing sample moves an IDP less than a QB."""
        quiet = [22.7, 29.0]
        qb = posterior(19.6, EPISTEMIC_ERROR_RATES["QB"], quiet)
        idp = posterior(19.6, EPISTEMIC_ERROR_RATES["LB"], quiet)
        self.assertGreater(qb - 19.6, (idp - 19.6) * 1.5)


class TestTheTranscriptionMatchesTheEngine(unittest.TestCase):
    """Rule 2: the helper above is a transcription, and a transcription proves nothing
    about the engine unless they are shown to agree."""

    def test_the_real_blend_reproduces_the_helper(self):
        import logging
        from unittest.mock import patch
        from fantasy_sim.simulation import FantasySimulationEngine
        from fantasy_sim.storage import (
            LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE,
            BASELINES_FILE, TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE,
            DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE, NFL_SCHEDULE_FILE,
            WEEKLY_ACTUALS_FILE,
        )
        teams = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis"]
        filler = [("QB", 18.0), ("RB", 17.0), ("RB", 15.0), ("WR", 16.0), ("WR", 13.0),
                  ("TE", 12.0), ("K", 9.0), ("DL", 9.0), ("LB", 9.0), ("DB", 9.0),
                  ("RB", 11.0), ("WR", 10.5), ("WR", 10.0)]
        base, rosters = {}, {}
        for t in teams:
            entries = []
            for i, (pos, mu) in enumerate(filler):
                n = f"{t[:2]}_{pos}_{i}"
                base[n] = {"mean": mu, "std_aleatoric": 5.0,
                           "std_epistemic": EPISTEMIC_ERROR_RATES[pos] * mu,
                           "pos": pos, "team": "CHI", "bye": 0}
                entries.append({"name": n, "pos": pos, "team": "CHI"})
            rosters[t] = entries
        # One LB and one WR, identical prior, identical observed games.
        for name, pos in (("Test LB", "LB"), ("Test WR", "WR")):
            base[name] = {"mean": PRIOR, "std_aleatoric": 5.0,
                          "std_epistemic": EPISTEMIC_ERROR_RATES[pos] * PRIOR,
                          "pos": pos, "team": "DET", "bye": 0}
            rosters[teams[0]].append({"name": name, "pos": pos, "team": "DET"})

        weekly = {}
        for wk, pts in enumerate(LOUD, start=1):
            weekly[f"week_{wk}"] = {
                "median_cutoff": 100.0,
                "team_results": {t: {"points_scored": 100.0, "h2h_win": 0.5,
                                     "median_win": 1, "remaining_faab": 100} for t in teams},
                "player_scores": {"Test LB": pts, "Test WR": pts},
            }
        fs = {
            LEAGUE_STATE_FILE: {"current_week": 3},
            LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in teams},
            VEGAS_FILE: {"_meta": {"week": 3, "source": "odds_api", "fetched_at": "x"},
                         "DET": {"total": 22.0, "spread": 0.0, "opponent": "CHI"},
                         "CHI": {"total": 22.0, "spread": 0.0, "opponent": "DET"}},
            LIVE_ROSTERS_FILE: rosters, BASELINES_FILE: base,
            TEAM_RATINGS_FILE: {"DET": {"off_rating": 22}, "CHI": {"off_rating": 22}},
            DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                     "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
            DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
            LEAGUE_SCHEDULE_FILE: [[[teams[0], teams[1]], [teams[2], teams[3]]]] * 14,
            NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
            WEEKLY_ACTUALS_FILE: weekly,
        }
        prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        with patch('os.path.exists', side_effect=lambda p: p in fs), \
             patch('fantasy_sim.simulation.load_json', side_effect=lambda p: fs[p]):
            e = FantasySimulationEngine()
        logging.getLogger().setLevel(prev)

        self.assertAlmostEqual(e.baselines["Test LB"]["mean"],
                               posterior(PRIOR, EPISTEMIC_ERROR_RATES["LB"], LOUD), places=6)
        self.assertAlmostEqual(e.baselines["Test WR"]["mean"],
                               posterior(PRIOR, EPISTEMIC_ERROR_RATES["WR"], LOUD), places=6)


class TestTheFloorIsNotTheLeverToPull(unittest.TestCase):
    """B1's trap: `actual_var` is floored at `0.5 * prior_var`, and with a tiny prior_var
    that floor is tiny too. It is tempting to raise the floor as a proxy for the rate.
    Do not -- the floor governs how much a NOISY sample is trusted, which is a different
    question from how confident the prior is."""

    def test_the_floor_is_still_half_the_prior_variance(self):
        import inspect
        from fantasy_sim.simulation import FantasySimulationEngine
        src = inspect.getsource(FantasySimulationEngine._apply_bayesian_updates)
        self.assertIn("max(raw_actual_var, 0.5 * prior_var)", src)

    def test_the_prior_is_still_worth_four_games(self):
        import inspect
        from fantasy_sim.simulation import FantasySimulationEngine
        src = inspect.getsource(FantasySimulationEngine._apply_bayesian_updates)
        self.assertIn("n_0 = 4.0", src)

    def test_the_loud_sample_is_above_the_floor_so_the_rate_is_what_binds(self):
        """If the floor were binding, changing the rate would not be the lever at all."""
        n = len(LOUD)
        raw = sum((s - sum(LOUD) / n) ** 2 for s in LOUD) / n
        prior_var = (EPISTEMIC_ERROR_RATES["LB"] * PRIOR) ** 2
        self.assertGreater(raw, 0.5 * prior_var)
        self.assertGreater(math.sqrt(raw), 0.0)


if __name__ == "__main__":
    unittest.main()

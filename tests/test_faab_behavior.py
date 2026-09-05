"""F31 behavioral fix: the FAAB bid curve, the upgrade-claim rate, and the two-parameter
per-manager profile blend (2025 prior updating from the decision log).

Written BEFORE the implementation (rule 1). The design under test:
  - bid sizes ~ lognormal(mu=1.423, sigma=1.120) fitted to the 99 real 2025 bids
    (median 4.15 vs real 4.0, mean 7.77 vs 7.35, p95 26 vs 21) x aggression, capped by
    remaining budget and the competitive ceiling (deflation removed -- F31: real 2025
    shows no proportional cooling);
  - an upgrade-bidding channel at a week-profiled residual rate x per-manager activity;
  - MANAGER_PROFILES' faab_agg/faab_activity as 2025-derived PRIORS blended with this
    season's attributed claims from the decision log (prior worth ~one season, weight 12).
"""
import math
import unittest

from fantasy_sim.config import (FAAB_BID_LOGNORMAL_MU, FAAB_LEAGUE_MEAN_BID_2025, MANAGER_PROFILES)
from fantasy_sim.simulation import (FantasySimulationEngine, blend_faab_profiles,
                                    _upgrade_claim_rate)


class TestBidCurve(unittest.TestCase):
    def test_median_bid_matches_the_fitted_real_median(self):
        # z = 0 is the lognormal median: exp(mu) ~ 4.15, the real league's median bid of 4
        bid = FantasySimulationEngine._compute_faab_bid(100.0, 0.0, 1.0, 100.0)
        self.assertAlmostEqual(bid, math.exp(FAAB_BID_LOGNORMAL_MU), places=6)

    def test_the_conviction_tail_exists(self):
        # a +2 sigma draw at neutral aggression lands in the real tail (real p95 ~ 21, max 39)
        bid = FantasySimulationEngine._compute_faab_bid(100.0, 2.0, 1.0, 100.0)
        self.assertGreater(bid, 20.0)
        self.assertLess(bid, 100.0)

    def test_scales_with_aggression_and_respects_budget_and_ceiling(self):
        lo = FantasySimulationEngine._compute_faab_bid(100.0, 0.0, 0.5, 100.0)
        hi = FantasySimulationEngine._compute_faab_bid(100.0, 0.0, 1.5, 100.0)
        self.assertAlmostEqual(hi / lo, 3.0, places=6)
        self.assertLessEqual(FantasySimulationEngine._compute_faab_bid(3.0, 4.0, 2.0, 100.0), 3.0)
        self.assertLessEqual(FantasySimulationEngine._compute_faab_bid(500.0, 4.0, 2.0, 40.0),
                             40.0 * 1.5 + 1e-9)


class TestUpgradeClaimRate(unittest.TestCase):
    def test_front_loaded_profile_scaled_by_activity(self):
        # early weeks bid more than late (real: 1.22 vs 0.68 claims/team-week, of which
        # the upgrade channel carries the residual); activity scales linearly
        self.assertGreater(_upgrade_claim_rate(2, 1.0), _upgrade_claim_rate(9, 1.0))
        self.assertAlmostEqual(_upgrade_claim_rate(9, 2.0), 2 * _upgrade_claim_rate(9, 1.0))
        self.assertEqual(_upgrade_claim_rate(9, 0.0), 0.0)


class TestProfileBlend(unittest.TestCase):
    PRIORS = {"A": {"faab_agg": 1.36, "faab_activity": 0.81},
              "B": {"faab_agg": 0.72, "faab_activity": 1.54}}

    def test_no_observations_returns_the_priors(self):
        out = blend_faab_profiles(self.PRIORS, {})
        self.assertEqual(out["A"]["faab_agg"], 1.36)
        self.assertEqual(out["B"]["faab_activity"], 1.54)

    def test_observations_pull_aggression_toward_observed_with_decaying_prior_weight(self):
        # team A bids at twice the 2025 league mean; with n = prior_weight the blend
        # should land halfway between prior and observed
        obs = {"A": [2 * FAAB_LEAGUE_MEAN_BID_2025] * 12}
        out = blend_faab_profiles(self.PRIORS, obs, prior_weight=12)
        self.assertAlmostEqual(out["A"]["faab_agg"], (1.36 + 2.0) / 2, places=6)
        self.assertEqual(out["B"]["faab_agg"], 0.72, "unobserved team keeps its prior")

    def test_unknown_team_in_observations_is_ignored(self):
        out = blend_faab_profiles(self.PRIORS, {"Nobody": [10.0]})
        self.assertEqual(set(out), {"A", "B"})


class TestDerivedProfileValues(unittest.TestCase):
    def test_profiles_carry_both_2025_derived_parameters(self):
        """Every team carries faab_agg AND faab_activity. The seven MEASURED profiles are
        ~1.0-centered (the 2025 league means); Quantum Ferrets is exempt from the centering
        check because it carries the owner's DECLARED 2026 strategy, not the measured
        prior (see the config comment). The old guessed 0-1 faab_agg scale is gone."""
        self.assertEqual(len(MANAGER_PROFILES), 8)
        measured = {t: p for t, p in MANAGER_PROFILES.items() if t != "Quantum Ferrets"}
        aggs = [p["faab_agg"] for p in measured.values()]
        acts = [p["faab_activity"] for p in measured.values()]
        self.assertAlmostEqual(sum(acts) / len(acts), 1.0, delta=0.06)
        self.assertAlmostEqual(sum(aggs) / len(aggs), 1.0, delta=0.15)
        for key in ("faab_agg", "faab_activity"):
            self.assertIn(key, MANAGER_PROFILES["Quantum Ferrets"])
        # the measured extremes, as derived from the 99 attributed claims
        self.assertGreater(MANAGER_PROFILES["Cosmic Badgers"]["faab_agg"], 1.5)
        self.assertLess(MANAGER_PROFILES["Cosmic Badgers"]["faab_activity"], 0.5)


class TestReadObservationsDuplicateTolerance(unittest.TestCase):
    def test_a_duplicated_waiver_row_counts_one_bid_not_two(self):
        """First-row-wins on transaction_id (tier 1.5 union-merge tolerance,
        2026-09-04): a double-counted claim would shift the F31 blend at engine init."""
        import json, os, tempfile
        from fantasy_sim.simulation import read_faab_observations
        tx = {"transaction_id": "w1", "type": "waiver", "week": 1,
              "teams": ["Quantum Ferrets"], "faab_bid": 9.0}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "decision_log.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(tx) + chr(10)); f.write(json.dumps(tx) + chr(10))
            obs = read_faab_observations(log_path=path)
        self.assertEqual(obs, {"Quantum Ferrets": [9.0]})

"""
tests.test_distributions

AUDIT_PLAN.md Phase 2 -- statistical core.

    Invariant: the sampler draws from the distribution it claims to.

HOW THESE TESTS REACH THE SAMPLER
---------------------------------
The per-player weekly draw (lognormal base, correlated z via the Cholesky factor, the
shared_z game-script mix, env_var, script_mult, the cap) is inline in run_simulation with no
seam of its own. Transcribing those formulas into a test would verify the transcription, not
the engine. So wherever a property can be observed through production code it is:

  - `ControlledSeason` runs the REAL run_simulation on a roster designed so that the team
    total is an analytically tractable sum: identical players, no injuries, no byes, no
    trades, no FAAB, no correlation (all FA). Its mean and variance are then closed-form,
    and epistemic structure shows up as within-season correlation of weekly scores.
  - Covariance tests call the real build_covariance_matrix. The Bayesian tests call the real
    _apply_bayesian_updates through the same mock-filesystem pattern test_simulation.py uses.
  - The environment tests call the real _compute_future_week_matchup_environment on the
    committed week01 fixture's real power and defensive ratings.

Seven tests fail. Each characterises a defect recorded in AUDIT_PHASE_2_FINDINGS.md; none is
a fix. Passing tests lock the properties that were verified to hold.

WHAT IS NOT COVERED (stated, per CLAUDE.md rule 2)
--------------------------------------------------
1. The variance budget under the REAL schedule (+17% per-player weekly variance against
   std_aleatoric^2, measured in the findings) is reported, not asserted. Its root cause -- the
   environment multiplier is not mean-preserving -- is asserted by
   TestEnvironmentModel.test_environment_multiplier_is_mean_preserving_over_the_schedule.
2. The cap's interaction with the tails is measured in the findings (max exceedance 4.3e-3,
   mean loss <= 0.06 pts/week) and already bounded by two tests in test_simulation.py; nothing
   new is added.
3. Batch seed independence (sequential np.random.seed) is Phase 0's item and is not tested
   here.
"""
import logging
import unittest
from unittest.mock import patch

import numpy as np

from fantasy_sim.config import NFL_TEAMS, SIM_CONFIG, REGULAR_SEASON_WEEKS
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)
from tests.golden_master import STAGE_A_ARG_NAMES, _sandbox

ENV_NOISE_SD = 0.10          # env_var ~ N(v_tot / env_norm, 0.10), inline in run_simulation
# In a controlled season every player is on team FA, so every environment is the 21.5
# fallback, the normaliser (mean implied total over the simulated schedule) is 21.5, and the
# environment multiplier is exactly 1. Before the finding-1 fix it was 21.5 / 22.0 = 0.977.
CONTROLLED_ENV_MULTIPLIER = 1.0

TEAMS = ["A", "B", "C", "D", "E", "F", "G", "H"]
SLOT_POSITIONS = ["QB", "K", "DB", "DL", "LB", "RB", "RB", "RB", "WR", "WR", "WR", "TE", "TE"]


# ------------------------------------------------------------------ controlled real engine
def controlled_season(mean, std_aleatoric, std_epistemic, sims, roster_override=None,
                      vegas=None):
    """Runs the REAL run_simulation on a tractable league and returns the pooled weekly team
    score matrix, shape (sims * 8, 14).

    Every team gets the same 13-man roster of independent players (all on team 'FA', so
    build_covariance_matrix returns the identity), injuries are switched off, manager
    profiles are zeroed so no trades or FAAB bids happen, and there are no byes or completed
    weeks. The weekly team score is then a sum of 13 iid draws with closed-form moments."""
    if roster_override is None:
        roster = {t: [{"name": "%s_%d" % (t, i), "pos": SLOT_POSITIONS[i], "team": "FA"}
                      for i in range(13)] for t in TEAMS}
    else:
        roster = roster_override
    baselines = {}
    for t in TEAMS:
        for p in roster[t]:
            baselines[p["name"]] = {
                "mean": mean, "std_aleatoric": std_aleatoric, "std_epistemic": std_epistemic,
                "pos": p["pos"], "team": p["team"],
            }
    fs = {
        LEAGUE_STATE_FILE: {"current_week": 1},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: vegas or {},
        LIVE_ROSTERS_FILE: roster,
        BASELINES_FILE: baselines,
        TEAM_RATINGS_FILE: {},
        DEFENSIVE_RATINGS_FILE: {},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[["A", "B"], ["C", "D"], ["E", "F"], ["G", "H"]]] * REGULAR_SEASON_WEEKS,
        NFL_SCHEDULE_FILE: {},
        WEEKLY_ACTUALS_FILE: {},
    }
    captured = {}

    def capture(engine, *args):
        captured.update(zip(STAGE_A_ARG_NAMES, args))

    no_injury = {k: 0.0 for k in SIM_CONFIG["INJURY_RATES"]}
    passive = {t: {"faab_agg": 0.0, "trade_will": 0.0} for t in TEAMS}
    prev_level = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.ERROR)
    orig = SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"]
    SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = 1, sims
    try:
        with patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]), \
             patch.dict(SIM_CONFIG["INJURY_RATES"], no_injury), \
             patch.dict("fantasy_sim.simulation.MANAGER_PROFILES", passive, clear=True), \
             patch.object(FantasySimulationEngine, "export_and_visualize", capture):
            FantasySimulationEngine().run_simulation()
    finally:
        SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = orig
        logging.getLogger().setLevel(prev_level)
    return np.vstack([captured["global_weekly_scores"][t] for t in TEAMS])


def player_weekly_var(mean, std_a, e_mu=CONTROLLED_ENV_MULTIPLIER):
    """Var[base * env] for one player: base ~ lognormal(E=mean, Var=std_a^2),
    env ~ N(e_mu, 0.10) independent.
    Var[BE] = Var[B]Var[E] + Var[B]E[E]^2 + E[B]^2 Var[E]."""
    e_var = ENV_NOISE_SD ** 2
    return std_a ** 2 * (e_var + e_mu ** 2) + mean ** 2 * e_var


class TestWeeklyDrawMoments(unittest.TestCase):
    """The plan's lognormal question, asked of the real engine: after the
    mu = log(mean) - sigma^2/2 correction, does E[score] land on the intended mean, and does
    the variance land on std_aleatoric^2 (times the environment factors)?"""

    MEAN, STD_A, SIMS = 12.0, 5.0, 300

    @classmethod
    def setUpClass(cls):
        cls.W = controlled_season(cls.MEAN, cls.STD_A, std_epistemic=0.0, sims=cls.SIMS)

    def test_team_weekly_mean_equals_thirteen_times_the_environment_scaled_player_mean(self):
        """13 players x mean x 1. Every player is FA so the environment multiplier is exactly
        1 (see CONTROLLED_ENV_MULTIPLIER); the lognormal identity is what is under test."""
        expected = 13 * self.MEAN * CONTROLLED_ENV_MULTIPLIER
        se = self.W.std() / np.sqrt(self.W.size)
        self.assertAlmostEqual(float(self.W.mean()), expected, delta=5 * se + 0.05,
                               msg="E[team score] %.3f vs analytic %.3f (SE %.3f): the "
                                   "lognormal mean correction is not landing on mean_val"
                                   % (self.W.mean(), expected, se))

    def test_team_weekly_variance_matches_the_stacked_lognormal_times_env_model(self):
        """With epistemic off and no correlation, Var[team] = 13 * Var[base * env]."""
        expected = 13 * player_weekly_var(self.MEAN, self.STD_A)
        observed = float(self.W.var())
        # SE of a sample variance ~ var * sqrt(2 / n)
        se = observed * np.sqrt(2.0 / self.W.size)
        self.assertAlmostEqual(observed, expected, delta=5 * se,
                               msg="Var[team score] %.2f vs analytic %.2f (SE %.2f)"
                                   % (observed, expected, se))


class TestEpistemicStructure(unittest.TestCase):
    """CLAUDE.md: epistemic variance is drawn once per simulated season and held; aleatoric
    is redrawn weekly. If that holds, (a) a player's 14 weeks share a season-level shift, so
    weekly scores are positively correlated WITHIN a season, and (b) Var[season total] / 14
    exceeds Var[week] by the held component. If it were redrawn weekly, both vanish; if it
    were drawn once per batch, (a) holds but every season in the batch shares it."""

    MEAN, STD_A, STD_E, SIMS = 12.0, 5.0, 3.0, 300

    @classmethod
    def setUpClass(cls):
        cls.W_on = controlled_season(cls.MEAN, cls.STD_A, cls.STD_E, sims=cls.SIMS)
        cls.W_off = controlled_season(cls.MEAN, cls.STD_A, 0.0, sims=cls.SIMS)

    @staticmethod
    def within_season_corr(W):
        C = np.corrcoef(W.T)
        return float(C[~np.eye(W.shape[1], dtype=bool)].mean())

    def test_epistemic_draw_is_held_within_a_season(self):
        e_mu = CONTROLLED_ENV_MULTIPLIER
        held_var = 13 * self.STD_E ** 2 * e_mu ** 2          # season-level shift, 13 players
        week_var = 13 * player_weekly_var(self.MEAN, self.STD_A)
        predicted_corr = held_var / (held_var + week_var)
        observed = self.within_season_corr(self.W_on)
        self.assertGreater(observed, 0.6 * predicted_corr,
                           msg="within-season weekly correlation %.3f, predicted %.3f under a "
                               "held-per-season draw; near 0 would mean epistemic is being "
                               "redrawn weekly" % (observed, predicted_corr))
        self.assertLess(observed, 1.4 * predicted_corr)

    def test_epistemic_off_removes_within_season_correlation(self):
        observed = self.within_season_corr(self.W_off)
        self.assertLess(abs(observed), 0.02,
                        msg="with std_epistemic=0 weekly scores are correlated %.4f within a "
                            "season; something other than epistemic is being held" % observed)

    def test_epistemic_widens_season_totals_not_just_weeks(self):
        """Var[season]/14 - Var[week] isolates the held component: it is 13 * (14-1) * ... no --
        for a sum of 14 weeks sharing a shift s per player: Var[sum] = 14 Var[week] +
        14 * 13 * Var[s] per player, so Var[sum]/14 - Var[week] = 13 * Var[s] summed over
        the 13 players."""
        e_mu = CONTROLLED_ENV_MULTIPLIER
        predicted_excess = 13 * 13 * self.STD_E ** 2 * e_mu ** 2
        season = self.W_on.sum(axis=1)
        excess = season.var() / REGULAR_SEASON_WEEKS - self.W_on.var()
        self.assertGreater(excess, 0.6 * predicted_excess,
                           msg="season-level excess variance %.1f vs predicted %.1f"
                               % (excess, predicted_excess))
        self.assertLess(excess, 1.4 * predicted_excess)
        excess_off = self.W_off.sum(axis=1).var() / REGULAR_SEASON_WEEKS - self.W_off.var()
        self.assertLess(abs(excess_off), 0.1 * predicted_excess)


# ------------------------------------------------------------------------------ covariance
def _bare_engine(pass_catchers):
    engine = FantasySimulationEngine.__new__(FantasySimulationEngine)
    engine.pass_catchers_meta = pass_catchers
    return engine


class TestCovarianceMatrix(unittest.TestCase):
    def test_cholesky_factor_is_finite_and_psd_for_random_rosters(self):
        """3000 random rosters over every position, same-team clusters up to 25 deep, FA and
        None teams, and consistent pass-catcher rankings. Must never raise and must always
        reconstruct to a PSD matrix."""
        rng = np.random.default_rng(11)
        positions = ["QB", "RB", "WR", "TE", "K", "DL", "LB", "DB"]
        teams = ["DET", "DET", "DET", "KC", "FA", None, "BUF"]
        for trial in range(3000):
            n = int(rng.integers(1, 26))
            players = ["P%d_%d" % (trial, i) for i in range(n)]
            meta = {p: {"pos": str(rng.choice(positions)), "team": rng.choice(teams)} for p in players}
            pc = {}
            for p in players:
                if meta[p]["team"] and meta[p]["pos"] in ("WR", "TE"):
                    pc.setdefault(meta[p]["team"], []).append((p, float(rng.uniform(1, 20))))
            for t in pc:
                pc[t].sort(key=lambda x: -x[1])
            L = _bare_engine(pc).build_covariance_matrix(players, meta)
            self.assertTrue(np.all(np.isfinite(L)), "non-finite Cholesky factor on trial %d" % trial)
            self.assertGreater(float(np.min(np.linalg.eigvalsh(L @ L.T))), -1e-9,
                               "L L^T not PSD on trial %d" % trial)

    def test_psd_repair_preserves_unit_marginal_variance(self):
        """Regression guard for Phase 2 finding 6. The repair branch adds (|min_eig| + 1e-4) * I;
        it used to take the Cholesky factor of that directly, so when it fired every diagonal
        entry of L L^T became 1 + delta: z_corr = L z was no longer unit-variance, every
        player's lognormal sigma on that roster was inflated by sqrt(1 + delta), and every
        effective correlation shrunk by 1/(1 + delta). It now rescales back to a correlation
        matrix. Uses the same 7-WR / rho=-0.18 scenario test_simulation.py uses to prove the
        branch fires."""
        n = 7
        players = ["WR_%d" % i for i in range(n)]
        meta = {p: {"pos": "WR", "team": "DET"} for p in players}
        pc = {"DET": [(p, 10.0 - i) for i, p in enumerate(players)]}
        with patch.dict(SIM_CONFIG["CORRELATIONS"], {"WR_WR": -0.18}):
            L = _bare_engine(pc).build_covariance_matrix(players, meta)
        diag = np.diag(L @ L.T)
        self.assertTrue(np.allclose(diag, 1.0, atol=1e-6),
                        msg="after PSD repair the marginal variances are %s, not 1: the "
                            "correlated z passed to every player on this roster has sd %.3f"
                            % (np.round(diag, 4), np.sqrt(diag[0])))

    def test_qb_correlation_is_monotone_in_pass_catcher_rank(self):
        """Regression guard for Phase 2 finding 7. Rank among the team's pass-catchers used
        to decide everything: rank 0 -> QB_WR1 (0.40), rank 1 -> QB_WR2 (0.315), and
        everything else -- WR3, WR4, ... as well as the TE -- fell through to QB_TE (0.35),
        so a team's third and fourth receivers were modelled as MORE correlated with their
        QB than the second. Now TEs always take QB_TE and WRs are ranked among WRs only,
        with WR3+ carrying WR2's value as an (unverified) ceiling."""
        players = ["QB", "WR1", "WR2", "WR3", "WR4"]
        meta = {p: {"pos": "QB" if p == "QB" else "WR", "team": "DET"} for p in players}
        pc = {"DET": [("WR1", 14.0), ("WR2", 11.0), ("WR3", 8.0), ("WR4", 5.0)]}
        L = _bare_engine(pc).build_covariance_matrix(players, meta)
        corr = L @ L.T
        qb = [corr[0, i] for i in range(1, 5)]
        for i in range(1, 4):
            self.assertLessEqual(qb[i], qb[i - 1] + 1e-12,
                                 msg="QB correlation by receiver rank is %s -- not monotone"
                                     % np.round(qb, 3))


# ------------------------------------------------------------------- shared game-script z
class TestSharedGameScript(unittest.TestCase):
    """Regression guards for Phase 2 finding 2. run_simulation used to give every QB/WR/TE
    z = 0.8 * z_corr + 0.6 * shared_z (one N(0,1) per NFL game) whenever total + spread > 23.
    That kept each marginal at unit variance but added 0.36 correlation between EVERY pair of
    qualifying pass-catchers on the team, on top of the copula -- overriding the calibrated
    SIM_CONFIG['CORRELATIONS'] for 44% of team-weeks. The mix has been removed; the copula is
    the one place correlation is set."""

    def test_total_plus_spread_is_the_opponents_implied_total(self):
        """Pins a property of the environment model that the removed gate silently depended
        on: total + spread is identically the OPPONENT's implied total, for Vegas weeks and
        the model's future weeks alike. Any future 'high-scoring game' condition built on
        these fields needs to know that."""
        with _sandbox("week01", 1, 1):
            with patch.object(FantasySimulationEngine, "export_and_visualize", lambda s, *a: None):
                engine = FantasySimulationEngine()
        checked = 0
        for t in NFL_TEAMS:
            v = engine.vegas.get(t)
            if not v or v.get("opponent", "FA") == "FA":
                continue
            o = engine.vegas[v["opponent"]]
            self.assertAlmostEqual(v["total"] + v["spread"], o["total"], places=6)
            checked += 1
        for wk in range(2, REGULAR_SEASON_WEEKS + 1):
            for t in NFL_TEAMS:
                opp = engine.nfl_schedule.get(str(wk), {}).get(t, "FA")
                e = engine._compute_future_week_matchup_environment(t, opp)
                if e["opponent"] == "FA":
                    continue
                eo = engine._compute_future_week_matchup_environment(opp, t)
                self.assertAlmostEqual(e["total"] + e["spread"], eo["total"], places=6)
                checked += 1
        self.assertGreater(checked, 300)

    def test_wr_wr_covariance_stays_near_its_calibrated_target_when_the_gate_fires(self):
        """SIM_CONFIG['CORRELATIONS']['WR_WR'] = -0.004 was measured on real same-team
        receiver pairs (backtest_player.analyze_correlations). Two DET receivers, nothing else
        rostered (the other 11 slots become streamers, whose variance does not depend on the
        environment), run with the opponent's implied total at 28 and at 20 -- the two sides
        of the removed gate. DET's own total is 24 in both, so the receivers' marginals are
        identical and 2 * (Cov_28 - Cov_20) must be ~0 under the calibrated target. Before
        the fix it was +57 (SE 6): the gate injected ~0.32 score correlation. Measured through
        the real run_simulation."""
        mean, std_a = 20.0, 8.0
        roster = {t: [{"name": "%s_WR1" % t, "pos": "WR", "team": "DET"},
                      {"name": "%s_WR2" % t, "pos": "WR", "team": "DET"}] for t in TEAMS}

        def season(opp_total):
            vegas = {"DET": {"total": 24.0, "spread": opp_total - 24.0, "opponent": "CHI",
                             "wind_mph": 0.0, "precip_prob": 0.0},
                     "CHI": {"total": opp_total, "spread": 24.0 - opp_total, "opponent": "DET",
                             "wind_mph": 0.0, "precip_prob": 0.0}}
            # NFL_SCHEDULE is empty in controlled_season, so weeks 2+ fall back to FA/21.5
            # for everyone; only week 1 (Vegas) carries the gate. Use week-1 scores only.
            return controlled_season(mean, std_a, 0.0, sims=600, roster_override=roster,
                                     vegas=vegas)[:, 0]

        on, off = season(28.0), season(20.0)
        # DET's own total is 24 in both runs, so the two receivers' marginal variance is the
        # same in both; only the gate differs. Two things make the comparison exact rather
        # than merely fair: (1) both runs start from np.random.seed(1000) and make identical
        # RNG calls, so every draw is paired and the difference is nearly noise-free; (2) the
        # streamer means differ BETWEEN teams (bid ordering is deterministic, so team A gets
        # the 12-point streamers and team H the 4-point ones) but are identical between runs,
        # so demeaning within team removes them from both variances alike.
        on = on.reshape(len(TEAMS), -1)
        off = off.reshape(len(TEAMS), -1)
        on_c = (on - on.mean(axis=1, keepdims=True)).ravel()
        off_c = (off - off.mean(axis=1, keepdims=True)).ravel()
        paired = on_c ** 2 - off_c ** 2           # E[paired] = Var_on - Var_off
        two_delta_cov = float(paired.mean())
        se = float(paired.std(ddof=1) / np.sqrt(paired.size))
        self.assertLess(abs(two_delta_cov), max(10.0, 4 * se),
                        msg="opening the shared_z gate raised Var[WR1 + WR2] by %.1f (SE %.1f); "
                            "the calibrated WR-WR correlation of -0.004 predicts ~0. The gate "
                            "is open for 44%% of team-weeks on the real schedule."
                            % (two_delta_cov, se))


# ------------------------------------------------------------------------ Bayesian update
class TestBayesianUpdate(unittest.TestCase):
    """_apply_bayesian_updates against the closed-form conjugate normal with known
    observation variance. The model carries exactly that known variance: std_aleatoric is
    calibrated to the empirical week-to-week std (backtest_player.analyze_aleatoric_variance).

        post_precision = 1 / std_epistemic^2 + n / std_aleatoric^2
        post_mean      = (prior / std_epistemic^2 + n * xbar / std_aleatoric^2) / post_precision

    The engine instead uses n_0 / std_epistemic^2 with n_0 = 4, and the sample variance of the
    n scores (ddof=0, floored at half the prior variance) in place of std_aleatoric^2."""

    PRIOR, STD_E, STD_A = 10.0, 5.5, 5.8

    def _engine_with_scores(self, scores):
        fs = {
            LEAGUE_STATE_FILE: {"current_week": len(scores) + 1},
            LEAGUE_STANDINGS_FILE: {"T": {"remaining_faab": 100}},
            VEGAS_FILE: {}, TEAM_RATINGS_FILE: {}, DEFENSIVE_RATINGS_FILE: {},
            DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
            LEAGUE_SCHEDULE_FILE: [], NFL_SCHEDULE_FILE: {},
            LIVE_ROSTERS_FILE: {"T": [{"name": "P", "pos": "WR", "team": "FA"}]},
            BASELINES_FILE: {"P": {"mean": self.PRIOR, "std_aleatoric": self.STD_A,
                                   "std_epistemic": self.STD_E, "pos": "WR", "team": "FA"}},
            WEEKLY_ACTUALS_FILE: {
                "week_%d" % (i + 1): {"team_results": {"T": {"points_scored": 100.0}},
                                      "player_scores": {"P": s}}
                for i, s in enumerate(scores)
            },
        }
        with patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]):
            return FantasySimulationEngine()

    def _closed_form(self, scores):
        v0, s2, n = self.STD_E ** 2, self.STD_A ** 2, len(scores)
        prec = 1.0 / v0 + n / s2
        mean = (self.PRIOR / v0 + n * float(np.mean(scores)) / s2) / prec
        return mean, float(np.sqrt(1.0 / prec))

    @unittest.expectedFailure
    def test_posterior_mean_matches_conjugate_normal(self):
        """CHARACTERISATION, deliberately still failing -- Phase 2 finding 4. The conjugate
        form was APPLIED in Phase 3 and REVERTED on real-data evidence: paired, seeded
        points-level backtest on the 2025 season moved the bias from +1.1% to +8.5% (mean z
        -0.51, ~8 SE). On real player scores the empirical data weight after five weeks is
        ~0.49 (WR 0.11), against 0.81 for the conjugate form and 0.71 for the retired one:
        weeks 6-11 run 17% below weeks 1-5 as byes/injuries accumulate (zero-week share
        9.6% -> 25.3%) and the stated prior variance does not describe rostered players.
        DEPENDENCY: bye/absence modelling (Phase 1 finding 7) and re-derived
        EPISTEMIC_ERROR_RATES (Phase 7). Re-run in bye-modelling step 5c: the conjugate form
        applies w 0.71 on real players (inside the bye-aware target 0.68 +/- 0.05) yet moves the
        real-2025 points bias +2.7% -> +10.8%, because the engine draws too little absence
        (follow-up F4). Blocked on F4; the weight criterion is already met.
        Five games averaging 14 against a prior of 10: conjugate puts 82% on the data, the
        engine 71%. Remove the expectedFailure when a replacement passes the backtest."""
        scores = [13.0, 15.0, 14.0, 13.0, 15.0]
        engine = self._engine_with_scores(scores)
        cf_mean, _ = self._closed_form(scores)
        self.assertAlmostEqual(engine.baselines["P"]["mean"], cf_mean, delta=0.1,
                               msg="engine posterior mean %.3f vs conjugate %.3f"
                                   % (engine.baselines["P"]["mean"], cf_mean))

    @unittest.expectedFailure
    def test_posterior_std_matches_conjugate_normal(self):
        """CHARACTERISATION, deliberately still failing -- Phase 2 finding 4, applied in
        Phase 3 and reverted on real-data evidence (see the sibling test's docstring for the
        numbers, the step-5c re-run and the dependency on F4). The posterior std is what feeds
        the once-per-season epistemic draw, so an over-confident posterior narrows every
        downstream season distribution. The engine's posterior std is ~0.63x the conjugate
        value here. Remove the expectedFailure when a replacement passes the backtest."""
        scores = [13.0, 15.0, 14.0, 13.0, 15.0]
        engine = self._engine_with_scores(scores)
        _, cf_std = self._closed_form(scores)
        self.assertAlmostEqual(engine.baselines["P"]["std_epistemic"], cf_std, delta=0.15,
                               msg="engine posterior std %.3f vs conjugate %.3f"
                                   % (engine.baselines["P"]["std_epistemic"], cf_std))

    def test_zero_score_weeks_are_not_treated_as_observed_performance(self):
        """GUARD for Phase 2 finding 5 -- fixed in bye-modelling step 5b.

        A weekly score of exactly 0.0 is a bye or a DNP, and
        backtest_player.collect_real_player_weekly_scores excludes them for that reason.
        _apply_bayesian_updates ingests them as games: two real games of 12 and 13 against a
        prior of 10, plus one 0.0, pull the posterior BELOW the prior. 20 of the 780
        player-weeks in the week06 fixture are exactly 0.0.

        The fix (skip zeros) was applied in Phase 2 and reverted on evidence: with byes
        unmodelled, these zeros were the only absence signal the posterior saw, and
        excluding them made simulated weekly team points +4.3% high against the real 2025
        season. Byes are modelled now (bye-modelling steps 1-4), and with them alone the
        same paired backtest overshot to -1.8% because the draw side skipped the bye while
        the history side still scored it as a game. Step 5b removes that double count;
        this test is its guard."""
        engine = self._engine_with_scores([12.0, 0.0, 13.0])
        self.assertGreater(engine.baselines["P"]["mean"], self.PRIOR,
                           msg="two above-prior games plus one 0.0 week moved the posterior to "
                               "%.3f, below the %.1f prior: the zero was scored as a real game"
                               % (engine.baselines["P"]["mean"], self.PRIOR))


# ------------------------------------------------------------------------- environment
class TestEnvironmentModel(unittest.TestCase):
    def test_environment_multiplier_is_mean_preserving_over_the_schedule(self):
        """Regression guard for Phase 2 finding 1. Every expected and realised score is
        multiplied by v_tot / env_norm. For the environment model to leave the calibrated
        means intact, that multiplier must average 1 over the games actually simulated.

        env_norm used to be a hardcoded 22.0, which matched neither LEAGUE_AVG_PPG (21.5)
        nor the ratings it divided (mean ~22.6): on the week01 fixture the multiplier
        averaged 1.028 -- every mean inflated 2.8%, every weekly variance 17%. It is now the
        mean implied total over the simulated schedule, built from the same
        _compute_week_environment the weekly loop uses, so this holds by construction; the
        test exists so a future literal cannot creep back in."""
        with _sandbox("week01", 1, 1):
            with patch.object(FantasySimulationEngine, "export_and_visualize", lambda s, *a: None):
                engine = FantasySimulationEngine()
        norm = engine._compute_environment_normaliser()
        self.assertGreater(norm, 15.0)
        self.assertLess(norm, 30.0)
        mult = [engine._compute_week_environment(wk, t)["total"] / norm
                for wk in range(engine.current_week, 17) for t in NFL_TEAMS]
        self.assertAlmostEqual(float(np.mean(mult)), 1.0, places=9,
                               msg="mean environment multiplier over the simulated schedule "
                                   "is %.6f, not 1" % float(np.mean(mult)))
        # And the regular-season weeks alone, which is where every exported statistic
        # comes from, must not sit materially off 1 either.
        reg = [engine._compute_week_environment(wk, t)["total"] / norm
               for wk in range(engine.current_week, REGULAR_SEASON_WEEKS + 1) for t in NFL_TEAMS]
        self.assertAlmostEqual(float(np.mean(reg)), 1.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()

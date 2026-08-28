"""
fantasy_sim.simulation

The Monte Carlo simulation engine: FantasySimulationEngine takes the real-world data produced
by fantasy_sim.sync and projects the rest of the season forward thousands of times, modeling
per-player variance and correlation, injuries, streaming/waiver behavior, trades, and playoff
outcomes.

This is kept as a single large, cohesive class rather than fragmented across files -- its
methods share substantial internal state (rosters, baselines, correlation structure) in ways
that would require real architectural changes (composition, mixins) to split safely, which is
a different and riskier undertaking than the pure reorganization this refactor is scoped to.

Run via `python -m fantasy_sim.simulation` (see scripts/run_simulation.py) or import
FantasySimulationEngine directly.
"""
import copy
import logging
import os

import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.optimize import linear_sum_assignment

from fantasy_sim.config import (
    SIM_CONFIG, MANAGER_PROFILES, DUAL_ELIGIBILITY, NFL_TEAMS, BASE_STREAMER_MEANS,
    REGULAR_SEASON_WEEKS,
    LEAGUE_AVG_PPG, REQUIRED_STARTING_SLOTS,
)
from fantasy_sim.storage import (
    load_json, save_json, ensure_data_dir, SIMULATION_AUDIT_LOG_FILE, SYNDICATE_WARNINGS_LOG_FILE,
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
    live_season_forecast_path, model_learning_report_path, syndicate_insights_path,
    syndicate_comprehensive_matrix_path, power_rankings_chart_path, season_outcomes_chart_path,
    all_teams_trajectories_chart_path, expected_wins_chart_path, h2h_heatmap_chart_path,
    seeding_distribution_path, weekly_scoring_density_path
)

# The logging handler below opens SYNDICATE_WARNINGS_LOG_FILE immediately, at import time --
# DATA_DIR must exist before that happens, since nothing else is guaranteed to have created it
# yet (e.g. `python -m fantasy_sim.simulation` run before any sync has ever populated data/).
ensure_data_dir()

logging.basicConfig(
    level=logging.WARNING,
    format='%(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(SYNDICATE_WARNINGS_LOG_FILE, mode='w'),
        logging.StreamHandler()
    ]
)
# CAVEAT: this log, and simulation_audit_log_sim0.json, only ever record simulation index 0
# out of the full batch (see "if sim_counter == 0" gates below). That is ONE random walk
# through a single simulated season, not an aggregate or expected-case summary across all
# 10,000+ simulations. A team showing repeated "ROSTER HOLE" warnings for the same position
# across many consecutive weeks here most often reflects one unlucky, long-duration injury
# draw in that one simulated path (weeks_missed ~ Exponential, occasionally large) -- not a
# systemic weakness in that team's real roster. Treat this output as a worked example for
# debugging/sanity-checking the mechanics, not as a probability-weighted forecast; for the
# latter, use the aggregated exports (syndicate_insights_week_N.json,
# syndicate_comprehensive_matrix_week_N.json).


def normalize_position(raw_pos):
    pos = str(raw_pos).upper().strip()
    if pos in ['RB', 'FB']: return 'RB'
    if pos in ['WR']: return 'WR'
    if pos in ['TE']: return 'TE'
    if pos in ['QB']: return 'QB'
    if pos in ['K']: return 'K'
    if pos in ['DL', 'DE', 'DT', 'NT']: return 'DL'
    if pos in ['LB', 'ILB', 'OLB', 'MLB']: return 'LB'
    if pos in ['DB', 'CB', 'FS', 'SS', 'S']: return 'DB'
    return 'FLEX'

class FantasySimulationEngine:
    def __init__(self):
        self.state = load_json(LEAGUE_STATE_FILE)
        self.current_week = self.state.get('current_week', 1)
        self.standings = load_json(LEAGUE_STANDINGS_FILE)
        self.vegas = load_json(VEGAS_FILE)
        self.rosters_raw = load_json(LIVE_ROSTERS_FILE)
        self.baselines = load_json(BASELINES_FILE)
        self.power_ratings = load_json(TEAM_RATINGS_FILE)
        self.defensive_ratings = load_json(DEFENSIVE_RATINGS_FILE)
        self.defensive_tiers = load_json(DEFENSIVE_TIERS_FILE)
        self.league_schedule = load_json(LEAGUE_SCHEDULE_FILE)
        self.nfl_schedule = load_json(NFL_SCHEDULE_FILE)
        # Teams whose current-week Vegas line must not be used (see _check_vegas_staleness).
        self.stale_vegas_teams = self._check_vegas_staleness()

        self.team_names = list(self.rosters_raw.keys())
        self.rosters = {t: [p['name'] for p in data] for t, data in self.rosters_raw.items()}
        self.meta = {t: {p['name']: {'pos': p['pos'], 'team': p.get('team', 'FA')} for p in data} for t, data in self.rosters_raw.items()}

        missing_players = []
        for t, p_dict in self.meta.items():
            for p_name, meta in p_dict.items():
                if p_name not in self.baselines or self.baselines[p_name].get('mean', 0.0) <= 0:
                    if p_name in SIM_CONFIG["KNOWN_MISSING_ASSETS"]:
                        # deepcopy, not a bare reference. Binding the config's own dict here
                        # made _apply_bayesian_updates -- which writes posterior 'mean' and
                        # 'std_epistemic' straight into entries of self.baselines -- overwrite
                        # the sourced constant in config.py for the rest of the process. That
                        # made results order-dependent (the same fixture gave different answers
                        # depending on what ran before it) and compounded across runs, since
                        # each run then treated the previous run's posterior as its prior and
                        # re-applied the same evidence: std_epistemic collapsed 1.17 -> 0.16
                        # over three runs on the week06 fixture. Both backtest harnesses run
                        # the engine in a loop and were exposed. See
                        # tests/test_invariants.py::TestConfigConstantsSurviveARun.
                        self.baselines[p_name] = copy.deepcopy(SIM_CONFIG["KNOWN_MISSING_ASSETS"][p_name])
                        print(f"[INFO] Imputed whitelisted missing asset: {p_name} ({t})")
                        # The whitelist is hand-typed and drifts from Sleeper's record. The
                        # roster file is built from that record, so compare against it and
                        # say so: a wrong team here silently drops the player from his real
                        # NFL position group and pass-catcher ranking (Phase 3 finding 6).
                        checks = (
                            ('team', self.baselines[p_name].get('team'), meta.get('team') or 'FA'),
                            ('pos', normalize_position(self.baselines[p_name].get('pos', 'FLEX')),
                             normalize_position(meta.get('pos', 'FLEX'))),
                        )
                        for field, listed, actual in checks:
                            if listed != actual:
                                logging.warning(
                                    "KNOWN_MISSING_ASSETS[%r] says %s=%r but the roster (from "
                                    "Sleeper) says %r. Fix the whitelist entry in config.py.",
                                    p_name, field, listed, actual)
                    else:
                        missing_players.append((t, p_name, meta['pos']))

        if missing_players:
            raise ValueError(
                f"CRITICAL ABORT: {len(missing_players)} rostered players lack projections. First 3: {missing_players[:3]}.\n"
                f"Add these to SIM_CONFIG['KNOWN_MISSING_ASSETS'] to explicitly bypass this failsafe."
            )
        print(f"[PRE-FLIGHT SUCCESS] {len(self.baselines)} Projections Validated.")

        self.actual_h2h_wins = {t: 0 for t in self.team_names}
        self.actual_median_wins = {t: 0 for t in self.team_names}
        self.actual_points = {t: 0.0 for t in self.team_names}
        self.current_faab = {t: self.standings.get(t, {}).get('remaining_faab', 100.0) for t in self.team_names}

        self.replacement_levels = self._calc_replacement_levels()
        self.pass_catchers_meta = self._build_pass_catcher_hierarchy()
        self.nfl_position_groups = self._build_nfl_position_groups()
        self.calibration_report = self._apply_bayesian_updates()

    def _build_nfl_position_groups(self):
        """Maps (normalized_position, real_nfl_team) -> [(player_name, baseline_mean), ...] across
        the ENTIRE real NFL player population, not just players rostered in this fantasy league.

        This is what makes honest vacated-volume conservation possible. player_baselines.json
        covers every player Sleeper publishes projections for (verified: generate_player_baselines
        iterates all projections, not live_rosters), so a real NFL team's full position group is
        known here even though only a fraction of it is fantasy-rostered. When a starter is
        injured, the vacated volume can then be apportioned across the whole REAL group -- and
        the share that would flow to unrostered teammates correctly does NOT get handed to a
        rostered player who happens to share the same team and position.
        """
        groups = {}
        for name, data in self.baselines.items():
            if not isinstance(data, dict):
                continue
            pos = normalize_position(data.get('pos', 'FLEX'))
            team = data.get('team') or 'FA'
            if team == 'FA':
                continue  # free agents have no real NFL team whose volume they could inherit
            groups.setdefault((pos, team), []).append((name, float(data.get('mean', 0.0) or 0.0)))
        return groups

    @staticmethod
    def _record_vacated_volume(team_vacated_volume, p_pos, nfl_team, season_mean):
        """Records one injured player's vacated production into the (position, real NFL team)
        pool, ACCUMULATING rather than overwriting.

        Regression guard for a real bug: this was previously a plain assignment, so when two
        players at the same position on the same real NFL team were injured in the same week, the
        second injury silently clobbered the first and that vacated volume vanished entirely.

        Positions outside VACATED_VOLUME_ELIGIBLE_POSITIONS are ignored, which is what keeps the
        pools position-siloed (a WR injury can never feed a TE, and a K/DB/DL/LB injury vacates
        nothing at all).

        Extracted as a method rather than left inline specifically so tests can exercise this real
        production code path directly instead of re-implementing a mirror of it.
        """
        if p_pos not in team_vacated_volume:
            return
        team_vacated_volume[p_pos][nfl_team] = (
            team_vacated_volume[p_pos].get(nfl_team, 0.0)
            + season_mean * SIM_CONFIG['VACATED_VOLUME_CAPTURE_RATE']
        )

    def _apportion_vacated_volume(self, team_vacated_volume, injury_clocks, newly_injured_this_week):
        """Apportions each (position, real NFL team) pool of injury-vacated volume across the
        HEALTHY members of that real NFL position group, weighted by baseline mean, returning a
        direct {player_name: contingency_pts} map.

        Fixes a real over-distribution bug: contingency_pts used to be a bare pool lookup, so
        EVERY rostered player sharing the injured player's team and position received the FULL
        vacated amount. Three fantasy teams each rostering a healthy DET WR meant one DET WR
        injury injected 3x its vacated volume into the league -- points that never existed,
        inflating scores in exactly the way an uncapped tail would.

        Weighting runs over the whole REAL group (from self.nfl_position_groups, built from every
        player in player_baselines.json, not just the fantasy-rostered ones), so the share
        attributable to unrostered teammates is simply never awarded. Volume that in reality
        flows to a player nobody rosters should not land on a rostered player's stat line. That
        is the substantive part of what "extend redistribution into the real NFL depth chart" was
        reaching for, achieved with data already on hand.

        KNOWN LIMITATION: mean-weighting is a proxy for depth-chart order, and it is imperfect in
        exactly the handcuff case -- a true backup RB carries a LOW projection precisely because
        he sits behind the starter, yet he is the man who most inherits that starter's role. Real
        depth-chart ordering (Sleeper exposes depth_chart_order on its player objects) would model
        this properly and is the natural next improvement. Mean-weighting is still a strict
        improvement over awarding every claimant 100%.

        Extracted as a method rather than left inline specifically so tests can exercise this
        real production code path directly, instead of re-implementing a mirror of it.
        """
        contingency_by_player = {}
        for vac_pos, team_pools in team_vacated_volume.items():
            for vac_team, vacated_amount in team_pools.items():
                if vacated_amount <= 0.0:
                    continue
                group = self.nfl_position_groups.get((vac_pos, vac_team), [])
                healthy = [
                    (nm, mn) for nm, mn in group
                    if injury_clocks.get(nm, 0) <= 0 and nm not in newly_injured_this_week and mn > 0.0
                ]
                total_weight = sum(mn for _, mn in healthy)
                if total_weight <= 0.0:
                    continue  # nobody healthy left to inherit; the volume correctly vanishes
                for nm, mn in healthy:
                    contingency_by_player[nm] = (
                        contingency_by_player.get(nm, 0.0)
                        + vacated_amount * (mn / total_weight)
                    )
        return contingency_by_player

    def _calc_replacement_levels(self):
        means = {pos: [] for pos in SIM_CONFIG['INJURY_RATES'].keys()}
        for p, d in self.baselines.items():
            pos = normalize_position(d.get('pos', 'FLEX'))
            if pos in means: means[pos].append(d.get('mean', 0.0))
        
        depths = {'QB': 10, 'RB': 24, 'WR': 24, 'TE': 12, 'K': 8, 'DL': 10, 'LB': 10, 'DB': 10}
        replacements = {}
        for pos, vals in means.items():
            vals.sort(reverse=True)
            idx = min(depths[pos], len(vals)-1) if vals else 0
            replacements[pos] = vals[idx] if len(vals) > idx else 4.0
        replacements['FLEX'] = min(replacements['RB'], replacements['WR'])
        return replacements

    def _build_pass_catcher_hierarchy(self):
        pc = {}
        for p_name, p_info in self.baselines.items():
            t = p_info.get('team', 'FA')
            pos = normalize_position(p_info.get('pos', 'FLEX'))
            if t not in ['FA', None] and pos in ['WR', 'TE']:
                pc.setdefault(t, []).append((p_name, p_info.get('mean', 0.0)))
        for t in pc:
            pc[t].sort(key=lambda x: x[1], reverse=True)
        return pc

    def _apply_bayesian_updates(self):
        report = {
            'completed_weeks_evaluated': 0,
            'team_scoring_mae': None,
            'model_health_verdict': 'Preseason Baseline (Awaiting Week 1 Results)'
        }
        try:
            actuals = load_json(WEEKLY_ACTUALS_FILE)
        except FileNotFoundError:
            return report

        completed_weeks = [k for k, v in actuals.items() if sum(t.get('points_scored', 0) for t in v.get('team_results', {}).values()) > 0]
        if not completed_weeks: return report

        report['completed_weeks_evaluated'] = len(completed_weeks)
        player_history = {}
        team_errors = []

        for wk_key in completed_weeks:
            wk_data = actuals[wk_key]
            for p_name, pts in wk_data.get('player_scores', {}).items():
                # A week of exactly 0.0 is a bye or a DNP, not an observed performance, and
                # statistically it should be excluded here (backtest_player does). It is
                # deliberately NOT excluded yet. The engine cannot model byes (Phase 1
                # finding 7: Sleeper's payload never populates team_bye), so these zeros
                # are the only bye/absence signal the posterior ever sees; dropping them
                # made the posterior "per game played" while the engine scores every player
                # every week. Measured on the real 2025 season (paired inputs and seed):
                # excluding zeros biased simulated weekly team points +4.3% on average and
                # +7.6% by week 12, mean z -0.34, >5 SE. Fix this together with bye
                # modelling, not before it. See AUDIT_PHASE_2_FINDINGS.md finding 5.
                player_history.setdefault(p_name, []).append(pts)

            for t_name, stats_dict in wk_data['team_results'].items():
                actual_pts = stats_dict['points_scored']
                baseline_exp = sum(self.baselines.get(p, {}).get('mean', 8.0) for p in self.rosters.get(t_name, [])[:13])
                team_errors.append(abs(actual_pts - baseline_exp))

        if team_errors:
            mae = float(np.mean(team_errors))
            report['team_scoring_mae'] = round(mae, 2)
            report['model_health_verdict'] = 'Calibrated & Learning' if mae < 18.0 else 'High Variance / Volatile'

        for p_name, data in self.baselines.items():
            if p_name in player_history:
                scores = player_history[p_name]
                n = len(scores)
                actual_mean = float(np.mean(scores))
                prior_mean = float(data['mean'])
                
                prior_var = max(0.1, float(data.get('std_epistemic', data.get('std', 3.0)) ** 2))
                raw_actual_var = float(np.var(scores)) if n > 1 else prior_var
                actual_var = max(raw_actual_var, 0.5 * prior_var)

                n_0 = 4.0
                post_var = 1.0 / ((n_0 / prior_var) + (n / actual_var))
                post_mean = ((n_0 * prior_mean / prior_var) + (n * actual_mean / actual_var)) * post_var

                self.baselines[p_name]['mean'] = float(post_mean)
                self.baselines[p_name]['std_epistemic'] = float(np.sqrt(post_var))

        for wk_key in completed_weeks:
            wk_data = actuals[wk_key]
            for t_name, stats_dict in wk_data['team_results'].items():
                self.actual_h2h_wins[t_name] += stats_dict.get('h2h_win', 0)
                self.actual_median_wins[t_name] += stats_dict.get('median_win', 0)
                self.actual_points[t_name] += stats_dict.get('points_scored', 0.0)

        return report

    @staticmethod
    def _solve_optimal_assignment(candidates):
        """
        True optimal lineup assignment via the Hungarian algorithm (scipy's
        linear_sum_assignment), replacing a previous greedy, fixed-position-order fill. The
        greedy approach could misassign dual-eligible players (e.g. Travis Hunter, WR/DB) to
        whichever position happened to be processed first in a hardcoded order, even when a
        different assignment would produce a strictly higher-value lineup overall -- see
        test_optimal_assignment_beats_greedy_for_dual_eligible_player for a worked example
        where greedy scores 24.5 and the true optimum is 38.0 on an identical roster.

        candidates: list of (player_name, position_eligibility_list, value) tuples.
        Returns (assigned, unfilled_slot_positions):
          assigned: list of (player_name, value, slot_position) for each filled slot.
          unfilled_slot_positions: list of position strings with no eligible player available
            (the live simulation injects a streamer for each of these; get_optimal_score just
            treats them as contributing zero, matching its previous behavior).
        """
        slots = list(REQUIRED_STARTING_SLOTS)
        n_players, n_slots = len(candidates), len(slots)
        if n_players == 0:
            return [], list(slots)

        LARGE = 1e6
        cost = np.full((n_players, n_slots), LARGE)
        for i, (name, pos_opts, value) in enumerate(candidates):
            flex_eligible = any(po in ('RB', 'WR', 'TE') for po in pos_opts)
            for j, slot_pos in enumerate(slots):
                if slot_pos == 'FLEX':
                    if flex_eligible:
                        cost[i, j] = -value
                elif slot_pos in pos_opts:
                    cost[i, j] = -value

        row_ind, col_ind = linear_sum_assignment(cost)

        assigned = []
        filled_slot_indices = set()
        for r, c in zip(row_ind, col_ind):
            if cost[r, c] >= LARGE:
                continue  # no genuinely eligible player was available for this slot
            name, pos_opts, value = candidates[r]
            assigned.append((name, value, slots[c]))
            filled_slot_indices.add(c)

        unfilled_slot_positions = [slots[j] for j in range(n_slots) if j not in filled_slot_indices]
        return assigned, unfilled_slot_positions

    def get_optimal_score(self, roster_list):
        candidates = []
        player_values = {}
        for p in roster_list:
            d = self.baselines.get(p, {})
            if not isinstance(d, dict): d = {}
            pos = normalize_position(d.get('pos', 'FLEX'))
            opts = DUAL_ELIGIBILITY.get(p, [pos])
            value = d.get('mean', 4.0)
            candidates.append((p, opts, value))
            player_values[p] = value

        assigned, _unfilled = self._solve_optimal_assignment(candidates)
        used = {name for name, value, slot in assigned}
        opt_score = sum(value for name, value, slot in assigned)
        bench = sum(player_values[p] for p in roster_list if p not in used)
        return opt_score + (bench * 0.1)

    @staticmethod
    def _compute_faab_bid(remaining_faab, raw_uniform_draw, aggression, needs, deflation, avg_league_faab):
        """
        Pure bid-sizing function, extracted from the streamer-acquisition loop so it can be
        unit tested directly. `raw_uniform_draw` is the externally-sampled np.random.uniform(6, 22)
        draw, passed in rather than sampled here, so the RNG call count/order inside
        run_simulation() is unchanged and this function stays fully deterministic for tests.

        Invariants this function must uphold:
          - A bid can never exceed the team's remaining FAAB (can't spend money you don't have).
          - A bid can never exceed the league-wide competitive ceiling (avg_league_faab * 1.5),
            which caps how much even a maximally aggressive manager can be modeled as bidding.
          - A bid scales up with both manager aggression and unmet roster need.
        """
        base_bid = raw_uniform_draw * aggression * (needs / 2.0)
        comp_ceiling = max(1.0, avg_league_faab * 1.5)
        return min(remaining_faab, base_bid * deflation, comp_ceiling)

    def build_covariance_matrix(self, players_list, team_meta):
        n = len(players_list)
        cov = np.eye(n)
        for i in range(n):
            p1 = players_list[i]
            p1_team = team_meta.get(p1, {}).get('team', 'FA')
            if not isinstance(p1_team, str): p1_team = 'FA'
            pos1 = normalize_position(team_meta.get(p1, {}).get('pos', 'FLEX'))
            
            for j in range(i + 1, n):
                p2 = players_list[j]
                p2_team = team_meta.get(p2, {}).get('team', 'FA')
                if not isinstance(p2_team, str): p2_team = 'FA'
                pos2 = normalize_position(team_meta.get(p2, {}).get('pos', 'FLEX'))
                
                corr = 0.0
                if p1_team == p2_team and p1_team not in ['FA', None]:
                    is_qb1 = (pos1 == 'QB' and pos2 in ['WR', 'TE'])
                    is_qb2 = (pos2 == 'QB' and pos1 in ['WR', 'TE'])
                    
                    if is_qb1 or is_qb2:
                        target = p2 if is_qb1 else p1
                        target_team = p2_team if is_qb1 else p1_team
                        target_pos = pos2 if is_qb1 else pos1
                        if target_pos == 'TE':
                            # A TE is a TE whatever its rank among the team's pass-catchers.
                            # Previously rank decided: a TE who out-projected the WRs got
                            # QB_WR1, and every WR from the third down got QB_TE (0.35) --
                            # more than WR2's 0.315. See AUDIT_PHASE_2_FINDINGS.md finding 7.
                            corr = SIM_CONFIG['CORRELATIONS']['QB_TE']
                        else:
                            # Rank among the team's WRs only (pass_catchers_meta ranks WRs
                            # and TEs together by projected mean).
                            rank = 2
                            if target_team in self.pass_catchers_meta:
                                team_wrs = [
                                    nm for nm, _mean in self.pass_catchers_meta[target_team]
                                    if normalize_position(
                                        team_meta.get(nm, {}).get('pos')
                                        or getattr(self, 'baselines', {}).get(nm, {}).get('pos', 'WR')
                                    ) == 'WR'
                                ]
                                if target in team_wrs: rank = team_wrs.index(target)
                            if rank == 0: corr = SIM_CONFIG['CORRELATIONS']['QB_WR1']
                            # WR2 and below. UNVERIFIED for rank >= 2: backtest_player
                            # calibrates QB_WR1 and QB_WR2 only, so WR2's value is carried
                            # down as a ceiling rather than inventing a smaller one. The
                            # property this guarantees is monotonicity in rank; the exact
                            # WR3+ value is a Phase 7 calibration item.
                            else: corr = SIM_CONFIG['CORRELATIONS']['QB_WR2']
                    elif pos1 == 'WR' and pos2 == 'WR':
                        corr = SIM_CONFIG['CORRELATIONS']['WR_WR']
                    elif (pos1 == 'RB' and pos2 == 'QB') or (pos2 == 'RB' and pos1 == 'QB'):
                        corr = SIM_CONFIG['CORRELATIONS']['QB_RB']
                cov[i, j] = corr
                cov[j, i] = corr

        min_eig = np.min(np.real(np.linalg.eigvals(cov)))
        if min_eig < 1e-4:
            # Diagonal loading: a fixed 1e-4 jitter only fixes matrices that are barely
            # non-PSD due to floating point rounding. Rosters with several same-team
            # players sharing a correlation (e.g. 7+ WRs on one NFL team, easily reached
            # on a deep bench) can push min_eig well below zero (e.g. -0.08), which a
            # fixed epsilon cannot repair. Scale the jitter to the actual deficiency so
            # this is robust regardless of roster composition.
            cov += (abs(min_eig) + 1e-4) * np.eye(n)
            # Loading the diagonal makes every marginal variance 1 + delta while leaving
            # the off-diagonals in absolute terms, so without this step z_corr = L z would
            # have sd sqrt(1 + delta) for EVERY player on the roster (inflating every
            # lognormal sigma on that team) and every effective correlation would be
            # corr / (1 + delta). Rescale back to a correlation matrix: same eigenvalue
            # shift, unit marginals. See AUDIT_PHASE_2_FINDINGS.md finding 6.
            d = np.sqrt(np.diag(cov))
            cov = cov / np.outer(d, d)
        return np.linalg.cholesky(cov)

    def _compute_future_week_matchup_environment(self, nfl_team, opp):
        """
        Computes the implied scoring environment for a real NFL team in a future week (not the
        current, Vegas-covered week), given the two teams' offensive power ratings and REAL
        empirical defensive strength. Extracted as its own method so this formula -- the fix
        for the def_rating self-mirroring bug -- can be tested directly.

        Each side's implied total blends its OWN offense with the OPPONENT's actual
        points-allowed tendency (see generate_defensive_ratings in 2026_sleeper_sync.py), not a
        mirror of its own offense. This is a genuine two-sided matchup model.
        """
        if opp == 'FA' or nfl_team not in self.power_ratings or opp not in self.power_ratings:
            return {'total': 21.5, 'spread': 0.0, 'wind_mph': 0.0, 'precip_prob': 0.0, 'opponent': 'FA'}

        off_pwr = self.power_ratings[nfl_team]['off_rating']
        opp_off = self.power_ratings[opp]['off_rating']
        opp_def_strength = self.defensive_ratings.get(opp, {}).get('points_allowed_estimate', LEAGUE_AVG_PPG)
        my_def_strength = self.defensive_ratings.get(nfl_team, {}).get('points_allowed_estimate', LEAGUE_AVG_PPG)

        implied_tot = round((off_pwr + opp_def_strength) / 2.0, 2)
        opp_implied = round((opp_off + my_def_strength) / 2.0, 2)
        game_spread = round(opp_implied - implied_tot, 2)
        return {'total': implied_tot, 'spread': game_spread, 'wind_mph': 0.0, 'precip_prob': 0.0, 'opponent': opp}

    def _check_vegas_staleness(self):
        """Decides, per team, whether the Vegas file on disk may be applied to the current
        week, and says so loudly when it may not.

        Two independent signals, either of which condemns a line:
          1. `_meta.week` (stamped by sync since Phase 3) is not the current week.
          2. The line's `opponent` disagrees with nfl_schedule.json for the current week --
             this catches an unstamped legacy file too, since week-1 lines carry week-1
             opponents.
        A condemned team's current-week environment falls back to the ratings model
        (_compute_future_week_matchup_environment), exactly as any future week does. The stale
        line is refused, not the run.

        Why this exists: the in-season sync fallbacks used to return without writing the Vegas
        file, so the week-1 preseason table stayed on disk and was applied to every current
        week, week-1 opponents included. The committed week06 fixture reproduces that state:
        28 of 28 scheduled teams carried the wrong opponent. AUDIT_PHASE_3_FINDINGS.md, finding 1."""
        meta = self.vegas.get('_meta') if isinstance(self.vegas, dict) else None
        stamped_week = meta.get('week') if isinstance(meta, dict) else None
        schedule = self.nfl_schedule.get(str(self.current_week), {})
        stale = set()
        for team, line in self.vegas.items():
            if team in ('FA', '_meta') or not isinstance(line, dict):
                continue
            scheduled_opp = schedule.get(team)
            wrong_week = stamped_week is not None and stamped_week != self.current_week
            wrong_opp = scheduled_opp is not None and line.get('opponent', 'FA') != scheduled_opp
            if wrong_week or wrong_opp:
                stale.add(team)
        if stamped_week is None:
            logging.warning(
                "VEGAS: vegas_totals.json carries no _meta week stamp (written by a sync older "
                "than Phase 3); staleness can only be judged by opponent against the schedule.")
        if stale:
            logging.error(
                "VEGAS STALE: %d of %d lines in vegas_totals.json are not for week %d "
                "(stamped week: %s). Those teams use the ratings-model environment instead of "
                "the stale line. Re-run the sync with ODDS_API_KEY set for real week-%d lines. "
                "Teams: %s", len(stale), sum(1 for t in self.vegas if t not in ('FA', '_meta')),
                self.current_week, stamped_week, self.current_week, ", ".join(sorted(stale)))
        elif meta and meta.get('source', '').startswith('fallback'):
            logging.warning(
                "VEGAS: this week's file is a sync fallback (%s): flat 21.5 totals, no opponents. "
                "Matchup and defensive-tier effects are OFF. Set ODDS_API_KEY for real lines.",
                meta.get('source'))
        return stale

    def _compute_week_environment(self, week_num, nfl_team):
        """The scoring environment one real NFL team faces in one week: Vegas for the current
        week (unless that team's line was condemned as stale -- see _check_vegas_staleness),
        the two-sided ratings model for every later week. Extracted from run_simulation so the
        environment normaliser below can be built from exactly the values the weekly loop will
        use -- one code path, not a mirror of it."""
        if week_num == self.current_week and nfl_team not in self.stale_vegas_teams:
            return self.vegas.get(nfl_team, {'total': 21.5, 'spread': 0.0, 'wind_mph': 0.0, 'precip_prob': 0.0, 'opponent': 'FA'})
        opp = self.nfl_schedule.get(str(week_num), {}).get(nfl_team, 'FA')
        return self._compute_future_week_matchup_environment(nfl_team, opp)

    def _compute_environment_normaliser(self):
        """Mean implied team total over every (NFL team, week) the simulation will play, weeks
        current_week..16. The weekly draw scales every player's mean by v_tot / normaliser, so
        for the environment model to leave the calibrated means intact the multiplier must
        average 1 over the games actually simulated -- which this makes true by construction.

        Replaces a hardcoded 22.0. That literal matched neither LEAGUE_AVG_PPG (21.5) nor the
        power/defensive ratings it was dividing (mean ~22.6), so on the real schedule the
        multiplier averaged 1.028: every calibrated mean inflated 2.8% and every per-player
        weekly variance 17%, in every week (AUDIT_PHASE_2_FINDINGS.md finding 1). Deterministic
        -- no RNG -- so computing it up front changes no draw order."""
        totals = [
            self._compute_week_environment(week_idx + 1, nfl_team)['total']
            for week_idx in range(self.current_week - 1, 16)
            for nfl_team in NFL_TEAMS
        ]
        return float(np.mean(totals)) if totals else LEAGUE_AVG_PPG

    def run_simulation(self):
        num_batches = SIM_CONFIG["NUM_BATCHES"]
        env_norm = self._compute_environment_normaliser()
        sims_per_batch = SIM_CONFIG["SIMS_PER_BATCH"]
        total_sims = num_batches * sims_per_batch

        batch_playoff_rates = {t: [] for t in self.team_names}
        batch_champ_rates = {t: [] for t in self.team_names}
        batch_toilet_rates = {t: [] for t in self.team_names}

        global_season_wins = {t: np.zeros(total_sims) for t in self.team_names}
        global_season_points = {t: np.zeros(total_sims) for t in self.team_names}
        global_trajectories = {t: np.zeros((total_sims, 14)) for t in self.team_names}
        global_weekly_scores = {t: np.zeros((total_sims, 14)) for t in self.team_names}
        seed_matrix = {t: np.zeros(len(self.team_names)) for t in self.team_names}
        h2h_matrix = {t: {opp: 0 for opp in self.team_names} for t in self.team_names}
        points_against = {t: 0.0 for t in self.team_names}
        all_play_wins = {t: 0 for t in self.team_names}
        championship_player_shares = {}
        
        max_single_week_score = 0.0
        max_score_team = ""
        max_score_week = 0
        audit_log = {'weeks': {}}

        print(f"\n[>>>] EXECUTING {num_batches} INDEPENDENT BATCHES ({total_sims:,} TOTAL RUNS)...")

        sim_counter = 0
        for batch in range(num_batches):
            np.random.seed(1000 + batch)
            b_playoffs = {t: 0 for t in self.team_names}
            b_champs = {t: 0 for t in self.team_names}
            b_toilets = {t: 0 for t in self.team_names}

            for _ in range(sims_per_batch):
                sim_rosters = copy.deepcopy(self.rosters)
                sim_meta = copy.deepcopy(self.meta)
                faab = copy.deepcopy(self.current_faab)
                injury_clocks = {p: 0 for t in sim_rosters.values() for p in t}

                sim_wins = {t: float(self.actual_h2h_wins[t] + self.actual_median_wins[t]) for t in self.team_names}
                sim_points = {t: float(self.actual_points[t]) for t in self.team_names}
                top4 = []
                w1, w2 = None, None
                
                sim_season_means = {}
                for p_name, p_info in self.baselines.items():
                    mu_0 = p_info.get('mean', 8.0)
                    sig_epistemic = p_info.get('std_epistemic', mu_0 * 0.18)
                    
                    if mu_0 <= 0.01:
                        sim_season_means[p_name] = 0.0
                    else:
                        sigma_e = np.sqrt(np.log(1 + (sig_epistemic / mu_0) ** 2))
                        mu_e = np.log(mu_0) - (sigma_e ** 2 / 2)
                        sim_season_means[p_name] = float(np.exp(np.random.normal(mu_e, sigma_e)))

                for week_idx in range(self.current_week - 1, 16):
                    week_num = week_idx + 1
                    week_scores = {}
                    team_starters = {}

                    if sim_counter == 0: audit_log['weeks'][week_num] = {'teams': {}}

                    # No per-game "shared_z" draw any more. It used to add 0.6 * N(0,1) per NFL
                    # game to every QB/WR/TE's z whenever the OPPONENT's implied total exceeded
                    # 23 (that is what total + spread is) -- 44% of team-weeks on the real
                    # schedule -- injecting +0.32 score correlation into every same-team
                    # pass-catcher pair on top of the copula, including WR-WR whose calibrated
                    # target is -0.004. SIM_CONFIG['CORRELATIONS'] was measured on real scores
                    # by backtest_player.analyze_correlations and therefore already contains
                    # whatever shared game-script effect exists; the copula is the one place
                    # correlation is set. See AUDIT_PHASE_2_FINDINGS.md finding 2.
                    team_environments = {nfl_team: self._compute_week_environment(week_num, nfl_team)
                                         for nfl_team in NFL_TEAMS}

                    if 6 <= week_num <= 10:
                        standings_order = sorted(self.team_names, key=lambda t: (sim_wins[t], sim_points[t]), reverse=True)
                        desperate = standings_order[4:8]
                        rich = standings_order[0:2]

                        for d_team in desperate:
                            if MANAGER_PROFILES.get(d_team, {}).get('trade_will', 0.0) > np.random.rand():
                                for r_team in rich:
                                    if MANAGER_PROFILES.get(r_team, {}).get('trade_will', 0.0) > np.random.rand():
                                        d_list = sorted(sim_rosters[d_team], key=lambda p: self.baselines.get(p, {}).get('mean', 0.0), reverse=True)
                                        r_list = sorted(sim_rosters[r_team], key=lambda p: self.baselines.get(p, {}).get('mean', 0.0), reverse=True)
                                        
                                        if len(d_list) > 0 and len(r_list) > 6:
                                            p1 = d_list[0]
                                            p2, p3 = r_list[5], r_list[6]
                                            curr_d = self.get_optimal_score(d_list)
                                            curr_r = self.get_optimal_score(r_list)

                                            tent_d = [p for p in d_list if p != p1] + [p2, p3]
                                            tent_d.sort(key=lambda p: self.baselines.get(p, {}).get('mean', 0.0), reverse=True)
                                            dropped = tent_d.pop()
                                            tent_r = [p for p in r_list if p not in [p2, p3]] + [p1]

                                            if self.get_optimal_score(tent_d) > curr_d and self.get_optimal_score(tent_r) > curr_r:
                                                sim_rosters[d_team] = tent_d
                                                sim_rosters[r_team] = tent_r
                                                sim_meta[d_team][p2] = sim_meta[r_team].get(p2, {})
                                                sim_meta[d_team][p3] = sim_meta[r_team].get(p3, {})
                                                sim_meta[r_team][p1] = sim_meta[d_team].get(p1, {})
                                                if dropped in sim_meta[d_team]: del sim_meta[d_team][dropped]
                                                break

                    streamer_needs = {t: 0 for t in self.team_names}
                    for t_name in self.team_names:
                        max_deficits = 0
                        for wk_check in [week_num, min(14, week_num + 1)]:
                            available = []
                            for p_name in sim_rosters[t_name]:
                                if injury_clocks.get(p_name, 0) > 0: continue
                                
                                p_info = self.baselines.get(p_name, {})
                                if not isinstance(p_info, dict): p_info = {}
                                if p_info.get('bye') == wk_check: continue
                                
                                p_meta = sim_meta.get(t_name, {}).get(p_name, {})
                                if not isinstance(p_meta, dict): p_meta = {}
                                
                                p_pos = normalize_position(p_meta.get('pos', p_info.get('pos', 'FLEX')))
                                available.append((p_name, DUAL_ELIGIBILITY.get(p_name, [p_pos])))

                            reqs = [('DB', 1), ('DL', 1), ('LB', 1), ('TE', 1), ('QB', 1), ('K', 1), ('RB', 2), ('WR', 2)]
                            used_p = set()
                            wk_deficits = 0
                            for pos, count in reqs:
                                taken = 0
                                for p_name, p_opts in available:
                                    if p_name not in used_p and pos in p_opts:
                                        used_p.add(p_name)
                                        taken += 1
                                    if taken == count: break
                                if taken < count: wk_deficits += (count - taken)
                            
                            flex_taken = 0
                            for p_name, p_opts in available:
                                if p_name not in used_p and any(po in ['RB', 'WR', 'TE'] for po in p_opts):
                                    used_p.add(p_name)
                                    flex_taken += 1
                                if flex_taken == 3: break
                            if flex_taken < 3: wk_deficits += (3 - flex_taken)
                            max_deficits = max(max_deficits, wk_deficits)
                        streamer_needs[t_name] = max_deficits

                    total_faab = sum(faab.values())
                    avg_faab = total_faab / len(self.team_names)
                    deflation = total_faab / (len(self.team_names) * 100.0) if total_faab > 0 else 0

                    bids = []
                    for t_name, needs in streamer_needs.items():
                        for _ in range(needs):
                            agg = MANAGER_PROFILES.get(t_name, {}).get('faab_agg', 0.5)
                            raw_draw = np.random.uniform(6, 22)
                            bid_amt = self._compute_faab_bid(faab[t_name], raw_draw, agg, needs, deflation, avg_faab)
                            bids.append((bid_amt, t_name))

                    bids.sort(key=lambda x: (x[0], MANAGER_PROFILES.get(x[1], {}).get('faab_agg', 0.5)), reverse=True)
                    available_streamers = [max(4.0, 12.0 - (i * 0.5)) for i in range(max(40, len(bids)))]
                    won_streamers = {t: [] for t in self.team_names}
                    for i, (b_amt, t_name) in enumerate(bids):
                        faab[t_name] -= min(b_amt, faab[t_name])
                        won_streamers[t_name].append(available_streamers[i])

                    # team_vacated_volume is keyed by [position][nfl_team] -- generalizes what
                    # was originally an RB-only mechanism (team_vacated_rb) to also cover WR
                    # and TE, as three separate, position-siloed pools (see SIM_CONFIG's
                    # VACATED_VOLUME_CAPTURE_RATE comment for why WR and TE aren't merged into
                    # one shared pool, and for this constant's real-data grounding and its
                    # honest limitations).
                    team_vacated_volume = {pos: {} for pos in SIM_CONFIG['VACATED_VOLUME_ELIGIBLE_POSITIONS']}
                    # PASS 1: determine ALL injury onsets across the WHOLE league for this
                    # week FIRST, before computing any scores. Fixes a real order-dependence
                    # bug: previously, whether a same-real-team backup RB received the
                    # vacated-volume bonus THE SAME WEEK a starter got hurt depended on which
                    # fantasy team happened to be processed first that week, and which order
                    # players appeared within a roster -- both arbitrary, neither by design.
                    # Now every injury for the week is fully known (and team_vacated_volume fully
                    # populated) before any score or contingency_pts lookup happens in PASS 2,
                    # regardless of iteration order.
                    newly_injured_this_week = set()
                    for t_name in self.team_names:
                        for p_name in sim_rosters[t_name]:
                            p_info = self.baselines.get(p_name, {})
                            if not isinstance(p_info, dict): p_info = {}
                            p_meta = sim_meta.get(t_name, {}).get(p_name, {})
                            if not isinstance(p_meta, dict): p_meta = {}
                            p_pos = normalize_position(p_meta.get('pos', p_info.get('pos', 'FLEX')))
                            nfl_team = p_meta.get('team', p_info.get('team', 'FA'))

                            if week_num == p_info.get('bye') or injury_clocks.get(p_name, 0) > 0: continue

                            season_mean = sim_season_means.get(p_name, p_info.get('mean', 8.0))

                            if np.random.rand() < SIM_CONFIG['INJURY_RATES'].get(p_pos, 0.025):
                                # Two-component duration mixture (see SIM_CONFIG's
                                # INJURY_SEVERE_PROBABILITY comment for the real-data sourcing
                                # and moment-matching solve behind these three parameters) --
                                # replaces a single Exponential(scale=2.5) that was
                                # structurally incapable of reproducing the real, well-
                                # documented bimodal pattern of "most injuries are brief, a
                                # distinct minority are season-altering".
                                if np.random.rand() < SIM_CONFIG['INJURY_SEVERE_PROBABILITY']:
                                    weeks_missed = int(np.random.exponential(scale=SIM_CONFIG['INJURY_SEVERE_DURATION_SCALE'])) + 1
                                else:
                                    weeks_missed = int(np.random.exponential(scale=SIM_CONFIG['INJURY_TYPICAL_DURATION_SCALE'])) + 1
                                injury_clocks[p_name] = min(16, weeks_missed)
                                newly_injured_this_week.add(p_name)
                                self._record_vacated_volume(team_vacated_volume, p_pos, nfl_team, season_mean)

                    # Apportion each pool across the real NFL position group, once per week
                    # (see _apportion_vacated_volume for the full rationale and limitations).
                    contingency_by_player = self._apportion_vacated_volume(
                        team_vacated_volume, injury_clocks, newly_injured_this_week
                    )

                    for t_name in self.team_names:
                        p_list = sim_rosters[t_name]
                        L = self.build_covariance_matrix(p_list, sim_meta.get(t_name, {}))
                        z_uncorr = np.random.normal(0, 1, len(p_list))
                        z_corr = np.dot(L, z_uncorr)

                        candidates = []
                        final_score_by_name = {}
                        for idx, p_name in enumerate(p_list):
                            p_info = self.baselines.get(p_name, {})
                            if not isinstance(p_info, dict): p_info = {}
                            
                            p_meta = sim_meta.get(t_name, {}).get(p_name, {})
                            if not isinstance(p_meta, dict): p_meta = {}

                            p_pos = normalize_position(p_meta.get('pos', p_info.get('pos', 'FLEX')))
                            nfl_team = p_meta.get('team', p_info.get('team', 'FA'))

                            # A player already out from a PRIOR week's injury is still
                            # excluded here; a player newly injured THIS week (determined in
                            # PASS 1 above) is NOT excluded -- they still play a reduced role
                            # the week they're hurt, exactly as before this restructuring.
                            already_out_from_prior_week = injury_clocks.get(p_name, 0) > 0 and p_name not in newly_injured_this_week
                            if week_num == p_info.get('bye') or already_out_from_prior_week: continue

                            season_mean = sim_season_means.get(p_name, p_info.get('mean', 8.0))
                            std_aleatoric = p_info.get('std_aleatoric', 3.0)

                            if p_name in newly_injured_this_week:
                                mean_val = season_mean * 0.35
                                std_val = std_aleatoric * 0.5
                            else:
                                mean_val = season_mean
                                std_val = std_aleatoric

                            veg = team_environments.get(nfl_team, {'total': 21.5, 'spread': 0.0, 'wind_mph': 0.0, 'precip_prob': 0.0, 'opponent': 'FA'})
                            v_tot, v_spr, v_opp = veg['total'], veg['spread'], veg['opponent']

                            eff_z = z_corr[idx]

                            if mean_val <= 0.01:
                                base_score = 0.0
                            else:
                                sigma_a = np.sqrt(np.log(1 + (std_val / mean_val) ** 2))
                                mu_a = np.log(mean_val) - (sigma_a ** 2 / 2)
                                base_score = float(np.exp(mu_a + sigma_a * eff_z))

                            script_mult = 1.0
                            # Empirically-derived defensive tiers (see nfl_defensive_tiers.json,
                            # built from real completed-game points-allowed data). Applied to
                            # both pass- and rush-relevant positions using the same overall
                            # defensive-strength signal -- see SIM_CONFIG's DEFENSIVE_RANKS
                            # removal comment for why this is no longer split by pass vs. rush.
                            top_def = self.defensive_tiers.get('TOP_DEFENSE', [])
                            bottom_def = self.defensive_tiers.get('BOTTOM_DEFENSE', [])
                            if v_opp in top_def and p_pos in ['QB', 'WR', 'TE']: script_mult -= 0.06
                            elif v_opp in bottom_def and p_pos in ['QB', 'WR', 'TE']: script_mult += 0.06
                            if v_opp in top_def and p_pos == 'RB': script_mult -= 0.06
                            elif v_opp in bottom_def and p_pos == 'RB': script_mult += 0.06

                            if v_spr <= -5.5:
                                if p_pos == 'RB': script_mult += 0.15
                                elif p_pos in ['DL', 'DEF']: script_mult += 0.10
                            elif v_spr >= 5.5:
                                if p_pos in ['QB', 'WR', 'TE']: script_mult += 0.10
                                elif p_pos == 'RB': script_mult -= 0.10

                            contingency_pts = contingency_by_player.get(p_name, 0.0)
                            # env_norm is the mean implied total over the simulated schedule
                            # (see _compute_environment_normaliser), so this multiplier
                            # averages exactly 1 across the season and the calibrated means
                            # survive the environment model intact.
                            env_var = float(np.random.normal(v_tot / env_norm, 0.10))

                            expected_pre = mean_val * (v_tot / env_norm) * script_mult + contingency_pts
                            final_score = (base_score + contingency_pts) * env_var * script_mult
                            # Applied AFTER environmental scaling, not to base_score before it,
                            # so this never interferes with the model's designed v_tot/script
                            # adjustments -- it only ever clips draws already far beyond any
                            # real NFL fantasy performance. See SIM_CONFIG's comment for the
                            # real-record justification and empirical verification of why this
                            # was added.
                            final_score = min(final_score, SIM_CONFIG['MAX_REALISTIC_WEEKLY_SCORE'])

                            pos_opts = DUAL_ELIGIBILITY.get(p_name, [p_pos])
                            candidates.append((p_name, pos_opts, expected_pre))
                            final_score_by_name[p_name] = final_score

                        # True optimal bipartite assignment (Hungarian algorithm) between
                        # eligible players and this week's 13 starting slots, based on
                        # pregame expectation (expected_pre) -- consistent with real lineup
                        # decisions being made before results are known. Replaces a previous
                        # greedy, fixed-position-order fill that could misassign dual-eligible
                        # players (e.g. Travis Hunter, WR/DB) suboptimally -- see
                        # test_optimal_assignment_beats_greedy_for_dual_eligible_player for a
                        # worked example of the magnitude of that error.
                        assigned, unfilled_slots = self._solve_optimal_assignment(candidates)
                        starters = [(value, final_score_by_name[name], name) for name, value, slot in assigned]

                        streamers_used = {k: 0 for k in BASE_STREAMER_MEANS.keys()}
                        for po in unfilled_slots:
                            if won_streamers[t_name]:
                                m_str = won_streamers[t_name].pop(0)
                            else:
                                m_str = max(self.replacement_levels.get(po, 4.0) * 0.8, BASE_STREAMER_MEANS.get(po, 8.0) * (SIM_CONFIG['STREAMER_DECAY_RATE'] ** streamers_used[po]))
                            s_score = max(0.0, np.random.normal(m_str, 2.2))
                            s_name = f"STREAMER_{po}_{streamers_used[po]}"
                            starters.append((m_str, s_score, s_name))
                            streamers_used[po] += 1
                            if sim_counter == 0: logging.warning(f"ROSTER HOLE: {t_name} has no valid {po}s available in Week {week_num}. Injecting {s_name}.")

                        total_score = sum(s[1] for s in starters)
                        week_scores[t_name] = total_score
                        # Regular season only. week_scores still carries weeks 15-16 -- the
                        # playoff rounds are decided on them just below -- but sim_points is
                        # what becomes the exported Expected_Points, which sits beside
                        # Expected_Wins, a 14-week figure.
                        #
                        # Every team scores a week 15 and a week 16 here, including the four
                        # eliminated at week 14 and the team that finished last, so folding
                        # those in credited all 8 teams with roughly two extra weeks of
                        # scoring (+327 to +379, about 12%) for games six of them never
                        # played. Seeding was never affected: the week-14 tiebreak below and
                        # the week 6-10 trade logic both read sim_points before any playoff
                        # week is added. See AUDIT_PHASE_1_FINDINGS.md finding 5.
                        if week_num <= REGULAR_SEASON_WEEKS:
                            sim_points[t_name] += total_score
                        team_starters[t_name] = [(s[2], s[1]) for s in starters]

                        if week_idx < 14:
                            global_weekly_scores[t_name][sim_counter, week_idx] = total_score

                        # Keep (name, actual_score) pairs, not just names, so downstream
                        # "championship value" analysis can measure real point contribution
                        # instead of mere lineup-slot occupancy (see championship_player_shares
                        # below -- a starting kicker has zero competition for his slot and would
                        # otherwise look "valuable" purely by never being benched).
                        team_starters[t_name] = [(s[2], s[1]) for s in starters]

                        if total_score > max_single_week_score:
                            max_single_week_score = total_score
                            max_score_team = t_name
                            max_score_week = week_num

                        if sim_counter == 0:
                            audit_log['weeks'][week_num]['teams'][t_name] = {
                                'starters': [{'name': s[2], 'expected': round(s[0], 2), 'actual': round(s[1], 2)} for s in starters],
                                'total_score': round(total_score, 2),
                                'injury_ward': [p for p, c in injury_clocks.items() if c > 0 and p in sim_rosters[t_name]]
                            }

                    if week_num <= 14:
                        if week_idx < len(self.league_schedule): matchups = self.league_schedule[week_idx]
                        else: matchups = []

                        median_cut = np.median(list(week_scores.values())) if week_scores else 0.0

                        if SIM_CONFIG.get('MEDIAN_SCORING_ENABLED', True):
                            for t_name, score in week_scores.items():
                                if score >= median_cut: sim_wins[t_name] += 1

                        for t1, t2 in matchups:
                            if week_scores.get(t1, 0) > week_scores.get(t2, 0):
                                sim_wins[t1] += 1
                            elif week_scores.get(t2, 0) > week_scores.get(t1, 0):
                                sim_wins[t2] += 1
                            else:
                                sim_wins[t1] += 0.5
                                sim_wins[t2] += 0.5
                                
                        for t in self.team_names:
                            global_trajectories[t][sim_counter, week_idx] = sim_wins[t]

                        if week_num == 14:
                            ranked = sorted(self.team_names, key=lambda t: (sim_wins[t], sim_points[t]), reverse=True)
                            top4 = ranked[:4]

                            for rank_idx, team_ranked in enumerate(ranked):
                                seed_matrix[team_ranked][rank_idx] += 1

                            for p in top4: b_playoffs[p] += 1
                            b_toilets[ranked[-1]] += 1

                        # This is the genuine "any given Sunday" all-play comparison: every team
                        # vs every other team, every week, regardless of the actual schedule.
                        # h2h_matrix backs the "Any Given Sunday" heatmap and must NOT be gated
                        # to only the weeks a pair was actually scheduled against each other --
                        # doing so previously undercounted by ~7x (each pair's real matchups are
                        # only 2 of the 14 weeks in this schedule) while still being divided by
                        # the full total_sims*14 denominator downstream, silently deflating every
                        # cell in that chart to ~1/7th of its true value.
                        for t1 in self.team_names:
                            for t2 in self.team_names:
                                if t1 != t2 and week_scores.get(t1, 0) > week_scores.get(t2, 0):
                                    all_play_wins[t1] += 1
                                    h2h_matrix[t1][t2] += 1

                        for t1, t2 in matchups:
                            points_against[t1] += week_scores.get(t2, 0)
                            points_against[t2] += week_scores.get(t1, 0)

                    elif week_num == 15:
                        s1, s2, s3, s4 = top4[0], top4[1], top4[2], top4[3]
                        w1 = s1 if week_scores.get(s1, 0) > week_scores.get(s4, 0) else s4
                        w2 = s2 if week_scores.get(s2, 0) > week_scores.get(s3, 0) else s3

                    elif week_num == 16:
                        champ = w1 if week_scores.get(w1, 0) > week_scores.get(w2, 0) else w2
                        b_champs[champ] += 1
                        for p, p_score in team_starters.get(champ, []):
                            if "STREAMER" not in p:
                                cs = championship_player_shares.setdefault(p, {'appearances': 0, 'total_points': 0.0})
                                cs['appearances'] += 1
                                cs['total_points'] += p_score

                    for p in list(injury_clocks.keys()):
                        if injury_clocks[p] > 0: injury_clocks[p] -= 1
                
                assert week_num >= 16, "CRITICAL ERROR: Simulation loop did not properly execute Weeks 15/16 playoff resolution."

                for t in self.team_names:
                    global_season_wins[t][sim_counter] = sim_wins[t]
                    global_season_points[t][sim_counter] = sim_points[t]

                sim_counter += 1

            for t in self.team_names:
                batch_playoff_rates[t].append(b_playoffs[t] / sims_per_batch)
                batch_champ_rates[t].append(b_champs[t] / sims_per_batch)
                batch_toilet_rates[t].append(b_toilets[t] / sims_per_batch)

        print("[SUCCESS] Markov simulation resolved across all batches. Rendering visual telemetry...")
        self.export_and_visualize(
            global_season_wins, global_season_points, batch_playoff_rates,
            batch_champ_rates, batch_toilet_rates, global_trajectories,
            h2h_matrix, points_against, all_play_wins, championship_player_shares,
            max_single_week_score, max_score_team, max_score_week, audit_log, total_sims,
            global_weekly_scores, seed_matrix
        )

    def export_and_visualize(self, wins, points, b_playoffs, b_champs, b_toilets, trajectories,
                             h2h, pts_against, all_play, champ_players, max_score, max_team, max_wk,
                             audit_log, total_sims, global_weekly_scores, seed_matrix):
        sns.set_theme(style="whitegrid")
        plt.rcParams.update({'font.sans-serif': 'DejaVu Sans', 'font.size': 10})
        import matplotlib.ticker as mtick

        # Normalisation basis for every per-week rate exported below.
        #
        # A run starting at self.current_week only simulates the REMAINDER of the regular
        # season, so h2h, all_play, pts_against and global_weekly_scores accumulate over
        # weeks current_week..14, not over all 14. Dividing by the full season length instead
        # scaled every one of those rates by weeks_simulated/14 -- at week 6 that put the
        # "Any Given Sunday" matrix at 64% of its true value, made the schedule-luck index
        # non-zero-sum (+142.86 across the league, when it must sum to 0), and understated
        # points-against per game by 36%. Correct at week 1 by coincidence, wrong from week 2
        # on. See AUDIT_PHASE_1_FINDINGS.md findings 1-3 and
        # tests/test_invariants.py::TestExportedRatesMatchWeeksSimulated.
        #
        # The window is verified empirically, not assumed: h2h/all_play/pts_against are
        # incremented inside run_simulation's `if week_num <= 14` block, so they span the
        # simulated REGULAR-SEASON weeks and exclude the weeks 15-16 playoff rounds.
        first_week_idx = self.current_week - 1
        weeks_simulated = REGULAR_SEASON_WEEKS - first_week_idx
        # Unreachable via run_simulation, which raises earlier for current_week > 14 (top4 is
        # never populated, so week 15 indexes an empty list). Asserted anyway because the
        # failure mode without it is silent: these are float divisions, so a non-positive
        # divisor exports inf/nan rather than raising. Week indexing is Phase 5's subject.
        assert weeks_simulated > 0, (
            f"CRITICAL ABORT: current_week={self.current_week} leaves no regular-season weeks "
            f"to normalise against."
        )
        opponents_per_week = len(self.team_names) - 1
        # 2 decisions per team per week under the league's hybrid H2H + median-beat format,
        # 1 when median scoring is off (the season backtest runs that way -- see
        # SIM_CONFIG['MEDIAN_SCORING_ENABLED']). Previously hardcoded 28.0, which silently
        # assumed the median bonus was always in play.
        decisions_per_week = 2 if SIM_CONFIG.get('MEDIAN_SCORING_ENABLED', True) else 1
        max_season_decisions = REGULAR_SEASON_WEEKS * decisions_per_week

        rows = []
        for t in self.team_names:
            p_mean = np.mean(b_playoffs[t]) * 100
            
            # Handle standard error safely for single-batch smoke tests
            if SIM_CONFIG["NUM_BATCHES"] > 1:
                p_se = (np.std(b_playoffs[t], ddof=1) / np.sqrt(SIM_CONFIG["NUM_BATCHES"])) * 100
            else:
                p_se = 0.0
                
            c_mean = np.mean(b_champs[t]) * 100
            t_mean = np.mean(b_toilets[t]) * 100
            rows.append({
                'Team': t,
                'Expected_Wins': float(np.mean(wins[t])),
                'Expected_Points': float(np.mean(points[t])),
                'Playoff_Pct': p_mean,
                'Playoff_SE': p_se,
                'Champ_Pct': c_mean,
                'Toilet_Pct': t_mean
            })

        summary_df = pd.DataFrame(rows).sort_values(by='Expected_Wins', ascending=False).reset_index(drop=True)

        win_std = float(np.std(summary_df['Expected_Wins']))
        if win_std < 0.5:
            raise ValueError(f"CRITICAL FAILSAPE: Win standard deviation across teams is {win_std:.2f}. Simulation flatlined.")

        team_baselines = {t: self.get_optimal_score(self.rosters[t]) for t in self.team_names}
        b_df = pd.DataFrame(list(team_baselines.items()), columns=['Team', 'Raw_Baseline_Score']).sort_values(by='Raw_Baseline_Score', ascending=True)

        fig, ax = plt.subplots(figsize=(10, 6))
        bars = ax.barh(b_df['Team'], b_df['Raw_Baseline_Score'], color=sns.color_palette("mako", len(b_df)), edgecolor='black', linewidth=0.5)
        ax.set_title(f"Week {self.current_week} True Optimal Lineup Strength (Positional Constraints Applied)", fontsize=13, fontweight='bold', pad=15)
        ax.set_xlabel("Optimal Valid Starting Lineup Baseline (Projected Points)", fontweight='bold')
        ax.set_xlim(0, max(b_df['Raw_Baseline_Score']) * 1.18)

        for bar in bars:
            w = bar.get_width()
            ax.text(w + 1.5, bar.get_y() + bar.get_height()/2, f"{w:.1f} pts", va='center', ha='left', fontsize=9, fontweight='bold')

        sns.despine(top=True, right=True)
        plt.tight_layout()
        plt.savefig(power_rankings_chart_path(self.current_week), dpi=300)
        plt.close()

        fig, axes = plt.subplots(1, 3, figsize=(18, 6), sharey=True)
        plot_df = summary_df.sort_values(by='Expected_Wins', ascending=True)
        metrics = [
            ('Playoff Odds (Top 4 Finish)', 'Playoff_Pct', '#2ecc71'),
            ('Championship Win Equity', 'Champ_Pct', '#f1c40f'),
            ('Toilet Bowl / Last Place Risk', 'Toilet_Pct', '#e74c3c')
        ]

        for idx, (title, col, col_color) in enumerate(metrics):
            ax = axes[idx]
            b_bars = ax.barh(plot_df['Team'], plot_df[col], color=col_color, alpha=0.85, edgecolor='black', linewidth=0.6)
            ax.set_title(title, fontsize=12, fontweight='bold', pad=12)
            ax.set_xlim(0, max(plot_df[col].max() * 1.25, 10.0))
            ax.xaxis.set_major_formatter(mtick.PercentFormatter())

            for bar in b_bars:
                w = bar.get_width()
                ax.text(w + 0.8, bar.get_y() + bar.get_height()/2, f"{w:.1f}%", va='center', ha='left', fontsize=9, fontweight='bold')
            sns.despine(ax=ax, top=True, right=True)

        plt.suptitle(f"Week {self.current_week} Syndicate Forecast: Season Likelihoods ({total_sims:,} Simulations)", fontsize=14, fontweight='bold', y=1.02)
        plt.tight_layout()
        plt.savefig(season_outcomes_chart_path(self.current_week), dpi=300, bbox_inches='tight')
        plt.close()

        fig, axes = plt.subplots(2, 4, figsize=(20, 10), sharex=True, sharey=True)
        axes = axes.flatten()
        weeks_range = np.arange(1, 15)
        sorted_teams = summary_df['Team'].tolist()
        palette = sns.color_palette('tab10', len(sorted_teams))
        coom_mean = np.mean(trajectories['Legion of Coom'], axis=0) if 'Legion of Coom' in trajectories else np.zeros(14)

        for idx, t in enumerate(sorted_teams):
            ax = axes[idx]
            team_matrix = trajectories[t]
            mean_w = np.mean(team_matrix, axis=0)
            p_01 = np.percentile(team_matrix, 1, axis=0)
            p_10 = np.percentile(team_matrix, 10, axis=0)
            p_25 = np.percentile(team_matrix, 25, axis=0)
            p_75 = np.percentile(team_matrix, 75, axis=0)
            p_90 = np.percentile(team_matrix, 90, axis=0)
            p_99 = np.percentile(team_matrix, 99, axis=0)

            color = 'purple' if t == 'Legion of Coom' else palette[idx]
            ax.fill_between(weeks_range, p_10, p_90, color=color, alpha=0.18, label='80% Conf. Interval')
            ax.fill_between(weeks_range, p_25, p_75, color=color, alpha=0.35, label='50% Likely Range')
            ax.plot(weeks_range, p_99, color='forestgreen', linestyle=':', linewidth=1.4, alpha=0.85, label='1% Best Case')
            ax.plot(weeks_range, p_01, color='crimson', linestyle=':', linewidth=1.4, alpha=0.85, label='1% Worst Case')

            if t != 'Legion of Coom':
                ax.plot(weeks_range, coom_mean, color='black', linestyle='--', linewidth=1.2, alpha=0.6, label='Legion of Coom Pace')

            ax.plot(weeks_range, mean_w, color='black' if t == 'Legion of Coom' else color, linewidth=2.5, label='Expected Mean')
            ax.axhline(16, color='gold', linestyle='--', linewidth=1.5, alpha=0.9)

            p_pct = summary_df.loc[summary_df['Team'] == t, 'Playoff_Pct'].values[0]
            ax.set_title(f'{t}\n(Exp: {mean_w[-1]:.1f} W | Playoff Odds: {p_pct:.1f}%)', fontsize=11, fontweight='bold', pad=8)
            ax.set_xticks(range(2, 15, 2))
            ax.set_yticks(range(0, 29, 4))
            ax.set_xlim(1, 14)
            ax.set_ylim(0, 28)

        fig.text(0.5, 0.02, 'Regular Season Week', ha='center', fontsize=13, fontweight='bold')
        fig.text(0.01, 0.5, 'Cumulative Wins (H2H + Median)', va='center', rotation='vertical', fontsize=13, fontweight='bold')

        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 0.98), ncol=6, frameon=True, facecolor='white', fontsize=10)
        fig.suptitle(f'Week {self.current_week} Syndicate Forecast: Season Trajectory (14-Week Regular Season)', fontsize=15, fontweight='bold', y=1.02)
        plt.tight_layout()
        plt.subplots_adjust(top=0.88, bottom=0.08, left=0.05, right=0.98)
        plt.savefig(all_teams_trajectories_chart_path(self.current_week), dpi=300)
        plt.close()

        violin_rows = []
        for t in summary_df['Team']:
            for w in wins[t]:
                violin_rows.append({'Team': t, 'Total Wins': w})
        df_violin = pd.DataFrame(violin_rows)

        fig, ax = plt.subplots(figsize=(14, 8))
        sns.violinplot(x='Total Wins', y='Team', data=df_violin, hue='Team', palette='magma', inner='quartile', density_norm='width', linewidth=1.5, legend=False, ax=ax)
        ax.axvline(14, color='black', linestyle='--', linewidth=2, alpha=0.9, label='.500 Break-Even (14 Wins)')

        for idx, row in summary_df.iterrows():
            ax.text(
                row['Expected_Wins'], idx - 0.15, f"{row['Expected_Wins']:.1f} W",
                color='black', fontweight='bold', ha='center', va='center', fontsize=10,
                bbox=dict(facecolor='white', alpha=0.85, edgecolor='none', boxstyle='round,pad=0.2')
            )

        ax.set_title(f'Week {self.current_week} Syndicate Forecast: Expected Wins & Variance Density', fontsize=15, fontweight='bold', pad=15)
        ax.set_xlabel('Total Regular Season Wins (28 Max Decisions)', fontsize=12, fontweight='bold')
        ax.set_ylabel('')
        ax.set_xlim(4, 28)
        ax.set_xticks(range(4, 29, 2))
        ax.legend(loc='upper right', frameon=True, facecolor='white')
        sns.despine(top=True, right=True)
        plt.tight_layout()
        plt.savefig(expected_wins_chart_path(self.current_week), dpi=300)
        plt.close()

        win_pct_matrix = pd.DataFrame.from_dict(h2h, orient='index') / (total_sims * weeks_simulated) * 100
        # NOTE: do not mutate win_pct_matrix.values in place (np.fill_diagonal). Under pandas'
        # Copy-on-Write semantics (default from pandas 2.x, mandatory in 3.x), .values on a
        # DataFrame produced by arithmetic can be a read-only view, and this raises
        # "ValueError: underlying array is read-only" on any sufficiently modern pandas.
        # .mask() achieves the same result (NaN out the diagonal) without touching the buffer.
        win_pct_matrix = win_pct_matrix.mask(np.eye(len(win_pct_matrix), dtype=bool))

        plt.figure(figsize=(10, 8))
        sns.heatmap(win_pct_matrix, annot=True, fmt=".1f", cmap="RdYlGn", cbar_kws={'label': 'Win Probability (%)'}, linewidths=.5)
        plt.title(f"Week {self.current_week} 'Any Given Sunday' H2H Win Probability Matrix", fontsize=13, fontweight='bold', pad=15)
        plt.tight_layout()
        plt.savefig(h2h_heatmap_chart_path(self.current_week), dpi=300)
        plt.close()

        # NOTE: this is deliberately NOT ranked by raw appearance-in-championship-lineup count.
        # A starting kicker or sole-starting DL/LB/DB has zero competition for their roster slot,
        # so they appear in ~100% of their own team's championship-winning simulations regardless
        # of actual scoring impact -- that made the old "most valuable players" list nearly
        # identical to "which team won the league" with a kicker on top. Rank by average points
        # actually scored in those championship-winning weeks instead, which is a genuine
        # per-player value signal. Require >= 50 appearances so single-digit-sample noise (e.g. a
        # streamer-tier player who happened to start once) can't dominate the top of the list.
        MIN_CHAMP_APPEARANCES_FOR_RANKING = 50
        eligible_champ_players = {
            k: v for k, v in champ_players.items() if v['appearances'] >= MIN_CHAMP_APPEARANCES_FOR_RANKING
        }
        sorted_champ_players = sorted(
            eligible_champ_players.items(),
            key=lambda x: x[1]['total_points'] / x[1]['appearances'],
            reverse=True
        )
        top_20_valuable_assets = {
            k: {
                "avg_points_per_championship_week": round(v['total_points'] / v['appearances'], 2),
                "championship_lineup_appearance_pct": round((v['appearances'] / total_sims) * 100, 1),
            }
            for k, v in sorted_champ_players[:20]
        }

        # KNOWN LIMITATION, not fixed here: the two terms below cover different spans on a
        # mid-season run. actual_exp_pct is a FULL-season win rate (wins[] carries the banked
        # results of already-completed weeks), while true_win_pct is an all-play rate over only
        # the weeks this run simulated. Correcting the divisors restores the property that
        # luck_rating sums to zero across the league -- one team's easy schedule is another's
        # hard one -- but making the two spans genuinely comparable needs historical all-play
        # recomputed from weekly_actuals, which is a real feature rather than a divisor change.
        # Recorded as an open item rather than papered over.
        schedule_luck = {}
        for t in self.team_names:
            true_win_pct = all_play[t] / (total_sims * weeks_simulated * opponents_per_week)
            actual_exp_pct = np.mean(wins[t]) / max_season_decisions
            schedule_luck[t] = {
                "luck_rating": round(float(actual_exp_pct - true_win_pct) * 100, 2),
                "avg_points_against_per_game": round(float(pts_against[t] / total_sims) / weeks_simulated, 2)
            }

        syndicate_insights = {
            "engine_simulations_run": total_sims,
            "batches_evaluated": SIM_CONFIG["NUM_BATCHES"],
            "highest_single_week_score_observed": round(float(max_score), 2),
            "team_with_highest_ceiling_game": max_team,
            "week_of_highest_score": max_wk,
            "most_valuable_players_championship_shares": top_20_valuable_assets,
            "schedule_luck_index": schedule_luck,
            "legion_of_coom_insights": {
                "championship_probability": round(float(summary_df.loc[summary_df['Team'] == 'Legion of Coom', 'Champ_Pct'].values[0]), 2) if 'Legion of Coom' in summary_df['Team'].values else 0.0,
                "playoff_probability": round(float(summary_df.loc[summary_df['Team'] == 'Legion of Coom', 'Playoff_Pct'].values[0]), 2) if 'Legion of Coom' in summary_df['Team'].values else 0.0,
                "playoff_standard_error": round(float(summary_df.loc[summary_df['Team'] == 'Legion of Coom', 'Playoff_SE'].values[0]), 3) if 'Legion of Coom' in summary_df['Team'].values else 0.0
            }
        }

        diagnostics = {}
        for t in self.team_names:
            p_prob = summary_df.loc[summary_df['Team'] == t, 'Playoff_Pct'].values[0]
            p_se = summary_df.loc[summary_df['Team'] == t, 'Playoff_SE'].values[0]
            exp_w = summary_df.loc[summary_df['Team'] == t, 'Expected_Wins'].values[0]
            exp_future = exp_w - (self.actual_h2h_wins[t] + self.actual_median_wins[t])
            magic_num = max(0, 16 - int(self.actual_h2h_wins[t] + self.actual_median_wins[t]))

            diagnostics[t] = {
                'current_state': {
                    'actual_wins_banked': int(self.actual_h2h_wins[t] + self.actual_median_wins[t]),
                    'actual_points_banked': round(float(self.actual_points[t]), 2),
                    'remaining_faab': float(self.current_faab[t]),
                },
                'forecast': {
                    'expected_final_wins': round(float(exp_w), 2),
                    'expected_future_wins': round(float(exp_future), 2),
                    'playoff_probability_pct': round(float(p_prob), 1),
                    'playoff_standard_error': round(float(p_se), 3),
                    'is_mathematically_eliminated': bool(p_prob == 0.0),
                    'approximate_magic_number': int(magic_num),
                },
            }

        ai_matrix = {
            "metadata": {"week": self.current_week, "simulations": total_sims, "batches": SIM_CONFIG["NUM_BATCHES"]},
            "power_rankings_baseline_pts": team_baselines,
            "season_outcomes": summary_df.to_dict(orient='records'),
            "h2h_win_probability_matrix": win_pct_matrix.to_dict(orient='index'),
            "win_distributions": {},
            "weekly_trajectories": {}
        }

        for t in self.team_names:
            w_arr = wins[t]
            ai_matrix["win_distributions"][t] = {
                "expected_mean": round(float(np.mean(w_arr)), 2),
                "p01_worst_case": round(float(np.percentile(w_arr, 1)), 2),
                "p10_floor": round(float(np.percentile(w_arr, 10)), 2),
                "p25_lower_bound": round(float(np.percentile(w_arr, 25)), 2),
                "p50_median": round(float(np.percentile(w_arr, 50)), 2),
                "p75_upper_bound": round(float(np.percentile(w_arr, 75)), 2),
                "p90_ceiling": round(float(np.percentile(w_arr, 90)), 2),
                "p99_best_case": round(float(np.percentile(w_arr, 99)), 2)
            }
            t_mat = trajectories[t]
            ai_matrix["weekly_trajectories"][t] = {
                "expected_cumulative_wins_by_week": np.mean(t_mat, axis=0).tolist()
            }

        # -------------------------------------------------------------
        # Finishing Seed Probability Distribution
        # -------------------------------------------------------------
        seed_df = pd.DataFrame.from_dict(seed_matrix, orient='index') / total_sims * 100
        seed_df.columns = [f"Seed {i}" for i in range(1, len(self.team_names) + 1)]
        seed_df = seed_df.loc[summary_df['Team']] # Sort by expected wins

        plt.figure(figsize=(11, 7))
        sns.heatmap(seed_df, annot=True, fmt=".1f", cmap="Purples", cbar_kws={'label': 'Probability (%)'}, linewidths=.5)
        plt.title(f"Week {self.current_week} Regular Season Finishing Seed Probabilities", fontsize=13, fontweight='bold', pad=15)
        plt.tight_layout()
        plt.savefig(seeding_distribution_path(self.current_week), dpi=300)
        plt.close()

        # -------------------------------------------------------------
        # Weekly Scoring Density (KDE)
        # -------------------------------------------------------------
        plt.figure(figsize=(14, 7))
        
        # global_weekly_scores is allocated as a full (total_sims, 14) array but written to
        # only for the weeks this run simulates, so on a mid-season run every column before
        # current_week is still at its initialised zero. Those cells are structural absences,
        # not observed scores of zero, and every statistic below must skip them: at week 6
        # they are 35.7% of the array, which dragged this chart's median-cut line from 175.50
        # to 112.82 and put a spike of zeros into each team's density estimate (hidden from
        # view only because xlim starts at 60, but still setting the KDE's bandwidth).
        # See AUDIT_PHASE_1_FINDINGS.md finding 4.
        played_weekly_scores = {t: global_weekly_scores[t][:, first_week_idx:]
                                for t in self.team_names}

        # Calculate the average median cutoff across simulations to plot a baseline
        median_cutoffs = []
        for s_idx in range(min(total_sims, 1000)): # Sample first 1000 for speed
            for w_idx in range(weeks_simulated):
                scores = [played_weekly_scores[t][s_idx, w_idx] for t in self.team_names]
                median_cutoffs.append(np.median(scores))
        avg_median_cut = float(np.mean(median_cutoffs))

        for t in summary_df['Team']:
            all_scores_flat = played_weekly_scores[t].flatten()
            sns.kdeplot(all_scores_flat, label=f"{t} (Exp: {np.mean(all_scores_flat):.1f})", linewidth=2.0)

        plt.axvline(avg_median_cut, color='black', linestyle='--', linewidth=2.0, label=f"Avg Median Cut({avg_median_cut:.1f})")
        plt.title(f"Week {self.current_week} Team Weekly Scoring Density Profiles", fontsize=14, fontweight='bold', pad=15)
        plt.xlabel("Simulated Weekly Points Scored", fontsize=11, fontweight='bold')
        plt.ylabel("Probability Density", fontsize=11, fontweight='bold')
        plt.xlim(60, 250)
        plt.legend(loc='upper right', frameon=True, facecolor='white', fontsize=9)
        sns.despine()
        plt.tight_layout()
        plt.savefig(weekly_scoring_density_path(self.current_week), dpi=300)
        plt.close()

        # -------------------------------------------------------------
        # NEW DATA: Append to ai_matrix before dumping to JSON
        # -------------------------------------------------------------
        ai_matrix["finishing_seed_probabilities"] = seed_df.to_dict(orient='index')
        ai_matrix["weekly_score_percentiles"] = {}
        
        for t in self.team_names:
            # Played weeks only -- see played_weekly_scores above. Including the unwritten
            # columns put p10_floor at exactly 0.00 for every team, i.e. the export stated a
            # 10% chance of a team scoring nothing in a week.
            scores_flat = played_weekly_scores[t].flatten()
            ai_matrix["weekly_score_percentiles"][t] = {
                "mean": round(float(np.mean(scores_flat)), 2),
                "std": round(float(np.std(scores_flat)), 2),
                "p10_floor": round(float(np.percentile(scores_flat, 10)), 2),
                "p90_ceiling": round(float(np.percentile(scores_flat, 90)), 2)
            }

        save_json(live_season_forecast_path(self.current_week), diagnostics)
        save_json(model_learning_report_path(self.current_week), self.calibration_report)
        save_json(syndicate_insights_path(self.current_week), syndicate_insights)
        save_json(syndicate_comprehensive_matrix_path(self.current_week), ai_matrix)
        save_json(SIMULATION_AUDIT_LOG_FILE, audit_log)

        print(f"\n[EXPORT COMPLETE] Telemetry and 5 visual artifacts rendered for Week {self.current_week}.")

def main():
    sim = FantasySimulationEngine()
    sim.run_simulation()
"""
player_level_backtest.py

Validates the model's core statistical "magic numbers" -- VOLATILITY_CONSTANTS (aleatoric
std), the epistemic shrinkage mechanism, and SIM_CONFIG['CORRELATIONS'] -- directly against
real 2025 player-level historical data.

WHY THIS IS SEPARATE FROM backtest_harness.py: the season-level backtest showed a real,
confirmed confound (2025 used team-DEFENSE scoring, not this engine's individual IDP slots,
so ~3/13 starting slots are generic streamer noise for every team, every week) that we
deliberately chose not to fix -- see the conversation this was built from. This file never
constructs a team roster, counts a win, or determines a playoff outcome, so that confound
cannot enter here at all: it only ever compares real, individual QB/RB/WR/TE weekly fantasy
scores against what the model's own formulas would have predicted for them.

WHAT'S TESTED:
1. Aleatoric variance (VOLATILITY_CONSTANTS): for real players with enough tracked weeks, is
   their empirical week-to-week score variance close to what k_val * sqrt(mean) predicts?
2. Correlation structure (SIM_CONFIG['CORRELATIONS']): do QB-WR1/WR2/TE/RB and WR-WR pairs on
   the same real NFL team actually correlate the way the copula assumes?
3. Epistemic calibration: using the EXACT posterior-update formula from
   FantasySimulationEngine._apply_bayesian_updates (verified line-by-line against the live
   source, and cross-checked to produce identical output to that real method on identical
   inputs -- see test_compute_bayesian_posterior_matches_real_production_method), do real
   future outcomes land where the model's own uncertainty says they should?

DATA SCOPE: player-week samples come from real weekly fantasy scores of players rostered
across this league's 8 real teams in 2025 (via the same players_points extraction already
used in backtest_harness.py) -- not every NFL player, but a real, substantial sample
(typically 100+ distinct players, several hundred player-weeks) covering the offensive
positions that actually matter for a fantasy roster.
"""
import math

import numpy as np

from fantasy_sim import sync
from fantasy_sim.config import ANON_VOLATILITY_K, ANON_EPISTEMIC_RATE
from fantasy_sim import simulation as simmod
from fantasy_sim import backtest_season as bt


# ============================================================================
# Data collection
# ============================================================================

def collect_real_player_weekly_scores(season_matchups, players_db, min_active_weeks=4):
    """
    Aggregates real per-player weekly fantasy scores across a full season, reusing the exact
    same extraction logic already used in production (sync._extract_weekly_player_scores) --
    not reimplemented here.

    Weeks with a real score of exactly 0 are excluded: these are almost always a bye week or
    inactive status, a fundamentally different kind of variance than in-game performance
    variance (which is what VOLATILITY_CONSTANTS models, and what SIM_CONFIG['INJURY_RATES']
    and bye-week handling separately cover in production).

    Returns {player_name: {'pos': str, 'team': str, 'weekly_scores': {week: score}}} for
    players with at least min_active_weeks real, nonzero scores.
    """
    weekly_by_player = {}
    for wk, matchups in season_matchups.items():
        wk_scores = sync._extract_weekly_player_scores(matchups, players_db)
        for name, score in wk_scores.items():
            if score > 0:
                weekly_by_player.setdefault(name, {})[wk] = score

    name_to_meta = {}
    for pid, p in players_db.items():
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        if name:
            name_to_meta[name] = {
                'pos': simmod.normalize_position(p.get('position', 'FLEX')),
                'team': p.get('team') or 'FA',
            }

    result = {}
    for name, weeks in weekly_by_player.items():
        if len(weeks) >= min_active_weeks:
            meta = name_to_meta.get(name, {'pos': 'FLEX', 'team': 'FA'})
            result[name] = {'pos': meta['pos'], 'team': meta['team'], 'weekly_scores': weeks}
    return result


# ============================================================================
# Test 1: aleatoric variance (VOLATILITY_CONSTANTS)
# ============================================================================

def analyze_aleatoric_variance(player_data):
    """
    For each real player, compares their EMPIRICAL weekly-score std against what
    VOLATILITY_CONSTANTS[pos] * sqrt(mean) predicts at their real mean. Aggregates by
    position: a median ratio consistently above 1.0 means the constant UNDERSTATES real
    variance for that position (should increase); consistently below 1.0 means it
    OVERSTATES it (should decrease).

    Returns (summary_by_position, per_player_detail).
    """
    by_position = {}
    for name, data in player_data.items():
        pos = data['pos']
        if pos not in sync.VOLATILITY_CONSTANTS:
            continue
        scores = np.array(list(data['weekly_scores'].values()), dtype=float)
        empirical_mean = float(np.mean(scores))
        empirical_std = float(np.std(scores, ddof=1))
        predicted_std = sync.VOLATILITY_CONSTANTS[pos] * math.sqrt(max(0.5, empirical_mean))
        if predicted_std <= 0:
            continue
        ratio = empirical_std / predicted_std
        by_position.setdefault(pos, []).append({
            'name': name, 'n_weeks': len(scores),
            'empirical_mean': round(empirical_mean, 2), 'empirical_std': round(empirical_std, 2),
            'predicted_std': round(predicted_std, 2), 'ratio': round(ratio, 3),
        })

    summary = {}
    for pos, entries in by_position.items():
        ratios = [e['ratio'] for e in entries]
        median_ratio = float(np.median(ratios))
        summary[pos] = {
            'n_players': len(entries),
            'median_ratio': round(median_ratio, 3),
            'mean_ratio': round(float(np.mean(ratios)), 3),
            'current_k_val': sync.VOLATILITY_CONSTANTS[pos],
            'suggested_k_val': round(sync.VOLATILITY_CONSTANTS[pos] * median_ratio, 3),
        }
    return summary, by_position


# ============================================================================
# Test 2: correlation structure (SIM_CONFIG['CORRELATIONS'])
# ============================================================================

def _aligned_pearson_corr(weeks_a, weeks_b, min_common_weeks=4):
    """Pearson correlation between two players' weekly scores, only over weeks BOTH have a
    real recorded score. Returns None if there isn't enough overlap or either series is
    constant (undefined correlation)."""
    common = sorted(set(weeks_a) & set(weeks_b))
    if len(common) < min_common_weeks:
        return None
    a = np.array([weeks_a[w] for w in common])
    b = np.array([weeks_b[w] for w in common])
    if np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def analyze_correlations(player_data):
    """
    Tests SIM_CONFIG['CORRELATIONS'] directly: for each real NFL team represented in
    player_data, identifies its QB and ranks its WRs by real average score (the same
    "who's WR1 vs WR2" logic the simulation's own pass-catcher hierarchy uses), then computes
    the REAL empirical week-by-week correlation between the QB and each teammate.

    Each individual pair's correlation, with as few as 4 overlapping weeks, is itself noisy --
    but aggregating the mean/median across every real team-pair found gives a genuinely
    informative estimate, the same way averaging many noisy measurements reduces overall
    noise. n_pairs is reported explicitly so you can judge how much to trust each number.
    """
    by_team = {}
    for name, data in player_data.items():
        if data['team'] in (None, 'FA'):
            continue
        by_team.setdefault(data['team'], []).append(name)

    pair_scores = {'QB_WR1': [], 'QB_WR2': [], 'QB_TE': [], 'QB_RB': [], 'WR_WR': []}

    for team, names in by_team.items():
        qbs = [n for n in names if player_data[n]['pos'] == 'QB']
        if not qbs:
            continue
        qb = max(qbs, key=lambda n: len(player_data[n]['weekly_scores']))
        qb_weeks = player_data[qb]['weekly_scores']

        wrs = sorted(
            [n for n in names if player_data[n]['pos'] == 'WR'],
            key=lambda n: -np.mean(list(player_data[n]['weekly_scores'].values()))
        )
        tes = [n for n in names if player_data[n]['pos'] == 'TE']
        rbs = [n for n in names if player_data[n]['pos'] == 'RB']

        if len(wrs) >= 1:
            r = _aligned_pearson_corr(qb_weeks, player_data[wrs[0]]['weekly_scores'])
            if r is not None: pair_scores['QB_WR1'].append(r)
        if len(wrs) >= 2:
            r = _aligned_pearson_corr(qb_weeks, player_data[wrs[1]]['weekly_scores'])
            if r is not None: pair_scores['QB_WR2'].append(r)
            r = _aligned_pearson_corr(player_data[wrs[0]]['weekly_scores'], player_data[wrs[1]]['weekly_scores'])
            if r is not None: pair_scores['WR_WR'].append(r)
        for te in tes:
            r = _aligned_pearson_corr(qb_weeks, player_data[te]['weekly_scores'])
            if r is not None: pair_scores['QB_TE'].append(r)
        for rb in rbs:
            r = _aligned_pearson_corr(qb_weeks, player_data[rb]['weekly_scores'])
            if r is not None: pair_scores['QB_RB'].append(r)

    summary = {}
    for pair_type, rs in pair_scores.items():
        if not rs:
            continue
        summary[pair_type] = {
            'n_pairs': len(rs),
            'empirical_mean_corr': round(float(np.mean(rs)), 3),
            'empirical_median_corr': round(float(np.median(rs)), 3),
            'current_assumed_corr': simmod.SIM_CONFIG['CORRELATIONS'].get(pair_type),
        }
    return summary


# ============================================================================
# Test 3: epistemic calibration
# ============================================================================

def compute_bayesian_posterior(prior_mean, prior_std_epistemic, real_scores, n_0=4.0):
    """
    Exact replica of FantasySimulationEngine._apply_bayesian_updates' per-player posterior
    update formula (2026_sleeper_simulation_adv.py). Verified line-by-line against the live
    source before writing this, and cross-checked to produce IDENTICAL output to the real
    method on identical synthetic inputs -- see
    test_compute_bayesian_posterior_matches_real_production_method -- rather than trusted as
    a by-eye copy.
    """
    prior_var = max(0.1, float(prior_std_epistemic) ** 2)
    n = len(real_scores)
    if n == 0:
        return float(prior_mean), math.sqrt(prior_var)

    actual_mean = float(np.mean(real_scores))
    raw_actual_var = float(np.var(real_scores)) if n > 1 else prior_var
    actual_var = max(raw_actual_var, 0.5 * prior_var)

    post_var = 1.0 / ((n_0 / prior_var) + (n / actual_var))
    post_mean = ((n_0 * prior_mean / prior_var) + (n * actual_mean / actual_var)) * post_var
    return float(post_mean), float(math.sqrt(post_var))


def compute_calibration_z(prior_mean, prior_std_epistemic, before_scores, after_scores, pos, n_0=4.0):
    """
    Computes a single player's calibration z-score, correctly accounting for TWO distinct
    sources of expected deviation between real_future_mean and the model's posterior:
      1. Genuine epistemic uncertainty about the true mean (post_std, via
         compute_bayesian_posterior).
      2. real_future_mean is itself only a FINITE-SAMPLE average of real future weeks -- even
         a perfectly accurate prior would still show some apparent "error" against a noisy
         few-week sample, purely from game-to-game aleatoric variance not fully averaging out.
         This is exactly VOLATILITY_CONSTANTS[pos]^2 * post_mean / n_after (the model's own
         aleatoric-variance formula, evaluated at the posterior mean, scaled down by sample
         size the way a sample mean's variance always is).

    Without (2), a well-calibrated model would still show std_z > 1 whenever n_after is small
    -- a real check on the tool's own output caught this before treating the resulting
    suggested EPISTEMIC_ERROR_RATES corrections as trustworthy: at typical sample sizes here,
    the missing noise term is comparable in size to the CURRENT epistemic std, but much
    smaller than the corrections TEST 3b's search was suggesting -- meaning the direction of
    those suggestions was real, but the exact magnitude was inflated. Returns None if
    post_std can't be computed or there are no future weeks.
    """
    post_mean, post_std = compute_bayesian_posterior(prior_mean, prior_std_epistemic, before_scores)
    n_after = len(after_scores)
    if post_std <= 0 or n_after == 0:
        return None

    aleatoric_std_at_post_mean = sync.VOLATILITY_CONSTANTS.get(pos, ANON_VOLATILITY_K) * math.sqrt(max(0.5, post_mean))
    future_mean_sampling_var = (aleatoric_std_at_post_mean ** 2) / n_after
    total_std = math.sqrt(post_std ** 2 + future_mean_sampling_var)

    real_future_mean = float(np.mean(after_scores))
    z = (real_future_mean - post_mean) / total_std
    return {
        'z': z, 'post_mean': post_mean, 'post_std': post_std,
        'future_mean_sampling_std': math.sqrt(future_mean_sampling_var),
        'total_std': total_std, 'real_future_mean': real_future_mean,
    }


def analyze_epistemic_calibration(player_data, checkpoint_week, min_future_weeks=3, min_peers=3):
    """
    For each real player with real weeks both before and after checkpoint_week: builds an
    "as-of-checkpoint" posterior, then compares that posterior against the player's REAL
    average score in weeks at/after the checkpoint.

    PRIOR CHOICE: uses the LEAVE-ONE-OUT mean of every OTHER tracked player's pre-checkpoint
    performance at the same position -- NOT BASE_STREAMER_MEANS. An earlier version of this
    function used BASE_STREAMER_MEANS (production's genuinely-uninformed REPLACEMENT-LEVEL
    floor, meant for a true cold start / streamer injection). That is systematically far too
    low a starting point here: collect_real_player_weekly_scores() only keeps players with
    several real active weeks, so by construction every player in this dataset is an
    established, actively-rostered contributor, not a replacement-level player. Starting
    every one of them from a replacement-level floor produced a large, positive,
    same-direction z-score bias for every position -- reflecting that choice of prior, not
    the shrinkage mechanism's real calibration. The leave-one-out peer mean only ever uses
    OTHER players' PRE-checkpoint scores (never their own post-checkpoint data, and never
    this player's own data), so it stays fully look-ahead-safe while giving a realistic,
    non-degenerate starting guess -- isolating what this test actually wants to measure:
    given a reasonable prior, does shrinking toward real data converge correctly.

    Standardizes each player's error as z = (real_future_mean - post_mean) / total_std, where
    total_std combines genuine epistemic uncertainty with the sampling noise inherent in
    averaging only a few real future weeks -- see compute_calibration_z's docstring for why
    that second term matters and was added after an earlier version of this function omitted
    it. If std_epistemic is well-calibrated, these z-scores across many players should have
    approximately unit variance: std(z) >> 1 means the model is OVERCONFIDENT (real
    surprises are bigger than it thinks); std(z) << 1 means it's UNDERCONFIDENT (padding
    uncertainty more than the real data justifies). mean(z) far from 0 would indicate a
    systematic directional bias in the shrinkage itself.
    """
    pre_checkpoint_means = {}
    for name, data in player_data.items():
        before_scores = [s for w, s in data['weekly_scores'].items() if w < checkpoint_week]
        if before_scores:
            pre_checkpoint_means.setdefault(data['pos'], {})[name] = float(np.mean(before_scores))

    by_position = {}
    for name, data in player_data.items():
        pos = data['pos']
        weeks = data['weekly_scores']
        before = [s for w, s in weeks.items() if w < checkpoint_week]
        after = [s for w, s in weeks.items() if w >= checkpoint_week]
        if len(after) < min_future_weeks:
            continue

        peer_means = [m for n, m in pre_checkpoint_means.get(pos, {}).items() if n != name]
        if len(peer_means) < min_peers:
            continue
        prior_mean = float(np.mean(peer_means))
        prior_std_epistemic = sync.EPISTEMIC_ERROR_RATES.get(pos, ANON_EPISTEMIC_RATE) * prior_mean

        result = compute_calibration_z(prior_mean, prior_std_epistemic, before, after, pos)
        if result is None:
            continue

        by_position.setdefault(pos, []).append({
            'name': name, 'n_weeks_before': len(before), 'n_weeks_after': len(after),
            'prior_mean': round(prior_mean, 2), 'post_mean': round(result['post_mean'], 2),
            'post_std': round(result['post_std'], 2),
            'future_mean_sampling_std': round(result['future_mean_sampling_std'], 2),
            'real_future_mean': round(result['real_future_mean'], 2), 'z': round(result['z'], 3),
        })

    summary = {}
    for pos, entries in by_position.items():
        zs = [e['z'] for e in entries]
        summary[pos] = {
            'n_players': len(entries),
            'mean_z': round(float(np.mean(zs)), 3),
            'std_z': round(float(np.std(zs, ddof=1)), 3) if len(zs) > 1 else None,
        }
    return summary, by_position


# ============================================================================
# Orchestration
# ============================================================================

def suggest_epistemic_rate_multiplier(player_data, checkpoint_week, min_future_weeks=3, min_peers=3,
                                       candidate_multipliers=None):
    """
    For each position, searches over a range of multipliers applied to EPISTEMIC_ERROR_RATES
    (holding everything else -- including the peer-based prior mean -- fixed) and reports
    which multiplier brings std_z closest to 1.0. This is a direct, searched correction, not a
    hand-derived estimate: the relationship between the epistemic rate and the resulting z is
    nonlinear (the rate affects both post_mean and post_std through the shrinkage formula), so
    "just multiply by roughly X" isn't something you can derive by inspection -- it has to
    actually be searched for.
    """
    if candidate_multipliers is None:
        candidate_multipliers = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0]

    pre_checkpoint_means = {}
    for name, data in player_data.items():
        before_scores = [s for w, s in data['weekly_scores'].items() if w < checkpoint_week]
        if before_scores:
            pre_checkpoint_means.setdefault(data['pos'], {})[name] = float(np.mean(before_scores))

    results = {}
    for pos in set(d['pos'] for d in player_data.values()):
        base_rate = sync.EPISTEMIC_ERROR_RATES.get(pos, ANON_EPISTEMIC_RATE)
        best_mult, best_diff, best_std_z = None, None, None
        for mult in candidate_multipliers:
            zs = []
            for name, data in player_data.items():
                if data['pos'] != pos:
                    continue
                weeks = data['weekly_scores']
                before = [s for w, s in weeks.items() if w < checkpoint_week]
                after = [s for w, s in weeks.items() if w >= checkpoint_week]
                if len(after) < min_future_weeks:
                    continue
                peer_means = [m for n, m in pre_checkpoint_means.get(pos, {}).items() if n != name]
                if len(peer_means) < min_peers:
                    continue
                prior_mean = float(np.mean(peer_means))
                prior_std_epistemic = base_rate * mult * prior_mean
                result = compute_calibration_z(prior_mean, prior_std_epistemic, before, after, pos)
                if result is None:
                    continue
                zs.append(result['z'])
            if len(zs) < 2:
                continue
            std_z = float(np.std(zs, ddof=1))
            diff = abs(std_z - 1.0)
            if best_diff is None or diff < best_diff:
                best_diff, best_mult, best_std_z = diff, mult, std_z
        if best_mult is not None:
            results[pos] = {
                'current_rate': base_rate, 'suggested_multiplier': best_mult,
                'suggested_rate': round(base_rate * best_mult, 3), 'achieved_std_z': round(best_std_z, 3),
            }
    return results


def run_full_player_level_backtest(season_league_id=bt.BACKTEST_SEASON_LEAGUE_ID,
                                    checkpoint_week=4, min_active_weeks=4):
    """Fetches real 2025 data once, then runs all three analyses and prints a report."""
    players_db = sync.update_player_cache()
    season_matchups = bt.fetch_season_matchups(season_league_id)
    player_data = collect_real_player_weekly_scores(season_matchups, players_db, min_active_weeks)
    print(f"Collected real weekly scores for {len(player_data)} players "
          f"(>= {min_active_weeks} active weeks each).\n")

    print("=" * 70)
    print("TEST 1: ALEATORIC VARIANCE (VOLATILITY_CONSTANTS)")
    print("=" * 70)
    aleatoric_summary, _ = analyze_aleatoric_variance(player_data)
    for pos, s in sorted(aleatoric_summary.items()):
        print(f"  {pos:5s} n={s['n_players']:>3}  current_k={s['current_k_val']}  "
              f"median_ratio={s['median_ratio']}  suggested_k={s['suggested_k_val']}")

    print("\n" + "=" * 70)
    print("TEST 2: CORRELATION STRUCTURE (SIM_CONFIG['CORRELATIONS'])")
    print("=" * 70)
    corr_summary = analyze_correlations(player_data)
    for pair_type, s in sorted(corr_summary.items()):
        print(f"  {pair_type:8s} n_pairs={s['n_pairs']:>3}  "
              f"empirical_mean={s['empirical_mean_corr']}  assumed={s['current_assumed_corr']}")

    print("\n" + "=" * 70)
    print(f"TEST 3: EPISTEMIC CALIBRATION (as-of week {checkpoint_week})")
    print("=" * 70)
    epistemic_summary, _ = analyze_epistemic_calibration(player_data, checkpoint_week)
    for pos, s in sorted(epistemic_summary.items()):
        print(f"  {pos:5s} n={s['n_players']:>3}  mean_z={s['mean_z']}  std_z={s['std_z']} "
              f"(target: mean_z~0, std_z~1)")

    print("\n" + "=" * 70)
    print("TEST 3b: SEARCHED EPISTEMIC_ERROR_RATES CORRECTION (if std_z is far from 1.0 above)")
    print("=" * 70)
    rate_suggestions = suggest_epistemic_rate_multiplier(player_data, checkpoint_week)
    for pos, s in sorted(rate_suggestions.items()):
        print(f"  {pos:5s} current_rate={s['current_rate']}  suggested_rate={s['suggested_rate']} "
              f"(x{s['suggested_multiplier']})  achieved_std_z={s['achieved_std_z']}")

    return {'aleatoric': aleatoric_summary, 'correlations': corr_summary,
            'epistemic': epistemic_summary, 'epistemic_rate_suggestions': rate_suggestions}


# ============================================================================
# F7: projection error from the sync-time projection log
# ============================================================================

def load_projection_log(path):
    """Reads data/projection_log.jsonl; keeps the LAST row per (season, week, player_id) so a
    re-sync within a week supersedes the earlier row."""
    import json
    last = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            last[(str(row["season"]), int(row["week"]), str(row["player_id"]))] = row
    return list(last.values())


def analyze_projection_error(rows, actual_by_pid_week, source="sleeper_mean", min_weeks=4):
    """
    The direct derivation of EPISTEMIC_ERROR_RATES that Phase 7 could not do for 2025 (Sleeper
    no longer served that season's projections). For each rostered player with >= min_weeks
    logged weeks that he PLAYED (actual > 0; a zero is an absence, not a projection error --
    Phase 2 finding 5): the projection error is mean(actual) - mean(projection) over those
    weeks. Per position: RMS of that error across players, minus the within-player sampling
    term (var(actual - projection) / n, averaged) -- the part of the RMS that is week-to-week
    noise rather than a wrong projection -- square-rooted, over the mean projection:
    that ratio is the epistemic rate a projection-based prior needs. Also returns the mean
    signed error (projection bias) and n. `actual_by_pid_week`: {(season, week, pid): points}.
    """
    by_player = {}
    for row in rows:
        proj = row.get(source)
        if proj is None:
            continue
        key = (str(row["season"]), int(row["week"]), str(row["player_id"]))
        actual = actual_by_pid_week.get(key)
        if actual is None or actual <= 0:
            continue
        by_player.setdefault((row["player_id"], row["pos"]), []).append((float(proj), float(actual)))
    per_pos = {}
    for (pid, pos), pairs in by_player.items():
        if len(pairs) < min_weeks:
            continue
        p = np.array([a for a, _ in pairs]); a = np.array([b for _, b in pairs])
        per_pos.setdefault(pos, []).append((float(p.mean()), float(a.mean() - p.mean()),
                                            float(np.var(a - p, ddof=1) / len(pairs))))
    out = {}
    for pos, entries in per_pos.items():
        proj_mean = float(np.mean([e[0] for e in entries]))
        errs = np.array([e[1] for e in entries]); sampling = float(np.mean([e[2] for e in entries]))
        rms2 = float(np.mean(errs ** 2))
        epistemic_var = max(0.0, rms2 - sampling)
        out[pos] = {
            "n_players": len(entries), "mean_projection": round(proj_mean, 2),
            "mean_signed_error": round(float(errs.mean()), 2), "rms_error": round(math.sqrt(rms2), 2),
            "sampling_term": round(math.sqrt(sampling), 2),
            "epistemic_sd": round(math.sqrt(epistemic_var), 2),
            "epistemic_rate": round(math.sqrt(epistemic_var) / proj_mean, 3) if proj_mean > 0 else None,
        }
    return out

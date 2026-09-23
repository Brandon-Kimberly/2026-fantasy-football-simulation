"""
fantasy_sim.epistemic_fit

B1 step 2. What SHOULD `EPISTEMIC_ERROR_RATES[pos]` be, measured rather than guessed?

`std_epistemic = EPISTEMIC_ERROR_RATES[pos] * mean` is the prior sd on a player's TRUE
weekly mean. It sets `prior_var` in `_apply_bayesian_updates`, so it decides how far a
season's observed games are allowed to move a preseason projection. `DL/LB/DB = 0.15`
against `QB 0.30, RB 0.63, WR 0.55` is a carried number, not a measured one
(`CLAUDE.md`: the IDP constants are "less rigorously sourced").

**THE INSTRUMENT IS PHASE 7's, ON PURPOSE.** The offensive rates came from "survey
measurement 2" in `docs/audit/AUDIT_PHASE_7_FINDINGS.md` — *"between-player variance of
season means minus the within-player sampling term -> sd_true / rostered mean"* — giving
QB 0.07, RB 0.28, WR 0.22, TE 0.20, K 0.25. B1's question is a COMPARISON against those,
so a different instrument would answer a different question. This is that one, written
down.

**Why the subtraction is the whole estimator.** Observed season means differ for two
reasons — players really do differ, and a finite sample of games is noisy:

    Var(observed season means)  =  Var(true means)  +  E[ within-player var / games ]

Only the first term is epistemic. Taking the raw spread of season means instead
overstates the rate by exactly the sampling noise, which is worst for the positions with
the fewest games — precisely where a wrong answer would do most damage.

**A NEGATIVE ESTIMATE IS A RESULT.** When the sampling term exceeds the observed spread
the difference goes below zero: the data cannot distinguish these players at all. It is
clamped to zero and reported as `degenerate`, never smuggled back as a small positive
number that reads like a measurement.

**WHAT THIS MODULE DOES NOT DO.** It does not adopt anything. Phase 7 already built,
gated and REVERTED a change that took its own fitted values into `config.py`; that pair
was worse on the points backtest in both configurations tested. A fitted number here is
evidence for a decision, not the decision — and B1 makes adoption conditional and MAJOR.

Pure: histories in, dicts out. Nothing in the engine imports this.
"""


def score_stat_line(stats, scoring_settings):
    """Fantasy points for one stat line under one league's settings.

    Categories the league does not score contribute nothing, which is also how a 0.0
    setting behaves — `idp_tkl` is 0.0 in this league and `idp_tkl_solo` carries it.
    """
    total = 0.0
    for key, value in (stats or {}).items():
        weight = (scoring_settings or {}).get(key)
        if not weight:
            continue
        try:
            total += float(weight) * float(value or 0)
        except (TypeError, ValueError):
            continue
    return total


def top_k_by_total(histories, k):
    """The `k` players with the highest season totals.

    The raw feed carries ~300 linebackers a week, almost all special-teamers with no
    startable mean. Fitting a prior across them would measure the gap between starters
    and bystanders, not the uncertainty about a startable player.
    """
    ranked = sorted((histories or {}).items(),
                    key=lambda kv: (-sum(kv[1] or []), kv[0]))
    return [name for name, _ in ranked[:max(0, int(k))]]


def variance_components(histories, min_games=4):
    """Split the spread of season means into true spread and sampling noise.

    `histories` is {player: [weekly score, ...]}. Zero weeks are dropped, matching
    `_apply_bayesian_updates` (`if pts == 0: continue`) — an estimator fitted on a
    different sample than the blend consumes is measuring a different quantity.

    Returns `rate` = sd_true / mean, the units `EPISTEMIC_ERROR_RATES` is in, plus the
    uncorrected `sd_observed` so the size of the correction is visible rather than
    implicit.
    """
    kept = {}
    for name, scores in (histories or {}).items():
        played = [float(s) for s in (scores or []) if s]
        if len(played) >= int(min_games):
            kept[name] = played

    n_players = len(kept)
    out = {"n_players": n_players, "included": sorted(kept), "min_games": int(min_games),
           "rate": None, "sd_true": 0.0, "sd_observed": 0.0, "mean": 0.0,
           "sampling_term": 0.0, "games_per_player": 0.0, "degenerate": True}
    if n_players < 2:
        return out

    means = [sum(v) / len(v) for v in kept.values()]
    grand = sum(means) / n_players
    observed_var = sum((m - grand) ** 2 for m in means) / (n_players - 1)

    # E[within-player var / games], averaged over players: the noise in each man's own
    # season mean. Sample variance (n-1) per player; a player with one game contributes
    # nothing measurable and is already excluded by min_games >= 2.
    sampling = 0.0
    for v in kept.values():
        n = len(v)
        if n < 2:
            continue
        mu = sum(v) / n
        within = sum((x - mu) ** 2 for x in v) / (n - 1)
        sampling += within / n
    sampling /= n_players

    true_var = observed_var - sampling
    degenerate = true_var <= 0 or grand <= 0
    sd_true = 0.0 if degenerate else true_var ** 0.5

    out.update({
        "mean": grand,
        "sd_observed": observed_var ** 0.5,
        "sampling_term": sampling,
        "sd_true": sd_true,
        "rate": (sd_true / grand) if grand > 0 else None,
        "games_per_player": sum(len(v) for v in kept.values()) / n_players,
        "degenerate": bool(degenerate),
    })
    return out


def blend(prior_mean, rate, scores, n_0=4.0):
    """`_apply_bayesian_updates` for one player, at an arbitrary rate.

    Transcribed deliberately rather than imported: the engine's copy runs over a whole
    baselines dict at init and cannot be asked "what would this player's posterior be at
    rate r". A test in `tests/test_idp_learning_rate.py` pins the engine against the same
    arithmetic, so the transcription is checked rather than trusted.
    """
    n = len(scores)
    if not n:
        return prior_mean
    prior_var = max(0.1, (float(rate) * float(prior_mean)) ** 2)
    mu = sum(scores) / n
    raw = (sum((s - mu) ** 2 for s in scores) / n) if n > 1 else prior_var
    actual_var = max(raw, 0.5 * prior_var)
    post_var = 1.0 / ((n_0 / prior_var) + (n / actual_var))
    return ((n_0 * prior_mean / prior_var) + (n * mu / actual_var)) * post_var


def holdout_rate_scan(histories, rates, split, min_each_side=3):
    """Which rate predicts the UNSEEN half best? B1's acceptance criterion.

    For each player: the prior is the LEAVE-ONE-OUT positional mean of the first half
    (look-ahead-safe, and the same peer-prior device Phase 7 used, because no stored 2025
    preseason projection exists -- Sleeper 404s on both URL forms). Blend it with that
    player's first-half games at rate `r`, then score the posterior against his SECOND
    half by mean squared error, which for a fixed observation variance is the Gaussian
    predictive likelihood up to a constant.

    A rate that merely fits the first half better will not win here; only one that
    predicts better does. Returns {rate: mse} plus the best rate and the population.
    """
    train, test = {}, {}
    for name, scores in (histories or {}).items():
        played = [(i, float(s)) for i, s in enumerate(scores or []) if s]
        a = [s for i, s in played if i < split]
        b = [s for i, s in played if i >= split]
        if len(a) >= min_each_side and len(b) >= min_each_side:
            train[name], test[name] = a, b
    if len(train) < 3:
        return {"n_players": len(train), "by_rate": {}, "best_rate": None,
                "degenerate": True}

    totals = {n: sum(v) / len(v) for n, v in train.items()}
    grand_sum, k = sum(totals.values()), len(totals)

    by_rate = {}
    for r in rates:
        se, n_obs = 0.0, 0
        for name in train:
            loo_prior = (grand_sum - totals[name]) / (k - 1)      # look-ahead-safe
            post = blend(loo_prior, r, train[name])
            for actual in test[name]:
                se += (actual - post) ** 2
                n_obs += 1
        by_rate[round(float(r), 4)] = se / n_obs if n_obs else None
    best = min((v, kk) for kk, v in by_rate.items() if v is not None)[1]
    return {"n_players": len(train), "by_rate": by_rate, "best_rate": best,
            "split": split, "degenerate": False}

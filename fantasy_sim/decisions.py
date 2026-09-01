"""
fantasy_sim.decisions

Decision-support tools for real weekly decisions, separate from the simulation's own season
reporting. Nothing here writes to data/current/ or data/weeks/; a decision run never overwrites
a season export (the reduced simulation below patches the engine's writers out).

TOOL 1 -- head-to-head start/sit comparator: P(A > B) from the players' simulated
distributions, not a mean comparison.

  * Rostered vs rostered ("joint"): run a reduced run_simulation() and read the engine's
    player_weekly_scores accumulator -- (total_sims, 14), NaN for structural absences, filled
    before lineup selection -- on the requested week's column. The two players' draws in the
    same sim share the copula (a QB and his WR), the same environment draw, and the same
    injury state, so P(A>B) computed sim-by-sim is the joint probability, not the product of
    two marginals.
  * Free agent ("light"): a free agent never enters a simulation, so his single-week score is
    sampled from his baseline parameters through the engine's OWN extracted transform
    (FantasySimulationEngine._weekly_score_from_z) plus the same season-mean (epistemic)
    lognormal, environment draw, game-script multiplier, bye, initial-absence clock (F4) and
    onset hazard (starter exposure). What it cannot carry: the copula (z is independent) and
    contingency points from teammates' injuries (a league-wide quantity). Stated on the output.

A structural absence (bye, injury) is a zero for the lineup slot, so P(A>B) treats NaN as 0.0
and reports each player's zero share separately.
"""
from unittest.mock import patch

import numpy as np

from fantasy_sim.config import normalize_position, SIM_CONFIG
from fantasy_sim.simulation import FantasySimulationEngine


# ------------------------------------------------------------------------------ primitives
def prob_a_beats_b(scores_a, scores_b):
    """Sim-aligned samples -> {p_a, p_b, p_tie, n}. NaN (structural absence) counts as 0.0."""
    a = np.nan_to_num(np.asarray(scores_a, dtype=float), nan=0.0)
    b = np.nan_to_num(np.asarray(scores_b, dtype=float), nan=0.0)
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError(f"samples must be aligned 1-D arrays of equal length, got {a.shape} vs {b.shape}")
    n = int(a.size)
    return {"p_a": float(np.mean(a > b)), "p_b": float(np.mean(b > a)),
            "p_tie": float(np.mean(a == b)), "n": n}


def summarise_scores(scores):
    s = np.nan_to_num(np.asarray(scores, dtype=float), nan=0.0)
    if s.size == 0:
        return {"n": 0}
    p10, p25, p50, p75, p90 = (float(x) for x in np.percentile(s, (10, 25, 50, 75, 90)))
    return {"n": int(s.size), "mean": float(s.mean()), "p10": p10, "p25": p25, "p50": p50,
            "p75": p75, "p90": p90, "p_zero": float(np.mean(s == 0.0))}


# ------------------------------------------------------------------------- light sampler
def sample_week_scores(engine, name, week, n, seed=None, starter=True):
    """n independent single-week scores for one player in `engine.baselines`, for NFL `week`.
    Mirrors the engine's weekly loop for one player in isolation (see module docstring for
    what is and is not carried). Draws from numpy's global stream, seeded here when `seed` is
    given, so a call is reproducible."""
    if name not in engine.baselines or not isinstance(engine.baselines[name], dict):
        raise KeyError(f"{name!r} is not in the baseline pool")
    p = engine.baselines[name]
    pos = normalize_position(p.get('pos', 'FLEX'))
    team = p.get('team', 'FA')
    if seed is not None:
        np.random.seed(seed)
    out = np.zeros(n, dtype=float)
    if week == p.get('bye'):
        return out

    mu_0 = float(p.get('mean', 8.0))
    sig_e = float(p.get('std_epistemic', mu_0 * 0.18))
    std_a = float(p.get('std_aleatoric', 3.0))
    veg = engine._compute_week_environment(week, team)
    env_ratio = veg['total'] / engine._compute_environment_normaliser()
    script_mult = engine._script_multiplier(pos, veg)
    exposure = SIM_CONFIG['ONSET_EXPOSURE_STARTER'] if starter else SIM_CONFIG['ONSET_EXPOSURE_BENCH']
    hazard = SIM_CONFIG['INJURY_RATES'].get(pos, 0.025) * exposure
    weeks_ahead = week - engine.current_week + 1     # 1 = the current week
    status, on_ir = p.get('injury_status'), bool(p.get('on_ir', False))

    for i in range(n):
        # F4: initial absence, drawn once per simulated season -- one draw per sample here.
        if engine._initial_absence_clock(status, on_ir) >= weeks_ahead:
            continue
        # onset this week: the slot realises nothing from this player (benched or locked zero)
        if np.random.rand() < hazard:
            continue
        if mu_0 <= 0.01:
            season_mean = 0.0
        else:
            sigma_e = np.sqrt(np.log(1 + (sig_e / mu_0) ** 2))
            mu_e = np.log(mu_0) - (sigma_e ** 2 / 2)
            season_mean = float(np.exp(np.random.normal(mu_e, sigma_e)))
        z = float(np.random.normal(0.0, 1.0))
        env_var = float(np.random.normal(env_ratio, 0.10))
        _, final = FantasySimulationEngine._weekly_score_from_z(
            season_mean, std_a, z, env_ratio, env_var, script_mult, 0.0)
        out[i] = final
    return out


# ------------------------------------------------------------------- reduced simulation
def run_reduced_simulation(engine, sims, batches=1):
    """Populate engine.player_weekly_scores with a reduced run. The engine's file writers are
    patched out for the duration: a decision run must never overwrite the season exports in
    data/weeks/. Batch settings are restored afterwards. Returns the accumulator."""
    original = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
    SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = batches, sims
    try:
        with patch('fantasy_sim.simulation.save_json'), patch('fantasy_sim.simulation.save_chart'), \
             patch('matplotlib.pyplot.close'):
            engine.run_simulation()
    finally:
        SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original
    return engine.player_weekly_scores


def _is_rostered(engine, name):
    return any(name in roster for roster in engine.rosters.values())


# ------------------------------------------------------------------------- comparator
def compare_players(engine, a, b, week, sims=2000, seed=None, light=False):
    """P(A > B) for one NFL week. Path: 'joint' (both rostered, accumulator column), 'mixed'
    (one free agent: rostered side from the accumulator, free agent from the light sampler,
    independent of each other), or 'light' (both sampled independently; forced by light=True,
    which skips the simulation entirely)."""
    for name in (a, b):
        if name not in engine.baselines:
            raise KeyError(f"{name!r} is not in the baseline pool")
    if week < engine.current_week or week > 14:
        raise ValueError(f"week must be within the simulated regular season ({engine.current_week}..14)")
    rostered = {name: _is_rostered(engine, name) for name in (a, b)}
    note = ""
    if light or not any(rostered.values()):
        path = "light"
        sa = sample_week_scores(engine, a, week, sims, seed=seed)
        sb = sample_week_scores(engine, b, week, sims, seed=None if seed is None else seed + 1)
        note = ("both players sampled independently from baseline parameters: no copula "
                "(same-NFL-team correlation is lost), no contingency points.")
    else:
        acc = run_reduced_simulation(engine, sims)
        col = week - 1
        if all(rostered.values()):
            path = "joint"
            sa, sb = acc[a][:, col], acc[b][:, col]
            note = "sim-by-sim comparison on the week column of a reduced simulation (joint distribution)."
        else:
            path = "mixed"
            fa = a if not rostered[a] else b
            samp = sample_week_scores(engine, fa, week, acc[a if rostered[a] else b].shape[0], seed=seed)
            sa = acc[a][:, col] if rostered[a] else samp
            sb = acc[b][:, col] if rostered[b] else samp
            note = (f"{fa} is a free agent: sampled independently from baseline parameters and paired "
                    "with the rostered player's simulated draws (their correlation, if any, is lost).")
    r = prob_a_beats_b(sa, sb)
    r.update({"a_name": a, "b_name": b, "week": week, "path": path, "note": note,
              "a": summarise_scores(sa), "b": summarise_scores(sb),
              "mean_diff": float(np.nan_to_num(sa, nan=0.0).mean() - np.nan_to_num(sb, nan=0.0).mean()),
              "se_p": float(np.sqrt(max(r["p_a"] * (1 - r["p_a"]), 1e-12) / r["n"]))})
    return r


# ==================================================================== TOOL 3: waiver targets
#
# Roster gaps are found the way the engine's own streamer-needs scan finds them (a starting
# slot no healthy, non-bye rostered player can fill), solved with the engine's optimal
# assignment on the REAL roster and baselines. Free agents are every baseline-pool player on
# no roster at a position this league starts. Ranking is by VORP = mean - replacement level at
# the player's position (engine.replacement_levels: depth-based, FLEX = min(RB, WR)) -- a
# MARGINAL quantity, the expectation of one player's own distribution, so the light sampler's
# independence (no copula) cannot affect it; the copula shapes only the joint law of two
# players' draws and leaves every marginal unchanged. The one place independence matters is
# the secondary "P(this player outscores the incumbent starter)" display, which is joint and
# carries INDEPENDENCE_CAVEAT exactly as tool 1's light path does.
#
# The suggested bid is a transparent VALUE-based heuristic (suggest_bid) and is UNVERIFIED:
# there are no real waiver outcomes to calibrate against until the season produces them. The
# engine's _compute_faab_bid is shown alongside as what the MODEL expects a typical manager to
# pay -- it is a behavioural model of other managers, not an optimiser, and is not the advice.
INDEPENDENCE_CAVEAT = ("sampled independently from baseline parameters: no copula (same-NFL-team "
                       "correlation with the incumbent is lost), no contingency points.")
_SLOT_POSITIONS = {"FLEX": ("RB", "WR", "TE")}
_STARTABLE = {"QB", "RB", "WR", "TE", "K", "DL", "LB", "DB"}


def _entry(engine, name):
    d = engine.baselines.get(name, {})
    return d if isinstance(d, dict) else {}


def _opts(engine, name):
    from fantasy_sim.config import DUAL_ELIGIBILITY
    return DUAL_ELIGIBILITY.get(name, [normalize_position(_entry(engine, name).get('pos', 'FLEX'))])


def _unavailable_now(entry):
    """Out with certainty this week: F4's initial-absence statuses or the league IR slot (the
    first week of an initial absence is certain -- see _initial_absence_clock)."""
    return entry.get('injury_status') in SIM_CONFIG["INITIAL_ABSENCE_STATUSES"] or bool(entry.get('on_ir', False))


def roster_gaps(engine, team, weeks):
    """{week: {'unfilled': [slot, ...], 'starters': {slot: [(name, expected), ...]}}} for the
    team's REAL roster: the engine's optimal assignment over players who are neither on bye
    that week nor out now, at their baseline mean."""
    if team not in engine.rosters:
        raise KeyError(f"unknown team {team!r}")
    out = {}
    for week in weeks:
        cands = []
        for name in engine.rosters[team]:
            e = _entry(engine, name)
            if e.get('bye') == week or _unavailable_now(e):
                continue
            cands.append((name, _opts(engine, name), float(e.get('mean', 4.0))))
        assigned, unfilled = engine._solve_optimal_assignment(cands)
        starters = {}
        for name, value, slot in assigned:
            starters.setdefault(slot, []).append((name, float(value)))
        for slot in starters:
            starters[slot].sort(key=lambda x: x[1])
        out[week] = {"unfilled": sorted(unfilled), "starters": starters}
    return out


def free_agents(engine):
    """Baseline-pool players on no roster, at a position this league starts."""
    rostered = {n for r in engine.rosters.values() for n in r}
    return sorted(n for n, e in engine.baselines.items()
                  if isinstance(e, dict) and n not in rostered
                  and normalize_position(e.get('pos', 'FLEX')) in _STARTABLE)


def suggest_bid(vorp, fills, remaining_faab, league_avg_faab, min_bid=1):
    """UNVERIFIED value heuristic (no real waiver outcomes exist yet to calibrate it): a share
    of the remaining budget proportional to value over replacement -- 4% of budget per point
    of VORP, capped at 40% for a hole and 25% for an upgrade -- scaled by league-wide budget
    deflation (remaining league average / 100) so bids fall as budgets do, floored at the
    league minimum and capped at what the team has. Deliberately simple and stated in full so
    it can be replaced by a measured rule once the season yields data."""
    share = max(0.0, 0.04 * vorp)
    share = min(share, 0.40 if fills == "hole" else 0.25)
    deflation = max(0.2, min(1.0, league_avg_faab / 100.0))
    bid = int(round(remaining_faab * share * deflation))
    return int(max(min_bid, min(bid, int(remaining_faab))))


def rank_waiver_targets(engine, team, week, top_n=15, sims=2000, seed=None, positions=None):
    """Rank free agents for `team` in `week`: hole-fillers first (a slot no rostered player can
    fill), then upgrades over the weakest incumbent at a slot; within each, by VORP. Each
    target carries tier (positional_tiers), a light-sampled week distribution, the suggested
    bid (unverified heuristic) and the engine's behavioural bid, and -- for upgrades -- the
    secondary P(beats incumbent) with INDEPENDENCE_CAVEAT."""
    from fantasy_sim.positional_tiers import compute_tiers
    from fantasy_sim.config import MANAGER_PROFILES
    gaps = roster_gaps(engine, team, weeks=(week, week + 1) if week < 14 else (week,))
    holes = gaps[week]["unfilled"]
    next_holes = gaps.get(week + 1, {}).get("unfilled", [])
    starters = gaps[week]["starters"]
    tier_of = {p['name']: p['tier'] for plist in compute_tiers(engine.baselines).values() for p in plist}
    remaining = float(engine.current_faab.get(team, 100.0))
    league_avg = float(np.mean(list(engine.current_faab.values()))) if engine.current_faab else 100.0
    agg = MANAGER_PROFILES.get(team, {}).get('faab_agg', 0.5)

    def slots_for(opts):
        s = [p for p in opts]
        if any(p in _SLOT_POSITIONS["FLEX"] for p in opts):
            s.append("FLEX")
        return s

    targets = []
    for name in free_agents(engine):
        e = _entry(engine, name)
        pos = normalize_position(e.get('pos', 'FLEX'))
        if positions and pos not in positions:
            continue
        if e.get('bye') == week or _unavailable_now(e):
            continue
        mean = float(e.get('mean', 0.0))
        opts = _opts(engine, name)
        my_slots = slots_for(opts)
        fills, incumbent = None, None
        if any(s in holes for s in my_slots):
            fills = "hole"
        else:
            weakest = [(starters[s][0][1], starters[s][0][0], s) for s in my_slots if s in starters and starters[s]]
            if weakest:
                val, inc_name, slot = min(weakest)
                if mean > val:
                    fills, incumbent = "upgrade", inc_name
        if fills is None:
            continue
        rep = engine.replacement_levels.get(pos, 4.0)
        vorp = mean - rep
        targets.append({"name": name, "pos": pos, "team": e.get('team', 'FA'), "mean": mean,
                        "replacement_level": float(rep), "vorp": float(vorp), "tier": tier_of.get(name),
                        "bye": e.get('bye'), "injury_status": e.get('injury_status'),
                        "fills": fills, "incumbent": incumbent,
                        "need_next_week": any(s in next_holes for s in my_slots)})
    targets.sort(key=lambda t: (0 if t["fills"] == "hole" else 1, -t["vorp"]))
    targets = targets[:top_n]

    for i, t in enumerate(targets):
        s = sample_week_scores(engine, t["name"], week, sims, seed=None if seed is None else seed + i)
        t["week"] = summarise_scores(s)
        weeks_of_need = 1 + (1 if t["need_next_week"] else 0)
        deflation = league_avg / 100.0 if league_avg > 0 else 0.0
        t["bid"] = {
            "suggested": suggest_bid(t["vorp"], t["fills"], remaining, league_avg),
            "typical_manager_model": round(float(engine._compute_faab_bid(
                remaining, 14.0, agg, weeks_of_need, deflation, league_avg)), 1),
            "remaining_faab": remaining,
            "basis": "UNVERIFIED value heuristic (see suggest_bid); the model bid is what a typical "
                     "manager is simulated to pay, not advice.",
        }
        if t["incumbent"] is not None:
            inc = sample_week_scores(engine, t["incumbent"], week, sims, seed=None if seed is None else seed + 1000 + i)
            pr = prob_a_beats_b(s, inc)
            t["p_beats_incumbent"] = {"p": pr["p_a"], "p_tie": pr["p_tie"], "n": pr["n"],
                                      "incumbent": t["incumbent"], "caveat": INDEPENDENCE_CAVEAT}
        else:
            t["p_beats_incumbent"] = None
    return {"team": team, "week": week, "holes": holes, "holes_next_week": next_holes,
            "remaining_faab": remaining, "league_avg_faab": league_avg,
            "targets": targets, "caveat": INDEPENDENCE_CAVEAT}

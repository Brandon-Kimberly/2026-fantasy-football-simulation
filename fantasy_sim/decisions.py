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
    fill), then upgrades over the weakest incumbent at a slot, then DEPTH upgrades -- a free
    agent who beats the team's worst BENCH player at his position (that player named as the
    natural drop candidate), or any positive-VORP free agent at a position whose bench is
    EMPTY behind a lone starter. Depth is block-ordered last and capped at three per
    position; it is never merged into the starter-facing ranking. Within each block, by
    VORP. Each
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

    starter_names = {nm for lst in starters.values() for nm, _v in lst}
    bench_by_pos = {}
    for r_ in engine.rosters.get(team, []):
        if r_ in starter_names:
            continue
        e_ = _entry(engine, r_)
        bench_by_pos.setdefault(normalize_position(e_.get('pos', 'FLEX')), []).append(
            (float(e_.get('mean', 0.0)), r_))

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
        rep = engine.replacement_levels.get(pos, 4.0)
        vorp = mean - rep
        if fills is None:
            bench = bench_by_pos.get(pos, [])
            if bench:
                worst_mean, worst_name = min(bench)
                if mean > worst_mean:
                    fills, incumbent = "depth", worst_name
            elif vorp > 0:
                fills, incumbent = "depth", None
        if fills is None:
            continue
        targets.append({"name": name, "pos": pos, "team": e.get('team', 'FA'), "mean": mean,
                        "replacement_level": float(rep), "vorp": float(vorp), "tier": tier_of.get(name),
                        "bye": e.get('bye'), "injury_status": e.get('injury_status'),
                        "fills": fills, "incumbent": incumbent,
                        "need_next_week": any(s in next_holes for s in my_slots)})
    targets.sort(key=lambda t: ({"hole": 0, "upgrade": 1, "depth": 2}[t["fills"]], -t["vorp"]))
    kept, depth_per_pos = [], {}
    for t in targets:
        if t["fills"] == "depth":
            if depth_per_pos.get(t["pos"], 0) >= 3:
                continue
            depth_per_pos[t["pos"]] = depth_per_pos.get(t["pos"], 0) + 1
        kept.append(t)
    targets = kept[:top_n]

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


# ================================================================== TOOL 2: trade evaluator
#
# A user-proposed trade, evaluated by two PAIRED full simulations: the league as it is, and the
# league with the trade applied. run_simulation reseeds np.random.seed(1000 + batch) at the
# top of every batch and draws nothing before it, so both arms consume the identical random
# stream batch for batch; each team's delta in Champ_Pct / Playoff_Pct / expected wins carries
# a paired-batch SE (the per-batch differences over the batches). This IS the real
# marginal-championship-equity number the engine's automatic trade block could not afford
# (AUDIT_PLAN.md F2, commit 3): the block evaluates ~20 candidate offers per simulated season
# across 10,000 seasons -- ~200,000 evaluations per run, each of which would need a nested
# simulation of hundreds of seasons -- whereas here it is one trade, once, on demand: two runs.
# Bystanders are reported too: a trade moves third parties, and every championship goes to
# exactly one team, so the Champ_Pct deltas sum to zero across the league by construction.
#
# The engine's own automatic trade block stays active in both arms (paired, so it cancels in
# expectation). Writers are patched out: a decision run never overwrites the season exports.
import copy as _copy

ACTIVE_ROSTER_LIMIT = 19   # Sleeper roster_positions for this league: 13 starters + 6 bench (IR slots separate)


def _active_count(engine, team):
    return sum(1 for n in engine.rosters[team] if not bool(_entry(engine, n).get('on_ir', False)))


def apply_trade(engine, team_a, a_gives, team_b, b_gives, drops=None):
    """A deep copy of `engine` with the trade applied to rosters and meta; the original is
    untouched. `drops` = {team: [names]} released to free agency after the trade. Raises
    ValueError if a player is not on the stated roster, a drop is not on that roster after the
    trade, or a side would exceed ACTIVE_ROSTER_LIMIT active players without a drop."""
    for t in (team_a, team_b):
        if t not in engine.rosters:
            raise KeyError(f"unknown team {t!r}")
    if team_a == team_b:
        raise ValueError("a trade needs two different teams")
    for t, names in ((team_a, a_gives), (team_b, b_gives)):
        missing = [n for n in names if n not in engine.rosters[t]]
        if missing:
            raise ValueError(f"{missing} not on {t}'s roster")
    if not a_gives and not b_gives:
        raise ValueError("nothing is being traded")
    e2 = _copy.deepcopy(engine)
    for src, dst, names in ((team_a, team_b, a_gives), (team_b, team_a, b_gives)):
        for n in names:
            e2.rosters[src].remove(n)
            e2.rosters[dst].append(n)
            e2.meta[dst][n] = e2.meta[src].pop(n, {'pos': _entry(engine, n).get('pos', 'FLEX'),
                                                   'team': _entry(engine, n).get('team', 'FA')})
    for t, names in (drops or {}).items():
        if t not in e2.rosters:
            raise KeyError(f"unknown team {t!r} in drops")
        for n in names:
            if n not in e2.rosters[t]:
                raise ValueError(f"cannot drop {n!r}: not on {t}'s post-trade roster")
            e2.rosters[t].remove(n)
            e2.meta[t].pop(n, None)
    for t in (team_a, team_b):
        if _active_count(e2, t) > ACTIVE_ROSTER_LIMIT:
            raise ValueError(f"{t} would carry {_active_count(e2, t)} active players (limit "
                             f"{ACTIVE_ROSTER_LIMIT}); specify drops for that side")
    return e2


def run_paired_capture(engine, batches, sims):
    """Run `engine` at batches x sims with the writers patched out and return the stage-A
    quantities the evaluator needs: {'wins': {team: (total_sims,)}, 'b_playoffs' /
    'b_champs': {team: [per-batch rate, ...]}}. Positional indices follow run_simulation's
    export_and_visualize call (tests.golden_master.STAGE_A_ARG_NAMES): wins=0, b_playoffs=2,
    b_champs=3. Batch settings restored afterwards."""
    captured = {}

    def capture(self_, *args, **kwargs):
        captured['wins'] = args[0]; captured['b_playoffs'] = args[2]; captured['b_champs'] = args[3]
        return None

    original = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
    SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = batches, sims
    try:
        with patch('fantasy_sim.simulation.save_json'), patch('fantasy_sim.simulation.save_chart'), \
             patch.object(FantasySimulationEngine, 'export_and_visualize', capture):
            engine.run_simulation()
    finally:
        SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original
    return captured


def _paired_evaluation(engine, with_engine, batches, sims):
    """The shared core of every paired evaluation: run both arms on identical seeds and return
    per-team champ_pct / playoff_pct / expected_wins, each with the paired-batch SE. Used by
    evaluate_trade and evaluate_add_drop so the two outputs are the same shape by
    construction."""
    base = run_paired_capture(_copy.deepcopy(engine), batches, sims)
    alt = run_paired_capture(with_engine, batches, sims)

    def paired(rates_with, rates_without, scale):
        d = (np.asarray(rates_with, float) - np.asarray(rates_without, float)) * scale
        se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else float('nan')
        return {"with": float(np.mean(rates_with) * scale), "without": float(np.mean(rates_without) * scale),
                "delta": float(d.mean()), "se": se}

    teams = {}
    for t in engine.team_names:
        w_with = np.asarray(alt['wins'][t], float).reshape(batches, sims).mean(axis=1)
        w_without = np.asarray(base['wins'][t], float).reshape(batches, sims).mean(axis=1)
        teams[t] = {
            "champ_pct": paired(alt['b_champs'][t], base['b_champs'][t], 100.0),
            "playoff_pct": paired(alt['b_playoffs'][t], base['b_playoffs'][t], 100.0),
            "expected_wins": paired(w_with, w_without, 1.0),
        }
    return teams


def evaluate_trade(engine, team_a, a_gives, team_b, b_gives, drops=None, batches=10, sims=300):
    """Paired evaluation of one proposed trade. Returns per-team deltas (with minus without)
    for champ_pct, playoff_pct and expected_wins, each with the paired-batch SE, plus the
    trade's terms, the sample size and a note on what the number is."""
    with_engine = apply_trade(engine, team_a, a_gives, team_b, b_gives, drops=drops)
    teams = _paired_evaluation(engine, with_engine, batches, sims)
    n = batches * sims
    for t, d in teams.items():
        d["side"] = "A" if t == team_a else ("B" if t == team_b else "bystander")
    return {
        "trade": {"team_a": team_a, "a_gives": list(a_gives), "team_b": team_b, "b_gives": list(b_gives),
                  "drops": {k: list(v) for k, v in (drops or {}).items()}},
        "n_sims": n, "batches": batches, "sims_per_batch": sims, "teams": teams,
        "note": ("paired full simulations on identical seeds; delta = with trade minus without; SE is "
                 "the paired-batch standard error. Real Champ_Pct/Playoff_Pct movement, not a proxy. "
                 "The engine's automatic trades stay active in both arms."),
    }


# ================================================================== ROSTER-GRADE REPORT
#
# Composition of what exists, no new modelling: tier from positional_tiers.compute_tiers (a
# player's standing in the WHOLE pool, free agents included -- a league-wide reference, not a
# rank within the roster), VORP = mean - engine.replacement_levels at the player's position,
# starter/bench from the engine's optimal assignment on the real roster (roster_gaps). Roll-ups:
#   lineup_vorp = sum over FILLED slots of (starter's expectation - replacement level of the slot
#                 he fills; FLEX = min(RB, WR)); an unfilled slot contributes 0, i.e. a streamer at
#                 replacement level -- the one number that compares across teams;
#   depth_vorp  = sum of the POSITIVE part of bench players' VORP (a bench player below
#                 replacement is not depth, he is a roster spot);
#   optimal_score = engine.get_optimal_score(roster), which includes the deliberate 0.1 x bench
#                 term (AUDIT_PHASE_1 finding 8) and is labelled as such.
# No letter grades: they would be an unsourced mapping from a sourced number. Rank is relative
# to the league.
def grade_roster(engine, team, week=None):
    from fantasy_sim.positional_tiers import compute_tiers
    week = week or engine.current_week
    if team not in engine.rosters:
        raise KeyError(f"unknown team {team!r}")
    tier_of = {p['name']: p['tier'] for plist in compute_tiers(engine.baselines).values() for p in plist}
    gaps = roster_gaps(engine, team, weeks=(week,))[week]
    slot_of = {name: slot for slot, lst in gaps["starters"].items() for name, _ in lst}
    rep = engine.replacement_levels
    fa_best = {}
    for name in free_agents(engine):
        e = _entry(engine, name)
        if e.get('bye') == week or _unavailable_now(e):
            continue
        pos = normalize_position(e.get('pos', 'FLEX'))
        v = float(e.get('mean', 0.0)) - rep.get(pos, 4.0)
        if pos not in fa_best or v > fa_best[pos]["vorp"]:
            fa_best[pos] = {"name": name, "vorp": float(v), "mean": float(e.get('mean', 0.0))}

    players, by_pos = [], {}
    for name in engine.rosters[team]:
        e = _entry(engine, name)
        pos = normalize_position(e.get('pos', 'FLEX'))
        mean = float(e.get('mean', 0.0))
        vorp = mean - rep.get(pos, 4.0)
        slot = slot_of.get(name)
        row = {"name": name, "pos": pos, "team": e.get('team', 'FA'), "mean": mean, "vorp": float(vorp),
               "tier": tier_of.get(name), "role": "starter" if slot else "bench", "slot": slot,
               "bye": e.get('bye'), "injury_status": e.get('injury_status'), "on_ir": bool(e.get('on_ir', False)),
               "available_this_week": not (e.get('bye') == week or _unavailable_now(e))}
        players.append(row)
        b = by_pos.setdefault(pos, {"n_starters": 0, "n_bench": 0, "starters_vorp": 0.0, "depth_vorp": 0.0,
                                    "tiers": [], "best_free_agent": fa_best.get(pos)})
        b["tiers"].append(row["tier"])
        if slot:
            b["n_starters"] += 1; b["starters_vorp"] += vorp
        else:
            b["n_bench"] += 1; b["depth_vorp"] += max(0.0, vorp)
    players.sort(key=lambda r: (0 if r["role"] == "starter" else 1, -r["vorp"]))

    lineup_vorp = 0.0
    for slot, lst in gaps["starters"].items():
        for _, value in lst:
            lineup_vorp += float(value) - rep.get(slot, 4.0)
    depth_vorp = sum(b["depth_vorp"] for b in by_pos.values())
    return {"team": team, "week": week, "players": players, "by_position": by_pos,
            "holes": gaps["unfilled"], "lineup_vorp": float(lineup_vorp), "depth_vorp": float(depth_vorp),
            "optimal_score": float(engine.get_optimal_score(engine.rosters[team])),
            "replacement_levels": {k: float(v) for k, v in rep.items()},
            "note": ("tier = standing in the whole pool (free agents included); VORP = mean - replacement "
                     "level at the player's position; lineup_vorp uses the replacement level of the slot "
                     "filled, unfilled slots count 0; depth_vorp = positive bench VORP only; optimal_score "
                     "includes the engine's deliberate 0.1 x bench term.")}


# ================================================================== TOOL 4: lineup optimizer
#
# This-week expectation is the engine's own pre-game form (expected_pre without contingency):
# baseline mean x environment ratio x game-script multiplier, and 0 when the player is on bye
# or out now (F4 statuses / IR). The lineup is the engine's optimal assignment on those
# expectations -- the same rule the simulation uses to set every lineup -- so this tool shows
# the engine's lineup for the real roster, with each starter's sampled p10/p50/p90 and the
# margin over the best bench alternative eligible for his slot. No draw enters the choice
# (the lookahead rule: lineups are chosen on expectation, never on realised scores).
def _env_norm(engine):
    cached = getattr(engine, "_decisions_env_norm", None)
    if cached is None:
        cached = engine._compute_environment_normaliser()
        engine._decisions_env_norm = cached
    return cached


def week_expectation(engine, name, week):
    e = _entry(engine, name)
    if e.get('bye') == week or _unavailable_now(e):
        return 0.0
    pos = normalize_position(e.get('pos', 'FLEX'))
    veg = engine._compute_week_environment(week, e.get('team', 'FA'))
    ratio = veg['total'] / _env_norm(engine)
    return float(e.get('mean', 0.0)) * ratio * engine._script_multiplier(pos, veg)


def _slot_positions(slot):
    return _SLOT_POSITIONS.get(slot, (slot,))


def optimize_lineup(engine, team, week, sims=1000, seed=None):
    if team not in engine.rosters:
        raise KeyError(f"unknown team {team!r}")
    names = list(engine.rosters[team])
    exp = {n: week_expectation(engine, n, week) for n in names}
    available = {n: not (_entry(engine, n).get('bye') == week or _unavailable_now(_entry(engine, n))) for n in names}
    cands = [(n, _opts(engine, n), exp[n]) for n in names if available[n]]
    assigned, unfilled = engine._solve_optimal_assignment(cands)
    started = {n for n, _, _ in assigned}

    lineup = []
    for i, (n, value, slot) in enumerate(sorted(assigned, key=lambda a: (a[2], -a[1]))):
        eligible = [m for m in names if m not in started and available[m]
                    and any(p in _slot_positions(slot) for p in _opts(engine, m))]
        alt = max(eligible, key=lambda m: exp[m]) if eligible else None
        s = summarise_scores(sample_week_scores(engine, n, week, sims, seed=None if seed is None else seed + i))
        lineup.append({"slot": slot, "name": n, "pos": normalize_position(_entry(engine, n).get('pos', 'FLEX')),
                       "expected": float(value), "p10": s["p10"], "p50": s["p50"], "p90": s["p90"],
                       "p_zero": s["p_zero"], "alternative": alt,
                       "margin": float(value - (exp[alt] if alt else 0.0))})
    bench = []
    for n in names:
        if n in started:
            continue
        e = _entry(engine, n)
        reason = "bye" if e.get('bye') == week else ("out" if _unavailable_now(e) else "")
        bench.append({"name": n, "pos": normalize_position(e.get('pos', 'FLEX')), "expected": exp[n],
                      "available": available[n], "reason": reason})
    bench.sort(key=lambda b: -b["expected"])
    return {"team": team, "week": week, "lineup": lineup, "unfilled": sorted(unfilled), "bench": bench,
            "expected_total": float(sum(r["expected"] for r in lineup)),
            "note": ("lineup = the engine's optimal assignment on this week's pre-game expectations (mean x "
                     "environment x script; bye/out = 0); p10/p50/p90 from independent per-player draws; "
                     "margin = expectation over the best bench alternative eligible for the slot.")}


# ======================================================= TOOL 5: opponent-aware lineups
#
# You play one specific opponent each week (league_schedule), and the right construction is
# asymmetric: a favourite wants to minimise variance, an underdog wants to maximise it. This
# tool samples BOTH rosters (and the other six, for the median-beat decision) jointly, through
# the engine's own copula -- build_covariance_matrix over the union of the rosters, so
# same-NFL-team pairs are correlated ACROSS the two rosters as well as within (the correlation
# the engine itself omits, AUDIT_PLAN.md F16; cross=False reproduces the engine's per-roster
# behaviour) -- then evaluates lineups on that one joint sample: any lineup's weekly total is a
# column sum, P(beat opponent) is a mean over sims. Constructions:
#   max_mean  the engine's own rule (optimal assignment on expectation),
#   safe      assignment on expectation - k * sd,
#   stack     assignment on expectation + k * sd, plus a bonus for pass-catchers on the NFL
#             team of the chosen QB (a correlated stack),
#   p_max     bounded local search from max_mean over single bench-for-starter swaps,
#             accepting only swaps that raise P(beat opponent) on the same sample.
# The favourite/underdog asymmetry is meant to EMERGE from the numbers, not be imposed.
# Opponent lineup: his max-expectation lineup (the engine's convention) unless supplied.
def weekly_scores_vectorised(season_means, std_val, z, env_ratio, env_var, script_mult, contingency_pts):
    """Elementwise mirror of FantasySimulationEngine._weekly_score_from_z (pinned equal by
    test): lognormal on z with E = season mean and sd = std_val, plus contingency, times the
    environment draw and script multiplier, capped."""
    m = np.asarray(season_means, dtype=float); z = np.asarray(z, dtype=float); env_var = np.asarray(env_var, dtype=float)
    base = np.zeros_like(m)
    ok = m > 0.01
    sigma_a = np.sqrt(np.log(1 + (std_val / m[ok]) ** 2))
    mu_a = np.log(m[ok]) - (sigma_a ** 2 / 2)
    base[ok] = np.exp(mu_a + sigma_a * z[ok])
    final = (base + contingency_pts) * env_var * script_mult
    return np.minimum(final, SIM_CONFIG['MAX_REALISTIC_WEEKLY_SCORE'])


def sample_week_matrix(engine, groups, week, n, seed=None, cross=True, starter_exposure=True):
    """(n, players) matrix of joint single-week scores for the players in `groups` (a list of
    name lists, one per fantasy roster). cross=True: one Cholesky factor over every name;
    cross=False: one per group with independent draws between groups (the engine's current
    behaviour). Mirrors the engine's per-player draw as sample_week_scores does; RNG order is
    its own (documented), seeded here when `seed` is given."""
    names = [nm for g in groups for nm in g]
    if len(set(names)) != len(names):
        raise ValueError("a player appears in more than one group")
    meta = {nm: {'pos': _entry(engine, nm).get('pos', 'FLEX'), 'team': _entry(engine, nm).get('team', 'FA')} for nm in names}
    if seed is not None:
        np.random.seed(seed)
    z = {}
    factors = [(names, engine.build_covariance_matrix(names, meta))] if cross else \
              [(g, engine.build_covariance_matrix(g, meta)) for g in groups]
    for g, L in factors:
        zc = np.random.normal(size=(n, len(g))) @ L.T
        for j, nm in enumerate(g):
            z[nm] = zc[:, j]
    exposure = SIM_CONFIG['ONSET_EXPOSURE_STARTER'] if starter_exposure else SIM_CONFIG['ONSET_EXPOSURE_BENCH']
    weeks_ahead = week - engine.current_week + 1
    M = np.zeros((n, len(names)))
    for j, nm in enumerate(names):
        e = _entry(engine, nm)
        if e.get('bye') == week:
            continue
        pos = normalize_position(e.get('pos', 'FLEX')); team = e.get('team', 'FA')
        status, on_ir = e.get('injury_status'), bool(e.get('on_ir', False))
        absent = np.zeros(n, dtype=bool)
        if status in SIM_CONFIG["INITIAL_ABSENCE_STATUSES"] or on_ir:
            clocks = np.array([engine._initial_absence_clock(status, on_ir) for _ in range(n)])
            absent = clocks >= weeks_ahead
        onset = np.random.rand(n) < SIM_CONFIG['INJURY_RATES'].get(pos, 0.025) * exposure
        mu_0 = float(e.get('mean', 8.0)); sig_e = float(e.get('std_epistemic', mu_0 * 0.18)); std_a = float(e.get('std_aleatoric', 3.0))
        if mu_0 <= 0.01:
            season = np.zeros(n)
        else:
            sigma_e = np.sqrt(np.log(1 + (sig_e / mu_0) ** 2)); mu_e = np.log(mu_0) - (sigma_e ** 2 / 2)
            season = np.exp(np.random.normal(mu_e, sigma_e, size=n))
        veg = engine._compute_week_environment(week, team)
        ratio = veg['total'] / _env_norm(engine)
        env_var = np.random.normal(ratio, 0.10, size=n)
        col = weekly_scores_vectorised(season, std_a, z[nm], ratio, env_var, engine._script_multiplier(pos, veg), 0.0)
        col[absent | onset] = 0.0
        M[:, j] = col
    return M, names


def matchup_lineups(engine, team, week, opponent=None, sims=5000, seed=None, cross=True, k=0.5,
                    stack_bonus=2.0, opponent_lineup=None, max_iter=30):
    if team not in engine.rosters:
        raise KeyError(f"unknown team {team!r}")
    if opponent is None:
        pairs = engine.league_schedule[week - 1] if week - 1 < len(engine.league_schedule) else []
        for a, b in pairs:
            if team in (a, b):
                opponent = b if a == team else a
        if opponent is None:
            raise ValueError(f"{team} has no scheduled opponent in week {week}")
    if opponent not in engine.rosters or opponent == team:
        raise ValueError(f"invalid opponent {opponent!r}")
    others = [t for t in engine.team_names if t not in (team, opponent)]
    groups = [list(engine.rosters[team]), list(engine.rosters[opponent])] + [list(engine.rosters[t]) for t in others]
    M, names = sample_week_matrix(engine, groups, week, sims, seed=seed, cross=cross)
    idx = {nm: i for i, nm in enumerate(names)}
    exp = {nm: week_expectation(engine, nm, week) for nm in names}
    sd = {nm: float(M[:, idx[nm]].std()) for nm in names}
    avail = {nm: not (_entry(engine, nm).get('bye') == week or _unavailable_now(_entry(engine, nm))) for nm in names}

    def assign(roster, score):
        cands = [(nm, _opts(engine, nm), score(nm)) for nm in roster if avail[nm]]
        assigned, _ = engine._solve_optimal_assignment(cands)
        return [(nm, slot) for nm, _v, slot in assigned]

    def total(lineup):
        return M[:, [idx[nm] for nm, _ in lineup]].sum(axis=1) if lineup else np.zeros(sims)

    if opponent_lineup:
        bad = [nm for nm in opponent_lineup if nm not in engine.rosters[opponent]]
        if bad:
            raise ValueError(f"{bad} not on {opponent}'s roster")
        opp_lineup = assign(opponent_lineup, lambda nm: exp[nm])
    else:
        opp_lineup = assign(engine.rosters[opponent], lambda nm: exp[nm])
    opp_total = total(opp_lineup)
    other_totals = [total(assign(engine.rosters[t], lambda nm: exp[nm])) for t in others]

    def evaluate(lineup):
        my = total(lineup)
        med = np.median(np.column_stack([my, opp_total] + other_totals), axis=1)
        p = float(np.mean(my > opp_total))
        return {"lineup": [{"slot": s, "name": nm, "expected": exp[nm], "sd": sd[nm],
                            "nfl_team": _entry(engine, nm).get('team', 'FA')} for nm, s in sorted(lineup, key=lambda x: x[1])],
                "mean": float(my.mean()), "sd": float(my.std()),
                "p_beat_opponent": p, "se": float(np.sqrt(max(p * (1 - p), 1e-12) / sims)),
                "p_beat_median": float(np.mean(my >= med)),
                "margin_mean": float((my - opp_total).mean()), "margin_sd": float((my - opp_total).std())}

    roster = engine.rosters[team]
    max_mean = assign(roster, lambda nm: exp[nm])
    safe = assign(roster, lambda nm: exp[nm] - k * sd[nm])
    boom = assign(roster, lambda nm: exp[nm] + k * sd[nm])
    qb_team = next((_entry(engine, nm).get('team') for nm, s in boom if s == 'QB'), None)

    def stack_score(nm):
        e = _entry(engine, nm)
        bonus = stack_bonus if (qb_team and e.get('team') == qb_team
                                and normalize_position(e.get('pos', 'FLEX')) in ('WR', 'TE')) else 0.0
        return exp[nm] + k * sd[nm] + bonus
    stack = assign(roster, stack_score)

    # p_max: local search over single swaps, same joint sample, accept only improvements
    cur, cur_p = list(max_mean), evaluate(max_mean)["p_beat_opponent"]
    started = {nm for nm, _ in cur}
    for _ in range(max_iter):
        best, best_p = None, cur_p
        for i, (nm, slot) in enumerate(cur):
            for b in roster:
                if b in started or not avail[b] or not any(p in _slot_positions(slot) for p in _opts(engine, b)):
                    continue
                cand = list(cur); cand[i] = (b, slot)
                p = float(np.mean(total(cand) > opp_total))
                if p > best_p + 1e-12:
                    best, best_p = cand, p
        if best is None:
            break
        cur, cur_p = best, best_p
        started = {nm for nm, _ in cur}

    constructions = {"max_mean": evaluate(max_mean), "safe": evaluate(safe), "stack": evaluate(stack), "p_max": evaluate(cur)}
    favoured = constructions["max_mean"]["p_beat_opponent"] > 0.5
    ranking = sorted(constructions, key=lambda c: -constructions[c]["p_beat_opponent"])
    return {"team": team, "opponent": opponent, "week": week, "n": sims, "cross": cross, "k": k,
            "favoured_by_max_mean": favoured, "ranking_by_p_beat_opponent": ranking,
            "opponent_lineup": [{"slot": s, "name": nm, "expected": exp[nm]} for nm, s in sorted(opp_lineup, key=lambda x: x[1])],
            "opponent_lineup_assumed": opponent_lineup is None,
            "constructions": constructions,
            "note": (("joint sample through the engine's copula over ALL rosters (same-NFL-team correlation "
                      "across rosters included -- the engine itself omits it, F16)" if cross else
                      "per-roster copula only, as the engine does (no cross-roster correlation)")
                     + "; opponent's lineup = his max-expectation lineup unless supplied; P(beat median) = "
                       "share of sims at or above the median of all eight totals.")}


# ================================================================== TRADE-TARGET FINDER
#
# "Give me ideas", built from F2's offer constructor: _construct_trade_offers(desperate, rich)
# returns the rich side's BENCH players who would start at the desperate side's weakest
# fillable slot, paired with the cheapest desperate-side player that still upgrades a rich
# starter. With MY roster as the desperate side against each other roster that is exactly
# "buried behind a stud on their team, starts for me" (buy); the other way round it is what
# each opponent would want from my bench (sell: surplus with an identified buyer). Gains are
# the engine's own acceptance rule (get_optimal_score both sides, 2-for-2 with the throw-in).
# Seller signal = the latest season export's Playoff_Pct (never MANAGER_PROFILES, whose
# trade_will is shown as "modelled willingness" but is of unverified provenance). Limitation,
# by construction: the search is need-driven (my weakest slots), not best-player-available --
# F2 option (B) territory, out of scope.
def _package(engine, d_team, r_team, p1, p2, p3):
    """The engine's 2-for-2: the desperate side's lowest-mean player AFTER the two received
    pieces are added goes back as the throw-in. When that lowest piece is one of the two
    received players, he is handed straight back -- a 1-for-1 in substance -- and the terms
    say so (found on real data: 'Tyrone Tracy not on Legion of Coom's roster' when the throw-in
    was listed as something I give). Returns (d_list, r_list, tent_d, tent_r, d_gives, r_gives)
    with d_gives/r_gives the real terms, valid for evaluate_trade."""
    mean = lambda p: _entry(engine, p).get('mean', 0.0)
    d_list, r_list = list(engine.rosters[d_team]), list(engine.rosters[r_team])
    tent_d = sorted([p for p in d_list if p != p1] + [p2, p3], key=mean, reverse=True)
    dropped = tent_d.pop()
    tent_r = [p for p in r_list if p not in (p2, p3)] + [p1, dropped]
    if dropped in (p2, p3):
        d_gives, r_gives = [p1], [p3 if dropped == p2 else p2]
    else:
        d_gives, r_gives = [p1, dropped], [p2, p3]
    return d_list, r_list, tent_d, tent_r, d_gives, r_gives


def find_trade_targets(engine, team, outcomes=None, week=None, seller_threshold=35.0, top_n=10,
                       evaluate_top=0, batches=3, sims=1000):
    from fantasy_sim.config import MANAGER_PROFILES
    week = week or engine.current_week
    if team not in engine.rosters:
        raise KeyError(f"unknown team {team!r}")
    gos = engine.get_optimal_score
    my_starters = roster_gaps(engine, team, (week,))[week]["starters"]
    contention_note = (f"seller = their Playoff_Pct below {seller_threshold:.0f}% in the season export supplied"
                       if outcomes else
                       "no season export supplied: seller flag unavailable (run scripts.run_simulation first)")
    buy, sell = [], []
    for other in engine.team_names:
        if other == team:
            continue
        their_starters = roster_gaps(engine, other, (week,))[week]["starters"]
        pp = (outcomes or {}).get(other)
        seen = set()
        for p1, p2, p3 in engine._construct_trade_offers(engine.rosters[team], engine.rosters[other]):
            if (p1, p2, p3) in seen:
                continue
            seen.add((p1, p2, p3))
            d_list, r_list, tent_d, tent_r, i_give, i_get = _package(engine, team, other, p1, p2, p3)
            my_gain, their_gain = gos(tent_d) - gos(d_list), gos(tent_r) - gos(r_list)
            opts2 = _opts(engine, p2)
            elig = [(lst[0][1], slot) for slot, lst in my_starters.items() if lst and any(p in _slot_positions(slot) for p in opts2)]
            behind = [(lst[-1][1], lst[-1][0]) for slot, lst in their_starters.items() if lst and any(p in _slot_positions(slot) for p in opts2)]
            buy.append({
                "with": other, "target": p2, "target_mean": float(_entry(engine, p2).get('mean', 0.0)),
                "buried_behind": max(behind)[1] if behind else None,
                "fills_my_slot": min(elig)[1] if elig else None,
                "i_give": i_give, "i_get": i_get,
                "my_gain": float(my_gain), "their_gain": float(their_gain),
                "acceptable": bool(my_gain > 0 and their_gain > 0),
                "their_playoff_pct": float(pp["Playoff_Pct"]) if pp else None,
                "their_expected_wins": float(pp["Expected_Wins"]) if pp else None,
                "seller": (float(pp["Playoff_Pct"]) < seller_threshold) if pp else None,
                "willingness": MANAGER_PROFILES.get(other, {}).get('trade_will'),
            })
        seen = set()
        for p1, p2, p3 in engine._construct_trade_offers(engine.rosters[other], engine.rosters[team]):
            if (p1, p2, p3) in seen:
                continue
            seen.add((p1, p2, p3))
            d_list, r_list, tent_d, tent_r, they_give, they_want = _package(engine, other, team, p1, p2, p3)
            their_gain, my_gain = gos(tent_d) - gos(d_list), gos(tent_r) - gos(r_list)
            sell.append({"buyer": other, "they_want": they_want, "they_give": they_give,
                         "my_gain": float(my_gain), "their_gain": float(their_gain),
                         "acceptable": bool(my_gain > 0 and their_gain > 0),
                         "their_playoff_pct": float(pp["Playoff_Pct"]) if pp else None,
                         "willingness": MANAGER_PROFILES.get(other, {}).get('trade_will')})
    buy.sort(key=lambda b: (0 if b["acceptable"] else 1, -b["my_gain"]))
    sell.sort(key=lambda s_: (0 if s_["acceptable"] else 1, -s_["my_gain"]))
    buy, sell = buy[:top_n], sell[:top_n]
    for b in buy[:evaluate_top]:
        b["evaluation"] = evaluate_trade(engine, team, b["i_give"], b["with"], b["i_get"], batches=batches, sims=sims)
    return {"team": team, "week": week, "buy": buy, "sell": sell, "contention_note": contention_note,
            "note": ("buy = F2's offer constructor with my roster as the desperate side (their buried bench "
                     "player who starts at my weakest fillable slot, for my cheapest player that upgrades one "
                     "of their starters, 2-for-2 with the throw-in); sell = the mirror. Gains = the engine's "
                     "acceptance rule (optimal score incl. 0.1 x bench). Need-driven, not best-available. "
                     "willingness = MANAGER_PROFILES trade_will, modelled and of unverified provenance, never "
                     "a filter.")}


# ============================================================== SINGLE-ROSTER MOVES
def apply_add_drop(engine, team, adds, drops):
    """A deep copy of `engine` with a single-roster move applied: `adds` come from the
    free-agent pool (each must be in the baseline pool and on no roster -- a player Sleeper
    never projected is a loud error, never an invented baseline), `drops` are released to it.
    The original engine is untouched. Raises on an unknown team, an add already rostered
    anywhere, a drop not on the roster, or a roster left above ACTIVE_ROSTER_LIMIT."""
    if team not in engine.rosters:
        raise KeyError(f"unknown team {team!r}")
    if not adds and not drops:
        raise ValueError("nothing to do: no adds and no drops")
    for n in adds:
        if any(n in r for r in engine.rosters.values()):
            raise ValueError(f"cannot add {n!r}: already on a roster")
        if n not in engine.baselines or not isinstance(engine.baselines[n], dict):
            raise ValueError(f"cannot add {n!r}: not in the baseline pool (no projection exists for him)")
    for n in drops:
        if n not in engine.rosters[team]:
            raise ValueError(f"cannot drop {n!r}: not on {team}'s roster")
    e2 = _copy.deepcopy(engine)
    for n in drops:
        e2.rosters[team].remove(n)
        e2.meta[team].pop(n, None)
    for n in adds:
        e2.rosters[team].append(n)
        e2.meta[team][n] = {'pos': _entry(engine, n).get('pos', 'FLEX'),
                            'team': _entry(engine, n).get('team', 'FA')}
    if _active_count(e2, team) > ACTIVE_ROSTER_LIMIT:
        raise ValueError(f"{team} would carry {_active_count(e2, team)} active players "
                         f"(limit {ACTIVE_ROSTER_LIMIT}); a drop is needed")
    return e2


def evaluate_add_drop(engine, team, adds, drops, batches=10, sims=300, faab_bid=None):
    """Paired evaluation of a single-roster move (free-agent add/drop, or a waiver claim when
    faab_bid is given): the same machinery as evaluate_trade, with one roster changing and no
    counterparty -- one 'team' side, seven bystanders. Champ deltas still sum to zero across
    the league (one champion per sim regardless of what changed)."""
    with_engine = apply_add_drop(engine, team, adds, drops)
    teams = _paired_evaluation(engine, with_engine, batches, sims)
    for t, d in teams.items():
        d["side"] = "team" if t == team else "bystander"
    move = {"team": team, "adds": list(adds), "drops": list(drops)}
    if faab_bid is not None:
        move["faab_bid"] = faab_bid
    return {"move": move, "n_sims": batches * sims, "batches": batches, "sims_per_batch": sims,
            "teams": teams,
            "note": ("paired full simulations on identical seeds; delta = with the move minus without; "
                     "SE is the paired-batch standard error. One roster changes; there is no counterparty.")}


# ============================================================ DECISION-LOG EVALUATION
#
# The decision log (data/logs/decision_log.jsonl, sync.ingest_transactions) records every
# completed league transaction with its projection snapshot. For trades, tool 2's paired
# evaluation can be attached after the fact: evaluate_logged_trade reads the logged terms,
# runs evaluate_trade, and appends an `evaluation` record referencing the transaction_id --
# opt-in per trade (scripts.evaluate_trade --log-tx), never automatic, because a full paired
# simulation per transaction inside every sync is exactly the cost trap F2 commit 3 avoided.
# A trade that has already EXECUTED is evaluated by reversing it on the current rosters and
# negating the deltas (the "without" arm is the undo); a trade the rosters still show as
# pending is evaluated directly; anything in between is roster drift, reported plainly --
# the evaluation is only meaningful near the time of the trade.
import datetime as _dt2
import json as _json


def _read_decision_log(path):
    """Open-and-catch rather than exists()-then-open: robust to TOCTOU, and to test harnesses
    that patch os.path.exists globally (the engine fixture does)."""
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(_json.loads(line))
    except FileNotFoundError:
        return []
    return rows


def unevaluated_my_trades(path=None):
    """My logged trades with no evaluation record yet -- the weekly digest's reminder list.
    Never raises: an unreadable log returns []."""
    if path is None:
        from fantasy_sim.storage import DECISION_LOG_FILE as path  # noqa: F811
    try:
        rows = _read_decision_log(path)
    except Exception:
        return []
    evaluated = {r.get("transaction_id") for r in rows if r.get("record_type") == "evaluation"}
    return [{"transaction_id": r["transaction_id"], "week": r.get("week"), "teams": r.get("teams", [])}
            for r in rows
            if r.get("record_type") is None and r.get("type") == "trade" and r.get("is_mine")
            and r.get("transaction_id") not in evaluated]


# FAAB budget context. A bid spends a finite season-long resource, so "was this bid too
# high" is a different question from "did this player help": the budget block below is
# reported SEPARATELY from the roster-change deltas and never merged with them. The market
# comparison uses the decision log's league-wide waiver claims (bid + frozen projection
# snapshot); below MARKET_MIN_COMPARABLES comparable claims it says "too few" and refuses to
# manufacture a rate. The threshold is a judgment call, not a derived constant: fewer than ~8
# claims cannot distinguish a bidding style from noise, and the honest output is the raw list.
MARKET_MIN_COMPARABLES = 8


def faab_context(engine, bid, team, log_path=None, exclude_tx=None):
    """The budget-cost block for one bid: what was bid, what the bidder has left (as of now),
    and what the league has paid for comparable claims -- comparables always listed raw, a
    market rate only at n >= MARKET_MIN_COMPARABLES. VORP uses each claim's FROZEN snapshot
    mean against CURRENT replacement levels (which drift; stated in the note)."""
    if log_path is None:
        from fantasy_sim.storage import DECISION_LOG_FILE as log_path  # noqa: F811
    comps = []
    for r in _read_decision_log(log_path):
        if r.get("record_type") is not None or r.get("type") != "waiver":
            continue
        if exclude_tx is not None and r.get("transaction_id") == exclude_tx:
            continue
        b = r.get("faab_bid")
        adds = r.get("adds") or []
        proj = adds[0].get("projection") if adds else None
        if b is None or not proj or proj.get("mean") is None:
            continue
        pos = normalize_position(proj.get("pos", "FLEX"))
        vorp = float(proj["mean"]) - engine.replacement_levels.get(pos, 4.0)
        comps.append({"bid": b, "player": adds[0].get("name"), "pos": pos,
                      "proj_mean": float(proj["mean"]), "vorp": round(vorp, 2),
                      "team": (r.get("teams") or [None])[0], "week": r.get("week"),
                      "snapshot_is_retroactive": r.get("snapshot_is_retroactive")})
    n = len(comps)
    market = None
    if n >= MARKET_MIN_COMPARABLES:
        import statistics
        bids = [c["bid"] for c in comps]
        market = {"n": n, "median_bid": statistics.median(bids)}
        per = [c["bid"] / c["vorp"] for c in comps if c["vorp"] > 0]
        if per:
            market["median_bid_per_vorp"] = statistics.median(per)
        market_note = f"market rate from n={n} league claims"
    else:
        market_note = (f"too few comparable claims (n={n}) to estimate a market rate -- "
                       "listing them raw instead; no verdict")
    faab = getattr(engine, "current_faab", {}) or {}
    return {"bid": bid, "team": team,
            "remaining_faab": float(faab.get(team, 100.0)),
            "league_avg_faab": float(np.mean(list(faab.values()))) if faab else 100.0,
            "n_comparables": n, "comparables": comps, "market": market, "market_note": market_note,
            "note": ("budget cost, reported separately from roster-change value and never merged with it. "
                     "remaining_faab is as of now, not as of the bid; VORP uses frozen snapshot means "
                     f"against current replacement levels. MARKET_MIN_COMPARABLES={MARKET_MIN_COMPARABLES} "
                     "is a judgment call.")}


def evaluate_logged_transaction(engine, transaction_id, batches=10, sims=300, log_path=None):
    """--log-tx, dispatched on the logged transaction's type: trades through evaluate_trade's
    two-roster path, free-agent moves and waiver claims through evaluate_add_drop's
    single-roster path. Either way: an already-executed move is evaluated by REVERSING it on
    current rosters with the deltas negated; a still-pending one directly; anything in
    between is roster drift, reported plainly. Appends an `evaluation` record referencing the
    transaction_id; dedupes on an existing one."""
    if log_path is None:
        from fantasy_sim.storage import DECISION_LOG_FILE as log_path  # noqa: F811
    rows = _read_decision_log(log_path)
    if any(r.get("record_type") == "evaluation" and r.get("transaction_id") == transaction_id for r in rows):
        return {"skipped": "already evaluated", "transaction_id": transaction_id}
    tx = next((r for r in rows if r.get("record_type") is None and r.get("transaction_id") == transaction_id), None)
    if tx is None:
        raise ValueError(f"{transaction_id!r} is not a logged transaction in {log_path}")
    if tx.get("type") in ("free_agent", "waiver") and tx.get("teams"):
        return _evaluate_logged_move(engine, tx, batches, sims, log_path)
    if tx.get("type") != "trade" or len(tx.get("teams", [])) < 2:
        raise ValueError(f"{transaction_id!r} has unsupported type {tx.get('type')!r}")
    team_a, team_b = tx["teams"][0], tx["teams"][1]
    a_received = [a["name"] for a in tx["adds"] if a["to_team"] == team_a]
    b_received = [a["name"] for a in tx["adds"] if a["to_team"] == team_b]

    def on(team, names):
        return all(n in engine.rosters.get(team, []) for n in names)

    if on(team_a, b_received) and on(team_b, a_received):
        # pre-trade state on disk: evaluate directly (A gives what B received)
        r = evaluate_trade(engine, team_a, b_received, team_b, a_received, batches=batches, sims=sims)
        reversed_eval = False
    elif on(team_a, a_received) and on(team_b, b_received):
        # already executed: evaluate the undo and negate every delta
        r = evaluate_trade(engine, team_a, a_received, team_b, b_received, batches=batches, sims=sims)
        for d in r["teams"].values():
            for k in ("champ_pct", "playoff_pct", "expected_wins"):
                d[k]["delta"] = -d[k]["delta"]
                d[k]["with"], d[k]["without"] = d[k]["without"], d[k]["with"]
        r["trade"] = {"team_a": team_a, "a_gives": b_received, "team_b": team_b, "b_gives": a_received, "drops": {}}
        r["note"] += " Evaluated post-execution by reversing the trade on current rosters; deltas negated."
        reversed_eval = True
    else:
        missing = [n for n in a_received + b_received
                   if n not in engine.rosters.get(team_a, []) and n not in engine.rosters.get(team_b, [])]
        raise ValueError(f"{missing or 'the logged players'} are no longer on the two rosters -- the paired "
                         "evaluation is only meaningful near the time of the trade (roster drift since).")
    record = {"record_type": "evaluation", "transaction_id": transaction_id,
              "evaluated_at": _dt2.datetime.now(_dt2.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "n_sims": r["n_sims"], "batches": batches, "post_execution_reversed": reversed_eval,
              "teams": {t: {"champ_pct": d["champ_pct"], "playoff_pct": d["playoff_pct"]}
                        for t, d in r["teams"].items()}}
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(_json.dumps(record, sort_keys=True) + chr(10))
    return r


def _evaluate_logged_move(engine, tx, batches, sims, log_path):
    team = tx["teams"][0]
    added = [a["name"] for a in tx.get("adds", [])]
    dropped = [a["name"] for a in tx.get("drops", [])]
    bid = tx.get("faab_bid")

    def on_team(names):
        return all(n in engine.rosters.get(team, []) for n in names)

    def in_pool(names):
        return all(not any(n in r for r in engine.rosters.values()) for n in names)

    if in_pool(added) and on_team(dropped):
        r = evaluate_add_drop(engine, team, added, dropped, batches=batches, sims=sims, faab_bid=bid)
        reversed_eval = False
    elif on_team(added) and in_pool(dropped):
        r = evaluate_add_drop(engine, team, dropped, added, batches=batches, sims=sims, faab_bid=bid)
        for d in r["teams"].values():
            for k in ("champ_pct", "playoff_pct", "expected_wins"):
                d[k]["delta"] = -d[k]["delta"]
                d[k]["with"], d[k]["without"] = d[k]["without"], d[k]["with"]
        r["move"] = {"team": team, "adds": added, "drops": dropped}
        if bid is not None:
            r["move"]["faab_bid"] = bid
        r["note"] += " Evaluated post-execution by reversing the move on current rosters; deltas negated."
        reversed_eval = True
    else:
        raise ValueError(f"roster drift since the logged move {tx['transaction_id']!r}: the involved players "
                         "are neither in their pre-move nor their post-move places -- the paired evaluation "
                         "is only meaningful near the time of the move.")
    if tx.get("type") == "waiver":
        r["faab"] = faab_context(engine, bid, team, log_path=log_path, exclude_tx=tx["transaction_id"])
    record = {"record_type": "evaluation", "transaction_id": tx["transaction_id"],
              "evaluated_at": _dt2.datetime.now(_dt2.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "n_sims": r["n_sims"], "batches": batches, "post_execution_reversed": reversed_eval,
              "teams": {t: {"champ_pct": d["champ_pct"], "playoff_pct": d["playoff_pct"]}
                        for t, d in r["teams"].items()}}
    if "faab" in r:
        record["faab"] = {"bid": r["faab"]["bid"], "n_comparables": r["faab"]["n_comparables"],
                          "market": r["faab"]["market"], "market_note": r["faab"]["market_note"]}
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(_json.dumps(record, sort_keys=True) + chr(10))
    return r


def pending_evaluations(log_path=None, mine_only=False, limit=None):
    """Logged transactions with no evaluation record yet, MY moves first (each group
    chronological), so --limit catches the owner up before spending engine-hours on the
    rest of the league. Read-only."""
    if log_path is None:
        from fantasy_sim.storage import DECISION_LOG_FILE as log_path  # noqa: F811
    rows = _read_decision_log(log_path)
    done = {r.get("transaction_id") for r in rows if r.get("record_type") == "evaluation"}
    txs = [r for r in rows if r.get("record_type") is None and r.get("transaction_id") not in done]
    if mine_only:
        txs = [t for t in txs if t.get("is_mine")]
    txs.sort(key=lambda t: (not t.get("is_mine"), t.get("created") or ""))
    return txs[:limit] if limit else txs


def evaluate_pending(engine, mine_only=False, limit=None, batches=10, sims=300,
                     log_path=None, progress=print):
    """The batch catch-up (scripts.evaluate_move --evaluate-unevaluated): every pending
    logged transaction through evaluate_logged_transaction, strictly SEQUENTIAL in this one
    process -- R1: never run engines concurrently on this machine. Roster drift (players
    re-moved since an old transaction; EXPECTED for backfilled moves reverse-evaluated on
    current rosters) and already-evaluated records are counted and reported, never fatal.
    Each completed evaluation is appended to the log before the next starts, so an
    interrupt between evaluations loses nothing. Deliberately manual and opt-in: automatic
    evaluation inside sync is exactly the cost trap F2 commit 3 avoided."""
    out = {"evaluated": [], "drift": [], "already": []}
    todo = pending_evaluations(log_path=log_path, mine_only=mine_only, limit=limit)
    for i, tx in enumerate(todo, 1):
        txid = tx["transaction_id"]
        label = f"[{i}/{len(todo)}] {txid} ({tx.get('type')}, {(tx.get('teams') or ['?'])[0]})"
        try:
            r = evaluate_logged_transaction(engine, txid, batches=batches, sims=sims,
                                            log_path=log_path)
        except ValueError as ex:
            if "drift" in str(ex).lower():
                out["drift"].append(txid)
                progress(f"{label}: SKIPPED -- {ex}")
                continue
            raise
        if r.get("skipped"):
            out["already"].append(txid)
            progress(f"{label}: skipped ({r['skipped']})")
            continue
        out["evaluated"].append(txid)
        m = r.get("move") or r.get("trade") or {}
        mover = m.get("team") or m.get("team_a")
        d = (r.get("teams") or {}).get(mover) or {}
        progress(f"{label}: {mover} Champ {d['champ_pct']['delta']:+.2f}+-{d['champ_pct']['se']:.2f}  "
                 f"Playoff {d['playoff_pct']['delta']:+.2f}+-{d['playoff_pct']['se']:.2f}  "
                 f"ExpW {d['expected_wins']['delta']:+.3f}+-{d['expected_wins']['se']:.3f}")
    return out


# Compatibility alias: the original trade-only entry point's name.
evaluate_logged_trade = evaluate_logged_transaction


# ================================================================ LEAGUE-WIDE THIS WEEK
#
# Every pairing on the schedule, P(win) both ways and P(>= median) for all eight teams, on ONE
# joint sample through the copula (sample_week_matrix over the union of the rosters, cross-
# roster correlation included by default) with each team on its max-expectation lineup -- the
# same machinery matchup_lineups uses for my matchup, applied to all pairings, so the two
# sections agree by construction. Genuinely new computation: only the loop over pairings.
def league_week_outlook(engine, week, sims=5000, seed=None, cross=True):
    pairs = engine.league_schedule[week - 1] if week - 1 < len(engine.league_schedule) else []
    if not pairs:
        raise ValueError(f"no scheduled pairings for week {week}")
    teams = list(engine.team_names)
    groups = [list(engine.rosters[t]) for t in teams]
    M, names = sample_week_matrix(engine, groups, week, sims, seed=seed, cross=cross)
    idx = {nm: i for i, nm in enumerate(names)}
    exp = {nm: week_expectation(engine, nm, week) for nm in names}
    sd = {nm: float(M[:, idx[nm]].std()) for nm in names}
    avail = {nm: not (_entry(engine, nm).get('bye') == week or _unavailable_now(_entry(engine, nm))) for nm in names}

    lineups, totals = {}, {}
    for t in teams:
        cands = [(nm, _opts(engine, nm), exp[nm]) for nm in engine.rosters[t] if avail[nm]]
        assigned, _ = engine._solve_optimal_assignment(cands)
        lineup = [(nm, slot) for nm, _v, slot in assigned]
        lineups[t] = lineup
        totals[t] = M[:, [idx[nm] for nm, _ in lineup]].sum(axis=1) if lineup else np.zeros(sims)
    all_totals = np.column_stack([totals[t] for t in teams])
    median = np.median(all_totals, axis=1)

    opponent = {}
    matchups = []
    for a, b in pairs:
        opponent[a], opponent[b] = b, a
        ta, tb = totals[a], totals[b]
        p_a, p_b = float(np.mean(ta > tb)), float(np.mean(tb > ta))
        matchups.append({"a": a, "b": b, "p_a": p_a, "p_b": p_b, "p_tie": float(np.mean(ta == tb)),
                         "se": float(np.sqrt(max(p_a * (1 - p_a), 1e-12) / sims)),
                         "a_expected": float(ta.mean()), "b_expected": float(tb.mean()),
                         "margin_mean": float((ta - tb).mean()), "margin_sd": float((ta - tb).std())})
    team_rows = {}
    for t in teams:
        team_rows[t] = {
            "opponent": opponent.get(t),
            "p_beat_median": float(np.mean(totals[t] >= median)),
            # sampled mean of the lineup total (absences and onsets priced in), the same
            # quantity the matchup rows' a_expected/b_expected report; the pre-game sum of
            # expectations (no hazard) is kept separately so the two are never confused.
            "expected_total": float(totals[t].mean()),
            "expected_pre_total": float(sum(exp[nm] for nm, _ in lineups[t])),
            "sd_total": float(totals[t].std()),
            "lineup": [{"slot": s_, "name": nm, "expected": exp[nm], "sd": sd[nm],
                        "nfl_team": _entry(engine, nm).get('team', 'FA')} for nm, s_ in sorted(lineups[t], key=lambda x: x[1])],
        }
    return {"week": week, "n": sims, "cross": cross, "matchups": matchups, "teams": team_rows,
            "note": ("one joint sample through the engine's copula over all rosters (cross-roster same-NFL-team "
                     "correlation included -- the engine itself omits it, F16" if cross else
                     "per-roster copula only, as the engine does") +
                    "); every team on its max-expectation lineup; P(>= median) = share of sims at or above "
                    "the median of all eight totals."}


def roster_grades(engine, week=None):
    """League table: every team's grade summary ranked by lineup_vorp (1 = best)."""
    week = week or engine.current_week
    rows = []
    for t in engine.team_names:
        g = grade_roster(engine, t, week)
        rows.append({"team": t, "lineup_vorp": g["lineup_vorp"], "depth_vorp": g["depth_vorp"],
                     "optimal_score": g["optimal_score"], "holes": len(g["holes"]),
                     "tier1_starters": sum(1 for p in g["players"] if p["role"] == "starter" and p["tier"] == 1),
                     "starters_below_replacement": sum(1 for p in g["players"] if p["role"] == "starter" and p["vorp"] < 0)})
    rows.sort(key=lambda r: -r["lineup_vorp"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"week": week, "teams": rows}

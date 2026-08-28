"""
backtest_harness.py

Backtests the fantasy simulation engine against REAL historical league outcomes, reusing the
real, unmodified production code from fantasy_sim.sync and fantasy_sim.simulation
rather than a separate reimplementation of the model's logic.

============================== v1 SCOPE AND LIMITATIONS ==============================
Read this before interpreting any results -- it explains exactly what this validates and,
just as importantly, what it does NOT.

- ONE historical season only (2025). Sleeper's previous_league_id chain for this league ends
  there. Results reflect one season's checkpoints, not true cross-season validation. This
  accumulates naturally over time as more seasons complete.

- Checkpoints start at week 3, never week 1/preseason. Confirmed empirically: Sleeper does not
  preserve historical weekly point projections (the projections endpoint returns only a generic
  ADP placeholder for past weeks, identical across different weeks requested -- not real,
  week-specific projected stats). Preseason/week-1 model behavior cannot be validated this way.

- Player baselines at each checkpoint are reconstructed from a POSITIONAL-ONLY prior (the same
  BASE_STREAMER_MEANS/VOLATILITY_CONSTANTS/EPISTEMIC_ERROR_RATES already used in production for
  "we know nothing yet" assumptions) blended with REAL realized weeks-1..(W-1) performance via
  the simulation engine's own, completely unmodified _apply_bayesian_updates() method -- not a
  parallel reimplementation of that shrinkage math. This is the load-bearing design choice: we
  trust the real code, we don't guess at a stand-in for it.

- Rosters use the season's FINAL (end-of-season) composition for every checkpoint, not true
  week-by-week snapshots (which would require replaying the full add/drop/trade transaction
  history -- a real, scoped-out-for-v1 undertaking). This modestly overstates early-checkpoint
  roster quality for teams that made helpful moves later in the season, concentrated mainly in
  streaming-tier bench slots rather than core roster value.

- Vegas-informed team strength, real NFL schedule/opponent context, and defensive priors are
  NOT tested in v1 -- verified historical access to odds data and a past season's ESPN schedule
  wasn't established, so these are held flat/neutral for every team and week. v1 specifically
  validates the model's PLAYER-LEVEL variance, correlation, injury, roster-construction
  (including the optimal-assignment solver), and playoff-format mechanics against real
  outcomes -- not the market-data layer.

- The real fantasy league schedule (who plays whom) for the FULL season IS used, including
  weeks after the checkpoint. This is not look-ahead bias: the schedule is fixed and known in
  advance in a real league, exactly as production already has access to the full
  league_schedule.json regardless of current_week.

- CONFIRMED via a live run, and fixed: the 2025 season used plain head-to-head scoring only,
  not the current league's hybrid H2H + weekly-median-beat format. The first real backtest run
  showed simulated win totals roughly double the real ones -- an exact match for "sim awards 2
  decisions/week, real season awarded 1/week". Fixed via SIM_CONFIG['MEDIAN_SCORING_ENABLED'],
  which this harness sets to False by default for this season. See run_backtest_checkpoint's
  docstring for the full explanation and verification.

- CONFIRMED via a live run, and NOT fixed: the 2025 season used a single team-DEFENSE roster
  slot, not the current engine's individual IDP (DB/DL/LB) slots. normalize_position() has no
  'DEF' case, so a real 2025 DEF entity silently becomes 'FLEX' -- meaning literally every
  team's 3 IDP slots get filled by generic streamer injections, every single week, confirmed
  directly in the first run's console output (100% streamer coverage, zero exceptions). This
  season's backtest therefore only meaningfully validates QB/RB/WR/TE mechanics; IDP-specific
  mechanics (streaming, roster construction) are untested by it. Building a genuine alternate
  DEF-slot roster mode was judged out of scope for this iteration -- see
  run_backtest_checkpoint's docstring for the full reasoning.
========================================================================================
"""
import json
import math
import os
import shutil
import sys
import unittest
from unittest.mock import patch, MagicMock

import numpy as np
import requests

from fantasy_sim import sync
from fantasy_sim import simulation as simmod
from fantasy_sim import storage
import logging

from fantasy_sim.config import BASE_URL, REGULAR_SEASON_WEEKS, derive_bye_weeks

BACKTEST_WORKDIR = "backtest_workdir"
BACKTEST_SEASON_LEAGUE_ID = "1253869352399142913"  # 2025 season, confirmed via prior diagnostic
DEFAULT_CHECKPOINT_WEEKS = (3, 6, 9, 12)


# ============================================================================
# Scoring: CRPS (Continuous Ranked Probability Score)
# ============================================================================

def compute_crps(samples, actual):
    """
    O(N log N) sorted-empirical CRPS between a set of Monte Carlo samples (a forecast
    distribution) and a single realized outcome. Lower is better; 0 is a perfect point forecast.

    Uses the closed-form identity for sorted samples x_(1) <= ... <= x_(N):
        sum_{i,j} |x_i - x_j| = 2 * sum_i (2i - N - 1) * x_(i)
    which avoids the naive O(N^2) pairwise computation. Hand-verified against samples=[10,20,30],
    actual=15 -> 3.8889 (matches the brute-force O(N^2) definition exactly; see
    test_compute_crps_matches_hand_computed_example).
    """
    x = np.sort(np.asarray(samples, dtype=float))
    n = len(x)
    if n == 0:
        return float('nan')
    term1 = np.mean(np.abs(x - actual))
    i = np.arange(1, n + 1)
    term2 = np.sum((2 * i - n - 1) * x) / (n * n)
    return float(term1 - term2)


# ============================================================================
# Real historical data fetching (network calls -- cannot be verified without a live run)
# ============================================================================

def fetch_league_chain(league_id):
    """Walks Sleeper's previous_league_id chain. Returns a list of season descriptors, most
    recent first, stopping at the first missing/unreachable link."""
    chain = []
    seen = set()
    lid = league_id
    while lid and lid not in seen:
        seen.add(lid)
        resp = requests.get(f"{BASE_URL}/league/{lid}")
        if resp.status_code != 200:
            break
        data = resp.json()
        chain.append({
            "league_id": lid,
            "season": data.get("season"),
            "previous_league_id": data.get("previous_league_id"),
        })
        lid = data.get("previous_league_id")
    return chain


def fetch_season_matchups(league_id, max_week=18):
    """Fetches every week's real matchup data for a historical league_id. Returns
    {week_int: matchups_json}, only including weeks that actually returned data."""
    matchups_by_week = {}
    for wk in range(1, max_week + 1):
        resp = requests.get(f"{BASE_URL}/league/{league_id}/matchups/{wk}")
        if resp.status_code == 200 and resp.json():
            matchups_by_week[wk] = resp.json()
    return matchups_by_week


def fetch_league_roster_data(league_id, players_db):
    """Mirrors sync_all()'s roster/user/standings construction (same TEAM_NAME_MAP, same
    field extraction), applied to a historical league_id instead of the current one. Returns
    (roster_map, live_rosters_payload, final_standings_payload)."""
    users = requests.get(f"{BASE_URL}/league/{league_id}/users").json()
    rosters = requests.get(f"{BASE_URL}/league/{league_id}/rosters").json()
    user_map = {u["user_id"]: u.get("display_name", "") for u in users}
    roster_map = {
        r["roster_id"]: sync.TEAM_NAME_MAP.get(user_map.get(r.get("owner_id"), ""), f"Unknown_{r['roster_id']}")
        for r in rosters
    }

    live_rosters_payload, final_standings_payload = {}, {}
    for r in rosters:
        sim_name = roster_map[r["roster_id"]]
        settings = r.get("settings", {})
        final_standings_payload[sim_name] = {
            "wins": int(settings.get("wins", 0)),
            "losses": int(settings.get("losses", 0)),
            "points_scored": float(f"{settings.get('fpts', 0)}.{settings.get('fpts_decimal', 0)}"),
        }
        # Reuses sync._build_roster_player_entry -- the same helper production uses, fixed
        # after this exact bug crashed a live backtest run (a player with Sleeper's real
        # "team": null propagating into the simulation engine as a literal None instead of
        # "FA"). Duplicating this logic here would have meant re-introducing that bug.
        live_rosters_payload[sim_name] = [
            sync._build_roster_player_entry(pid, players_db)
            for pid in r.get("players", []) if str(pid) in players_db
        ]
    return roster_map, live_rosters_payload, final_standings_payload


def fetch_real_playoff_teams(league_id):
    """Uses Sleeper's winners_bracket endpoint -- the REAL recorded playoff results -- as
    ground truth for which teams actually made the playoffs, rather than re-deriving it from
    our own tiebreak assumptions (which could subtly differ from the real league's actual
    rules). Returns a set of roster_ids, or None if the bracket isn't available."""
    resp = requests.get(f"{BASE_URL}/league/{league_id}/winners_bracket")
    if resp.status_code != 200:
        return None
    bracket = resp.json()
    if not bracket:
        return None
    roster_ids = set()
    for game in bracket:
        for key in ("t1", "t2"):
            rid = game.get(key)
            if rid is not None:
                roster_ids.add(rid)
    return roster_ids


# ============================================================================
# Reconstruction of "as-of-checkpoint-week" inputs, purely from real historical data
# ============================================================================

def build_blank_slate_baselines(live_rosters_payload, byes=None):
    """
    Positional-only prior for every rostered player -- no real signal at all, exactly what a
    genuinely uninformed model would assume before any real games exist this season. Reuses
    the EXACT same constants production already uses for replacement-level assumptions
    (BASE_STREAMER_MEANS from the sim engine, VOLATILITY_CONSTANTS/EPISTEMIC_ERROR_RATES from
    sync.py), imported directly rather than duplicated here.

    This is deliberately the ONLY place custom reconstruction logic lives -- everything after
    this (blending toward real realized performance) is delegated entirely to the simulation
    engine's own, unmodified _apply_bayesian_updates(), which runs automatically when
    FantasySimulationEngine() is instantiated.
    """
    baselines = {}
    for team, players in live_rosters_payload.items():
        for p in players:
            name = p["name"]
            pos = simmod.normalize_position(p["pos"])
            nfl_team = p.get("team", "FA")
            mean = simmod.BASE_STREAMER_MEANS.get(pos, 8.0)
            k_val = sync.VOLATILITY_CONSTANTS.get(pos, 1.5)
            err = sync.EPISTEMIC_ERROR_RATES.get(pos, 0.18)
            baselines[name] = {
                "pos": pos,
                "mean": mean,
                "std_aleatoric": round(k_val * math.sqrt(max(0.5, mean)), 2),
                "std_epistemic": round(err * mean, 2),
                # From the season's real pairings when supplied (fetch_nfl_pairings ->
                # derive_bye_weeks), else 0 as in v1.
                "bye": (byes or {}).get(nfl_team, 0),
                "team": nfl_team,
            }
    return baselines


def build_realized_weekly_actuals(season_matchups, players_db, roster_map, through_week):
    """
    Real historical team_results (h2h_win, median_win, points_scored) + player_scores for
    weeks 1..(through_week - 1), using the exact same production extraction logic
    (_extract_weekly_h2h_results, _extract_weekly_player_scores) rather than a separate
    reimplementation -- both were fixed as real, standalone bugs this session, and reusing
    them here means the backtester automatically benefits from the same correctness.
    """
    all_weeks_actuals = {}
    for wk in range(1, through_week):
        if wk not in season_matchups:
            continue
        matchups = season_matchups[wk]
        wk_scores = {roster_map.get(e["roster_id"]): float(e.get("points", 0.0)) for e in matchups}
        median_cut = float(np.median(list(wk_scores.values()))) if wk_scores else 0.0

        wk_h2h_results = sync._extract_weekly_h2h_results(matchups, roster_map)
        wk_player_scores = sync._extract_weekly_player_scores(matchups, players_db)

        t_res = {
            t: {
                "points_scored": s,
                "h2h_win": wk_h2h_results.get(t, 0.0),
                "median_win": 1 if s >= median_cut else 0,
                "remaining_faab": 100.0,  # v1 simplification -- FAAB history not reconstructed
            }
            for t, s in wk_scores.items()
        }
        all_weeks_actuals[f"week_{wk}"] = {
            "median_cutoff": median_cut, "team_results": t_res, "player_scores": wk_player_scores,
        }
    return all_weeks_actuals


def build_asof_standings(season_matchups, roster_map, through_week):
    """Real cumulative wins (h2h) and points banked through week (through_week - 1), computed
    directly from real historical matchup pairings -- this is the checkpoint's starting
    league_standings.json equivalent."""
    all_teams = set(roster_map.values())
    wins = {t: 0.0 for t in all_teams}
    points = {t: 0.0 for t in all_teams}

    for wk in range(1, through_week):
        if wk not in season_matchups:
            continue
        matchups = season_matchups[wk]
        h2h_results = sync._extract_weekly_h2h_results(matchups, roster_map)
        for entry in matchups:
            t = roster_map.get(entry["roster_id"])
            if t:
                points[t] += float(entry.get("points", 0.0))
        for t, result in h2h_results.items():
            wins[t] += result

    return {
        t: {"h2h_wins": int(wins[t]), "points_scored": round(points[t], 2), "remaining_faab": 100.0}
        for t in all_teams
    }


def build_full_season_league_schedule(season_matchups, roster_map, regular_season_weeks=REGULAR_SEASON_WEEKS):
    """
    Real matchup pairings for the FULL regular season (weeks 1..regular_season_weeks),
    matching generate_league_schedule()'s output format exactly (a list of weeks, each a list
    of [team1, team2] pairs). Includes weeks after any checkpoint -- see module docstring for
    why that's the real, known schedule and not look-ahead bias.
    """
    full_schedule = []
    for wk in range(1, regular_season_weeks + 1):
        week_matchups = []
        if wk in season_matchups:
            by_matchup = {}
            for entry in season_matchups[wk]:
                by_matchup.setdefault(entry.get("matchup_id"), []).append(entry)
            for pair in by_matchup.values():
                if len(pair) == 2:
                    t1, t2 = roster_map.get(pair[0]["roster_id"]), roster_map.get(pair[1]["roster_id"])
                    if t1 and t2:
                        week_matchups.append([t1, t2])
        full_schedule.append(week_matchups)
    return full_schedule


def fetch_nfl_pairings(season_year):
    """Real NFL pairings for a past season from ESPN's public scoreboard (the same source and
    parsing generate_nfl_schedule uses for the live season, with `dates=<year>`). Returns
    ({week_str: {team: opponent}}, failed_weeks). A week that cannot be fetched is empty and
    recorded, exactly as in production, so derive_bye_weeks can exclude it."""
    pairings, failed = {}, []
    for wk in range(1, 19):
        pairings[str(wk)] = {}
        url = (f"http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
               f"?week={wk}&seasontype=2&dates={season_year}")
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            events = resp.json().get("events", [])
        except Exception as e:  # noqa: BLE001 -- recorded, not swallowed
            failed.append(wk)
            logging.warning("BACKTEST: %s NFL pairings for week %d unavailable (%s: %s); no byes derivable for it.",
                            season_year, wk, type(e).__name__, e)
            continue
        for event in events:
            try:
                comp = event["competitions"][0]["competitors"]
                t1, t2 = comp[0]["team"]["abbreviation"], comp[1]["team"]["abbreviation"]
            except (KeyError, IndexError, TypeError):
                continue
            t1 = "WAS" if t1 == "WSH" else t1
            t2 = "WAS" if t2 == "WSH" else t2
            pairings[str(wk)][t1] = t2
            pairings[str(wk)][t2] = t1
    return pairings, failed


def build_flat_nfl_environment_files(power_rating_value=21.5, pairings=None, failed_weeks=()):
    """v1 simplification (see module docstring): no verified historical Vegas data, so every
    real NFL team gets an identical, neutral scoring environment -- the Vegas/schedule-
    informed layer contributes nothing distinguishing here, intentionally.

    Since the bye work, real PAIRINGS may be supplied (fetch_nfl_pairings). They add exactly one
    piece of information -- who is absent which week -- and nothing else: with every power and
    defensive rating flat, a real opponent's implied total is (21.5 + 21.5) / 2 = 21.5 and the
    spread 0, identical to the 'FA' fallback. The schedule's _meta.byes is derived the same way
    production's is, so the backtest can represent absences at all (the reason Phase 2's
    findings 4 and 5 were reverted). Without pairings the schedule is empty, as before.
    Returns (power_ratings, defensive_ratings, defensive_tiers, nfl_schedule)."""
    power_ratings = {team: {"off_rating": power_rating_value} for team in sync.NFL_TEAM_ABBREVIATIONS.values()}
    defensive_ratings = {team: {"points_allowed_estimate": power_rating_value, "games_sampled": 0}
                          for team in sync.NFL_TEAM_ABBREVIATIONS.values()}
    defensive_tiers = {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []}
    if pairings:
        nfl_schedule = {str(wk): dict(pairings.get(str(wk), {})) for wk in range(1, 19)}
        nfl_schedule["_meta"] = {"failed_weeks": list(failed_weeks),
                                 "byes": derive_bye_weeks(nfl_schedule, failed_weeks)}
    else:
        nfl_schedule = {str(wk): {} for wk in range(1, 19)}  # every team resolves to 'FA' -> neutral fallback
    return power_ratings, defensive_ratings, defensive_tiers, nfl_schedule


# ============================================================================
# Orchestration: run one checkpoint end-to-end against the REAL, unmodified simulation engine
# ============================================================================

def run_backtest_checkpoint(checkpoint_week, season_league_id=BACKTEST_SEASON_LEAGUE_ID,
                             num_batches=1, sims_per_batch=2000, keep_workdir=False,
                             median_scoring_enabled=False, season_year="2025"):
    """
    Runs one backtest checkpoint end-to-end:
      1. Fetch real historical data for the season (matchups, rosters, final standings).
      2. Reconstruct "as-of-checkpoint_week" inputs using ONLY data from weeks before it.
      3. Write those inputs to an ISOLATED working directory (never touches real production
         files in the current directory -- see the chdir block below).
      4. Instantiate the REAL, unmodified FantasySimulationEngine (which runs
         _apply_bayesian_updates automatically) and call run_simulation().
      5. Read back the simulation's own output and score it against the REAL final outcome
         for that season (CRPS on win totals, playoff-appearance correctness).

    median_scoring_enabled defaults to False: this league's real 2025 season used plain
    head-to-head scoring only (confirmed directly by the user), not the hybrid H2H +
    weekly-median-beat format the current league uses. Comparing a simulation that awards 2
    decisions/week against a real season that only ever awarded 1/week produced simulated win
    totals roughly double the real ones in the first backtest run -- a real, confirmed scale
    mismatch, not a modeling error. See SIM_CONFIG['MEDIAN_SCORING_ENABLED']'s docstring.

    KNOWN, UNRESOLVED LIMITATION (read before trusting these results): the 2025 season used a
    single team-DEFENSE roster slot, not the individual IDP (DB/DL/LB) slots the current
    engine's REQUIRED_STARTING_SLOTS expects. normalize_position() has no 'DEF' case, so any
    team-defense entity on a real 2025 roster silently falls through to 'FLEX' -- meaning it's
    never recognized as DB/DL/LB-eligible, and literally every team's 3 IDP slots get filled by
    generic streamer injections, every single week, for this entire season (confirmed directly
    in the first backtest run's console output -- 100% streamer coverage on DB/DL/LB with zero
    exceptions). This was NOT fixed in this iteration: building a genuine alternate DEF-slot
    roster configuration is a real, separate undertaking, and grafting real 2025 DEF-era
    results onto the current IDP-era engine's mechanics risks testing something that never
    actually happened rather than validating anything real. Practical consequence: roughly
    3 of 13 starting slots' worth of scoring (offense-dominant, so somewhat less than 3/13 of
    total points) is generic positional noise rather than real historical performance, for
    every team, every week, in this specific season's backtest. IDP-specific mechanics
    (streaming, roster construction, optimal assignment for those slots) are NOT validated by
    this season at all -- only QB/RB/WR/TE mechanics are meaningfully tested here. A real
    IDP-era historical season (i.e. this current 2026 season, once it's complete) would be
    needed to validate that layer.
    """
    players_db = sync.update_player_cache()
    season_matchups = fetch_season_matchups(season_league_id)
    roster_map, live_rosters_payload, final_standings_payload = fetch_league_roster_data(season_league_id, players_db)
    real_playoff_roster_ids = fetch_real_playoff_teams(season_league_id)

    if checkpoint_week not in season_matchups and checkpoint_week - 1 not in season_matchups:
        print(f"[SKIP] No real matchup data available for week {checkpoint_week - 1} to build from.")
        return None

    # Real pairings for the season: byes only (totals stay flat -- see
    # build_flat_nfl_environment_files). A failed fetch leaves the schedule empty, as in v1.
    pairings, failed_weeks = fetch_nfl_pairings(season_year)
    power_ratings, defensive_ratings, defensive_tiers, nfl_schedule = build_flat_nfl_environment_files(
        pairings=pairings, failed_weeks=failed_weeks)
    byes = nfl_schedule.get("_meta", {}).get("byes", {})
    baselines = build_blank_slate_baselines(live_rosters_payload, byes=byes)
    weekly_actuals = build_realized_weekly_actuals(season_matchups, players_db, roster_map, checkpoint_week)
    standings = build_asof_standings(season_matchups, roster_map, checkpoint_week)
    league_schedule = build_full_season_league_schedule(season_matchups, roster_map)

    original_cwd = os.getcwd()
    os.makedirs(BACKTEST_WORKDIR, exist_ok=True)
    try:
        os.chdir(BACKTEST_WORKDIR)
        # storage.save_json resolves paths under DATA_DIR ("data/") relative to the current
        # working directory -- since we've already chdir'd into BACKTEST_WORKDIR, this
        # correctly creates and writes to BACKTEST_WORKDIR/data/, keeping the exact same
        # isolation from real production files as before, just with the same path layout the
        # real pipeline uses instead of bare filenames.
        storage.save_json(storage.LEAGUE_STATE_FILE, {"current_week": checkpoint_week})
        storage.save_json(storage.LEAGUE_STANDINGS_FILE, standings)
        storage.save_json(storage.VEGAS_FILE, {})  # only the current week's entries matter here; none
                                                     # needed at a historical checkpoint since we hold
                                                     # Vegas data flat (see module docstring)
        storage.save_json(storage.LIVE_ROSTERS_FILE, live_rosters_payload)
        storage.save_json(storage.BASELINES_FILE, baselines)
        storage.save_json(storage.TEAM_RATINGS_FILE, power_ratings)
        storage.save_json(storage.DEFENSIVE_RATINGS_FILE, defensive_ratings)
        storage.save_json(storage.DEFENSIVE_TIERS_FILE, defensive_tiers)
        storage.save_json(storage.LEAGUE_SCHEDULE_FILE, league_schedule)
        storage.save_json(storage.NFL_SCHEDULE_FILE, nfl_schedule)
        storage.save_json(storage.WEEKLY_ACTUALS_FILE, weekly_actuals)

        original_batches, original_sims = simmod.SIM_CONFIG['NUM_BATCHES'], simmod.SIM_CONFIG['SIMS_PER_BATCH']
        original_median_flag = simmod.SIM_CONFIG['MEDIAN_SCORING_ENABLED']
        simmod.SIM_CONFIG['NUM_BATCHES'] = num_batches
        simmod.SIM_CONFIG['SIMS_PER_BATCH'] = sims_per_batch
        simmod.SIM_CONFIG['MEDIAN_SCORING_ENABLED'] = median_scoring_enabled
        try:
            with patch('matplotlib.pyplot.savefig'):
                sim = simmod.FantasySimulationEngine()
                captured = {}
                original_export = sim.export_and_visualize

                def capturing_export(*args, **kwargs):
                    captured['wins'] = args[0]
                    captured['b_playoffs'] = args[2]
                    return original_export(*args, **kwargs)

                sim.export_and_visualize = capturing_export
                sim.run_simulation()
        finally:
            simmod.SIM_CONFIG['NUM_BATCHES'], simmod.SIM_CONFIG['SIMS_PER_BATCH'] = original_batches, original_sims
            simmod.SIM_CONFIG['MEDIAN_SCORING_ENABLED'] = original_median_flag
    finally:
        os.chdir(original_cwd)
        if not keep_workdir:
            shutil.rmtree(BACKTEST_WORKDIR, ignore_errors=True)

    if 'wins' not in captured:
        print("[SKIP] Simulation did not produce output for this checkpoint.")
        return None

    return score_checkpoint_result(captured, final_standings_payload, roster_map, real_playoff_roster_ids)


def score_checkpoint_result(captured, final_standings_payload, roster_map, real_playoff_roster_ids):
    """
    Scores one checkpoint's simulated forecast against the REAL final outcome for that season.
      - CRPS between each team's simulated final-win-total distribution and their real final
        win total (h2h wins + median wins, matching the simulation's own decision-counting
        convention).
      - Whether the team the model gave the highest/lowest playoff odds actually did make/miss
        the real playoffs (a lightweight sanity signal -- see module docstring for why a full
        calibration curve needs more than one season/checkpoint's worth of teams to be
        meaningful, and is deferred to a later iteration once more data exists).
    """
    wins_by_team = captured['wins']  # {team: np.array of shape (total_sims,)}
    playoff_pct_by_team = {t: float(np.mean(np.asarray(v))) for t, v in captured['b_playoffs'].items()} \
        if isinstance(captured['b_playoffs'], dict) else {}

    name_to_roster_id = {v: k for k, v in roster_map.items()}
    results = {}
    for team, sim_samples in wins_by_team.items():
        real = final_standings_payload.get(team, {})
        real_h2h_wins = real.get("wins", 0)
        # With MEDIAN_SCORING_ENABLED=False (the harness's default for this season), the
        # simulation awards exactly one decision per team per week, same as the real 2025
        # season -- so sim_samples and real_h2h_wins are now on the same scale and directly
        # comparable. This was NOT true before that flag existed; see
        # run_backtest_checkpoint's docstring for the ~2x scale mismatch that produced.
        crps = compute_crps(sim_samples, real_h2h_wins)

        rid = name_to_roster_id.get(team)
        made_real_playoffs = (rid in real_playoff_roster_ids) if real_playoff_roster_ids and rid is not None else None

        results[team] = {
            "real_h2h_wins": real_h2h_wins,
            "sim_expected_wins": round(float(np.mean(sim_samples)), 2),
            "crps": round(crps, 3),
            "made_real_playoffs": made_real_playoffs,
            "sim_playoff_pct": round(playoff_pct_by_team.get(team, float('nan')) * 100, 1) if playoff_pct_by_team else None,
        }
    return results


def run_full_backtest(checkpoint_weeks=DEFAULT_CHECKPOINT_WEEKS, season_league_id=BACKTEST_SEASON_LEAGUE_ID,
                       num_batches=1, sims_per_batch=2000, median_scoring_enabled=False):
    """Runs every checkpoint, prints a per-checkpoint, per-team report, and a season-level
    summary (mean CRPS across all team/checkpoint instances). median_scoring_enabled defaults
    to False -- see run_backtest_checkpoint's docstring for why that matches this season's
    real scoring format."""
    all_crps = []
    for wk in checkpoint_weeks:
        print(f"\n{'=' * 70}\nCHECKPOINT: week {wk}\n{'=' * 70}")
        result = run_backtest_checkpoint(wk, season_league_id=season_league_id,
                                          num_batches=num_batches, sims_per_batch=sims_per_batch,
                                          median_scoring_enabled=median_scoring_enabled)
        if not result:
            continue
        for team, r in sorted(result.items(), key=lambda x: x[1]['crps']):
            print(f"  {team:20s} real_h2h_wins={r['real_h2h_wins']:>2} sim_expected={r['sim_expected_wins']:>5} "
                  f"CRPS={r['crps']:>6} made_playoffs={r['made_real_playoffs']} sim_playoff_pct={r['sim_playoff_pct']}")
            all_crps.append(r['crps'])

    if all_crps:
        print(f"\n{'=' * 70}\nSUMMARY across {len(all_crps)} (team, checkpoint) instances")
        print(f"Mean CRPS: {round(float(np.mean(all_crps)), 3)}")
        print(f"Median CRPS: {round(float(np.median(all_crps)), 3)}")
        print(f"{'=' * 70}")

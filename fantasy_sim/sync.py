"""
fantasy_sim.sync

The data ingestion pipeline: pulls real data from Sleeper, ESPN, the-odds-api, and
Open-Meteo, and writes everything the simulation engine needs to run into DATA_DIR. This is
the "gather reality" half of the project; fantasy_sim.simulation is the "project reality
forward" half.

Run via `python -m fantasy_sim.sync` (see scripts/run_sync.py) or import sync_all() directly.
"""
import math
import os
from datetime import datetime

import numpy as np
import requests

from fantasy_sim.config import (
    BASE_URL, LEAGUE_ID, TEAM_NAME_MAP, ODDS_API_KEY, LEAGUE_AVG_PPG, DEF_RATING_SHRINKAGE_N0,
    PRESEASON_DEFENSIVE_PRIOR, NFL_TEAM_ABBREVIATIONS, OUTDOOR_STADIUMS, WEEK_1_VERIFIED_VEGAS,
    DEFAULT_FALLBACK_TOTALS, VOLATILITY_CONSTANTS, EPISTEMIC_ERROR_RATES,
)
from fantasy_sim.storage import (
    PLAYER_CACHE_FILE, VEGAS_FILE, BASELINES_FILE, TEAM_RATINGS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_STATE_FILE,
    LIVE_ROSTERS_FILE, LEAGUE_STANDINGS_FILE, WEEKLY_ACTUALS_FILE, load_json, save_json,
)
from fantasy_sim.clients.sleeper import update_player_cache
from fantasy_sim.clients.espn import fetch_espn_projections, normalize_player_name_for_matching as _normalize_player_name_for_matching


def generate_nfl_schedule(current_nfl_week=1):
    """
    Fetches the official NFL schedule from ESPN's public scoreboard API (free, no key) for all
    18 weeks. While already making this pass, also captures each COMPLETED game's final score
    for weeks strictly before current_nfl_week -- this is the same free data source powering
    generate_defensive_ratings() below, so no second API or paid data source is needed.

    Returns completed_results: a list of (team_abbr, points_allowed) tuples, one entry per team
    per completed real game, used to build an empirical defensive-strength estimate.
    """
    print("[INIT] Fetching official NFL schedule and completed results for defensive model...")
    nfl_schedule = {}
    completed_results = []

    for wk in range(1, 19):
        nfl_schedule[str(wk)] = {}
        url = f"http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?week={wk}&seasontype=2"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                events = resp.json().get('events', [])
                for event in events:
                    competition = event['competitions'][0]
                    competitors = competition['competitors']
                    t1_info, t2_info = competitors[0], competitors[1]
                    t1 = t1_info['team']['abbreviation']
                    t2 = t2_info['team']['abbreviation']
                    if t1 == 'WSH': t1 = 'WAS'
                    if t2 == 'WSH': t2 = 'WAS'
                    nfl_schedule[str(wk)][t1] = t2
                    nfl_schedule[str(wk)][t2] = t1

                    # Only trust a score for weeks that have already happened, and only once
                    # ESPN marks the game as actually completed (in-progress games also carry a
                    # score field, which we must not treat as final).
                    if wk < current_nfl_week:
                        is_final = competition.get('status', {}).get('type', {}).get('completed', False)
                        if is_final:
                            try:
                                t1_score = float(t1_info.get('score', 0))
                                t2_score = float(t2_info.get('score', 0))
                                # Points ALLOWED by t1 == points SCORED by t2, and vice versa.
                                completed_results.append((t1, t2_score))
                                completed_results.append((t2, t1_score))
                            except (TypeError, ValueError):
                                pass
        except Exception:
            pass

    if not nfl_schedule.get("1"):
        nfl_schedule["1"] = {team: data["opponent"] for team, data in WEEK_1_VERIFIED_VEGAS.items() if team != "FA"}

    save_json(NFL_SCHEDULE_FILE, nfl_schedule)

    return completed_results


def generate_defensive_ratings(completed_results):
    """
    Builds a defensive strength estimate per NFL team, blending a preseason prior with real,
    empirically-derived data from actual completed-game final scores (free, sourced from
    generate_nfl_schedule() above) as the season progresses. This replaces the old approach,
    which derived "def_rating" as (43.0 - off_rating) -- a pure algebraic mirror of a team's OWN
    offense that carried zero real defensive information (see the KNOWN LIMITATION note
    previously attached to generate_nfl_power_ratings).

    The prior comes from PRESEASON_DEFENSIVE_PRIOR (see that dict's docstring for how to fill
    it in from any free public source) if a team is listed there, otherwise the honest,
    uninformed LEAGUE_AVG_PPG fallback. Uses the same empirical-Bayes shrinkage pattern as the
    player-baseline model in the simulation engine (n_0 = 4.0 "games" of trust in the prior), so
    a defense that looked strong on paper but is actually getting torched will correctly drift
    toward the empirical reality after a handful of real games -- the preseason take is a
    starting point, never a permanent label.

    Also derives the top-5 / bottom-5 defensive tiers that replace the previously static,
    hand-typed SIM_CONFIG['DEFENSIVE_RANKS'] team lists in the simulation engine. NOTE: this is
    a single overall defensive-strength signal (points allowed), not separately split by pass
    vs. rush -- the old hardcoded lists were never actually built from a real pass/rush-split
    data source either, so this trades an illusory distinction for a real, if coarser, one.
    """
    per_team_allowed = {}
    for team, pts_allowed in completed_results:
        per_team_allowed.setdefault(team, []).append(pts_allowed)

    ratings = {}
    for team in NFL_TEAM_ABBREVIATIONS.values():
        samples = per_team_allowed.get(team, [])
        n = len(samples)
        prior = PRESEASON_DEFENSIVE_PRIOR.get(team, LEAGUE_AVG_PPG)
        if n == 0:
            estimate = prior
        else:
            empirical_avg = sum(samples) / n
            estimate = ((DEF_RATING_SHRINKAGE_N0 * prior) + (n * empirical_avg)) / (DEF_RATING_SHRINKAGE_N0 + n)
        ratings[team] = {"points_allowed_estimate": round(estimate, 2), "games_sampled": n}

    save_json(DEFENSIVE_RATINGS_FILE, ratings)

    # Derive top-5/bottom-5 defensive tiers from whatever real signal currently exists -- either
    # the preseason prior (if PRESEASON_DEFENSIVE_PRIOR is filled in) or real empirical data (once
    # games are played), or both blended together. If NEITHER exists yet, every team's estimate
    # is identically LEAGUE_AVG_PPG (zero variance) and ranking them would be arbitrary
    # tie-breaking, not a real signal -- in that specific case only, tiers stay honestly empty.
    all_estimates = [r["points_allowed_estimate"] for r in ratings.values()]
    has_real_variance = (max(all_estimates) - min(all_estimates)) > 0.05
    tiers = {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []}
    if has_real_variance:
        sorted_teams = sorted(ratings.items(), key=lambda x: x[1]["points_allowed_estimate"])
        tiers["TOP_DEFENSE"] = [t for t, _ in sorted_teams[:5]]
        tiers["BOTTOM_DEFENSE"] = [t for t, _ in sorted_teams[-5:]]
    save_json(DEFENSIVE_TIERS_FILE, tiers)

    return ratings, tiers

def generate_nfl_power_ratings(live_totals):
    """
    Computes each team's OFFENSIVE power rating from real, market-implied Vegas data (their own
    implied point total, adjusted for spread). This remains a legitimate signal -- Vegas totals
    reflect real market information about expected scoring.

    def_rating is INTENTIONALLY no longer computed here. It used to be derived as
    (43.0 - off_rating), a pure algebraic mirror of a team's own offense that carried zero real
    defensive information (a team with a great offense always got a "bad defense" score
    regardless of actual points allowed). Real defensive strength now comes from
    generate_defensive_ratings(), which is built from actual completed-game results -- see
    that function's docstring for the full explanation.
    """
    ratings = {}
    for team, data in live_totals.items():
        if team == "FA": continue
        tot = data.get("total", 21.5)
        spr = data.get("spread", 0.0)
        off_rating = tot + (spr * -0.5)
        ratings[team] = {"off_rating": round(float(off_rating), 2)}
    save_json(TEAM_RATINGS_FILE, ratings)

def fetch_vegas_implied_totals(current_nfl_week, sharp_polling=False):
    if datetime.now() < datetime(2026, 9, 9):
        save_json(VEGAS_FILE, WEEK_1_VERIFIED_VEGAS)
        generate_nfl_power_ratings(WEEK_1_VERIFIED_VEGAS)
        return WEEK_1_VERIFIED_VEGAS

    if not ODDS_API_KEY:
        generate_nfl_power_ratings(DEFAULT_FALLBACK_TOTALS)
        return DEFAULT_FALLBACK_TOTALS

    url = f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/?apiKey={ODDS_API_KEY}&regions=us&markets=spreads,totals&bookmakers=draftkings"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        games = response.json()
    except Exception as e:
        generate_nfl_power_ratings(DEFAULT_FALLBACK_TOTALS)
        return DEFAULT_FALLBACK_TOTALS

    implied_totals = {"FA": {"total": 20.0, "spread": 0.0, "wind_mph": 0.0, "precip_prob": 0.0, "opponent": "FA"}}
    for game in games:
        home_team = NFL_TEAM_ABBREVIATIONS.get(game.get("home_team"))
        away_team = NFL_TEAM_ABBREVIATIONS.get(game.get("away_team"))
        if not home_team or not away_team: continue

        bookmakers = game.get("bookmakers", [])
        if not bookmakers: continue

        markets = bookmakers[0].get("markets", [])
        spread_market = next((m for m in markets if m["key"] == "spreads"), None)
        total_market = next((m for m in markets if m["key"] == "totals"), None)
        if not spread_market or not total_market: continue

        over_under = total_market["outcomes"][0].get("point", 43.0)
        home_spread = 0.0
        for outcome in spread_market["outcomes"]:
            if outcome["name"] == game["home_team"]:
                home_spread = outcome.get("point", 0.0)
                break

        wind_mph = 0.0
        precip_prob = 0.0
        commence_time = game.get("commence_time", "")
        date_str = commence_time.split("T")[0] if commence_time else ""
        
        if home_team in OUTDOOR_STADIUMS and date_str:
            lat, lon = OUTDOOR_STADIUMS[home_team]
            try:
                wx_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=wind_speed_10m_max,precipitation_probability_max&timezone=America%2FNew_York&start_date={date_str}&end_date={date_str}"
                wx_resp = requests.get(wx_url, timeout=3)
                if wx_resp.status_code == 200:
                    wind_kmh = wx_resp.json().get("daily", {}).get("wind_speed_10m_max", [0])[0]
                    if wind_kmh: wind_mph = round(wind_kmh * 0.621371, 2)
                    precip_api = wx_resp.json().get("daily", {}).get("precipitation_probability_max", [0])[0]
                    if precip_api: precip_prob = float(precip_api)
            except Exception:
                pass

        implied_totals[home_team] = {"total": round((over_under / 2.0) - (home_spread / 2.0), 2), "spread": home_spread, "wind_mph": wind_mph, "precip_prob": precip_prob, "opponent": away_team}
        implied_totals[away_team] = {"total": round((over_under / 2.0) + (home_spread / 2.0), 2), "spread": -home_spread, "wind_mph": wind_mph, "precip_prob": precip_prob, "opponent": home_team}

    for team in NFL_TEAM_ABBREVIATIONS.values():
        if team not in implied_totals: implied_totals[team] = {"total": 21.5, "spread": 0.0, "wind_mph": 0.0, "precip_prob": 0.0, "opponent": "FA"}

    save_json(VEGAS_FILE, implied_totals)
    generate_nfl_power_ratings(implied_totals)
    return implied_totals

def generate_player_baselines(league_scoring_settings, players_db, live_rosters, current_year="2026", week=1):
    existing_baselines = {}
    if os.path.exists(BASELINES_FILE):
        try:
            existing_baselines = load_json(BASELINES_FILE)
        except Exception: pass

    projections = {}
    url_weekly = f"{BASE_URL}/projections/nfl/regular/{current_year}/{week}"
    try:
        r = requests.get(url_weekly, timeout=8)
        if r.status_code == 200 and r.json(): projections = r.json()
    except Exception: pass

    fallback_season = False
    if not projections:
        url_season = f"{BASE_URL}/projections/nfl/regular/{current_year}"
        try:
            r = requests.get(url_season, timeout=8)
            if r.status_code == 200 and r.json():
                projections = r.json()
                fallback_season = True
        except Exception: pass

    # Second, independent projection source (free, see fetch_espn_projections docstring). A
    # failure here must never break baseline generation -- espn_projections simply stays {}
    # and every player below falls back to Sleeper-only, exactly as before this change.
    espn_projections = {}
    try:
        espn_projections = fetch_espn_projections(current_year, week)
    except Exception:
        espn_projections = {}

    baselines = {}
    for pid, proj_data in projections.items():
        player = players_db.get(str(pid))
        if not player: continue

        name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        raw_pos = player.get("position", "FLEX")
        team = player.get("team", "FA")
        stats_dict = proj_data.get("stats", proj_data)

        games_played = stats_dict.get("gp", 16.0) if fallback_season else 1.0
        if games_played <= 0: games_played = 16.0

        total_pts = sum(stats_dict.get(k, 0.0) * mult for k, mult in league_scoring_settings.items())
        if total_pts <= 0.0: total_pts = stats_dict.get("pts_half_ppr", stats_dict.get("pts_ppr", stats_dict.get("pts_std", 0.0)))

        sleeper_weekly_mean = round(total_pts / games_played, 2)
        if sleeper_weekly_mean <= 0.0: continue

        # Multi-source blend: if ESPN has an independent projection for this player this week,
        # average the two sources instead of trusting Sleeper alone, and use how much the two
        # sources DISAGREE as a real, data-driven signal for how uncertain we should be --
        # two independent estimates disagreeing is genuine evidence of uncertainty, not just a
        # hand-picked positional error rate.
        espn_key = _normalize_player_name_for_matching(name)
        espn_weekly_mean = espn_projections.get(espn_key)
        source_disagreement = None
        if espn_weekly_mean is not None and espn_weekly_mean > 0:
            fresh_mean = round((sleeper_weekly_mean + espn_weekly_mean) / 2.0, 2)
            source_disagreement = abs(sleeper_weekly_mean - espn_weekly_mean)
        else:
            fresh_mean = sleeper_weekly_mean

        if name in existing_baselines:
            posterior_mean = existing_baselines[name].get("mean", fresh_mean)
            final_mean = round((fresh_mean * 0.6) + (posterior_mean * 0.4), 2)
        else:
            final_mean = fresh_mean

        k_val = VOLATILITY_CONSTANTS.get(raw_pos, 1.5)
        error_margin = EPISTEMIC_ERROR_RATES.get(raw_pos, 0.18)

        std_aleatoric = round(k_val * math.sqrt(max(0.5, final_mean)), 2)
        std_epistemic_floor = error_margin * final_mean
        if source_disagreement is not None:
            # Half the absolute disagreement between two independent estimators is a standard,
            # data-driven lower bound on how uncertain we should be about the true value -- take
            # whichever is larger: the hand-set positional floor, or what the sources themselves
            # are telling us via how much they disagree.
            std_epistemic = round(max(std_epistemic_floor, source_disagreement / 2.0), 2)
        else:
            std_epistemic = round(std_epistemic_floor, 2)

        baselines[name] = {
            "pos": raw_pos, "mean": final_mean,
            "std_aleatoric": std_aleatoric, "std_epistemic": std_epistemic,
            "bye": player.get("team_bye", 0), "team": team,
        }

    save_json(BASELINES_FILE, baselines)
    return baselines


def generate_league_schedule(roster_map, regular_season_weeks=14):
    full_schedule = []
    for wk in range(1, regular_season_weeks + 1):
        resp = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/matchups/{wk}")
        if resp.status_code != 200 or not resp.json(): continue
        matchups_data = resp.json()
        matchup_dict = {}
        for entry in matchups_data:
            m_id = entry.get("matchup_id")
            t_name = roster_map.get(entry["roster_id"], f"Roster_{entry['roster_id']}")
            matchup_dict.setdefault(m_id, []).append(t_name)
        week_matchups = [tuple(pair) for pair in matchup_dict.values() if len(pair) == 2]
        full_schedule.append(week_matchups)

    save_json(LEAGUE_SCHEDULE_FILE, full_schedule)

def _extract_weekly_h2h_results(wk_matchups, roster_map):
    """
    Computes each team's real head-to-head win/loss for one week from Sleeper's matchup data,
    by grouping entries by matchup_id and comparing the paired scores. Returns
    {team_name: 1.0 (win), 0.5 (tie), or 0.0 (loss)}.

    This was previously hardcoded to 0 for every team, every week (see the h2h_win field in
    sync_all's weekly_actuals construction) -- meaning _apply_bayesian_updates' accumulation of
    self.actual_h2h_wins in the simulation engine has always summed to 0 regardless of real
    results. Since actual_wins_banked = actual_h2h_wins + actual_median_wins, and a normal week
    awards one decision of each kind, this understated every team's real banked progress by
    roughly half in every past production run -- and would have understated it identically for
    the current season the moment real games started, had it not been caught here.
    """
    by_matchup = {}
    for entry in wk_matchups:
        by_matchup.setdefault(entry.get("matchup_id"), []).append(entry)

    h2h_results = {}
    for pair in by_matchup.values():
        if len(pair) != 2:
            continue  # a bye or malformed pairing; no h2h decision to award
        t1, t2 = roster_map.get(pair[0]["roster_id"]), roster_map.get(pair[1]["roster_id"])
        s1, s2 = float(pair[0].get("points", 0.0)), float(pair[1].get("points", 0.0))
        if s1 > s2:
            if t1: h2h_results[t1] = 1.0
            if t2: h2h_results[t2] = 0.0
        elif s2 > s1:
            if t1: h2h_results[t1] = 0.0
            if t2: h2h_results[t2] = 1.0
        else:
            if t1: h2h_results[t1] = 0.5
            if t2: h2h_results[t2] = 0.5
    return h2h_results

def _extract_weekly_player_scores(wk_matchups, players_db):
    """
    Extracts real per-player weekly actual fantasy scores from a Sleeper matchups response,
    keyed by full player name (matching self.baselines' keying convention in the simulation
    engine). Sleeper's matchup entries already include a "players_points" field (player_id ->
    points scored that week) alongside the team-total "points" field that was already being
    used -- this was simply never extracted before, which meant player_scores in
    weekly_actuals.json was always {}, and _apply_bayesian_updates' player-level posterior
    refinement (in the simulation engine) has never had real data to update against in
    production, silently, since it was written.
    """
    wk_player_scores = {}
    for entry in wk_matchups:
        for pid, pts in entry.get("players_points", {}).items():
            player = players_db.get(str(pid))
            if not player:
                continue
            name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            if name and pts is not None:
                wk_player_scores[name] = float(pts)
    return wk_player_scores

def _build_roster_player_entry(pid, players_db):
    """
    Builds one player's live_rosters.json entry from Sleeper's player database. Handles a real
    bug found via a live backtest run: Sleeper's real player records commonly have "team":
    null (JSON null -> Python None) for anyone not currently on an active NFL roster (a free
    agent, recently released, retired, etc.) -- and `.get("team", "FA")` does NOT catch this,
    since .get()'s default only applies when the KEY IS MISSING, not when it's present with an
    explicit None value. That None then silently propagated all the way into the simulation
    engine's per-player scoring loop, where it broke a sorted() comparison
    (TypeError: '<' not supported between instances of 'str' and 'NoneType') the first time a
    real rostered player actually had this field. `or "FA"` correctly falls back for both the
    missing-key and explicit-None cases.
    """
    player = players_db.get(str(pid), {})
    return {
        "name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
        "pos": player.get("position", "FLEX"),
        "team": player.get("team") or "FA",
    }

def sync_all(sharp_polling=False):
    players_db = update_player_cache()
    league_info = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}").json()
    scoring_settings = league_info.get("scoring_settings", {})
    state = requests.get(f"{BASE_URL}/state/nfl").json()

    season_type = state.get("season_type", "regular")
    current_nfl_week = 1 if season_type == "pre" else state.get("week", 1)
    save_json(LEAGUE_STATE_FILE, {"current_week": current_nfl_week})

    users = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/users").json()
    rosters = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/rosters").json()
    user_map = {u["user_id"]: u.get("display_name", "") for u in users}
    roster_map = {r["roster_id"]: TEAM_NAME_MAP.get(user_map.get(r.get("owner_id"), ""), "Unknown") for r in rosters}

    live_rosters_payload, standings_payload = {}, {}
    for r in rosters:
        sim_name = roster_map[r["roster_id"]]
        settings = r.get("settings", {})
        standings_payload[sim_name] = {
            "h2h_wins": int(settings.get("wins", 0)),
            "points_scored": float(f"{settings.get('fpts', 0)}.{settings.get('fpts_decimal', 0)}"),
            "remaining_faab": max(0.0, 100.0 - float(settings.get("waiver_budget_used", 0))),
        }
        live_rosters_payload[sim_name] = [
            _build_roster_player_entry(pid, players_db)
            for pid in r.get("players", []) if str(pid) in players_db
        ]

    save_json(LIVE_ROSTERS_FILE, live_rosters_payload)
    save_json(LEAGUE_STANDINGS_FILE, standings_payload)

    generate_league_schedule(roster_map)
    completed_results = generate_nfl_schedule(current_nfl_week)
    generate_defensive_ratings(completed_results)
    generate_player_baselines(scoring_settings, players_db, live_rosters_payload, str(state.get("season", "2026")), current_nfl_week)
    fetch_vegas_implied_totals(current_nfl_week, sharp_polling=sharp_polling)

    all_weeks_actuals = {}
    for wk in range(1, max(0, current_nfl_week - 1) + 1):
        m_resp = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/matchups/{wk}")
        if m_resp.status_code != 200 or not m_resp.json(): continue

        wk_matchups = m_resp.json()
        wk_scores = {roster_map.get(entry["roster_id"]): float(entry.get("points", 0.0)) for entry in wk_matchups}
        median_cut = np.median(list(wk_scores.values())) if wk_scores else 0

        # Real per-player weekly actual scores, keyed by full name to match self.baselines'
        # keying convention. This feeds _apply_bayesian_updates' player-level posterior
        # refinement in the simulation engine -- previously always empty (see
        # _extract_weekly_player_scores docstring), meaning that update loop has never
        # executed against real data in production.
        wk_player_scores = _extract_weekly_player_scores(wk_matchups, players_db)
        # Real head-to-head win/loss per team -- previously hardcoded to 0 for everyone, every
        # week (see _extract_weekly_h2h_results docstring for the consequence of that).
        wk_h2h_results = _extract_weekly_h2h_results(wk_matchups, roster_map)

        t_res = {t: {"points_scored": score, "h2h_win": wk_h2h_results.get(t, 0.0), "median_win": 1 if score >= median_cut else 0, "remaining_faab": standings_payload[t]["remaining_faab"]} for t, score in wk_scores.items()}
        all_weeks_actuals[f"week_{wk}"] = {"median_cutoff": median_cut, "team_results": t_res, "player_scores": wk_player_scores}

    save_json(WEEKLY_ACTUALS_FILE, all_weeks_actuals)

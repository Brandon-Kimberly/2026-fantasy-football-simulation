"""
fantasy_sim.sync

The data ingestion pipeline: pulls real data from Sleeper, ESPN, the-odds-api, and
Open-Meteo, and writes everything the simulation engine needs to run into DATA_DIR. This is
the "gather reality" half of the project; fantasy_sim.simulation is the "project reality
forward" half.

Run via `python -m fantasy_sim.sync` (see scripts/run_sync.py) or import sync_all() directly.
"""
import json
import logging
import math
import os
from datetime import datetime

import numpy as np
import requests

from fantasy_sim.config import (
    BASE_URL, LEAGUE_ID, TEAM_NAME_MAP, ODDS_API_KEY, LEAGUE_AVG_PPG, DEF_RATING_SHRINKAGE_N0,
    PRESEASON_DEFENSIVE_PRIOR, NFL_TEAM_ABBREVIATIONS, OUTDOOR_STADIUMS, WEEK_1_VERIFIED_VEGAS,
    DEFAULT_FALLBACK_TOTALS, VOLATILITY_CONSTANTS, EPISTEMIC_ERROR_RATES, normalize_position,
    derive_bye_weeks,
)
from fantasy_sim.storage import (
    VEGAS_FILE, BASELINES_FILE, TEAM_RATINGS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_STATE_FILE,
    LIVE_ROSTERS_FILE, LEAGUE_STANDINGS_FILE, WEEKLY_ACTUALS_FILE, load_json, save_json, PROJECTION_LOG_FILE, PLAYOFF_BRACKET_FILE,
    SYNC_MANIFEST_FILE, SYNC_OUTPUT_FILES, PLAYER_CACHE_FILE,
)
from fantasy_sim.clients.sleeper import update_player_cache
from fantasy_sim.clients.espn import fetch_espn_projections, normalize_player_name_for_matching as _normalize_player_name_for_matching


def generate_nfl_schedule(current_nfl_week=1):
    """
    Fetches the official NFL schedule from ESPN's public scoreboard API (free, no key) for all
    18 weeks. While already making this pass, also captures each COMPLETED game's final score
    for weeks strictly before current_nfl_week -- this is the same free data source powering
    generate_defensive_ratings() below, so no second API or paid data source is needed.

    A week whose fetch fails is recorded under nfl_schedule["_meta"]["failed_weeks"] and logged
    at WARNING. It used to be swallowed by a bare `except: pass`, which left that week `{}`:
    every team resolved to 'FA' (flat 21.5, no opponent, no defensive tier) and, because the
    completed scores are harvested in the same pass, every team silently lost a game from the
    defensive-rating sample. See AUDIT_PHASE_3_FINDINGS.md finding 2. The engine reads weeks
    with .get(str(week)), so the "_meta" key is invisible to it.

    Returns completed_results: a list of (team_abbr, points_allowed) tuples, one entry per team
    per completed real game, used to build an empirical defensive-strength estimate.
    """
    print("[INIT] Fetching official NFL schedule and completed results for defensive model...")
    nfl_schedule = {}
    completed_results = []
    failed_weeks = []

    for wk in range(1, 19):
        nfl_schedule[str(wk)] = {}
        url = f"http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?week={wk}&seasontype=2"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            events = resp.json().get('events', [])
        except Exception as e:
            failed_weeks.append(wk)
            logging.warning(
                "NFL SCHEDULE: week %d could not be fetched (%s: %s). That week has no opponents "
                "(every team gets the flat 21.5 / 'FA' environment)%s. Re-run the sync.",
                wk, type(e).__name__, e,
                " and its completed games are missing from the defensive sample" if wk < current_nfl_week else "")
            continue

        for event in events:
            try:
                competition = event['competitions'][0]
                competitors = competition['competitors']
                t1_info, t2_info = competitors[0], competitors[1]
                t1 = t1_info['team']['abbreviation']
                t2 = t2_info['team']['abbreviation']
            except (KeyError, IndexError, TypeError) as e:
                logging.warning("NFL SCHEDULE: week %d has a malformed event (%s); skipped.", wk, e)
                continue
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
                    except (TypeError, ValueError):
                        logging.warning(
                            "NFL SCHEDULE: week %d %s-%s is marked completed but has no numeric "
                            "score (%r / %r); dropped from the defensive sample.",
                            wk, t1, t2, t1_info.get('score'), t2_info.get('score'))
                        continue
                    # Points ALLOWED by t1 == points SCORED by t2, and vice versa.
                    completed_results.append((t1, t2_score))
                    completed_results.append((t2, t1_score))

    if not nfl_schedule.get("1"):
        nfl_schedule["1"] = {team: data["opponent"] for team, data in WEEK_1_VERIFIED_VEGAS.items() if team != "FA"}
        if 1 in failed_weeks:
            logging.warning("NFL SCHEDULE: week 1 populated from the verified preseason table.")

    # Bye weeks, derived from the pairings (config.derive_bye_weeks): the one usable week a
    # team appears in no game. Written into _meta so baselines, the roster and the engine's
    # whitelist imputation all read one value. Teams with no derivable bye are announced.
    byes = derive_bye_weeks(nfl_schedule, failed_weeks)
    missing_bye = [t for t in NFL_TEAM_ABBREVIATIONS.values() if t not in byes]
    if missing_bye:
        logging.warning("NFL SCHEDULE: no single bye week derivable for %d teams (absent from 0 or "
                        "several usable weeks; failed_weeks=%s): %s. Their players get bye 0 (never on bye).",
                        len(missing_bye), failed_weeks, ", ".join(missing_bye))
    nfl_schedule["_meta"] = {"failed_weeks": failed_weeks, "byes": byes}
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
    uninformed LEAGUE_AVG_PPG fallback. Shrinkage is a conjugate normal update with the prior's
    variance expressed as a pseudo-count of games, DEF_RATING_SHRINKAGE_N0 (derived from the
    real 2025 season's within- vs between-team variance -- see config.py). A defense that
    looked strong on paper but is actually getting torched drifts toward the empirical reality
    as games accumulate (weight on data n / (n + n_0)); the preseason take is a starting point,
    never a permanent label. This is deliberately NOT the same construct as the engine's
    player update, whose prior states its own variance.

    Also derives the top-5 / bottom-5 defensive tiers that replace the previously static,
    hand-typed SIM_CONFIG['DEFENSIVE_RANKS'] team lists in the simulation engine. NOTE: this is
    a single overall defensive-strength signal (points allowed), not separately split by pass
    vs. rush -- the old hardcoded lists were never actually built from a real pass/rush-split
    data source either, so this trades an illusory distinction for a real, if coarser, one.
    """
    per_team_allowed = {}
    for team, pts_allowed in completed_results:
        per_team_allowed.setdefault(team, []).append(pts_allowed)

    # A team missing from the prior table gets the TABLE's own mean, not LEAGUE_AVG_PPG: the
    # table averages ~22.8 (and real 2025 points allowed 23.0) against 21.5, so the old
    # fallback would have ranked a missing team as an above-average defence by construction
    # (Phase 3 finding 8). LEAGUE_AVG_PPG remains the fallback only when the table is empty.
    table_mean = (sum(PRESEASON_DEFENSIVE_PRIOR.values()) / len(PRESEASON_DEFENSIVE_PRIOR)
                  if PRESEASON_DEFENSIVE_PRIOR else LEAGUE_AVG_PPG)
    missing = [t for t in NFL_TEAM_ABBREVIATIONS.values() if t not in PRESEASON_DEFENSIVE_PRIOR]
    if missing and PRESEASON_DEFENSIVE_PRIOR:
        logging.warning("DEFENSIVE RATINGS: %d teams missing from PRESEASON_DEFENSIVE_PRIOR use the "
                        "table mean %.2f as their prior: %s", len(missing), table_mean, ", ".join(missing))

    ratings = {}
    for team in NFL_TEAM_ABBREVIATIONS.values():
        samples = per_team_allowed.get(team, [])
        n = len(samples)
        prior = PRESEASON_DEFENSIVE_PRIOR.get(team, table_mean)
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
        if team == "FA" or team == VEGAS_META_KEY or not isinstance(data, dict): continue
        tot = data.get("total", 21.5)
        spr = data.get("spread", 0.0)
        off_rating = tot + (spr * -0.5)
        ratings[team] = {"off_rating": round(float(off_rating), 2)}
    save_json(TEAM_RATINGS_FILE, ratings)

VEGAS_META_KEY = "_meta"


def _stamp_vegas(totals, week, source):
    """Returns a copy of a Vegas totals dict carrying a `_meta` record: the NFL week the lines
    are FOR, where they came from, and when. The engine reads `_meta.week` to refuse lines
    that were not produced for the week it is simulating (see
    FantasySimulationEngine._check_vegas_staleness). Consumers that iterate the dict must skip
    this key; the engine only ever does `.get(team)`."""
    stamped = dict(totals)
    stamped[VEGAS_META_KEY] = {
        "week": int(week),
        "source": source,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    return stamped


def _write_vegas(totals, week, source):
    """Every path out of fetch_vegas_implied_totals goes through here, so the file on disk is
    ALWAYS the data the engine will be handed for this week -- never a leftover from an earlier
    sync. Two of the three in-season fallback paths used to return without writing, which left
    the week-1 table on disk for the rest of the season; the engine then applied week-1 lines,
    week-1 opponents included, to every current week. See AUDIT_PHASE_3_FINDINGS.md finding 1."""
    stamped = _stamp_vegas(totals, week, source)
    save_json(VEGAS_FILE, stamped)
    generate_nfl_power_ratings(stamped)
    return stamped


def fetch_vegas_implied_totals(current_nfl_week, sharp_polling=False):
    """Market-implied team totals for `current_nfl_week`, stamped with the week they are for.

    THE FIX FOR CORRECT IN-SEASON OPPONENTS IS ODDS_API_KEY. Without it there is no market
    data after the preseason gate, and every team gets the flat 21.5 / no-opponent fallback:
    no matchup information, no defensive-tier adjustments, and a normaliser built from a flat
    schedule. The write-and-stamp discipline below makes that state VISIBLE (loud here, refused
    by the engine); it does not make it correct. See config.ODDS_API_KEY."""
    if datetime.now() < datetime(2026, 9, 9):
        # UNVERIFIED: 2026-09-09 is assumed to be the regular-season kickoff. If the real
        # kickoff is earlier, week-1 games would run on the verified table (fine); if later,
        # the API is polled during the preseason (harmless, returns no games -> loud fallback).
        return _write_vegas(WEEK_1_VERIFIED_VEGAS, current_nfl_week, "week1_verified_table")

    if not ODDS_API_KEY:
        logging.warning(
            "VEGAS FALLBACK (week %d): ODDS_API_KEY is not set. Every team gets a flat 21.5 "
            "total with no opponent; matchup and defensive-tier effects are OFF. Set ODDS_API_KEY "
            "(see config.py) for real lines.", current_nfl_week)
        return _write_vegas(DEFAULT_FALLBACK_TOTALS, current_nfl_week, "fallback_no_api_key")

    url = f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/?apiKey={ODDS_API_KEY}&regions=us&markets=spreads,totals&bookmakers=draftkings"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        games = response.json()
    except Exception as e:
        logging.warning(
            "VEGAS FALLBACK (week %d): odds API request failed (%s: %s). Every team gets a flat "
            "21.5 total with no opponent for this run.", current_nfl_week, type(e).__name__, e)
        return _write_vegas(DEFAULT_FALLBACK_TOTALS, current_nfl_week, "fallback_api_error")

    if not games:
        logging.warning(
            "VEGAS FALLBACK (week %d): odds API returned no games (market not posted, or wrong "
            "window). Every team gets a flat 21.5 total with no opponent for this run.",
            current_nfl_week)
        return _write_vegas(DEFAULT_FALLBACK_TOTALS, current_nfl_week, "fallback_empty_payload")

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

    unfilled = [team for team in NFL_TEAM_ABBREVIATIONS.values() if team not in implied_totals]
    for team in unfilled:
        implied_totals[team] = {"total": 21.5, "spread": 0.0, "wind_mph": 0.0, "precip_prob": 0.0, "opponent": "FA"}
    if unfilled:
        # A bye week legitimately leaves a few teams without a game; more than that means the
        # market payload was partial (missing bookmaker / market / unrecognised team name).
        logging.warning(
            "VEGAS (week %d): %d teams had no usable line and got the flat 21.5 / no-opponent "
            "fallback: %s", current_nfl_week, len(unfilled), ", ".join(sorted(unfilled)))

    return _write_vegas(implied_totals, current_nfl_week, "odds_api")

def _player_name(player):
    return f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()


def resolve_player_keys(pids, players_db, rostered_pids=None):
    """Maps each Sleeper pid to the NAME KEY the rest of the pipeline uses, made unique.

    Every downstream structure -- baselines, rosters, weekly player scores, the engine's
    dicts -- is keyed by full name, and Sleeper has players who share one (today: two Justin
    Jeffersons, two Byron Murphys). Before this, `baselines[name] = ...` let whichever pid
    iterated last silently overwrite the other; Byron Murphy's committed baseline was the
    SEA DL's, not the MIN CB's. See AUDIT_PHASE_3_FINDINGS.md finding 5.

    Collision rule, deterministic and loud:
      - exactly one colliding pid is rostered -> it keeps the plain name (rosters are minted
        from the same name, so the rostered player's baseline stays reachable); every other
        colliding pid becomes "Name (pid)". WARNING.
      - none rostered -> all become "Name (pid)". If one is rostered later, the plain name
        will not match and the engine's pre-flight abort fires -- loud at the point of
        rostering, and self-correcting on the next sync, which will see him rostered.
      - two or more colliding pids rostered -> genuinely ambiguous under name keying: raise.
        The pid-based rekey (AUDIT_PLAN.md, follow-up F1) is the real fix.
    Non-colliding names are returned unchanged. This is the interim guard, not the rekey."""
    rostered_pids = set(str(p) for p in (rostered_pids or ()))
    by_name = {}
    # dict.fromkeys: dedupe while keeping first-seen order. The same pid can legitimately
    # appear more than once in the input (e.g. once per matchup entry that lists him) and
    # must never be treated as colliding with itself.
    for pid in dict.fromkeys(str(p) for p in pids):
        player = players_db.get(pid)
        if not player:
            continue
        by_name.setdefault(_player_name(player), []).append(str(pid))

    keys = {}
    for name, group in by_name.items():
        if len(group) == 1:
            keys[group[0]] = name
            continue
        records = ", ".join(
            "pid %s (%s, %s)" % (p, players_db[p].get("position"), players_db[p].get("team"))
            for p in group)
        rostered = [p for p in group if p in rostered_pids]
        if len(rostered) > 1:
            raise ValueError(
                f"NAME COLLISION between rostered players: {name!r} is {records}, and "
                f"{len(rostered)} of them are on league rosters. Name-keyed data cannot "
                f"represent this; the pid-based rekey (AUDIT_PLAN.md F1) is required.")
        logging.warning(
            "NAME COLLISION: %r is %s. %s", name, records,
            ("pid %s is rostered and keeps the plain name; the rest are stored as 'Name (pid)'."
             % rostered[0]) if rostered else
            "None are rostered; all are stored as 'Name (pid)' until one is rostered.")
        for p in group:
            keys[p] = name if p in rostered_pids else f"{name} ({p})"
    return keys


def _last_logged_projections(path=PROJECTION_LOG_FILE):
    """{pid: (sleeper_mean, espn_mean or None)} from the LAST non-zero row per pid in F7's
    projection log -- the second data-sourced fallback for a rostered player whose projection
    is zero now and whose prior the baselines file has already lost. Reads the log directly
    (backtest_player.load_projection_log would be a circular import)."""
    out = {}
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                mean = float(row.get("sleeper_mean") or 0.0)
                if mean > 0.0:
                    espn = row.get("espn_mean")
                    out[str(row.get("player_id"))] = (mean, float(espn) if espn else None)
    except Exception as ex:
        logging.warning("PROJECTION LOG: could not read %s for carried priors (%s).", path, ex)
    return out


def generate_player_baselines(league_scoring_settings, players_db, live_rosters, current_year="2026", week=1,
                              rostered_pids=None, byes=None, reserve_pids=None):
    existing_baselines = {}
    if os.path.exists(BASELINES_FILE):
        try:
            existing_baselines = load_json(BASELINES_FILE)
        except Exception: pass
    # The prior blend below smooths this sync's projection with LAST sync's stored mean (an
    # exponential moving average across syncs, weight 0.4). Look that prior up by pid, not by
    # name: a player whose name key flips between "Name" and "Name (pid)" as roster status
    # changes must carry his own history across the flip, and must never inherit the OTHER
    # same-name player's -- which is exactly what a name lookup did (the committed file held
    # the SEA DL under the plain "Byron Murphy"). Entries written before this change carry no
    # player_id; for those, fall back to the name only when the name is not a collision.
    existing_by_pid = {
        str(entry["player_id"]): entry for entry in existing_baselines.values()
        if isinstance(entry, dict) and entry.get("player_id") is not None
    }
    logged_projections = None   # F7 log, read lazily only if a carried prior is needed

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

    keys = resolve_player_keys(projections.keys(), players_db, rostered_pids)
    colliding_names = {_player_name(players_db[p]) for p, k in keys.items() if k != _player_name(players_db[p])}

    baselines = {}
    unconstrained_positions = {}
    rostered_names = {p.get("name") for team in (live_rosters or {}).values() for p in team}
    projection_rows = []          # F7: what this sync projected for each rostered player
    synced_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    for pid, proj_data in projections.items():
        player = players_db.get(str(pid))
        if not player: continue

        name = keys[str(pid)]
        raw_pos = player.get("position", "FLEX")
        # `or "FA"`, not a .get default: Sleeper sends an explicit null team for anyone not on
        # an active roster, and .get's default only covers a MISSING key. Same bug
        # _build_roster_player_entry documents; it had been fixed there and not here
        # (Phase 3 finding 4).
        team = player.get("team") or "FA"
        stats_dict = proj_data.get("stats", proj_data)

        games_played = stats_dict.get("gp", 16.0) if fallback_season else 1.0
        if games_played <= 0: games_played = 16.0

        total_pts = sum(stats_dict.get(k, 0.0) * mult for k, mult in league_scoring_settings.items())
        if total_pts <= 0.0: total_pts = stats_dict.get("pts_half_ppr", stats_dict.get("pts_ppr", stats_dict.get("pts_std", 0.0)))

        sleeper_weekly_mean = round(total_pts / games_played, 2)
        if sleeper_weekly_mean <= 0.0:
            if name in rostered_names:
                # A rostered player with no projection. If a previous sync stored a baseline
                # for this pid, CARRY it (2026-09-01): a zero projection for a player Sleeper
                # marks absent (IR / PUP / NA / the league IR slot) is not "no data", it is
                # "out now" -- F4's case, which needs the absence signal (kept below from
                # today's roster) and a healthy-week mean for the return; the prior IS that
                # mean (Sleeper's own earlier projection), never an invented number. Live
                # cases: Josh Jacobs (NA, Commissioner Exempt) and Zach Charbonnet (PUP, on
                # IR), both of whose zero projections aborted the engine one stage later.
                # Flagged and warned every sync it persists, so the manifest shows it.
                prior = existing_by_pid.get(str(pid))
                carried_mean, source, prior_sd = None, None, (None, None)
                if prior is not None and prior.get("mean", 0.0) > 0.0:
                    carried_mean, source = float(prior["mean"]), "carried_prior"
                    prior_sd = (prior.get("std_aleatoric"), prior.get("std_epistemic"))
                else:
                    if logged_projections is None:
                        logged_projections = _last_logged_projections()
                    logged = logged_projections.get(str(pid))
                    if logged:
                        s_mean, e_mean = logged
                        carried_mean = round((s_mean + e_mean) / 2.0, 2) if e_mean else s_mean
                        source = "carried_log"
                if carried_mean is not None:
                    slot = normalize_position(raw_pos)
                    baselines[name] = {
                        "pos": raw_pos, "mean": carried_mean,
                        "std_aleatoric": float(prior_sd[0] if prior_sd[0] else round(VOLATILITY_CONSTANTS.get(slot, 1.5) * math.sqrt(max(0.5, carried_mean)), 2)),
                        "std_epistemic": float(prior_sd[1] if prior_sd[1] else round(EPISTEMIC_ERROR_RATES.get(slot, 0.18) * carried_mean, 2)),
                        "bye": (byes or {}).get(team, 0), "team": team, "player_id": str(pid),
                        "injury_status": player.get("injury_status"),
                        "on_ir": str(pid) in (reserve_pids or ()),
                        "projection_source": source,
                    }
                    logging.warning(
                        "BASELINES: rostered player %r (%s, %s) has a zero/empty Sleeper projection "
                        "(injury_status=%s, on_ir=%s); CARRIED %s mean %.2f as his healthy-week "
                        "expectation. He enters the engine through F4's absence handling if his "
                        "status warrants it, not at full strength.",
                        name, raw_pos, team, player.get("injury_status"), str(pid) in (reserve_pids or ()),
                        "the previous sync's baseline" if source == "carried_prior" else "the projection log's last",
                        carried_mean)
                    continue
                # No prior either: the engine aborts on him unless KNOWN_MISSING_ASSETS carries
                # a hand-typed entry. That used to happen silently, and the only signal was
                # the crash one stage later (Phase 3 finding 6 / inventory P5).
                logging.warning(
                    "BASELINES: rostered player %r (%s, %s) has a zero/empty Sleeper projection "
                    "and is NOT in baselines. The engine will abort on him unless "
                    "SIM_CONFIG['KNOWN_MISSING_ASSETS'] carries an entry (team must match: %s).",
                    name, raw_pos, team, team)
            continue

        # Multi-source blend: if ESPN has an independent projection for this player this week,
        # average the two sources instead of trusting Sleeper alone, and use how much the two
        # sources DISAGREE as a real, data-driven signal for how uncertain we should be --
        # two independent estimates disagreeing is genuine evidence of uncertainty, not just a
        # hand-picked positional error rate.
        espn_key = _normalize_player_name_for_matching(_player_name(player))  # plain name, never the "(pid)" key
        espn_weekly_mean = espn_projections.get(espn_key)
        if name in rostered_names:
            projection_rows.append({
                "season": str(current_year), "week": int(week), "synced_at": synced_at,
                "player_id": str(pid), "name": name, "pos": raw_pos, "team": team,
                "sleeper_mean": sleeper_weekly_mean,
                "espn_mean": (round(float(espn_weekly_mean), 2) if espn_weekly_mean is not None and espn_weekly_mean > 0 else None),
                "fallback_season": bool(fallback_season),
            })
        source_disagreement = None
        if espn_weekly_mean is not None and espn_weekly_mean > 0:
            fresh_mean = round((sleeper_weekly_mean + espn_weekly_mean) / 2.0, 2)
            source_disagreement = abs(sleeper_weekly_mean - espn_weekly_mean)
        else:
            fresh_mean = sleeper_weekly_mean

        prior = existing_by_pid.get(str(pid))
        if prior is None and not existing_by_pid and name in existing_baselines:
            # Legacy file (no pids anywhere). A plain colliding name could be either player,
            # so only trust it when the name is unambiguous.
            plain = _player_name(player)
            if plain in colliding_names:
                logging.warning(
                    "PRIOR SKIPPED: %r collides and the previous baselines file carries no "
                    "player_id, so its stored mean cannot be attributed. Fresh projection only "
                    "this sync; the pid is written now and carries forward from here.", name)
            else:
                prior = existing_baselines[name]
        if prior is not None:
            posterior_mean = prior.get("mean", fresh_mean)
            final_mean = round((fresh_mean * 0.6) + (posterior_mean * 0.4), 2)
        else:
            final_mean = fresh_mean

        # Constants are keyed by the engine's slot position; Sleeper reports DE/DT/NT/CB/S/FS/
        # SS/FB. Looking up by the raw string gave all of those the anonymous default
        # (Phase 3 finding 3). The stored "pos" stays raw -- the engine normalises on read.
        slot_pos = normalize_position(raw_pos)
        if slot_pos not in VOLATILITY_CONSTANTS:
            unconstrained_positions[raw_pos] = unconstrained_positions.get(raw_pos, 0) + 1
        k_val = VOLATILITY_CONSTANTS.get(slot_pos, 1.5)
        error_margin = EPISTEMIC_ERROR_RATES.get(slot_pos, 0.18)

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
            # From the NFL schedule (config.derive_bye_weeks), not from Sleeper: its payload
            # has no bye field, which is why this was 0 for every player until the bye work.
            "bye": (byes or {}).get(team, 0), "team": team,
            # Sleeper's id, so the prior blend above can follow this player across a name-key
            # change. The engine does not read it.
            "player_id": str(pid),
            # F4: availability, additive; semantics documented at _build_roster_player_entry.
            "injury_status": player.get("injury_status"),
            "on_ir": str(pid) in (reserve_pids or ()),
        }

    if unconstrained_positions:
        # Team DEF entities (32) and the odd unmapped position land here every sync; one line,
        # not one per player.
        logging.warning("BASELINES: %d entries have positions with no calibrated constants and use "
                        "the anonymous defaults (k=1.5, rate=0.18): %s",
                        sum(unconstrained_positions.values()), dict(sorted(unconstrained_positions.items())))
    save_json(BASELINES_FILE, baselines)
    append_projection_log(projection_rows)
    return baselines


def generate_league_schedule(roster_map, regular_season_weeks=14):
    """Fantasy matchups for weeks 1..regular_season_weeks, as a list indexed by week - 1.

    The engine indexes this list positionally (league_schedule[week_idx]), so the list MUST
    have exactly one entry per week. A failed week used to be skipped with `continue`, which
    shifted every later week's matchups one index earlier -- silently. It now contributes an
    empty week (no H2H decisions that week, which the engine already tolerates) and logs at
    WARNING. See AUDIT_PHASE_3_FINDINGS.md finding 2b."""
    full_schedule = []
    failed_weeks = []
    for wk in range(1, regular_season_weeks + 1):
        try:
            resp = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/matchups/{wk}", timeout=10)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            matchups_data = resp.json() or []
        except Exception as e:
            failed_weeks.append(wk)
            logging.warning(
                "LEAGUE SCHEDULE: week %d could not be fetched (%s: %s); recorded as an EMPTY "
                "week so later weeks keep their index. No H2H decisions will be simulated for "
                "week %d until the sync is re-run.", wk, type(e).__name__, e, wk)
            full_schedule.append([])
            continue
        if not matchups_data:
            logging.warning("LEAGUE SCHEDULE: week %d returned no matchups (not yet published?); "
                            "recorded as an empty week.", wk)
        matchup_dict = {}
        for entry in matchups_data:
            m_id = entry.get("matchup_id")
            t_name = roster_map.get(entry["roster_id"], f"Roster_{entry['roster_id']}")
            matchup_dict.setdefault(m_id, []).append(t_name)
        week_matchups = [tuple(pair) for pair in matchup_dict.values() if len(pair) == 2]
        full_schedule.append(week_matchups)

    assert len(full_schedule) == regular_season_weeks, "league schedule must have one entry per week"
    save_json(LEAGUE_SCHEDULE_FILE, full_schedule)
    return failed_weeks

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

def _extract_weekly_player_scores(wk_matchups, players_db, rostered_pids=None):
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
    all_pids = [pid for entry in wk_matchups for pid in entry.get("players_points", {})]
    # Same collision rule as the baselines, so a colliding player's scores land under the
    # same key his baseline uses (see resolve_player_keys).
    keys = resolve_player_keys(all_pids, players_db, rostered_pids)
    wk_player_scores = {}
    for entry in wk_matchups:
        for pid, pts in entry.get("players_points", {}).items():
            name = keys.get(str(pid))
            if name and pts is not None:
                wk_player_scores[name] = float(pts)
    return wk_player_scores

def _build_roster_player_entry(pid, players_db, reserve_pids=()):
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
        # F4 (AUDIT_PLAN.md): availability, additive. `injury_status` is Sleeper's own field
        # (IR / PUP / Out / Sus / DNR / Doubtful / Questionable / COV / NA / None; its
        # `injury_start_date` is never populated, so it is not carried). `on_ir` is the
        # LEAGUE's IR slot (the roster payload's `reserve` list): a manager decision, treated
        # as "absent regardless of status" because the player has been removed from the
        # lineup, which is what the engine models. Accepted cost, recorded in AUDIT_PLAN F4:
        # a Questionable player parked on IR is modelled as out.
        "injury_status": player.get("injury_status"),
        "on_ir": str(pid) in reserve_pids,
    }

class _WarningCollector(logging.Handler):
    """Collects every WARNING/ERROR logged during one sync -- the tolerated failures (ESPN,
    odds, weather, a schedule week, ...) that sync_all degrades through rather than raising
    on -- so the manifest can say a sync was degraded without anyone reading the log."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(f"{record.levelname} | {record.getMessage()}")


def _is_routine_notice(message):
    """A warning that is the pipeline working as designed, not a degradation: the F1
    collision guard announcing same-named UNROSTERED players (130 of them on a real sync --
    listing those as 'tolerated failures' buried the two real ones)."""
    return "NAME COLLISION" in message and "None are rostered" in message


def write_sync_manifest(started_at, current_week, season, warnings, sharp_polling, path=SYNC_MANIFEST_FILE):
    """See storage.SYNC_MANIFEST_FILE. Written LAST, after every other sync output.
    `degraded` = every WARNING/ERROR that is not a routine notice; `notices` = the routine ones
    (counted, first few kept)."""
    files = {os.path.basename(p): (os.path.getmtime(p) if os.path.exists(p) else None) for p in SYNC_OUTPUT_FILES}
    degraded = [w for w in warnings if not _is_routine_notice(w)]
    notices = [w for w in warnings if _is_routine_notice(w)]
    cache_age_days = ((datetime.now().timestamp() - os.path.getmtime(PLAYER_CACHE_FILE)) / 86400.0
                      if os.path.exists(PLAYER_CACHE_FILE) else None)
    save_json(path, {
        "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": season, "current_week": int(current_week), "sharp_polling": bool(sharp_polling),
        "degraded": degraded, "notices_count": len(notices), "notices_sample": notices[:5],
        "player_cache_age_days": cache_age_days, "files": files, "ok": True,
    })


def sync_all(sharp_polling=False):
    """Runs the full sync (_sync_body) and, only if it completes, writes the manifest last. An
    exception anywhere propagates and leaves no fresh manifest -- the orchestrator and
    check_freshness read that absence as "sync did not complete", never as stale-but-usable."""
    started_at = datetime.utcnow()
    collector = _WarningCollector()
    root = logging.getLogger()
    root.addHandler(collector)
    try:
        current_week, season = _sync_body(sharp_polling)
    finally:
        root.removeHandler(collector)
    write_sync_manifest(started_at, current_week, season, collector.messages, sharp_polling)


def _sync_body(sharp_polling=False):
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
    reserve_pids = set()
    for r in rosters:
        sim_name = roster_map[r["roster_id"]]
        settings = r.get("settings", {})
        standings_payload[sim_name] = {
            "h2h_wins": int(settings.get("wins", 0)),
            "points_scored": float(f"{settings.get('fpts', 0)}.{settings.get('fpts_decimal', 0)}"),
            "remaining_faab": max(0.0, 100.0 - float(settings.get("waiver_budget_used", 0))),
        }
        reserve = {str(p) for p in (r.get("reserve") or [])}
        reserve_pids |= reserve
        live_rosters_payload[sim_name] = [
            _build_roster_player_entry(pid, players_db, reserve)
            for pid in r.get("players", []) if str(pid) in players_db
        ]

    save_json(LIVE_ROSTERS_FILE, live_rosters_payload)
    save_json(LEAGUE_STANDINGS_FILE, standings_payload)

    generate_league_schedule(roster_map)
    generate_playoff_bracket(league_info, roster_map)
    completed_results = generate_nfl_schedule(current_nfl_week)
    generate_defensive_ratings(completed_results)
    # Bye weeks come from the schedule just written (its _meta.byes), so every baseline
    # carries the same value the engine will read.
    byes = load_json(NFL_SCHEDULE_FILE).get("_meta", {}).get("byes", {})
    rostered_pids = {str(pid) for r in rosters for pid in r.get("players", [])}
    generate_player_baselines(scoring_settings, players_db, live_rosters_payload, str(state.get("season", "2026")), current_nfl_week,
                              rostered_pids=rostered_pids, byes=byes, reserve_pids=reserve_pids)
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
        wk_player_scores = _extract_weekly_player_scores(wk_matchups, players_db, rostered_pids)
        # Real head-to-head win/loss per team -- previously hardcoded to 0 for everyone, every
        # week (see _extract_weekly_h2h_results docstring for the consequence of that).
        wk_h2h_results = _extract_weekly_h2h_results(wk_matchups, roster_map)

        t_res = {t: {"points_scored": score, "h2h_win": wk_h2h_results.get(t, 0.0), "median_win": 1 if score >= median_cut else 0, "remaining_faab": standings_payload[t]["remaining_faab"]} for t, score in wk_scores.items()}
        all_weeks_actuals[f"week_{wk}"] = {"median_cutoff": median_cut, "team_results": t_res, "player_scores": wk_player_scores}

    save_json(WEEKLY_ACTUALS_FILE, all_weeks_actuals)
    return current_nfl_week, str(state.get("season", "2026"))


def append_projection_log(rows, path=PROJECTION_LOG_FILE):
    """
    F7 (AUDIT_PLAN.md). Appends one JSON line per rostered player with the projections this
    sync used (Sleeper weekly mean, ESPN weekly mean if matched, whether Sleeper fell back to a
    season projection). Sleeper serves only the current week's projections and 2025's are gone,
    so this file is the only record from which projection error -- the quantity
    EPISTEMIC_ERROR_RATES actually denotes -- can be measured next season
    (backtest_player.analyze_projection_error). Append-only; a re-sync within a week appends
    again and the analysis keeps the last row per (season, week, player_id). A failure here
    must never break a sync: it logs and returns 0.
    """
    if not rows:
        return 0
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        return len(rows)
    except Exception as ex:
        logging.warning("PROJECTION LOG: could not append %d rows to %s (%s). Projection error "
                        "for this week cannot be measured next season.", len(rows), path, ex)
        return 0


def generate_playoff_bracket(league_info, roster_map):
    """
    F3 (AUDIT_PLAN.md). Fetches Sleeper's /winners_bracket and writes playoff_bracket.json with
    every roster id resolved to the engine's team name: {"playoff_week_start", "playoff_teams",
    "seeds" (round-1 participants, 1 v 4 first then 2 v 3), "rounds": [{"round", "match", "t1",
    "t2", "winner", "loser"}, ...]}. Round-2 entries whose sides are "from" earlier matches carry
    t1/t2 as None until Sleeper fills them. The engine seeds from banked standings and uses this
    file as the authority on the field and on round-1 winners when it exists; a fetch failure
    writes {} and warns, and the engine then falls back to weekly_actuals' week-15 results.
    """
    settings = (league_info or {}).get("settings", {}) or {}
    payload = {"playoff_week_start": settings.get("playoff_week_start"), "playoff_teams": settings.get("playoff_teams"),
               "seeds": [], "rounds": []}
    try:
        resp = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/winners_bracket", timeout=8)
        matches = resp.json() if resp.status_code == 200 else None
    except Exception as ex:
        matches = None
        logging.warning("PLAYOFF BRACKET: fetch failed (%s); writing an empty bracket. The engine will seed from banked standings only.", ex)
    if not matches:
        save_json(PLAYOFF_BRACKET_FILE, {})
        return {}
    name = lambda rid: roster_map.get(rid) if rid is not None else None
    for m in sorted(matches, key=lambda x: (x.get("r", 0), x.get("m", 0))):
        entry = {"round": m.get("r"), "match": m.get("m"), "t1": name(m.get("t1")), "t2": name(m.get("t2")),
                 "winner": name(m.get("w")), "loser": name(m.get("l"))}
        if m.get("p") is not None:
            entry["place"] = m.get("p")
        payload["rounds"].append(entry)
    r1 = [e for e in payload["rounds"] if e["round"] == 1 and e["t1"] and e["t2"]]
    if len(r1) == 2:
        m1, m2 = sorted(r1, key=lambda e: e["match"])
        payload["seeds"] = [m1["t1"], m2["t1"], m2["t2"], m1["t2"]]
    save_json(PLAYOFF_BRACKET_FILE, payload)
    return payload

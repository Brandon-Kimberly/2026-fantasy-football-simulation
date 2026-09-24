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
from datetime import datetime, timedelta

import numpy as np
import requests

from fantasy_sim.config import (
    SIM_CONFIG,
    BASE_URL, LEAGUE_ID, TEAM_NAME_MAP, ODDS_API_KEY, LEAGUE_AVG_PPG, DEF_RATING_SHRINKAGE_N0,
    PRESEASON_DEFENSIVE_PRIOR, NFL_TEAM_ABBREVIATIONS, OUTDOOR_STADIUMS, WEEK_1_VERIFIED_VEGAS,
    DEFAULT_FALLBACK_TOTALS, VOLATILITY_CONSTANTS, EPISTEMIC_ERROR_RATES, normalize_position,
    PROJECTION_LOG_SCHEMA_VERSION,
    ANON_VOLATILITY_K, ANON_EPISTEMIC_RATE,
    derive_bye_weeks,
)
from fantasy_sim.storage import (
    VEGAS_FILE, BASELINES_FILE, TEAM_RATINGS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_STATE_FILE,
    LIVE_ROSTERS_FILE, LEAGUE_STANDINGS_FILE, WEEKLY_ACTUALS_FILE, load_json, save_json, PROJECTION_LOG_FILE, PLAYOFF_BRACKET_FILE,
    SYNC_PROVENANCE_FILE, git_head_short, FIRST_SCORES_FILE, DESIGNATIONS_FILE,
    SYNC_MANIFEST_FILE, SYNC_OUTPUT_FILES, PLAYER_CACHE_FILE, DECISION_LOG_FILE,
    draft_log_file, season_log_file,
)
from fantasy_sim.clients.sleeper import update_player_cache
from fantasy_sim.clients.espn import fetch_espn_projection_data, normalize_player_name_for_matching as _normalize_player_name_for_matching
from fantasy_sim.clients.espn import fetch_espn_projections  # noqa: F401 -- deliberate re-export: tests import this from fantasy_sim.sync


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
    kickoffs = {}

    for wk in range(1, 19):
        nfl_schedule[str(wk)] = {}
        url = f"http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?week={wk}&seasontype=2"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            events = resp.json().get('events', [])
            # Real kickoff datetimes (UTC), persisted for scripts.run_windows' canonical-run
            # deadlines: _meta is invisible to the engine like the rest of this block.
            kickoffs[str(wk)] = sorted(e["date"] for e in events if e.get("date"))
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
    nfl_schedule["_meta"] = {"failed_weeks": failed_weeks, "byes": byes, "kickoffs": kickoffs}
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


def _is_fallback(source):
    return str(source or "").startswith("fallback")


def _keepable_real_lines(week):
    """The file already on disk IF it is real market data for THIS week, else None (C3).

    Never raises: unreadable or absent means "nothing to keep", and the caller writes as
    it always did. A record is not a dependency.
    """
    try:
        if not os.path.exists(VEGAS_FILE):
            return None
        existing = load_json(VEGAS_FILE) or {}
        meta = existing.get(VEGAS_META_KEY) or {}
        if _is_fallback(meta.get("source")) or meta.get("source") is None:
            return None
        if int(meta.get("week", -1)) != int(week):
            return None
        return existing
    except Exception:
        return None


def _write_vegas(totals, week, source):
    """Every path out of fetch_vegas_implied_totals goes through here, so the file on disk is
    ALWAYS the data the engine will be handed for this week -- never a leftover from an earlier
    sync. Two of the three in-season fallback paths used to return without writing, which left
    the week-1 table on disk for the rest of the season; the engine then applied week-1 lines,
    week-1 opponents included, to every current week. See AUDIT_PHASE_3_FINDINGS.md finding 1.

    C3 (2026-09-23) narrows that to "always write SOMETHING correct for this week", because
    always writing the FALLBACK was destroying good data. A sync with a dead ODDS_API_KEY
    401'd and flattened real week-3 lines to the 21.5 table; `data/current/` is untracked, so
    there was nothing to restore and every week-level projection degraded until the next good
    sync.

    A real file is kept only when it is real AND for the SAME week -- last week's real lines
    are not this week's, which is exactly what Phase 3 finding 1 was about. The keep is
    stamped `stale_since` so "kept from an earlier sync" is distinguishable from "fetched
    now"; silently preserving it would be its own quiet failure.
    """
    if _is_fallback(source):
        kept = _keepable_real_lines(week)
        if kept is not None:
            kept = dict(kept)
            meta = dict(kept.get(VEGAS_META_KEY) or {})
            meta.setdefault("stale_since", datetime.now().isoformat(timespec="seconds"))
            meta["stale_reason"] = source
            kept[VEGAS_META_KEY] = meta
            logging.warning(
                "VEGAS (week %s): the odds fetch failed (%s), so the REAL lines already on "
                "disk for this week are being kept rather than overwritten with the flat "
                "21.5 fallback (C3). They are stamped stale_since=%s; re-run the sync with a "
                "working ODDS_API_KEY for current lines.",
                week, source, meta["stale_since"])
            save_json(VEGAS_FILE, kept)
            generate_nfl_power_ratings(kept)
            return kept

    stamped = _stamp_vegas(totals, week, source)
    save_json(VEGAS_FILE, stamped)
    generate_nfl_power_ratings(stamped)
    return stamped


def fetch_vegas_implied_totals(current_nfl_week, sharp_polling=False, week_schedule=None):
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
        record_source("vegas_odds", ok=True, rows=len(WEEK_1_VERIFIED_VEGAS))
        return _write_vegas(WEEK_1_VERIFIED_VEGAS, current_nfl_week, "week1_verified_table")

    if not ODDS_API_KEY:
        logging.warning(
            "VEGAS FALLBACK (week %d): ODDS_API_KEY is not set. Every team gets a flat 21.5 "
            "total with no opponent; matchup and defensive-tier effects are OFF. Set ODDS_API_KEY "
            "(see config.py) for real lines.", current_nfl_week)
        record_source("vegas_odds", ok=False, rows=0, fallback="flat 21.5 totals, no opponents")
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
        record_source("vegas_odds", ok=False, rows=0, fallback="flat 21.5 totals, no opponents")
        return _write_vegas(DEFAULT_FALLBACK_TOTALS, current_nfl_week, "fallback_api_error")

    if not games:
        logging.warning(
            "VEGAS FALLBACK (week %d): odds API returned no games (market not posted, or wrong "
            "window). Every team gets a flat 21.5 total with no opponent for this run.",
            current_nfl_week)
        record_source("vegas_odds", ok=False, rows=0, fallback="flat 21.5 totals, no opponents")
        return _write_vegas(DEFAULT_FALLBACK_TOTALS, current_nfl_week, "fallback_empty_payload")

    # B18: FA is the free-agent pseudo-team, not a club. It plays no game, so it
    # carries `no_game` like a bye rather than an implied calm afternoon.
    implied_totals = {"FA": dict({"total": 20.0, "spread": 0.0, "opponent": "FA"},
                                 **unknown_weather("no_game"))}
    _wx_failures, _wx_ok = [], 0   # F57 (B6): accumulated here, reported ONCE after the loop
    for game in games:
        home_team = NFL_TEAM_ABBREVIATIONS.get(game.get("home_team"))
        away_team = NFL_TEAM_ABBREVIATIONS.get(game.get("away_team"))
        if not home_team or not away_team: continue

        # THIS WEEK'S GAMES ONLY (2026-09-11). The odds API returns every remaining game
        # of the season -- 213 across 54 dates when this was measured -- and the loop
        # below writes implied_totals[team] unconditionally, so without this filter each
        # team ended up holding whichever of its 14-17 listed games came LAST: a matchup
        # months away with the wrong opponent. The engine then rejected all 32 lines as
        # stale (its rule: a line's opponent must match the week's schedule) and ran on
        # the ratings-model fallback, which is how every forecast from the odds gate
        # opening through week 1 -- the pre-registered baseline included -- came out
        # matchup-blind. Filtering with the ENGINE'S OWN RULE means sync writes exactly
        # what the engine accepts. No schedule -> no filtering: without it there is no
        # way to tell the weeks apart, and guessing is worse than the honest fallback.
        if week_schedule and week_schedule.get(home_team) != away_team:
            continue

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

        commence_time = game.get("commence_time", "")

        # B18 (F55's three data faults). Indoors is KNOWN calm; a failed lookup is
        # UNKNOWN. They used to be spelled identically -- wind=0, precip=0 -- so the
        # offseason study would have read a dead endpoint as a perfect day.
        if home_team not in OUTDOOR_STADIUMS:
            wx = dome_weather()
        else:
            wx = unknown_weather()
            dates = weather_request_dates(commence_time)
            lat, lon = OUTDOOR_STADIUMS[home_team]
            if dates is None:
                _wx_failures.append(f"{home_team} (no kickoff time)")
            else:
                try:
                    # HOURLY, not daily: fault 2. The window spans two dates whenever a
                    # night game runs past midnight UTC, which is why both are requested.
                    wx_url = (
                        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                        f"&hourly=wind_speed_10m,precipitation,precipitation_probability"
                        f"&timezone=UTC&start_date={dates[0]}&end_date={dates[1]}"
                    )
                    wx_resp = requests.get(wx_url, timeout=5)
                    if wx_resp.status_code == 200:
                        got = game_window_weather(wx_resp.json().get("hourly") or {},
                                                  commence_time)
                        if got is None:
                            # The payload parsed but did not cover the game window. A
                            # partial window is not a game, so it stays unknown.
                            _wx_failures.append(f"{home_team} (window not covered)")
                        else:
                            wx, _wx_ok = got, _wx_ok + 1
                    else:
                        # F57: a non-200 never raised, so it fell through to zeros WITHOUT
                        # even reaching the except below -- the quietest of the six.
                        _wx_failures.append(f"{home_team} (HTTP {wx_resp.status_code})")
                except Exception as _wx_ex:
                    # F57 (B6): counted here, reported ONCE after the loop. This runs per
                    # outdoor game, so warning inline would emit up to 16 near-identical
                    # lines a sync.
                    _wx_failures.append(f"{home_team} ({type(_wx_ex).__name__})")

        _wx_fields = {k: v for k, v in wx.items() if k != "hours_used"}
        implied_totals[home_team] = dict(
            {"total": round((over_under / 2.0) - (home_spread / 2.0), 2),
             "spread": home_spread, "opponent": away_team}, **_wx_fields)
        implied_totals[away_team] = dict(
            {"total": round((over_under / 2.0) + (home_spread / 2.0), 2),
             "spread": -home_spread, "opponent": home_team}, **_wx_fields)

    unfilled = [team for team in NFL_TEAM_ABBREVIATIONS.values() if team not in implied_totals]
    for team in unfilled:
        # B18: a bye team has no game, so its weather is not unknown -- it is absent.
        # `no_game` says that rather than implying a calm afternoon somewhere.
        implied_totals[team] = dict({"total": 21.5, "spread": 0.0, "opponent": "FA"},
                                    **unknown_weather("no_game"))
    if unfilled:
        # A bye week legitimately leaves a few teams without a game; more than that means the
        # market payload was partial (missing bookmaker / market / unrecognised team name).
        logging.warning(
            "VEGAS (week %d): %d teams had no usable line and got the flat 21.5 / no-opponent "
            "fallback: %s", current_nfl_week, len(unfilled), ", ".join(sorted(unfilled)))

    # F57 (B6): ONE weather line per sync, not one per outdoor game.
    if _wx_failures:
        logging.warning(
            "WEATHER (week %d): the forecast lookup failed for %d game(s) [%s]; those carry "
            "weather_source='unavailable' with NULL wind and precipitation (B18 fault 3), so "
            "a dead endpoint no longer reads as a calm day. Nothing consumes these fields yet "
            "(F55) -- today this is a dead field, not a biased projection, and that stops "
            "being true the moment F55's offseason study adopts a term.",
            current_nfl_week, len(_wx_failures), ", ".join(_wx_failures))
    record_source("weather", ok=not _wx_failures, rows=_wx_ok,
                  fallback="weather_source=unavailable (nulls)" if _wx_failures else None)
    record_source("vegas_odds", ok=not unfilled, rows=len(implied_totals) - len(unfilled),
                  fallback="flat 21.5 / no opponent" if unfilled else None)
    return _write_vegas(implied_totals, current_nfl_week, "odds_api")

# B18 / F55. What a team entry's `weather_source` may say, so a reader of
# vegas_totals.json can tell a real forecast from a dome from a failed lookup WITHOUT
# going to the sync log. F55's live hazard is that "a populated field that nothing reads
# is indistinguishable from a working feature"; this is the label that distinguishes them.
WEATHER_SOURCES = ("forecast", "dome", "unavailable", "no_game")

# An NFL game runs about three hours. Wind is AVERAGED over that window and precipitation
# is SUMMED over it -- the two are different physical quantities and averaging rainfall
# would understate a cloudburst that stops at halftime.
GAME_WINDOW_HOURS = 3


def dome_weather():
    """Indoor: genuinely calm, and that is a FACT, not a fallback.

    It must not be spelled the same way as a failed lookup, or the offseason study cannot
    tell "no wind" from "no data" -- which is fault 3 in a different coat.
    """
    return {"wind_mph": 0.0, "precip_in": 0.0, "precip_prob": 0.0,
            "weather_source": "dome"}


def unknown_weather(source="unavailable"):
    """Weather we do not have. NULLS, not zeros (B18 fault 3).

    F57 made the sync LOG loud about a failed forecast, which was half the fix. The stored
    value stayed `0.0`, so anyone reading the file -- including F55's offseason study
    reading it back months later, long after the log has gone -- sees a perfect day.
    """
    return {"wind_mph": None, "precip_in": None, "precip_prob": None,
            "weather_source": source}


def weather_request_dates(commence_time):
    """The (start_date, end_date) an hourly forecast request must cover, UTC.

    A Sunday-night kickoff is 00:20Z the NEXT day and its window can run past midnight, so
    a single-date request silently drops the late hours -- and with them every night game,
    the population where wind matters most.
    """
    start = _parse_kickoff(commence_time)
    if start is None:
        return None
    end = start + timedelta(hours=GAME_WINDOW_HOURS)
    return (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))


def _parse_kickoff(commence_time):
    """An ISO-8601 UTC kickoff to a datetime floored to the hour, or None."""
    if not commence_time or not isinstance(commence_time, str):
        return None
    text = commence_time.strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(minute=0, second=0, microsecond=0)
        except ValueError:
            continue
    return None


def game_window_weather(hourly, commence_time, window_hours=GAME_WINDOW_HOURS):
    """Weather over the GAME, from an Open-Meteo hourly payload. None when unknown.

    B18 faults 1 and 2, together:

      * `wind_mph` is the MEAN over the kickoff window, not the day's peak. The old
        `wind_speed_10m_max` handed a 1pm kickoff the 10pm gale.
      * `precip_in` is the ACCUMULATION over the window, in inches. The old
        `precipitation_probability_max` read 100 for a certain drizzle and 100 for a
        certain flood, so the one game that prompted all of this was invisible to it.
      * `precip_prob` is kept beside it as the window MAX -- not replaced. The two answer
        different questions and the study wants both.

    A window the data does not FULLY cover returns None. Averaging the hours that happen
    to be present would quietly shrink the window and report the result as if it were a
    full game, which is the same class of silent degradation as fault 3.

    `hourly` is Open-Meteo's `hourly` block requested with `timezone=UTC`; wind arrives in
    km/h and precipitation in mm.
    """
    start = _parse_kickoff(commence_time)
    if start is None or not isinstance(hourly, dict):
        return None
    times = hourly.get("time") or []
    index = {t: i for i, t in enumerate(times)}

    wanted = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00")
              for h in range(int(window_hours))]
    idxs = [index.get(t) for t in wanted]
    if any(i is None for i in idxs):
        return None

    def _col(key):
        series = hourly.get(key) or []
        try:
            return [float(series[i]) for i in idxs]
        except (IndexError, TypeError, ValueError):
            return None

    winds, precs, probs = _col("wind_speed_10m"), _col("precipitation"), _col("precipitation_probability")
    if winds is None or precs is None:
        return None

    return {
        "wind_mph": round(sum(winds) / len(winds) * 0.621371, 2),
        "precip_in": round(sum(precs) / 25.4, 3),
        "precip_prob": (round(max(probs), 1) if probs else None),
        "hours_used": len(idxs),
        "weather_source": "forecast",
    }


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


# F29 (docs/AUDIT_PLAN.md): the K/IDP source-disagreement signal is computed on the
# category subset BOTH sources project, scored under this league's own settings.
# idp_tkl_loss is deliberately absent: ESPN's nearest projected stat ('Stuffs', id 112)
# measures a narrower quantity (~40% lower at slope 0.88 vs Sleeper's TFL) and scoring it
# as TFL would build a systematic shortfall into the signal. The ESPN side of the same
# subset lives in clients/espn.py (ESPN_IDP_BREAKDOWN_MAP + espn_*_subscore); the two
# must stay in lockstep.
IDP_SHARED_DISAGREEMENT_KEYS = (
    'idp_tkl_solo', 'idp_tkl_ast', 'idp_sack', 'idp_int', 'idp_pass_def',
    'idp_ff', 'idp_fum_rec', 'idp_def_td', 'idp_safe', 'idp_blk_kick', 'idp_qb_hit',
)
SUBSCORE_SLOTS = {'K', 'DL', 'LB', 'DB'}


def _shared_subscore(stats_dict, league_scoring_settings, slot):
    """Sleeper's side of the F29 shared sub-score. K excludes the per-yard
    fgm_yds_over_30 bonus on both sides (ESPN projects bands, not yards; a within-band
    yardage distribution would be an invented constant). Returns None when the stat line
    carries nothing scoreable, so callers fall back to the positional floor."""
    if slot == 'K':
        fgm = float(stats_dict.get('fgm', 0.0) or 0.0)
        fga = float(stats_dict.get('fga', 0.0) or 0.0)
        xpm = float(stats_dict.get('xpm', 0.0) or 0.0)
        xpa = float(stats_dict.get('xpa', 0.0) or 0.0)
        if fgm <= 0 and xpm <= 0:
            return None
        return (fgm * float(league_scoring_settings.get('fgm', 0.0) or 0.0)
                + xpm * float(league_scoring_settings.get('xpm', 0.0) or 0.0)
                + max(0.0, fga - fgm) * float(league_scoring_settings.get('fgmiss', 0.0) or 0.0)
                + max(0.0, xpa - xpm) * float(league_scoring_settings.get('xpmiss', 0.0) or 0.0))
    total = sum(float(stats_dict.get(k, 0.0) or 0.0) * float(league_scoring_settings.get(k, 0.0) or 0.0)
                for k in IDP_SHARED_DISAGREEMENT_KEYS)
    return total if total > 0 else None


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
    # F58: both of these were `except Exception: pass` with no log line, and a non-200 did
    # not even reach the handler -- so the PRIMARY projection source failing was completely
    # invisible. B6's grep missed them because it required the `except` to end the line.
    _proj_why = []
    url_weekly = f"{BASE_URL}/projections/nfl/regular/{current_year}/{week}"
    try:
        r = requests.get(url_weekly, timeout=8)
        if r.status_code == 200 and r.json():
            projections = r.json()
        else:
            _proj_why.append(f"weekly: HTTP {r.status_code}"
                             + ("" if r.status_code != 200 else " with an empty body"))
    except Exception as ex:
        _proj_why.append(f"weekly: {type(ex).__name__}: {ex}")

    fallback_season = False
    if not projections:
        url_season = f"{BASE_URL}/projections/nfl/regular/{current_year}"
        try:
            r = requests.get(url_season, timeout=8)
            if r.status_code == 200 and r.json():
                projections = r.json()
                fallback_season = True
            else:
                _proj_why.append(f"season: HTTP {r.status_code}"
                                 + ("" if r.status_code != 200 else " with an empty body"))
        except Exception as ex:
            _proj_why.append(f"season: {type(ex).__name__}: {ex}")

    if _proj_why:
        logging.warning("PROJECTIONS (week %s): Sleeper's projection endpoint did not serve "
                        "usable data [%s].%s", week, "; ".join(_proj_why),
                        "" if projections else " NOTHING to build baselines from.")
    record_source("sleeper_projections", ok=not _proj_why, rows=len(projections),
                  fallback=("season-long projections" if fallback_season else
                            ("none -- the sync refuses to continue" if not projections else None)))

    if not projections:
        # F58. THE DESTRUCTIVE CASE, and the reason this refuses instead of degrading.
        # With projections empty the loop below never executes, `baselines` stays {}, and
        # save_json would OVERWRITE player_baselines.json with an empty dict -- while
        # nothing raised, so sync_all wrote an ok:True manifest and check_freshness saw a
        # freshly-written file and reported OK. Every downstream tool then read nothing.
        #
        # Raising is the documented contract for a sync that cannot complete (sync_all:
        # "an exception anywhere propagates and leaves no fresh manifest -- check_freshness
        # reads that absence as 'sync did not complete'"). Degrading is NOT available here:
        # there is no partial answer, only a wiped file.
        raise RuntimeError(
            f"PROJECTIONS (week {week}): Sleeper returned no usable projection data "
            f"[{'; '.join(_proj_why) or 'empty payload'}]. Refusing to continue: building "
            f"baselines from an empty payload would overwrite player_baselines.json with "
            f"nothing. The previous sync's baselines are left untouched -- re-run the sync.")

    # Second, independent projection source (free, see fetch_espn_projections docstring). A
    # failure here must never break baseline generation -- espn_projections simply stays {}
    # and every player below falls back to Sleeper-only, exactly as before this change.
    espn_projections, espn_subscores = {}, {}
    try:
        espn_projections, espn_subscores = fetch_espn_projection_data(
            current_year, week, league_scoring_settings)
    except Exception as ex:
        espn_projections, espn_subscores = {}, {}
        # Visible on purpose (F36 gate, 2026-09-04): this used to fail SILENTLY, so a
        # Sleeper-only sync was indistinguishable from a blended one in the manifest.
        # The degraded channel exists for exactly this class of tolerated failure.
        logging.warning("ESPN BLEND: fetch failed (%s); all players fall back to "
                        "Sleeper-only this sync.", type(ex).__name__)
    # F52 (2026-09-20): F36's guard above fires only on an EXCEPTION. A call that
    # succeeds and returns {} is the same outcome and was invisible -- which is how a
    # missing `week` argument kept the blend off from week 2 onward without one notice.
    # An empty result is now as loud as a raised one, which is what F36 actually intended.
    if not espn_projections:
        logging.warning("ESPN BLEND: zero usable projections returned for week %s; every "
                        "player falls back to Sleeper-only this sync and std_epistemic "
                        "loses the source-disagreement signal.", week)
    # F57: the positive record. F52 lived in the gap between "no warning" and "it worked".
    record_source("espn_projections", ok=bool(espn_projections), rows=len(espn_projections),
                  fallback=None if espn_projections else "Sleeper-only mean, no disagreement signal")
    record_source("espn_subscores", ok=bool(espn_subscores), rows=len(espn_subscores),
                  fallback=None if espn_subscores else "no K/IDP epistemic channel (F29)")

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
                        "std_aleatoric": float(prior_sd[0] if prior_sd[0] else round(VOLATILITY_CONSTANTS.get(slot, ANON_VOLATILITY_K) * math.sqrt(max(0.5, carried_mean)), 2)),
                        "std_epistemic": float(prior_sd[1] if prior_sd[1] else round(EPISTEMIC_ERROR_RATES.get(slot, ANON_EPISTEMIC_RATE) * carried_mean, 2)),
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
                # The warning SPLITS on whether the whitelist covers him (2026-09-04, found
                # by F36's gate replay): the covered case fired the same alarming text for
                # a week after Tyson's entry landed, and the canonical gate read every one
                # of the week's syncs as blocked on a case the engine handles cleanly.
                wl = (SIM_CONFIG.get("KNOWN_MISSING_ASSETS") or {}).get(name)
                if wl is not None and wl.get("team") == team:
                    logging.warning(
                        "BASELINES: rostered player %r (%s, %s) has a zero/empty Sleeper "
                        "projection; covered by KNOWN_MISSING_ASSETS -- the engine imputes "
                        "the whitelisted baseline.", name, raw_pos, team)
                else:
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
        slot_pos = normalize_position(raw_pos)
        # F29: K/IDP get no points-level ESPN mean (points under ESPN's scoring are not
        # comparable for these positions -- the original, still-correct half of the old
        # exclusion), but both sources project raw STAT LINES, so the disagreement signal
        # comes from scoring both lines under this league's settings on the shared subset.
        # Epistemic-only by design: ESPN projects no TFL, so a blended mean would be
        # biased low; the mean stays Sleeper's. Sub-scores land in the F7 log for F22's
        # eventual epistemic derivation.
        sleeper_sub = espn_sub = None
        if slot_pos in SUBSCORE_SLOTS:
            espn_sub = espn_subscores.get(espn_key)
            if espn_sub is not None:
                sleeper_sub = _shared_subscore(stats_dict, league_scoring_settings, slot_pos)
        if name in rostered_names:
            projection_rows.append({
                "season": str(current_year), "week": int(week), "synced_at": synced_at,
                "player_id": str(pid), "name": name, "pos": raw_pos, "team": team,
                "sleeper_mean": sleeper_weekly_mean,
                "espn_mean": (round(float(espn_weekly_mean), 2) if espn_weekly_mean is not None and espn_weekly_mean > 0 else None),
                "fallback_season": bool(fallback_season),
                "sleeper_sub": (round(sleeper_sub, 2) if sleeper_sub is not None else None),
                "espn_sub": (round(float(espn_sub), 2) if espn_sub is not None else None),
            })
        source_disagreement = None
        if espn_weekly_mean is not None and espn_weekly_mean > 0:
            fresh_mean = round((sleeper_weekly_mean + espn_weekly_mean) / 2.0, 2)
            source_disagreement = abs(sleeper_weekly_mean - espn_weekly_mean)
        else:
            fresh_mean = sleeper_weekly_mean
            if sleeper_sub is not None and espn_sub is not None:
                source_disagreement = abs(sleeper_sub - float(espn_sub))

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
        # (slot_pos computed above, at the F29 sub-score block.)
        if slot_pos not in VOLATILITY_CONSTANTS:
            unconstrained_positions[raw_pos] = unconstrained_positions.get(raw_pos, 0) + 1
        k_val = VOLATILITY_CONSTANTS.get(slot_pos, ANON_VOLATILITY_K)
        error_margin = EPISTEMIC_ERROR_RATES.get(slot_pos, ANON_EPISTEMIC_RATE)

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
                        f"the anonymous defaults (k={ANON_VOLATILITY_K}, rate={ANON_EPISTEMIC_RATE}): %s",
                        sum(unconstrained_positions.values()), dict(sorted(unconstrained_positions.items())))
    save_json(BASELINES_FILE, baselines)
    append_projection_log(projection_rows)   # F56: also writes the provenance sidecar
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

def fetch_league_wide_player_scores(year, week, league_scoring_settings, fetch=None):
    """{player_id: points} for EVERY player with a stat line that week, scored under this
    league's own settings.

    F54. Sleeper's matchup payload carries only rostered players, so without this the
    posterior refinement could never see the ~750 projected players nobody owns -- which
    is exactly the pool every waiver claim is drawn from. Returns {} on any failure: a
    missing stats feed must degrade to the old matchup-only behaviour, never break a sync.
    """
    fetch = fetch or (lambda u: requests.get(u, timeout=45).json())
    out = {}
    positions = ("QB", "RB", "WR", "TE", "K", "DL", "DE", "DT", "LB", "OLB", "ILB",
                 "DB", "CB", "S")
    failed = []      # F57 (B6): 14 positions, ONE aggregated notice
    for pos in positions:
        try:
            rows = fetch(f"https://api.sleeper.app/stats/nfl/{year}/{int(week)}"
                         f"?season_type=regular&position[]={pos}") or []
        except Exception as ex:
            failed.append(f"{pos} ({type(ex).__name__})")
            continue
        for r in rows:
            pid, stats = r.get("player_id"), (r.get("stats") or {})
            if pid is None or not stats:
                continue
            pts = sum(float(league_scoring_settings.get(k, 0)) * float(v or 0)
                      for k, v in stats.items() if k in league_scoring_settings)
            if pts:
                out[str(pid)] = float(pts)
    # F57. This feed is what took F54's posterior from 157 players to ~1,018. Losing it
    # silently drops every unrostered player -- the entire waiver pool -- back onto an
    # untouched preseason prior while rostered players keep a corrected one, which is a
    # systematic ranking bias, not a missing nicety. Name it once, with the count.
    if failed:
        logging.warning("STATS (week %s): the league-wide stats feed failed for %d of %d "
                        "positions [%s]. The Bayesian posterior sees only the players those "
                        "positions would have carried; unrostered players fall back to the "
                        "preseason prior.", week, len(failed), len(positions), ", ".join(failed))
    record_source("sleeper_stats", ok=not failed, rows=len(out),
                  fallback="matchup-only posterior (rostered players only)" if failed else None)
    return out


def _extract_weekly_player_scores(wk_matchups, players_db, rostered_pids=None,
                                  league_wide=None):
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
    all_pids += [pid for pid in (league_wide or {})]
    # Same collision rule as the baselines, so a colliding player's scores land under the
    # same key his baseline uses (see resolve_player_keys).
    keys = resolve_player_keys(all_pids, players_db, rostered_pids)
    wk_player_scores = {}
    # F54: league-wide first, so the matchup value OVERWRITES it for anyone rostered.
    # Sleeper's credited total is authoritative -- it is what this league actually paid --
    # and the stats feed exists only to fill in the players no matchup payload can reach.
    for pid, pts in (league_wide or {}).items():
        name = keys.get(str(pid))
        if name and pts is not None:
            wk_player_scores[name] = float(pts)
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


# ------------------------------------------------------------------ the source ledger
# F57 (B6). The manifest's `degraded` list records what went WRONG. It records nothing
# about what went RIGHT, so "no warning" and "the source returned an empty payload" are
# the same manifest -- which is precisely how F52 hid for a fortnight. This is the
# positive half: every external source states what it actually delivered, and
# check_freshness reads zero rows as DEGRADED whether or not anything warned.
#
# Module-level rather than threaded through _sync_body because the recording sites are
# four calls deep (clients/espn.py has no handle on the sync). Reset at the top of
# sync_all, read by write_sync_manifest, so one sync's ledger cannot leak into the next.
_SOURCE_LEDGER = {}


def reset_sources():
    """Called at the start of every sync. See _SOURCE_LEDGER."""
    _SOURCE_LEDGER.clear()


def record_source(name, ok=True, rows=0, fallback=None):
    """Record what one external source delivered. ACCUMULATES: calling this once per team
    (weather, 32x), per position (the stats feed, 14x) or per player (the ESPN parse,
    ~2000x) yields ONE entry whose `rows` is the total -- B6's named trap, made structural
    rather than left to each call site to remember. A source is `ok` only if no call fell
    back; the first fallback taken is kept, since 30 identical ones say nothing more."""
    entry = _SOURCE_LEDGER.setdefault(name, {"ok": True, "rows": 0, "fallback": None})
    entry["rows"] += int(rows)
    if not ok:
        entry["ok"] = False
        if entry["fallback"] is None:
            entry["fallback"] = fallback


def collected_sources():
    """A copy -- callers must not be able to mutate the ledger through the manifest."""
    return {k: dict(v) for k, v in _SOURCE_LEDGER.items()}


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
        # F57: what each external source actually DELIVERED. `degraded` says what went
        # wrong; without this, an empty payload and a clean run are the same manifest.
        "sources": collected_sources(),
    })


def sync_all(sharp_polling=False):
    """Runs the full sync (_sync_body) and, only if it completes, writes the manifest last. An
    exception anywhere propagates and leaves no fresh manifest -- the orchestrator and
    check_freshness read that absence as "sync did not complete", never as stale-but-usable."""
    started_at = datetime.utcnow()
    reset_sources()   # F57: one sync's ledger must not leak into the next
    collector = _WarningCollector()
    root = logging.getLogger()
    root.addHandler(collector)
    try:
        current_week, season = _sync_body(sharp_polling)
    finally:
        root.removeHandler(collector)
    write_sync_manifest(started_at, current_week, season, collector.messages, sharp_polling)


def _sync_body(sharp_polling=False):
    if not LEAGUE_ID:
        raise SystemExit("SLEEPER_LEAGUE_ID is not set (F37: league ids are env-only). "
                         "Locally: setx SLEEPER_LEAGUE_ID <id> and open a NEW terminal; "
                         "on the runner: the repo secret of the same name.")
    players_db = update_player_cache()
    league_info = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}").json()
    scoring_settings = league_info.get("scoring_settings", {})
    state = requests.get(f"{BASE_URL}/state/nfl").json()

    season_type = state.get("season_type", "regular")
    current_nfl_week = 1 if season_type == "pre" else state.get("week", 1)
    save_json(LEAGUE_STATE_FILE, {"current_week": current_nfl_week})

    rosters = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/rosters").json()
    # F57: the Sleeper core calls have no try/except -- a failure raises and leaves no
    # manifest, which is the right contract. They are recorded anyway so the sources block
    # is a COMPLETE inventory: "espn_projections is the only name missing" is a much
    # harder question to answer than "espn_projections says 0 rows".
    record_source("sleeper_players", rows=len(players_db or {}))
    record_source("sleeper_league", rows=len(league_info or {}))
    record_source("sleeper_rosters", rows=len(rosters or []))
    # F37 (2026-09-05): keyed by roster_id directly. The old display-name hop published
    # real usernames in config and broke whenever a manager renamed themselves; roster_id
    # is stable, opaque, and meaningless without the (env-only) league id.
    roster_map = {r["roster_id"]: TEAM_NAME_MAP.get(str(r["roster_id"]), "Unknown") for r in rosters}

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
    record_source("nfl_schedule", rows=len(load_json(NFL_SCHEDULE_FILE).get(str(current_nfl_week), {}) or {}))
    generate_defensive_ratings(completed_results)
    # Bye weeks come from the schedule just written (its _meta.byes), so every baseline
    # carries the same value the engine will read.
    byes = load_json(NFL_SCHEDULE_FILE).get("_meta", {}).get("byes", {})
    rostered_pids = {str(pid) for r in rosters for pid in r.get("players", [])}
    baselines = generate_player_baselines(scoring_settings, players_db, live_rosters_payload, str(state.get("season", "2026")), current_nfl_week,
                              rostered_pids=rostered_pids, byes=byes, reserve_pids=reserve_pids)
    record_source("player_baselines", ok=bool(baselines), rows=len(baselines or {}))
    _wk_sched = {}
    try:
        _wk_sched = (load_json(NFL_SCHEDULE_FILE) or {}).get(str(current_nfl_week), {}) or {}
    except (FileNotFoundError, OSError, ValueError):
        _wk_sched = {}
    if not _wk_sched:
        logging.warning(
            "VEGAS (week %d): no schedule available to match the odds payload against, so "
            "every game in it is accepted -- lines for other weeks may land in the file and "
            "the engine will reject them. Re-run after the schedule fetch succeeds.",
            current_nfl_week)
    fetch_vegas_implied_totals(current_nfl_week, sharp_polling=sharp_polling,
                               week_schedule=_wk_sched)

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
        # F54: union in EVERY player's scored stat line, not just the rostered ones the
        # matchup payload can carry. Without this the posterior only ever reached ~157 of
        # ~1,140 players, and every free agent -- the entire waiver pool -- was ranked on
        # an untouched preseason prior while rostered players were ranked on a corrected
        # one. Degrades to {} on any failure, i.e. back to the old behaviour.
        wk_league_wide = fetch_league_wide_player_scores(
            str(state.get("season", "2026")), wk, scoring_settings or {})
        wk_player_scores = _extract_weekly_player_scores(
            wk_matchups, players_db, rostered_pids, league_wide=wk_league_wide)
        # Real head-to-head win/loss per team -- previously hardcoded to 0 for everyone, every
        # week (see _extract_weekly_h2h_results docstring for the consequence of that).
        wk_h2h_results = _extract_weekly_h2h_results(wk_matchups, roster_map)

        t_res = {t: {"points_scored": score, "h2h_win": wk_h2h_results.get(t, 0.0), "median_win": 1 if score >= median_cut else 0, "remaining_faab": standings_payload[t]["remaining_faab"]} for t, score in wk_scores.items()}
        all_weeks_actuals[f"week_{wk}"] = {"median_cutoff": median_cut, "team_results": t_res, "player_scores": wk_player_scores}

    record_source("sleeper_matchups", rows=len(all_weeks_actuals))
    save_json(WEEKLY_ACTUALS_FILE, all_weeks_actuals)
    # B19: freeze what was FIRST reported, before a correction can overwrite it.
    n_first = append_first_recorded_scores(all_weeks_actuals, current_nfl_week, baselines)
    if n_first:
        print(f"[FIRST SCORES] {n_first} score(s) recorded for the first time.")
    # B21: the designation series the player cache overwrites every sync.
    n_des = append_designations(live_rosters_payload, players_db, baselines, current_nfl_week)
    if n_des:
        print(f"[DESIGNATIONS] {n_des} new designation(s) recorded.")
    n_tx = ingest_transactions(roster_map, current_nfl_week, baselines, players_db, standings=standings_payload)
    if n_tx:
        print(f"[DECISION LOG] {n_tx} new transaction(s) ingested.")
    n_draft = ingest_drafts(roster_map)
    if n_draft:
        print(f"[DRAFT LOG] {n_draft} draft(s) ingested.")
    warn_depth_mean_disagreements(baselines, players_db)
    return current_nfl_week, str(state.get("season", "2026"))


def _now_ms():
    return int(datetime.now().timestamp() * 1000)


def ingest_transactions(roster_map, current_week, baselines, players_db, my_team=None, path=DECISION_LOG_FILE, standings=None):
    """The decision log (see storage.DECISION_LOG_FILE): fetches every week's transactions
    from Sleeper, appends the COMPLETED ones not yet in the log (dedupe on transaction_id),
    one JSON line each: date and week, exact terms (players by name and pid, destination
    team, FAAB bid, both sides of a trade), is_mine, and each involved player's model
    projection AT INGESTION TIME -- the baseline record (season-level weekly mean,
    std_epistemic, injury status, projection_source). That snapshot is as of THIS sync, not
    the moment of the click: `snapshot_lag_days` records the gap and
    `snapshot_is_retroactive` is set when it exceeds one day, so a later retrospective never
    treats a backfilled projection as contemporaneous (the first ingestion backfills every
    existing transaction this way). A failure here must never break a sync: it warns (into
    the manifest) and returns 0.

    my_team defaults to config.MY_TEAM. Returns the number of records appended."""
    if my_team is None:
        from fantasy_sim.config import MY_TEAM as my_team  # noqa: F811
    seen = set()
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        seen.add(json.loads(line).get("transaction_id"))
    except Exception as ex:
        logging.warning("DECISION LOG: could not read %s (%s); skipping ingestion this sync "
                        "rather than risking duplicates.", path, ex)
        return 0

    by_pid = {str(e.get("player_id")): e for e in baselines.values()
              if isinstance(e, dict) and e.get("player_id") is not None}

    def player_entry(pid, roster_id):
        pdb = players_db.get(str(pid), {})
        name = f"{pdb.get('first_name', '')} {pdb.get('last_name', '')}".strip() or str(pid)
        b = by_pid.get(str(pid))
        projection = None
        if b is not None:
            projection = {"mean": b.get("mean"), "std_epistemic": b.get("std_epistemic"),
                          "pos": b.get("pos"), "injury_status": b.get("injury_status"),
                          "on_ir": b.get("on_ir"), "projection_source": b.get("projection_source")}
        return {"player_id": str(pid), "name": name,
                "to_team": roster_map.get(roster_id, f"roster_{roster_id}"), "projection": projection}

    now = _now_ms()
    appended = 0
    records = []
    for wk in range(1, max(1, int(current_week)) + 1):
        try:
            resp = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/transactions/{wk}", timeout=10)
            txs = resp.json() if resp.status_code == 200 else []
        except Exception as ex:
            logging.warning("DECISION LOG: transactions for week %d could not be fetched (%s); "
                            "they will be picked up by a later sync.", wk, ex)
            continue
        for tx in txs or []:
            txid = tx.get("transaction_id")
            if not txid or txid in seen or tx.get("status") != "complete":
                continue
            seen.add(txid)
            created = int(tx.get("created") or now)
            lag_days = max(0.0, (now - created) / 86_400_000.0)
            teams = [roster_map.get(rid, f"roster_{rid}") for rid in (tx.get("roster_ids") or [])]
            records.append({
                "transaction_id": txid, "type": tx.get("type"), "week": tx.get("leg", wk),
                "created": datetime.utcfromtimestamp(created / 1000.0).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "snapshot_at": datetime.utcfromtimestamp(now / 1000.0).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "snapshot_lag_days": round(lag_days, 3),
                "snapshot_is_retroactive": lag_days > 1.0,
                "teams": teams, "is_mine": my_team in teams,
                "faab_bid": (tx.get("settings") or {}).get("waiver_bid"),
                # As of the INGESTING sync, post-bid (Sleeper's waiver_budget_used already
                # includes it); null on non-waiver records and when standings were not passed.
                "bidder_remaining_faab": ((standings or {}).get(teams[0], {}).get("remaining_faab")
                                          if tx.get("type") == "waiver" and teams else None),
                "adds": [player_entry(pid, rid) for pid, rid in (tx.get("adds") or {}).items()],
                "drops": [player_entry(pid, rid) for pid, rid in (tx.get("drops") or {}).items()],
            })
    if not records:
        return 0
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for r in records:
                handle.write(json.dumps(r, sort_keys=True) + "\n")
                appended += 1
    except Exception as ex:
        logging.warning("DECISION LOG: could not append %d records to %s (%s). They will be "
                        "re-ingested by the next successful sync.", len(records), path, ex)
        return 0
    return appended


def warn_depth_mean_disagreements(baselines, players_db):
    """F24's watchdog. The 2025 study cleared mean-weighted vacated-volume apportionment
    (ties depth weighting, matches observed inheritance concentration), leaving one rare
    failure mode: baseline means misordering a backfield's true depth. The chart is not
    simply trusted instead -- in the one live disagreement measured (2026-09-02), the
    CHART was wrong (Josh Jacobs on the Commissioner Exempt list, charted depth 4 while
    being GB's lead) and the mean was right. So sync WARNS when the two signals disagree
    about a team's top healthy backup RB and lets a human judge; the warning lands in the
    manifest's degraded list like every other sync warning. Returns warnings emitted."""
    by_pid = {}
    for v in players_db.values():
        if isinstance(v, dict) and v.get("player_id") is not None:
            by_pid[str(v["player_id"])] = v
    teams = {}
    for name, b in baselines.items():
        if not isinstance(b, dict) or normalize_position(b.get("pos") or "") != "RB":
            continue
        p = by_pid.get(str(b.get("player_id")), {})
        do = p.get("depth_chart_order")
        if do is None or not b.get("team"):
            continue
        teams.setdefault(b["team"], []).append((int(do), float(b.get("mean", 0.0)), name))
    n_warn = 0
    for team, rbs in teams.items():
        backups = sorted(r for r in rbs if r[0] >= 2)
        if len(backups) < 2:
            continue
        by_depth = backups[0]
        by_mean = max(backups, key=lambda r: r[1])
        if by_depth[2] != by_mean[2]:
            logging.warning(
                "DEPTH WATCHDOG: %s backup RBs -- depth chart says %r (depth %d, mean %.1f) "
                "but baseline means say %r (depth %d, mean %.1f). Vacated-volume weighting "
                "follows the MEANS (F24: measured correct on 2025 events); judge this case "
                "by hand if that backfield's lead goes down.",
                team, by_depth[2], by_depth[0], by_depth[1],
                by_mean[2], by_mean[0], by_mean[1])
            n_warn += 1
    return n_warn


def ingest_drafts(roster_map, league_id=None, path_fn=None):
    """F15 ingestion row (AUDIT_PLAN.md): every COMPLETED draft in the league's renewal chain
    (current league, then previous_league_id links), one document per season at
    data/logs/draft_{season}.json -- the immutable historical record of who drafted whom. A
    draft file that already exists is NEVER rewritten. Picks carry the team name resolved via
    the CURRENT league's roster map; Sleeper keeps roster_id stable across a renewed league,
    but for past seasons that is an assumption, so the raw roster_id and picked_by user id
    are stored on every pick to make any mis-resolution recoverable. A failure here must
    never break a sync: it warns (into the manifest) and moves on. Returns files written."""
    if league_id is None:
        league_id = LEAGUE_ID
    if path_fn is None:
        path_fn = draft_log_file

    def pick_row(p):
        md = p.get("metadata") or {}
        rid = p.get("roster_id")
        return {"pick_no": p.get("pick_no"), "round": p.get("round"),
                "draft_slot": p.get("draft_slot"), "roster_id": rid,
                "team": roster_map.get(rid, f"roster_{rid}"),
                "picked_by": p.get("picked_by"), "player_id": str(p.get("player_id")),
                "is_keeper": bool(p.get("is_keeper")),
                "name": f"{md.get('first_name', '')} {md.get('last_name', '')}".strip(),
                "pos": md.get("position"), "nfl_team": md.get("team")}

    written = 0
    lid, seen = league_id, set()
    while lid and lid not in seen:
        seen.add(lid)
        try:
            info = requests.get(f"{BASE_URL}/league/{lid}", timeout=10).json() or {}
            resp = requests.get(f"{BASE_URL}/league/{lid}/drafts", timeout=10)
            drafts = resp.json() if resp.status_code == 200 else []
        except Exception as ex:
            logging.warning("DRAFT LOG: league %s could not be fetched (%s); its draft(s) "
                            "will be picked up by a later sync.", lid, ex)
            break
        for d in drafts or []:
            season = str(d.get("season") or info.get("season") or "")
            if d.get("status") != "complete" or not season:
                continue
            path = path_fn(season)
            if os.path.exists(path):
                continue  # immutable once written
            try:
                p_resp = requests.get(f"{BASE_URL}/draft/{d.get('draft_id')}/picks", timeout=10)
                picks = p_resp.json() if p_resp.status_code == 200 else None
            except Exception as ex:
                logging.warning("DRAFT LOG: picks for draft %s (season %s) could not be "
                                "fetched (%s); a later sync will retry.", d.get("draft_id"), season, ex)
                continue
            if not picks:
                logging.warning("DRAFT LOG: draft %s (season %s) returned no picks; a later "
                                "sync will retry.", d.get("draft_id"), season)
                continue
            payload = {"draft_id": d.get("draft_id"), "season": season, "league_id": lid,
                       "status": d.get("status"), "start_time": d.get("start_time"),
                       "settings": d.get("settings"),
                       "ingested_at": datetime.utcfromtimestamp(_now_ms() / 1000.0).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "picks": [pick_row(p) for p in picks]}
            try:
                save_json(path, payload)
                written += 1
            except Exception as ex:
                logging.warning("DRAFT LOG: could not write %s (%s); a later sync will retry.", path, ex)
        lid = info.get("previous_league_id")
    return written


def ingest_season(league_id, path_fn=None):
    """The season-retrospective bundle (storage.season_log_file): one document per season --
    the league's metadata (roster_positions, playoff_week_start, league_average_match), the
    roster map resolved to team names, final standings, and every week's real matchups
    trimmed to what a retrospective needs (roster_id, matchup_id, points, players, starters,
    players_points -- per-player REALIZED scores, no projections). A bundle that already
    exists is NEVER rewritten. Not called from the sync body: a completed historical season
    does not belong in every sync -- scripts.season_retrospective ingests on demand. Any
    fetch failure warns and writes nothing (no partial bundle). Returns files written."""
    if path_fn is None:
        path_fn = season_log_file
    try:
        info = requests.get(f"{BASE_URL}/league/{league_id}", timeout=10).json() or {}
        season = str(info.get("season") or "")
        if not season:
            logging.warning("SEASON LOG: league %s returned no season; nothing written.", league_id)
            return 0
        path = path_fn(season)
        if os.path.exists(path):
            return 0  # immutable once written
        rosters = requests.get(f"{BASE_URL}/league/{league_id}/rosters", timeout=10).json() or []
        # F37: roster_id-keyed (Sleeper keeps roster_id stable across a renewed league,
        # the same assumption ingest_drafts already documents).
        roster_map = {str(r["roster_id"]): TEAM_NAME_MAP.get(str(r["roster_id"]),
                                                             f"roster_{r['roster_id']}")
                      for r in rosters}
        final_standings = {}
        for r in rosters:
            st = r.get("settings", {})
            final_standings[roster_map[str(r["roster_id"])]] = {
                "wins": int(st.get("wins", 0)), "losses": int(st.get("losses", 0)),
                "ties": int(st.get("ties", 0)),
                "points_scored": float(f"{st.get('fpts', 0)}.{st.get('fpts_decimal', 0)}"),
            }
        matchups = {}
        for wk in range(1, 19):
            resp = requests.get(f"{BASE_URL}/league/{league_id}/matchups/{wk}", timeout=10)
            wk_data = resp.json() if resp.status_code == 200 else None
            if not wk_data:
                continue
            matchups[str(wk)] = [{"roster_id": e.get("roster_id"), "matchup_id": e.get("matchup_id"),
                                  "points": e.get("points"), "players": e.get("players"),
                                  "starters": e.get("starters"),
                                  "players_points": e.get("players_points")} for e in wk_data]
        bundle = {"league_id": league_id, "season": season, "name": info.get("name"),
                  "status": info.get("status"),
                  "roster_positions": info.get("roster_positions"),
                  "settings": {"playoff_week_start": (info.get("settings") or {}).get("playoff_week_start"),
                               "league_average_match": (info.get("settings") or {}).get("league_average_match")},
                  "roster_map": roster_map, "final_standings": final_standings,
                  "matchups": matchups,
                  "ingested_at": datetime.utcfromtimestamp(_now_ms() / 1000.0).strftime("%Y-%m-%dT%H:%M:%SZ")}
        save_json(path, bundle)
        return 1
    except Exception as ex:
        logging.warning("SEASON LOG: season bundle for league %s could not be ingested (%s); "
                        "nothing written.", league_id, ex)
        return 0


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
        # F56: the provenance sidecar for the rows just written. Called from INSIDE this
        # function on purpose. When the two writers were separate, a test that patched
        # only this one (test_sync_handlers:177 does exactly that) left the provenance
        # writer live against the real data/logs/ -- the F11 class, caught by
        # test_zz_log_integrity. One operation, one seam, impossible to half-mock.
        append_sync_provenance(rows, path=_provenance_path_for(path))
        return len(rows)
    except Exception as ex:
        logging.warning("PROJECTION LOG: could not append %d rows to %s (%s). Projection error "
                        "for this week cannot be measured next season.", len(rows), path, ex)
        return 0


def append_designations(live_rosters, players_db, baselines, week, path=DESIGNATIONS_FILE):
    """B21. The injury-designation series `sleeper_players_cache.json` throws away.

    That file holds TODAY's status and is overwritten every sync, so "how many weeks was
    this player Questionable" has no answer. B10 needs it, and it is the evidence B4
    would require before PRICING a designation rather than merely surfacing it.

    Deduped on (week, pid, injury_status) -- the first appearance of each DISTINCT status
    in a week. The alternatives are both worse: (week, pid) loses a Friday Questionable
    that became a Sunday Out, which is the transition worth studying, and no dedupe at
    all writes a row per sync (twenty-four of them in week 2 alone).

    THE ROLL CALL IS WRITTEN TOO, healthy men included with a null `injury_status`
    (F63, 2026-09-23). This function used to skip them, reasoning that "a row per healthy
    man per week is 150 rows of 'nothing happened', and the question is about
    designations, not roll call." That is backwards for the one study this log exists to
    feed. B10 asks whether a designation predicts a later DNP **above the base rate**,
    and a base rate is a rate among the UNDESIGNATED -- who were never recorded, and are
    not recoverable afterwards, because `live_rosters.json` is overwritten every sync and
    the players cache holds only today.

    Without the roll call the study has to borrow its denominator from the LEAGUE-WIDE
    scored feed (~800 players a week against the ~152 rostered), where players nobody
    tracked sit in the "undesignated" group carrying designations that were never logged.
    That contaminates the comparison group and biases the measured lift DOWNWARD -- a
    study that can only understate its effect. The price of fixing it is about 2,700 rows
    a season.

    The dedupe rule carries the roll call without inflating it: a healthy player's
    (week, pid, None) key is written once and every later sync that week is a no-op, and
    if he is listed Questionable on Friday that is a DISTINCT key, so the transition is
    still captured.

    Returns rows appended. Never raises: a record is not a dependency.
    """
    try:
        seen = set()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    seen.add((r.get("week"), r.get("player_id"), r.get("injury_status")))

        stamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = []
        for team, players in (live_rosters or {}).items():
            for p in players or []:
                name = p.get("name")
                pid = ((baselines or {}).get(name) or {}).get("player_id")
                if pid is None:
                    continue
                rec = (players_db or {}).get(str(pid))
                if not rec:
                    continue
                # F63: a healthy man is a roll-call row (status None), not a skip.
                status = rec.get("injury_status") or None
                key = (int(week), str(pid), status)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"week": int(week), "player_id": str(pid), "name": name,
                             "team": team, "injury_status": status,
                             "injury_body_part": rec.get("injury_body_part"),
                             "practice_participation": rec.get("practice_participation"),
                             "recorded_at": stamp})
        if not rows:
            return 0
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for r in sorted(rows, key=lambda x: (x["team"], x["name"])):
                handle.write(json.dumps(r, sort_keys=True) + "\n")
        return len(rows)
    except Exception as ex:
        logging.warning("DESIGNATIONS: could not append to %s (%s). The injury-history "
                        "series will have a gap at week %s.", path, ex, week)
        return 0


def append_first_recorded_scores(weekly_actuals, current_week, baselines=None,
                                 path=FIRST_SCORES_FILE):
    """B19. One row per scored player per COMPLETED week, written once, never updated.

    `weekly_actuals.json` is rewritten every sync, so a stat correction destroys the
    number it corrected. This is the only record of what was ORIGINALLY reported.

    Three rules, each of which would destroy the evidence if broken:

      * the CURRENT week is skipped -- it is still accruing, and freezing a half-played
        week would make every later update look like a correction;
      * a (week, name) already present is left alone, including when the score has
        CHANGED. That difference is the finding;
      * a player with no baseline is still recorded, with a null pid, because dropping
        him loses the very score a correction might later move.

    Keyed on (week, name): those names come from resolve_player_keys and are already
    disambiguated, a colliding player being stored "Name (pid)". The pid is carried
    alongside so the log outlives the naming convention.

    Returns rows appended. Never raises: a record is not a dependency.
    """
    try:
        seen = set()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    seen.add((r.get("week"), r.get("name")))

        stamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        bl = baselines or {}
        new_rows = []
        for wk_key, payload in (weekly_actuals or {}).items():
            try:
                wk = int(str(wk_key).split("_")[-1])
            except ValueError:
                continue
            if wk >= int(current_week):
                continue
            for name, pts in ((payload or {}).get("player_scores") or {}).items():
                if (wk, name) in seen:
                    continue
                entry = bl.get(name) or {}
                pid = entry.get("player_id")
                new_rows.append({"week": wk, "name": name,
                                 "player_id": str(pid) if pid is not None else None,
                                 "points": float(pts or 0.0), "recorded_at": stamp})
                seen.add((wk, name))
        if not new_rows:
            return 0
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for r in sorted(new_rows, key=lambda x: (x["week"], x["name"])):
                handle.write(json.dumps(r, sort_keys=True) + "\n")
        return len(new_rows)
    except Exception as ex:
        logging.warning("FIRST SCORES: could not append to %s (%s). Stat corrections for "
                        "these weeks will not be detectable.", path, ex)
        return 0


def _provenance_path_for(projection_log_path):
    """The sidecar that belongs beside a given projection log.

    DERIVED rather than a fixed constant so that redirecting the projection log --
    which tests and the golden-sync harness both do -- carries the provenance file with
    it automatically. A fixed default would bind at def time and keep writing to the
    real data/logs/ even when the caller had redirected everything else, which is the
    bug test_zz_log_integrity caught during this finding's own implementation.
    """
    return os.path.join(os.path.dirname(projection_log_path) or ".",
                        os.path.basename(SYNC_PROVENANCE_FILE))


def append_sync_provenance(rows, path=SYNC_PROVENANCE_FILE):
    """
    F56. One row per sync recording WHICH BUILD produced that sync's projection rows,
    joined to projection_log.jsonl on `synced_at`.

    The projection log records what was projected and never which code projected it. The
    live log holds 77 distinct sync stamps, 24 inside week 2 alone, spanning the F52
    boundary -- and January's calibration is required to partition the season at two
    boundaries that do not coincide (F49's IDP scoring change, F52/F54's blend
    restoration). Without this, that partition is a hand-match against git log.

    `espn_rows` is the field that cannot be recovered any other way: it is the count of
    rows whose ESPN blend actually fired, and the pre/post-F52 difference (0/152 against
    110/150 in week 2) IS the boundary.

    A SIDECAR, not columns on each projection row: golden_sync hashes
    projection_log.jsonl byte-exactly, so widening that schema would force a MAJOR
    regeneration, and a git hash inside a byte-pinned file changes on every commit --
    the harness freezes datetime but has no seam for git HEAD, so the golden would pass
    once and fail forever after.

    Append-only, exactly like the log it describes. A failure here must never break a
    sync: it logs and returns 0.
    """
    if not rows:
        return 0
    try:
        # Row construction lives INSIDE the try on purpose: the docstring promises a
        # failure here never breaks a sync, and a NameError or a bad week value while
        # BUILDING the row would otherwise escape and do exactly that.
        # season / week / synced_at are DERIVED from the rows, never passed in: that
        # makes the join key byte-identical to the log this row describes by
        # construction, rather than by every caller remembering to pass the same value.
        first = rows[0]
        row = {
            "synced_at": first.get("synced_at"),
            "git_commit": git_head_short(),
            "schema_version": PROJECTION_LOG_SCHEMA_VERSION,
            "season": str(first.get("season")),
            "week": int(first.get("week")),
            "espn_rows": sum(1 for r in rows if r.get("espn_mean") is not None),
            "total_rows": len(rows),
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        return 1
    except Exception as ex:
        logging.warning("SYNC PROVENANCE: could not append a row to %s (%s). That sync's "
                        "projection rows cannot be attributed to a build, which "
                        "January's partition needs.", path, ex)
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

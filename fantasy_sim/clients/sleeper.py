"""
fantasy_sim.clients.sleeper

Thin wrapper around Sleeper's public player database endpoint. The rest of this project's
Sleeper API usage (rosters, matchups, projections, league settings) lives alongside the
business logic that consumes it in fantasy_sim.sync, since those calls are tightly interleaved
with parsing/transformation rather than being clean, reusable units on their own -- this
client only covers the one piece that already was.
"""
import logging
import os
import time

import requests

from fantasy_sim.config import BASE_URL
from fantasy_sim.storage import PLAYER_CACHE_FILE, load_json, save_json

# Sleeper asks that /players/nfl (~20 MB) be fetched at most once a day. Every name, team,
# position and injury status in the pipeline comes from this file, and those fields move
# daily in-season (cuts, trades, IR), so a day is also about as stale as it should get.
PLAYER_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60


def update_player_cache(force=False, max_age_seconds=PLAYER_CACHE_MAX_AGE_SECONDS):
    """Sleeper's full NFL player database, refreshed when the local cache is older than
    max_age_seconds (default one day) or when force=True.

    It used to be fetched exactly once and read from disk forever: no age check, no force
    path. The live comparison on 2026-08-28 found a one-day-old cache already differing from
    Sleeper on a rostered player's injury_status. See AUDIT_PHASE_3_FINDINGS.md finding 7.
    A refresh that fails keeps serving the stale file, loudly."""
    exists = os.path.exists(PLAYER_CACHE_FILE)
    age = (time.time() - os.path.getmtime(PLAYER_CACHE_FILE)) if exists else None
    if exists and not force and age <= max_age_seconds:
        return load_json(PLAYER_CACHE_FILE)

    reason = "forced" if force else ("no cache on disk" if not exists else "cache is %.1f days old" % (age / 86400.0))
    try:
        r = requests.get(f"{BASE_URL}/players/nfl", timeout=60)
        r.raise_for_status()
        payload = r.json()
        if not payload:
            raise ValueError("empty payload")
        save_json(PLAYER_CACHE_FILE, payload)
        logging.info("PLAYER CACHE: refreshed from Sleeper (%s).", reason)
        return payload
    except Exception as e:
        if not exists:
            raise
        logging.warning("PLAYER CACHE: refresh failed (%s: %s); serving the cached file, which is "
                        "%.1f days old. Names, teams and positions may be stale.",
                        type(e).__name__, e, age / 86400.0)
        return load_json(PLAYER_CACHE_FILE)

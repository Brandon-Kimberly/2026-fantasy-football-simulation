"""
fantasy_sim.clients.sleeper

Thin wrapper around Sleeper's public player database endpoint. The rest of this project's
Sleeper API usage (rosters, matchups, projections, league settings) lives alongside the
business logic that consumes it in fantasy_sim.sync, since those calls are tightly interleaved
with parsing/transformation rather than being clean, reusable units on their own -- this
client only covers the one piece that already was.
"""
import requests

from fantasy_sim.config import BASE_URL
from fantasy_sim.storage import PLAYER_CACHE_FILE, load_json, save_json
import os


def update_player_cache():
    """Fetches and caches Sleeper's full NFL player database on first call; subsequent calls
    read from the local cache rather than re-fetching."""
    if not os.path.exists(PLAYER_CACHE_FILE):
        r = requests.get(f"{BASE_URL}/players/nfl")
        save_json(PLAYER_CACHE_FILE, r.json())
    return load_json(PLAYER_CACHE_FILE)

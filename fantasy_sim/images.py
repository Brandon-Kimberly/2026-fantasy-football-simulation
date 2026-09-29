"""fantasy_sim.images -- the local image cache the sync fills (docs/WEB_UI_ROADMAP.md Decision 3,
UI-E7; owner ruling 2026-09-29: cache at sync).

Each rostered player's Sleeper headshot and the 32 NFL team logos are downloaded into
data/images/ once, so the web UI loads them locally with no third-party request per page view
and works offline. A file already on disk is never fetched again.

The images are cosmetic, so nothing here can fail a sync: a player with no headshot (Sleeper
answers 403), a network error, or a response that is not an image is counted and skipped, and
logged at INFO -- never WARNING, which the sync manifest would record as a degradation. Only
bytes that begin as a JPEG or a PNG are written, and each is written to a temporary name and
renamed, so an interrupted sync never leaves half an image behind. data/ is gitignored, so
nothing here is committed.
"""
import logging
import os

from .config import NFL_TEAMS
from .storage import _path

IMAGES_DIR = _path("images")
HEADSHOT_URL = "https://sleepercdn.com/content/nfl/players/thumb/{}.jpg"
LOGO_URL = "https://sleepercdn.com/images/team_logos/nfl/{}.png"   # Sleeper's names are lower case (DET.png is a 404)
JPEG_MAGIC, PNG_MAGIC = b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n"
# Sleeper serves every headshot as PNG bytes at a .jpg URL, labelled image/jpeg (checked
# 2026-09-29), so a headshot is accepted when its bytes are either; the UI serves the type
# the bytes are, whatever the name says.
MAGIC = {".jpg": (JPEG_MAGIC, PNG_MAGIC), ".png": (PNG_MAGIC,)}
TIMEOUT_S = 10


def default_teams():
    return list(NFL_TEAMS)


def _fetch(get, url, dest, ext):
    """True when the image was written; False for anything else, never an exception."""
    try:
        r = get(url, timeout=TIMEOUT_S)
        body = getattr(r, "content", b"") or b""
        if getattr(r, "status_code", None) != 200 or not body.startswith(MAGIC[ext]):
            return False
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".part"
        with open(tmp, "wb") as fh:
            fh.write(body)
        os.replace(tmp, dest)
        return True
    except Exception:
        return False


def cache_images(player_ids, teams=None, dest=None, get=None):
    """Download each headshot and logo not already on disk. `player_ids` are Sleeper ids; a
    defence's id is its team code, which has no headshot (its logo covers it). Returns
    {"fetched", "cached", "missing"}."""
    if get is None:
        import requests
        get = requests.get
    dest = dest or IMAGES_DIR
    jobs = []
    for pid in sorted({str(p) for p in player_ids or ()}):
        if pid.isdigit():
            jobs.append((HEADSHOT_URL.format(pid), os.path.join(dest, "players", pid + ".jpg"), ".jpg"))
    for team in sorted({str(t).lower() for t in (default_teams() if teams is None else teams)}):
        if team.isalpha():
            jobs.append((LOGO_URL.format(team), os.path.join(dest, "teams", team + ".png"), ".png"))
    n = {"fetched": 0, "cached": 0, "missing": 0}
    for url, path, ext in jobs:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            n["cached"] += 1
        elif _fetch(get, url, path, ext):
            n["fetched"] += 1
        else:
            n["missing"] += 1
    logging.info("IMAGES: %d fetched, %d already cached, %d not available", n["fetched"], n["cached"], n["missing"])
    return n

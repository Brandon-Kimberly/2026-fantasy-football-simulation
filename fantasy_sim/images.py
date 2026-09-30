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
import time

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
# A sync must not wait on an image server (audit 2026-09-29: ~230 files at up to 10 s each could
# hold a sync for over half an hour). Both are judgement, not measurement: a run of errors this
# long means the server is down, not that five players lack photos; and two minutes is ample for
# a normal first fill (~200 small files) and a small share of a sync's run time.
MAX_CONSECUTIVE_ERRORS = 5
BUDGET_S = 120


def default_teams():
    return list(NFL_TEAMS)


def _fetch(get, url, dest, ext):
    """"ok" when the image was written, "miss" for an answer that is not one (a 403, a page),
    "error" when the request itself failed; never an exception."""
    try:
        r = get(url, timeout=TIMEOUT_S)
    except Exception:
        return "error"
    try:
        body = getattr(r, "content", b"") or b""
        if getattr(r, "status_code", None) != 200 or not body.startswith(MAGIC[ext]):
            return "miss"
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".part"
        with open(tmp, "wb") as fh:
            fh.write(body)
        os.replace(tmp, dest)
        return "ok"
    except Exception:
        return "error"


def cache_images(player_ids, teams=None, dest=None, get=None, clock=time.monotonic, budget_s=BUDGET_S,
                 max_errors=MAX_CONSECUTIVE_ERRORS):
    """Download each headshot and logo not already on disk. `player_ids` are Sleeper ids; a
    defence's id is its team code, which has no headshot (its logo covers it). It stops after
    `max_errors` failed requests in a row or once `budget_s` has passed; what it did not try is
    "skipped", and the next sync picks it up. Returns {"fetched", "cached", "missing", "skipped"}."""
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
    n = {"fetched": 0, "cached": 0, "missing": 0, "skipped": 0}
    start, errors = clock(), 0
    for url, path, ext in jobs:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            n["cached"] += 1
            continue
        if errors >= max_errors or clock() - start > budget_s:
            n["skipped"] += 1
            continue
        got = _fetch(get, url, path, ext)
        errors = errors + 1 if got == "error" else 0
        n["fetched" if got == "ok" else "missing"] += 1
    logging.info("IMAGES: %d fetched, %d already cached, %d not available, %d left for the next sync",
                 n["fetched"], n["cached"], n["missing"], n["skipped"])
    return n

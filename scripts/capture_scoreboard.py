"""
scripts.capture_scoreboard -- record ESPN's live NFL scoreboard while games are on (UI-M5's prerequisite).

    py -3.10 -m scripts.capture_scoreboard [--out scoreboard_capture] [--minutes 20] [--every 60]

UI-M5 (a red-zone marker beside a starter whose team has the ball inside the 20, a possession
marker on the game strip) reads the scoreboard's `situation` block, which exists only while a game
is live. The recorded week-4 board in tests/fixtures was taken before kickoff and has none, so the
item is built from what this records. It polls the public scoreboard every `--every` seconds for
`--minutes` and keeps every distinct snapshot in which a game is live, gzipped, as
scoreboard_<UTC time>_<content hash>.json.gz ({captured_at, board}); pre-game boards and repeats
are dropped, and a failed fetch is skipped. Public endpoint, no key, nothing tracked is written.
The scoreboard-capture workflow runs it inside Sunday's game windows and keeps the files as an
artifact: `gh run download <run id> -n scoreboard-live-<run id>`.
"""
import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

URL = "http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


def is_live(board):
    """Any game with a `situation` block: the live down, distance, possession and red-zone flag."""
    for ev in (board or {}).get("events") or []:
        for comp in ev.get("competitions") or []:
            if comp.get("situation"):
                return True
    return False


def _fetch():
    import requests
    r = requests.get(URL, timeout=15)
    r.raise_for_status()
    return r.json()


def capture(out, minutes=20, every=60, fetch=None, clock=None, sleep=None):
    """Poll for `minutes`; write each distinct live snapshot to `out`. Returns how many were kept."""
    fetch, clock, sleep = fetch or _fetch, clock or time.monotonic, sleep or time.sleep
    os.makedirs(out, exist_ok=True)
    seen, kept, start = set(), 0, clock()
    while clock() - start < minutes * 60:
        try:
            board = fetch()
        except Exception as ex:                                   # noqa: BLE001 -- one miss is not the run
            print(f"capture_scoreboard: fetch failed ({type(ex).__name__}); trying again", file=sys.stderr)
            board = None
        if board is not None and is_live(board):
            digest = hashlib.sha256(json.dumps(board, sort_keys=True).encode("utf-8")).hexdigest()[:8]
            if digest not in seen:
                seen.add(digest)
                now = datetime.now(timezone.utc)
                name = f"scoreboard_{now:%Y%m%dT%H%M%S}Z_{digest}.json.gz"
                with gzip.open(os.path.join(out, name), "wt", encoding="utf-8") as fh:
                    json.dump({"captured_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "board": board}, fh)
                kept += 1
        sleep(every)
    return kept


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="scoreboard_capture")
    ap.add_argument("--minutes", type=float, default=20)
    ap.add_argument("--every", type=float, default=60)
    a = ap.parse_args(argv)
    n = capture(a.out, a.minutes, a.every)
    print(f"capture_scoreboard: kept {n} distinct live snapshot(s) in {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

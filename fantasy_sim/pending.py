"""Players committed to a PENDING trade -- advisory only, and never in the engine.

T3 (docs/SCOPED_BACKLOG_2.md). With a trade pending on 2026-09-23, `find_trades
--require-mutual` ranked a player already promised to somebody else FIRST. Sleeper's
transactions endpoint returns those with `status: "pending"`, and `sync.ingest_transactions`
deliberately keeps only `complete` -- B14's premise is that the decision log records what
HAPPENED -- so nothing downstream had ever seen a pending trade.

**PENDING IS NOT CERTAIN.** A trade can be vetoed or withdrawn and the players come
straight back. Three consequences, all load-bearing:

1. The exclusion is **advisory**. Every screen that applies it reports how many players it
   dropped and why, rather than silently producing a shorter list.
2. Every tool takes a flag to turn it off. A withdrawn offer must not leave the finder
   permanently blind to a player.
3. **The engine never sees any of this.** Applying a pending trade to `engine.rosters` as
   if it were complete would put unowned players into lineups, into the paired simulation
   and into the weekly projections -- a far worse error than the one being fixed. This
   module returns NAMES for a screen to skip, and touches nothing.

**Matching is by `player_id`.** The raw cache carries 220 colliding names, seven of them
involving a player rostered in this league (B17), so a name-keyed exclusion would remove
the wrong man. The file stores pids; the engine resolves them.

**Absence is unknown, not "nothing pending".** A missing or unreadable file excludes
nobody -- the same rule the bid ledger follows for an unresolved claim. Excluding everyone
on a read error would be far worse than proposing one stale trade.
"""
import json
import logging

from fantasy_sim.storage import PENDING_TRADES_FILE


def load_pending(path=PENDING_TRADES_FILE):
    """The pending-trade document, or an empty one. Never raises."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and isinstance(doc.get("trades"), list):
            return doc
    except (OSError, ValueError) as ex:
        logging.debug("PENDING TRADES: %s unreadable (%s); excluding nobody.", path, ex)
    return {"_meta": {}, "trades": []}


def committed_player_ids(path=PENDING_TRADES_FILE):
    """Every `player_id` named on either side of a pending trade."""
    out = set()
    for trade in load_pending(path).get("trades") or []:
        for p in trade.get("players") or []:
            pid = p.get("player_id")
            if pid is not None:
                out.add(str(pid))
    return out


def committed_players(engine, path=PENDING_TRADES_FILE):
    """Those pids resolved to the names this engine knows. Unknown pids are skipped.

    Skipped rather than carried through as a name: a pid nobody in this league owns cannot
    match a candidate anyway, and inventing a name for it would put a string into an
    exclusion set that no roster can ever contain.
    """
    wanted = committed_player_ids(path)
    if not wanted:
        return set()
    return {name for name, e in (engine.baselines or {}).items()
            if isinstance(e, dict) and str(e.get("player_id")) in wanted}


def note(excluded):
    """The one line every screen prints when it drops somebody. Advisory, and says so."""
    if not excluded:
        return ""
    return (f"{len(excluded)} player(s) excluded: committed to a pending trade "
            f"({', '.join(sorted(excluded))}). ADVISORY -- pending is not complete, a "
            f"vetoed trade returns them, and no roster or simulation has been changed. "
            f"Pass --include-pending to consider them anyway.")

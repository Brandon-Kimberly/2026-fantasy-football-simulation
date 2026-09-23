"""
fantasy_sim.league_chain

Walking the Sleeper league renewal chain, across a break in it.

B20. Sleeper links one season's league to the previous one through
`previous_league_id`. **This league's chain is broken at 2025** -- that league's
`previous_league_id` is null -- so a walker starting from the current season reaches 2025
and stops, and 2024 is orphaned. `scripts.luck_ledger --all` and
`scripts.season_retrospective` both did exactly that, silently: no error, just one season
fewer than the owner asked for.

`config.KNOWN_LEAGUE_IDS` is the documented fallback, read from the environment (F37).
This module is the one place that combines the two, so the two callers cannot drift.

PRECEDENCE, and it is deliberate: **the live chain wins.** The map fills in seasons the
chain cannot reach; it never overrides a season the chain already resolved. A
disagreement between the two is a stale environment variable and is WARNED about rather
than silently preferred -- an env var that quietly replaced live data would be the worst
of both.

Fetch-injected so callers keep their own HTTP policy (timeouts, caching, error handling)
and so the tests touch no network and no environment (F48).
"""
import logging


def resolve_chain(current_league_id, fetch, known=None):
    """[(season, league_id), ...] newest first, chain first and `known` for the rest.

    `fetch(league_id)` returns that league's Sleeper object (or {}); a falsy return ends
    the walk. `known` is a {season: league_id} map, normally config.KNOWN_LEAGUE_IDS.

    Terminates on a cycle: a malformed chain that points back at itself is bounded by the
    set of ids already seen, not by a hop limit.
    """
    if not current_league_id:
        raise ValueError(
            "no current league id: set SLEEPER_LEAGUE_ID (F37: league ids are env-only)")

    known = {str(k): str(v) for k, v in (known or {}).items() if v}
    out, lid, seen = [], str(current_league_id), set()
    while lid and lid not in seen:
        seen.add(lid)
        info = fetch(lid) or {}
        season = str(info.get("season"))
        out.append((season, lid))
        nxt = info.get("previous_league_id")
        lid = str(nxt) if nxt else None

    from_chain = {season for season, _ in out}

    # A season the chain DID resolve, whose mapped id differs: the map is stale. Say so
    # and keep the live value.
    for season, lid_chain in out:
        mapped = known.get(season)
        if mapped and mapped != lid_chain:
            logging.warning(
                "LEAGUE CHAIN: season %s resolves to a different league than the "
                "environment says (chain wins; the SLEEPER_LEAGUE_ID_%s variable looks "
                "stale). Using the chain's value.", season, season)

    # Seasons the chain could not reach -- the whole point of the map.
    extra = sorted((s for s in known if s not in from_chain), reverse=True)
    for season in extra:
        out.append((season, known[season]))
    if extra:
        logging.info(
            "LEAGUE CHAIN: %d season(s) came from KNOWN_LEAGUE_IDS rather than "
            "previous_league_id (%s). This league's chain is broken at 2025.",
            len(extra), ", ".join(extra))
    return out

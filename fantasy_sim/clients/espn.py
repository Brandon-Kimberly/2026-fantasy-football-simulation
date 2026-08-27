"""
fantasy_sim.clients.espn

Fetches per-player projected fantasy points from a real, dedicated ESPN league via the
community-maintained `espn_api` package. See fetch_espn_projections' docstring for why this
replaced an earlier raw-HTTP approach against ESPN's undocumented generic API (three rounds of
live diagnostics found real, serious problems with that approach -- see the conversation
history this project was built from for the full account).
"""
from fantasy_sim.config import ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID, ESPN_BLEND_ELIGIBLE_POSITIONS


def normalize_player_name_for_matching(name):
    """
    Normalizes a player name for cross-source matching (Sleeper's name vs. ESPN's name for the
    same real person). Strips common suffixes, punctuation, and case so that e.g. "Michael
    Pittman Jr." and "Michael Pittman" match. Inherently imperfect -- a player whose name isn't
    normalized to the same string in both sources will simply not be matched, and that player
    falls back to Sleeper-only data. They are never dropped from the roster over this.
    """
    if not name:
        return ""
    n = name.lower().strip()
    for suffix in (' jr.', ' jr', ' sr.', ' sr', ' ii', ' iii', ' iv', ' v'):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
            break
    n = n.replace(".", "").replace("'", "").replace("-", " ")
    return " ".join(n.split())


def fetch_espn_projections(year, week):
    """
    Fetches per-player projected fantasy points from a real, dedicated ESPN league
    (ESPN_LEAGUE_ID) via the community-maintained `espn_api` package. See the config module for
    why this replaced an earlier raw-HTTP approach, and why it's restricted to
    ESPN_BLEND_ELIGIBLE_POSITIONS.

    Returns {normalized_name: projected_points_this_week}, or {} on any failure -- including
    espn_api not being installed at all (the import is attempted lazily, inside the try block,
    specifically so a missing dependency degrades the same way a network failure would, rather
    than crashing the whole sync). Callers must always be able to fall back to Sleeper-only data.
    """
    try:
        from espn_api.football import League
    except ImportError:
        return {}

    try:
        if ESPN_S2 and ESPN_SWID:
            league = League(league_id=ESPN_LEAGUE_ID, year=int(year), espn_s2=ESPN_S2, swid=ESPN_SWID)
        else:
            league = League(league_id=ESPN_LEAGUE_ID, year=int(year))
    except Exception:
        return {}

    all_players = []
    try:
        all_players.extend(league.free_agents(size=2000))
    except Exception:
        pass
    # Defensive extra coverage: also pull rostered players from each team, in case the dummy
    # league ever has anyone drafted/added (free_agents() only returns UNrostered players).
    # Wrapped separately so a failure here never loses the free_agents() results above.
    try:
        for team in league.teams:
            all_players.extend(team.roster)
    except Exception:
        pass

    projections = {}
    week_int = int(week)
    for p in all_players:
        try:
            pos = getattr(p, 'position', None)
            if pos not in ESPN_BLEND_ELIGIBLE_POSITIONS:
                continue
            name = getattr(p, 'name', None)
            if not name:
                continue
            stats = getattr(p, 'stats', None) or {}
            week_stats = stats.get(week_int)
            if not week_stats:
                continue
            proj = week_stats.get('projected_points')
            if proj is None or proj <= 0:
                continue
            key = normalize_player_name_for_matching(name)
            if key:
                # A player could appear in both free_agents() and a team roster in principle;
                # keep whichever value was found first rather than overwrite -- both come from
                # the same underlying league data, so this is de-duplication, not a conflict.
                projections.setdefault(key, float(proj))
        except Exception:
            continue

    return projections

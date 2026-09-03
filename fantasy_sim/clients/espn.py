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


# F29 (docs/AUDIT_PLAN.md): ESPN's projected_breakdown carries raw stat lines for every
# position, which dissolves the points-level K/IDP exclusion AT THE STAT LEVEL: a stat
# line can be scored under this league's own settings exactly. The maps below carry the
# cross-source identification performed on 748 name-matched IDP projections (2026-09-02):
#   - 10 named keys map directly; 'defensiveFumbles' = fumble recoveries per ESPN's own
#     scoring metadata (id 96, "FR -- Each Fumble Recovered").
#   - unnamed id '100' = QB HITS (slope +0.97 vs Sleeper's projected idp_qb_hit at matched
#     scale; positional fingerprint DE 0.55 > DT 0.27 > LB 0.14 >> S/CB ~0).
#   - unnamed id '112' ("STF -- Stuffs") is TFL-FAMILY BUT NARROWER (slope 0.88 at ~40%
#     lower level: run stuffs, not all TFL) -- deliberately NOT mapped; scoring it as
#     idp_tkl_loss would build a systematic shortfall into the disagreement signal.
#   - unnamed ids '110'/'111' are unreliable (110 integer-valued display-stat-shaped,
#     111 correlates with nothing) -- do not score from them.
ESPN_IDP_BREAKDOWN_MAP = {
    'idp_tkl_solo': 'defensiveSoloTackles',
    'idp_tkl_ast': 'defensiveAssistedTackles',
    'idp_sack': 'defensiveSacks',
    'idp_int': 'defensiveInterceptions',
    'idp_pass_def': 'defensivePassesDefensed',
    'idp_ff': 'defensiveForcedFumbles',
    'idp_fum_rec': 'defensiveFumbles',
    'idp_def_td': 'defensiveTouchdowns',
    'idp_safe': 'defensiveSafeties',
    'idp_blk_kick': 'defensiveBlockedKicks',
    'idp_qb_hit': '100',
}
# ESPN raw position -> the engine slot whose sub-score formula applies.
ESPN_SUBSCORE_POSITIONS = {'K': 'K', 'DE': 'DL', 'DT': 'DL', 'LB': 'LB', 'CB': 'DB', 'S': 'DB'}


def espn_idp_subscore(breakdown, league_scoring_settings):
    """Scores an ESPN projected_breakdown under THIS league's settings on the shared
    category subset (see ESPN_IDP_BREAKDOWN_MAP). Sleeper's side of the same subset is
    sync._shared_subscore -- the two must stay in lockstep or the disagreement signal
    becomes a systematic artifact."""
    total = 0.0
    for league_key, espn_key in ESPN_IDP_BREAKDOWN_MAP.items():
        mult = league_scoring_settings.get(league_key)
        if not mult:
            continue
        v = breakdown.get(espn_key)
        if v is None and espn_key.isdigit():
            v = breakdown.get(int(espn_key))
        if v:
            total += float(v) * float(mult)
    return total


def espn_k_subscore(breakdown, league_scoring_settings):
    """K shared sub-score: made/missed FG and XP totals only. The league's per-yard
    fgm_yds_over_30 bonus is EXCLUDED on both sides -- ESPN projects distance bands, not
    yards, and a within-band yardage distribution would be an invented constant (F29)."""
    s = league_scoring_settings
    return (float(breakdown.get('madeFieldGoals', 0.0) or 0.0) * float(s.get('fgm', 0.0) or 0.0)
            + float(breakdown.get('madeExtraPoints', 0.0) or 0.0) * float(s.get('xpm', 0.0) or 0.0)
            + float(breakdown.get('missedFieldGoals', 0.0) or 0.0) * float(s.get('fgmiss', 0.0) or 0.0)
            + float(breakdown.get('missedExtraPoints', 0.0) or 0.0) * float(s.get('xpmiss', 0.0) or 0.0))


def fetch_espn_projections(year, week):
    """Back-compatible wrapper: the points dict only (see fetch_espn_projection_data)."""
    return fetch_espn_projection_data(year, week, None)[0]


def fetch_espn_projection_data(year, week, league_scoring_settings=None):
    """
    One fetch, two channels, from the dedicated ESPN league (ESPN_LEAGUE_ID) via the
    community-maintained `espn_api` package. See the config module for why this replaced an
    earlier raw-HTTP approach.

    Returns (projections, subscores):
      projections -- {normalized_name: projected_points_this_week} for
        ESPN_BLEND_ELIGIBLE_POSITIONS only (the points-level mean blend, unchanged: points
        under ESPN's scoring are comparable for offense because the dummy league mirrors it,
        and NOT comparable for K/IDP).
      subscores -- {normalized_name: shared-subset sub-score} for K/IDP positions, computed
        from projected_breakdown under THIS league's settings (F29) -- empty unless
        league_scoring_settings is provided. Feeds the epistemic disagreement signal only,
        never the mean.

    Both are {} on any failure -- including espn_api not being installed at all (the import
    is attempted lazily, inside the try block, specifically so a missing dependency degrades
    the same way a network failure would, rather than crashing the whole sync). Callers must
    always be able to fall back to Sleeper-only data.
    """
    try:
        from espn_api.football import League
    except ImportError:
        return {}, {}

    try:
        if ESPN_S2 and ESPN_SWID:
            league = League(league_id=ESPN_LEAGUE_ID, year=int(year), espn_s2=ESPN_S2, swid=ESPN_SWID)
        else:
            league = League(league_id=ESPN_LEAGUE_ID, year=int(year))
    except Exception:
        return {}, {}

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
    subscores = {}
    week_int = int(week)
    for p in all_players:
        try:
            pos = getattr(p, 'position', None)
            name = getattr(p, 'name', None)
            if not name:
                continue
            stats = getattr(p, 'stats', None) or {}
            week_stats = stats.get(week_int)
            if not week_stats:
                continue
            key = normalize_player_name_for_matching(name)
            if not key:
                continue
            if pos in ESPN_BLEND_ELIGIBLE_POSITIONS:
                proj = week_stats.get('projected_points')
                if proj is None or proj <= 0:
                    continue
                # A player could appear in both free_agents() and a team roster in principle;
                # keep whichever value was found first rather than overwrite -- both come from
                # the same underlying league data, so this is de-duplication, not a conflict.
                projections.setdefault(key, float(proj))
            elif league_scoring_settings and pos in ESPN_SUBSCORE_POSITIONS:
                breakdown = week_stats.get('projected_breakdown') or {}
                if not breakdown:
                    continue
                slot = ESPN_SUBSCORE_POSITIONS[pos]
                sub = (espn_k_subscore(breakdown, league_scoring_settings) if slot == 'K'
                       else espn_idp_subscore(breakdown, league_scoring_settings))
                if sub > 0:
                    subscores.setdefault(key, round(sub, 2))
        except Exception:
            continue

    return projections, subscores

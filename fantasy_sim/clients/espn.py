"""
fantasy_sim.clients.espn

Fetches per-player projected fantasy points from a real, dedicated ESPN league via the
community-maintained `espn_api` package. See fetch_espn_projections' docstring for why this
replaced an earlier raw-HTTP approach against ESPN's undocumented generic API (three rounds of
live diagnostics found real, serious problems with that approach -- see the conversation
history this project was built from for the full account).
"""
import logging

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
        league = _espn_league(year)
    except ImportError:
        # F57: espn_api is IN requirements.txt. Missing it at sync time is a real
        # degradation (the whole blend dies), not a configuration choice -- the three
        # tests that skip without it are a different situation from a live sync.
        logging.warning("ESPN: espn_api is not installed, so no ESPN projections this sync. "
                        "The mean blend, the source_disagreement epistemic signal and F29's "
                        "K/IDP subscore channel all fall back to Sleeper-only. "
                        "py -3.10 -m pip install -r requirements.txt")
        return {}, {}
    except Exception as ex:
        # F57 (was a silent `return {}, {}`). An auth failure or a renamed league id used
        # to be indistinguishable from "ESPN had nothing to say", which is exactly how F52
        # survived a fortnight.
        # The league id is deliberately NOT in this message. F37 made league ids env-only,
        # and this warning lands in the sync manifest's `degraded` list -- which
        # make_sample_report scans as forbidden content. Relying on that downstream leak
        # check to scrub a value this line chose to emit is backwards; there is exactly
        # one ESPN league, so naming it adds nothing an operator does not already know.
        logging.warning("ESPN: could not open the projection league for %s (%s). Falling "
                        "back to Sleeper-only projections for every player this sync. "
                        "Check ESPN_LEAGUE_ID / ESPN_S2 / ESPN_SWID.", year, ex)
        return {}, {}

    all_players = []
    week_int = int(week)
    try:
        # F52: the week MUST be passed. espn_api scopes the payload to the scoring period
        # it is asked for, and with week=None it uses the league's current_week -- which is
        # 0 for this deliberately inactive dummy league. That returned weeks [0, 1], so
        # stats.get(1) worked and every later week silently found nothing.
        all_players.extend(league.free_agents(week=week_int, size=2000))
    except Exception as ex:
        # F57. THE F52 SITE. free_agents() is where essentially every ESPN row comes from
        # -- the dummy league has nobody rostered -- so a failure here empties the blend.
        # It used to `pass` in silence.
        logging.warning("ESPN: free_agents(week=%d) failed (%s). This is the call that "
                        "supplies essentially every ESPN row; the blend is Sleeper-only "
                        "this sync.", week_int, ex)
    # Defensive extra coverage: also pull rostered players from each team, in case the dummy
    # league ever has anyone drafted/added (free_agents() only returns UNrostered players).
    # Wrapped separately so a failure here never loses the free_agents() results above.
    #
    # F57 reviewed this as one of B6's six silent fallbacks and deliberately left it SILENT,
    # the only one of the six: the ESPN league is a dedicated dummy with an empty draft, so
    # this loop's expected yield is ZERO players and its failure costs nothing measurable.
    # Warning here would fire on a path whose success and failure are indistinguishable by
    # construction -- the cry-wolf shape F41 was filed for. The free_agents() call above is
    # the one that matters, and it is loud. If the dummy league is ever drafted, revisit.
    try:
        for team in league.teams:
            all_players.extend(team.roster)
    except Exception:
        pass

    projections = {}
    subscores = {}
    unreadable = 0          # F57: counted, then reported ONCE (B6's trap: ~2000 players)
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
            unreadable += 1
            continue

    # F57. One aggregated line, never one per player. A handful of malformed entries is
    # routine ESPN noise; ALL of them is the blend silently dying, and the two used to look
    # identical from the manifest.
    if unreadable:
        logging.warning("ESPN: %d of %d player entries were unreadable and skipped "
                        "(%d projections, %d subscores kept).",
                        unreadable, len(all_players), len(projections), len(subscores))
    return projections, subscores


def _espn_league(year):
    """Construct the espn_api League. A SEAM, extracted by F57: the construction used to be
    inline inside a bare `except Exception: return {}, {}`, which made the failure both
    silent and untestable. Raises ImportError when espn_api is absent (handled separately
    by the caller -- a missing dependency and a bad credential deserve different messages).
    """
    from espn_api.football import League
    if ESPN_S2 and ESPN_SWID:
        return League(league_id=ESPN_LEAGUE_ID, year=int(year), espn_s2=ESPN_S2, swid=ESPN_SWID)
    return League(league_id=ESPN_LEAGUE_ID, year=int(year))

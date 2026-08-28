"""
fantasy_sim.config

Every tunable constant and league-specific setting used across the sync pipeline, simulation
engine, and backtesting tools, in one place. Previously these were split across
2026_sleeper_sync.py and 2026_sleeper_simulation_adv.py, which made it easy to lose track of
which file "owned" a given constant (LEAGUE_AVG_PPG, for instance, was independently defined
in both files and had to be kept manually in sync by comment convention alone).

Values here are unchanged from their prior locations -- this is a structural move, not a
recalibration. Where a value's provenance or reasoning matters, that explanation is preserved
in the surrounding comment exactly as it was.
"""
import os

# ==============================================================================
# LEAGUE IDENTITY
# ==============================================================================
LEAGUE_ID = "1310010483033522176"
BASE_URL = "https://api.sleeper.app/v1"

TEAM_NAME_MAP = {
    "Borkug": "Legion of Coom",
    "connerjkimble": "Canton Killers",
    "Penguinator": "Wine Drinkers",
    "dolphinswarm": "Femboy Cats",
    "Clayylmao": "Clankers",
    "antobius": "The Glutton",
    "JTWald": "Drunk Cats",
    "jbodie7": "Year of Jarvis",
}

# ==============================================================================
# SECURITY -- credentials loaded from environment variables, never hardcoded
# ==============================================================================
# REQUIRED for correct in-season forecasts. Before the 2026-09-09 gate in
# sync.fetch_vegas_implied_totals the engine runs on WEEK_1_VERIFIED_VEGAS; after it, this key
# is the ONLY source of real lines. Without it every team gets a flat 21.5 total with no
# opponent: matchup effects, defensive-tier adjustments and the environment normaliser all
# degrade to a flat schedule. That state is now loud (sync warns, the engine refuses stale
# lines and says so -- see AUDIT_PHASE_3_FINDINGS.md finding 1) but it is not correct; the
# key is the fix. Free tier at https://the-odds-api.com covers one sync per week comfortably.
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")

# Dedicated, real ESPN Fantasy Football league (created specifically for this integration, with
# scoring settings manually configured to match this Sleeper league as closely as ESPN's UI
# allows) accessed via the community-maintained `espn_api` package (pip install espn_api).
#
# This REPLACES an earlier approach that hand-built raw HTTP requests against ESPN's
# undocumented generic API. Three rounds of live diagnostics against that approach found real,
# serious problems: the generic /players endpoint returns stats as raw numeric category IDs
# with no precomputed point total (hand-decoding proved unreliable -- a guessed mapping only
# partially matched a real player's actual numbers), and even the /leaguedefaults endpoint that
# did return a usable precomputed total was hard-capped at 50 players with no reliable way found
# to expand it (an attempted filter header was confirmed to make ESPN reject the request outright
# with a 400). Verified live against this real league: `league.free_agents(size=2000)` returns
# up to 2000 real players, and each player's `.stats[week]['projected_points']` is a per-week
# point total ESPN computes DIRECTLY under this league's actual configured scoring rules -- no
# raw stat-ID decoding needed at all, and no 50-player ceiling.
#
# ESPN_S2 / ESPN_SWID are only needed if the league is private (log into fantasy.espn.com, open
# browser DevTools -> Application/Storage -> Cookies -> fantasy.espn.com, copy the "espn_s2" and
# "SWID" cookie values). Verified live: this specific league connects fine with neither set, so
# leave both blank unless/until the league is made private.
ESPN_LEAGUE_ID = 798378381
ESPN_S2 = os.getenv("ESPN_S2", "")
ESPN_SWID = os.getenv("ESPN_SWID", "")
# Kicker and IDP scoring categories could not be matched exactly between Sleeper and ESPN's
# platforms (confirmed manually by the user configuring this league) -- comparing point totals
# computed under genuinely different rules would corrupt the disagreement-driven epistemic
# uncertainty signal rather than inform it. ESPN blending is therefore intentionally restricted
# to these positions; K/DL/LB/DB/FLEX always fall back to Sleeper-only, exactly as if ESPN had
# no data for those positions at all.
ESPN_BLEND_ELIGIBLE_POSITIONS = {"QB", "RB", "WR", "TE"}

# ==============================================================================
# NFL REFERENCE DATA
# ==============================================================================
NFL_TEAM_ABBREVIATIONS = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LAR",
    "Las Vegas Raiders": "LV", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "Seattle Seahawks": "SEA", "San Francisco 49ers": "SF", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}

NFL_TEAMS = [
    'ARI', 'ATL', 'BAL', 'BUF', 'CAR', 'CHI', 'CIN', 'CLE', 'DAL', 'DEN', 'DET',
    'GB', 'HOU', 'IND', 'JAX', 'KC', 'LAC', 'LAR', 'LV', 'MIA', 'MIN', 'NE',
    'NO', 'NYG', 'NYJ', 'PHI', 'PIT', 'SEA', 'SF', 'TB', 'TEN', 'WAS'
]

OUTDOOR_STADIUMS = {
    "BAL": (39.278, -76.623), "BUF": (42.774, -78.787), "CAR": (35.226, -80.853),
    "CHI": (41.862, -87.617), "CIN": (39.095, -84.516), "CLE": (41.506, -81.700),
    "DEN": (39.744, -105.020), "GB": (44.501, -88.062), "JAX": (30.324, -81.637),
    "KC": (39.049, -94.484), "MIA": (25.958, -80.239), "NE": (42.091, -71.264),
    "NYG": (40.812, -74.077), "NYJ": (40.812, -74.077), "PHI": (39.901, -75.167),
    "PIT": (40.447, -80.016), "SEA": (47.595, -122.332), "SF": (37.403, -121.970),
    "TB": (27.976, -82.503), "TEN": (36.166, -86.771), "WAS": (38.908, -76.864)
}

WEEK_1_VERIFIED_VEGAS = {
    "SEA": {"total": 24.75, "spread": -4.0, "opponent": "NE", "wind_mph": 0.0, "precip_prob": 0.0},
    "NE": {"total": 20.75, "spread": 4.0, "opponent": "SEA", "wind_mph": 0.0, "precip_prob": 0.0},
    "LAR": {"total": 26.0, "spread": -3.5, "opponent": "SF", "wind_mph": 0.0, "precip_prob": 0.0},
    "SF": {"total": 22.5, "spread": 3.5, "opponent": "LAR", "wind_mph": 0.0, "precip_prob": 0.0},
    "TEN": {"total": 20.75, "spread": -3.0, "opponent": "NYJ", "wind_mph": 0.0, "precip_prob": 0.0},
    "NYJ": {"total": 17.75, "spread": 3.0, "opponent": "TEN", "wind_mph": 0.0, "precip_prob": 0.0},
    "BUF": {"total": 23.0, "spread": -1.5, "opponent": "HOU", "wind_mph": 0.0, "precip_prob": 0.0},
    "HOU": {"total": 21.5, "spread": 1.5, "opponent": "BUF", "wind_mph": 0.0, "precip_prob": 0.0},
    "CHI": {"total": 23.5, "spread": -2.5, "opponent": "CAR", "wind_mph": 0.0, "precip_prob": 0.0},
    "CAR": {"total": 21.0, "spread": 2.5, "opponent": "CHI", "wind_mph": 0.0, "precip_prob": 0.0},
    "BAL": {"total": 26.5, "spread": -3.5, "opponent": "IND", "wind_mph": 0.0, "precip_prob": 0.0},
    "IND": {"total": 23.0, "spread": 3.5, "opponent": "BAL", "wind_mph": 0.0, "precip_prob": 0.0},
    "DET": {"total": 28.25, "spread": -7.0, "opponent": "NO", "wind_mph": 0.0, "precip_prob": 0.0},
    "NO": {"total": 21.25, "spread": 7.0, "opponent": "DET", "wind_mph": 0.0, "precip_prob": 0.0},
    "JAX": {"total": 24.0, "spread": -7.5, "opponent": "CLE", "wind_mph": 0.0, "precip_prob": 0.0},
    "CLE": {"total": 16.5, "spread": 7.5, "opponent": "JAX", "wind_mph": 0.0, "precip_prob": 0.0},
    "PIT": {"total": 22.75, "spread": -3.0, "opponent": "ATL", "wind_mph": 0.0, "precip_prob": 0.0},
    "ATL": {"total": 19.75, "spread": 3.0, "opponent": "PIT", "wind_mph": 0.0, "precip_prob": 0.0},
    "CIN": {"total": 27.0, "spread": -3.5, "opponent": "TB", "wind_mph": 0.0, "precip_prob": 0.0},
    "TB": {"total": 23.5, "spread": 3.5, "opponent": "CIN", "wind_mph": 0.0, "precip_prob": 0.0},
    "PHI": {"total": 25.75, "spread": -4.5, "opponent": "WAS", "wind_mph": 0.0, "precip_prob": 0.0},
    "WAS": {"total": 21.25, "spread": 4.5, "opponent": "PHI", "wind_mph": 0.0, "precip_prob": 0.0},
    "GB": {"total": 23.0, "spread": -1.5, "opponent": "MIN", "wind_mph": 0.0, "precip_prob": 0.0},
    "MIN": {"total": 21.5, "spread": 1.5, "opponent": "GB", "wind_mph": 0.0, "precip_prob": 0.0},
    "LAC": {"total": 28.5, "spread": -11.5, "opponent": "ARI", "wind_mph": 0.0, "precip_prob": 0.0},
    "ARI": {"total": 17.0, "spread": 11.5, "opponent": "LAC", "wind_mph": 0.0, "precip_prob": 0.0},
    "LV": {"total": 22.25, "spread": -3.0, "opponent": "MIA", "wind_mph": 0.0, "precip_prob": 0.0},
    "MIA": {"total": 19.25, "spread": 3.0, "opponent": "LV", "wind_mph": 0.0, "precip_prob": 0.0},
    "DAL": {"total": 25.5, "spread": -2.5, "opponent": "NYG", "wind_mph": 0.0, "precip_prob": 0.0},
    "NYG": {"total": 23.0, "spread": 2.5, "opponent": "DAL", "wind_mph": 0.0, "precip_prob": 0.0},
    "KC": {"total": 22.5, "spread": -2.5, "opponent": "DEN", "wind_mph": 0.0, "precip_prob": 0.0},
    "DEN": {"total": 20.0, "spread": 2.5, "opponent": "KC", "wind_mph": 0.0, "precip_prob": 0.0},
    "FA": {"total": 20.0, "spread": 0.0, "opponent": "FA", "wind_mph": 0.0, "precip_prob": 0.0},
}

DEFAULT_FALLBACK_TOTALS = {team: {"total": 21.5, "spread": 0.0, "wind_mph": 0.0, "precip_prob": 0.0, "opponent": "FA"} for team in NFL_TEAM_ABBREVIATIONS.values()}
DEFAULT_FALLBACK_TOTALS["FA"] = {"total": 20.0, "spread": 0.0, "wind_mph": 0.0, "precip_prob": 0.0, "opponent": "FA"}

LEAGUE_AVG_PPG = 21.5

# ==============================================================================
# DEFENSIVE MODEL
# ==============================================================================
DEF_RATING_SHRINKAGE_N0 = 4.0  # same "trust N games of prior" shrinkage strength used for player
                                 # baselines in the simulation engine, for statistical consistency

# Manually-sourced preseason defensive strength prior, keyed by team abbreviation, value = a
# projected points-allowed-per-game estimate for that defense heading into the season (lower =
# better/stingier defense). Used as the SHRINKAGE PRIOR in generate_defensive_ratings -- i.e.
# before any real games are played, a team's estimate starts here (not a flat league average
# for everyone), and as real games accumulate, the same empirical-Bayes shrinkage mechanism
# used elsewhere in this codebase (n_0=4.0 "games" of trust) smoothly pulls each team's
# estimate toward what's actually happening on the field. A defense that looked great on paper
# but is actually getting torched will correctly drift toward the empirical reality after a
# handful of real games; it is never permanently locked to the preseason take.
#
# Sourced from a preseason team-defense fantasy projection table (season-long "PA" = points
# allowed, converted to a per-game estimate via PA / 17 regular-season games). Any team left
# out of this dict falls back to the flat LEAGUE_AVG_PPG prior.
PRESEASON_DEFENSIVE_PRIOR = {
    "SEA": 18.47, "DEN": 18.76, "HOU": 18.95, "PHI": 19.04, "PIT": 20.38, "LAR": 20.41,
    "KC": 20.45, "BAL": 20.91, "NE": 21.14, "MIN": 21.21, "LAC": 21.52, "GB": 21.52,
    "DET": 21.98, "CLE": 22.75, "JAX": 22.75, "CHI": 22.91, "TB": 23.01, "BUF": 23.08,
    "NO": 23.11, "SF": 23.12, "IND": 23.45, "CAR": 23.63, "CIN": 24.22, "NYG": 24.63,
    "ATL": 24.66, "NYJ": 25.42, "DAL": 25.74, "WAS": 25.79, "LV": 25.91, "MIA": 26.46,
    "TEN": 27.28, "ARI": 27.41,
}

# ==============================================================================
# PLAYER BASELINE MODEL -- see player_level_backtest.py for how these are calibrated
# against real historical player data, and the conversation history for the calibration
# rounds these current values were derived from.
# ==============================================================================
VOLATILITY_CONSTANTS = {'QB': 1.65, 'RB': 1.98, 'WR': 1.8, 'TE': 2.0, 'K': 1.57, 'DL': 1.5, 'LB': 1.5, 'DB': 1.5}
EPISTEMIC_ERROR_RATES = {
    'QB': 0.30, 'RB': 0.63, 'WR': 0.55, 'TE': 0.50,
    'K': 0.40, 'DL': 0.15, 'LB': 0.15, 'DB': 0.15, 'FLEX': 0.18
}
BASE_STREAMER_MEANS = {'QB': 14.0, 'RB': 9.0, 'WR': 9.0, 'TE': 7.5, 'K': 8.0, 'DL': 7.5, 'LB': 8.0, 'DB': 8.0, 'FLEX': 8.5}

# ==============================================================================
# SEASON STRUCTURE
# ==============================================================================
# Weeks 1..14 are the regular season; 15 and 16 are the playoff semi-final and final. This is
# the league's actual format, and it is what generate_league_schedule pulls matchups for.
#
# Consolidated here from backtest_season.py, which defined it locally. It is now read by the
# simulation engine too, and a season length that disagrees between the engine and the backtest
# would be exactly the class of drift this module's docstring describes for LEAGUE_AVG_PPG.
#
# Anything normalising a per-week rate must divide by the weeks a given run actually simulated
# -- REGULAR_SEASON_WEEKS - (current_week - 1) -- not by this constant. A mid-season run only
# simulates the remainder of the season, and using the full length as the divisor deflates every
# rate by weeks_simulated/14 (see AUDIT_PHASE_1_FINDINGS.md, findings 1-3).
REGULAR_SEASON_WEEKS = 14

# ==============================================================================
# POSITIONS
# ==============================================================================
def normalize_position(raw_pos):
    """Sleeper's raw position (DE, DT, NT, CB, S, FS, SS, FB, ...) -> the engine's slot
    position (DL, DB, RB, ...). Lives here, not in simulation.py, because sync must apply the
    SAME mapping before looking up VOLATILITY_CONSTANTS / EPISTEMIC_ERROR_RATES: those are
    keyed by slot position, and looking them up by the raw string handed every DE/DT/CB/S/FB
    the anonymous default (k=1.5, rate 0.18) -- Phase 3 finding 3."""
    pos = str(raw_pos).upper().strip()
    if pos in ['RB', 'FB']: return 'RB'
    if pos in ['WR']: return 'WR'
    if pos in ['TE']: return 'TE'
    if pos in ['QB']: return 'QB'
    if pos in ['K']: return 'K'
    if pos in ['DL', 'DE', 'DT', 'NT']: return 'DL'
    if pos in ['LB', 'ILB', 'OLB', 'MLB']: return 'LB'
    if pos in ['DB', 'CB', 'FS', 'SS', 'S']: return 'DB'
    return 'FLEX'


# ==============================================================================
# ROSTER FORMAT
# ==============================================================================
# The full set of 13 required starting slots for this league's roster format, expanded to one
# entry per individual slot (e.g. two 'RB' entries for the two RB slots). Used by
# _solve_optimal_assignment for the true optimal bipartite matching between players and slots.
REQUIRED_STARTING_SLOTS = ['DB', 'DL', 'LB', 'TE', 'QB', 'K', 'RB', 'RB', 'WR', 'WR', 'FLEX', 'FLEX', 'FLEX']

DUAL_ELIGIBILITY = {
    "Travis Hunter": ["WR", "DB"], "T.J. Watt": ["LB", "DL"], "Micah Parsons": ["LB", "DL"],
    "Maxx Crosby": ["DL", "LB"], "Brian Burns": ["DL", "LB"], "Danielle Hunter": ["DL", "LB"],
    "Josh Hines-Allen": ["DL", "LB"], "Khalil Mack": ["DL", "LB"]
}

# ==============================================================================
# MANAGER BEHAVIOR PRIORS -- see the conversation history for why these are deliberately
# excluded from the data-driven calibration process applied to the constants above (risk of
# the optimizer using unrealistic manager-behavior values to compensate for errors elsewhere
# in the model, plus insufficient per-manager sample size to calibrate reliably from data).
# ==============================================================================
MANAGER_PROFILES = {
    'Legion of Coom': {'faab_agg': 0.15, 'trade_will': 0.05, 'style': 'The Fortress'},
    'Femboy Cats': {'faab_agg': 0.85, 'trade_will': 0.85, 'style': 'High-risk trader'},
    'Year of Jarvis': {'faab_agg': 0.80, 'trade_will': 0.80, 'style': 'Rule exploiter'},
    'Drunk Cats': {'faab_agg': 0.70, 'trade_will': 0.60, 'style': 'Measured active'},
    'The Glutton': {'faab_agg': 0.50, 'trade_will': 0.40, 'style': 'Average'},
    'Canton Killers': {'faab_agg': 0.40, 'trade_will': 0.30, 'style': 'Casual'},
    'Clankers': {'faab_agg': 0.15, 'trade_will': 0.10, 'style': 'Passive / Autopilot'},
    'Wine Drinkers': {'faab_agg': 0.10, 'trade_will': 0.05, 'style': 'Autodraft'},
}

# ==============================================================================
# SIMULATION ENGINE PARAMETERS
# ==============================================================================
SIM_CONFIG = {
    "NUM_BATCHES": 10,
    "SIMS_PER_BATCH": 1000,
    "STREAMER_DECAY_RATE": 0.85,
    # Defaults to True, matching the current league's real hybrid H2H + weekly-median-beat
    # scoring format (2 decisions awarded per team per week). Set to False when backtesting a
    # historical season that only used plain head-to-head scoring (no median bonus win) --
    # e.g. this league's own 2025 season, confirmed by the user directly. Leaving this at its
    # default never changes any current-season behavior; it exists specifically so a
    # historical season with a genuinely different scoring FORMAT (not just different player
    # values) can be simulated under the rules that actually applied, rather than silently
    # awarding real historical teams up to 2x the deciding wins they could have actually earned.
    "MEDIAN_SCORING_ENABLED": True,
    # Not previously present: no ceiling existed on an individual player's simulated weekly
    # score. Verified this was a real gap, not a hypothetical one -- 2M simulated realistic
    # QB-week draws produced 0.74% exceeding 50 points and a max of 128.8. Team-level
    # aggregates (what actually drives wins/playoff odds) turned out to be much better
    # behaved -- summing a 13-man lineup gave a team-level mean exactly matching the sum of
    # calibrated player means, with the 99.9th percentile only ~1.6x the mean, versus ~4x at
    # the individual level -- so this was never distorting headline outputs the way an
    # unbounded tail naively suggests. Still worth a real ceiling regardless: cheap, safe, and
    # removes a genuine absurdity. Set well above the actual real-NFL fantasy record across any
    # position (Jamaal Charles, 59.5 PPR pts, RB, 2013; Tyreek Hill, 57.9, WR, 2020; Josh
    # Allen, 51.9, QB, 2024) so it only ever clips the truly-unrealistic extreme tail, never
    # the legitimate right-skew the variance calibration was built to capture.
    "MAX_REALISTIC_WEEKLY_SCORE": 80.0,
    # Recalibrated against real NFL injury data (previous values were unjustified guesses --
    # see the conversation history for the initial critique that prompted this). Sourced from
    # two "percent of players missing at least one game per season" studies, converted to an
    # implied weekly onset rate via 1-(1-p)^(1/17): a more recent RB-specific study (2017-2024,
    # footballguys.com, 73% of RBs miss >=1 game/season -> implies ~0.074/week) and an older,
    # broader study (2002-2018, LinkedIn/Football-Reference-style methodology, ~50% of RBs and
    # ~50% of WRs miss >=1 game/season -> implies ~0.040/week for each). These two RB estimates
    # disagree meaningfully (0.074 vs 0.040) -- different eras, samples, and methodology, not a
    # single precise ground truth -- so RB below is weighted toward the more recent estimate
    # but pulled somewhat conservative rather than taken at face value.
    #
    # TE/QB/DL/LB/DB do NOT have the same directly-applicable position-specific modern data
    # behind them; they're nudged upward from their prior (undercalibrated-looking) values
    # toward a real "league-wide average" anchor (~0.028-0.041/week across two studies,
    # ~0.041/week directly reported by ProFootballLogic's 2015 game-by-game analysis), while
    # preserving their existing RELATIVE ordering, which has qualitative (not precisely
    # quantified) support in the literature -- e.g. TE and DB show elevated injury/concussion
    # exposure specifically. This is a real, acknowledged limitation: these five values are
    # less rigorously sourced than RB/WR, and would benefit from dedicated position-specific
    # research as a follow-up, not treated as equally well-verified.
    #
    # K is left unchanged -- no data found suggesting the existing very-low rate is wrong, and
    # kicker durability relative to every other position is well-established and uncontroversial.
    "INJURY_RATES": {
        'RB': 0.070, 'WR': 0.040, 'TE': 0.035, 'QB': 0.025,
        'DL': 0.025, 'LB': 0.025, 'DB': 0.020, 'K': 0.005
    },
    # Injury DURATION model (given an onset event, from INJURY_RATES, has occurred -- i.e.
    # already conditioned on "this causes at least one missed game", matching how
    # INJURY_RATES itself was calibrated against real "% of players missing >=1 game/season"
    # data). Previously a single Exponential(scale=2.5), which is unimodal and memoryless --
    # structurally incapable of representing the real, well-documented pattern of "most
    # injuries are brief, but a distinct minority are genuinely season-altering". Real data:
    # ProFootballLogic's 2015 game-by-game analysis found 64% of missed-time injuries result
    # in <=2 games missed, but the OVERALL mean is 3.1 games -- "much higher than the median
    # due to the skewed nature of the data" (their words), direct confirmation of the bimodal
    # shape a single exponential can't produce. INJURY_SEVERE_PROBABILITY is anchored to two
    # independent real sources that landed close together: the NFLPA's 2010 injury report
    # (13% of injuries required an IR placement) and a 2016-2021 NFL neck-injury study (7.8%
    # season-ending + 4.5% career-ending = 12.3% of neck injuries). INJURY_TYPICAL_DURATION_SCALE
    # and INJURY_SEVERE_DURATION_SCALE were then numerically solved (see the conversation
    # history for the exact solve) so the resulting two-component mixture reproduces both real
    # target moments above (64% <=2 games, 3.1-game overall mean) as closely as possible given
    # the anchored severe-injury probability.
    "INJURY_SEVERE_PROBABILITY": 0.125,
    "INJURY_TYPICAL_DURATION_SCALE": 1.66,
    "INJURY_SEVERE_DURATION_SCALE": 12.3,
    # Vacated-volume redistribution: when a player at one of these positions is injured, some
    # of their production flows to a healthy teammate at the SAME position on the SAME real
    # NFL team, modeling real target/touch redistribution rather than assuming vacated
    # opportunity simply vanishes. Originally RB-only; extended to WR and TE here as separate,
    # position-siloed pools (a WR injury only boosts other WRs, a TE injury only boosts other
    # TEs) rather than a shared WR/TE pool -- real redistribution clearly does sometimes cross
    # between WR and TE (and even to RBs receiving work, per real examples like the 2021 Colts
    # backfield absorbing volume after WR injuries), but the SPLIT of how much crosses each
    # boundary isn't precisely quantifiable from available data, so this stays conservatively
    # scoped to same-position redistribution only, an honest limitation rather than a modeled
    # guess.
    #
    # VACATED_VOLUME_CAPTURE_RATE (0.65, applied to all three positions) is NOT as precisely
    # grounded as the injury-rate or duration-mixture constants above -- no clean aggregate
    # statistic was found for "what fraction of a departed player's production a teammate
    # captures." It carries over the RB value unchanged (itself not independently derived from
    # data) for consistency rather than a fresh derivation. Real, concrete anecdotal support it
    # isn't zero or trivial: George Pickens' target share rose from 15.3% to 24.3% (a real,
    # documented jump) after CeeDee Lamb's 2025 injury. Treat this constant as directionally
    # reasonable, not precisely calibrated -- a good candidate for tightening if better
    # aggregate redistribution data turns up.
    "VACATED_VOLUME_CAPTURE_RATE": 0.65,
    "VACATED_VOLUME_ELIGIBLE_POSITIONS": ['RB', 'WR', 'TE'],
    "CORRELATIONS": {
        "QB_WR1": 0.4, "QB_WR2": 0.315, "QB_TE": 0.35, "QB_RB": 0, "WR_WR": -0.004
    },
    # DEFENSIVE_RANKS removed: this used to be a static, hand-typed list of "top" and "bottom"
    # pass/rush defenses that never updated all season. It's now sourced live from
    # nfl_defensive_tiers.json (see FantasySimulationEngine.defensive_tiers), which is derived
    # from real completed-game points-allowed data in the sync pipeline. See
    # generate_defensive_ratings()'s docstring for why this is now a single overall defensive
    # signal (TOP_DEFENSE / BOTTOM_DEFENSE) rather than a separate pass/rush split -- the old
    # hardcoded lists were never actually built from a real pass/rush-specific data source
    # either, so this trades an illusory distinction for a real, if coarser, one.
    # Hand-typed baselines for rostered players Sleeper publishes no projection for. The
    # `team` field MUST match Sleeper's record for the player: the engine reads it for the
    # real-NFL position group (vacated-volume apportionment) and the pass-catcher ranking
    # (QB correlation), so a wrong team silently drops the player from both. It is checked
    # against the roster at engine start (a mismatch logs a WARNING) and against the player
    # cache by tests/test_ingestion.py.
    #   Jordyn Tyson: WR, NO (Sleeper pid 13281, rookie, depth_chart_order 3). Sleeper's week-1
    #   projection entry exists but carries an empty stats block, so sync drops him. mean 6.5 is
    #   UNVERIFIED (carried over); team was 'FA' until Phase 3 finding 6 corrected it to NO.
    "KNOWN_MISSING_ASSETS": {
        "Jordyn Tyson": {"mean": 6.5, "std_aleatoric": 3.0, "std_epistemic": 1.17, "pos": "WR", "team": "NO", "bye": 0}
    }
}
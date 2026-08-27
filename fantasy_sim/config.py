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
    "INJURY_RATES": {
        'RB': 0.055, 'WR': 0.035, 'TE': 0.030, 'QB': 0.020,
        'DL': 0.020, 'LB': 0.020, 'DB': 0.015, 'K': 0.005
    },
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
    "KNOWN_MISSING_ASSETS": {
        "Jordyn Tyson": {"mean": 6.5, "std_aleatoric": 3.0, "std_epistemic": 1.17, "pos": "WR", "team": "FA", "bye": 0}
    }
}

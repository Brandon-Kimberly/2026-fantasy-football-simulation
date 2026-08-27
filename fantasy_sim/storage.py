"""
fantasy_sim.storage

Every file this project reads or writes, in one place, plus small typed load/save helpers used
consistently everywhere instead of ad-hoc open()/json.load()/json.dump() calls scattered
through the codebase.

Previously, filenames like "league_state.json", "live_rosters.json", "weekly_actuals.json",
and "league_standings.json" were used as bare string literals at each call site -- never given
named constants at all -- while others (VEGAS_FILE, BASELINES_FILE, etc.) were constants but
defined only in 2026_sleeper_sync.py, leaving 2026_sleeper_simulation_adv.py and the
backtesting scripts to duplicate the literal filename independently. A typo in any one of
those duplicated literals would fail silently at runtime with no import-time signal. Every
filename this project touches -- inputs from the sync pipeline, outputs from the simulation
engine, backtesting artifacts -- is named exactly once here.

All paths resolve under DATA_DIR, keeping the repository root clean of runtime output (JSON
results, PNG charts, logs) -- that directory is gitignored.
"""
import json
import os

DATA_DIR = "data"


def _path(filename):
    return os.path.join(DATA_DIR, filename)


def ensure_data_dir():
    """Creates DATA_DIR if it doesn't already exist. Called once at the start of any
    entrypoint that writes output, so callers never need their own os.makedirs() calls."""
    os.makedirs(DATA_DIR, exist_ok=True)


# ==============================================================================
# Sync pipeline inputs/outputs (written by fantasy_sim.sync, read by fantasy_sim.simulation)
# ==============================================================================
PLAYER_CACHE_FILE = _path("sleeper_players_cache.json")
VEGAS_FILE = _path("vegas_totals.json")
BASELINES_FILE = _path("player_baselines.json")
TEAM_RATINGS_FILE = _path("nfl_team_power_ratings.json")
LEAGUE_SCHEDULE_FILE = _path("league_schedule.json")
NFL_SCHEDULE_FILE = _path("nfl_schedule.json")
DEFENSIVE_RATINGS_FILE = _path("nfl_defensive_ratings.json")
DEFENSIVE_TIERS_FILE = _path("nfl_defensive_tiers.json")
LEAGUE_STATE_FILE = _path("league_state.json")
LIVE_ROSTERS_FILE = _path("live_rosters.json")
LEAGUE_STANDINGS_FILE = _path("league_standings.json")
WEEKLY_ACTUALS_FILE = _path("weekly_actuals.json")

# ==============================================================================
# Simulation engine outputs (week-parameterized -- one set per week the sim is run for)
# ==============================================================================
def live_season_forecast_path(week):
    return _path(f"live_season_forecast_week_{week}.json")


def model_learning_report_path(week):
    return _path(f"model_learning_report_week_{week}.json")


def syndicate_insights_path(week):
    return _path(f"syndicate_insights_week_{week}.json")


def syndicate_comprehensive_matrix_path(week):
    return _path(f"syndicate_comprehensive_matrix_week_{week}.json")


SIMULATION_AUDIT_LOG_FILE = _path("simulation_audit_log_sim0.json")
SYNDICATE_WARNINGS_LOG_FILE = _path("syndicate_warnings.log")


def power_rankings_chart_path(week):
    return _path(f"Week_{week}_Power_Rankings.png")


def season_outcomes_chart_path(week):
    return _path(f"Week_{week}_Season_Outcomes.png")


def all_teams_trajectories_chart_path(week):
    return _path(f"Week_{week}_All_Teams_Trajectories.png")


def expected_wins_chart_path(week):
    return _path(f"Week_{week}_Expected_Wins.png")


def h2h_heatmap_chart_path(week):
    return _path(f"Week_{week}_H2H_Heatmap.png")


# ==============================================================================
# JSON I/O helpers
# ==============================================================================
def load_json(path):
    """Reads and parses a JSON file. Raises FileNotFoundError with a clear message pointing at
    the sync entrypoint if the file doesn't exist -- this is the exact behavior previously
    embedded directly in FantasySimulationEngine's module scope."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing required file: '{path}'. Run `python -m fantasy_sim.sync` first.")
    with open(path, 'r') as f:
        return json.load(f)


def save_json(path, data, indent=2):
    """Writes data as JSON to path, creating DATA_DIR first if needed."""
    ensure_data_dir()
    with open(path, 'w') as f:
        json.dump(data, f, indent=indent)

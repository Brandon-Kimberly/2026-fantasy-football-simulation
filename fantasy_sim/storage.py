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

All paths resolve under DATA_DIR.

DIRECTORY LAYOUT (season-long retention -- AUDIT_PLAN.md, "data/ directory structure"):
    data/current/   -- sync's snapshot of the world as of the LAST sync. Always overwritten in
                        place; never historical. (Two exceptions live here despite their
                        "log"-sounding names: SIMULATION_AUDIT_LOG_FILE and
                        SYNDICATE_WARNINGS_LOG_FILE are both opened in overwrite mode --
                        verified by reading their write sites, not assumed from the filename --
                        so they behave exactly like the rest of this bucket, not like logs/.
                        Flagged as a pre-existing retention gap, not fixed here: making them
                        genuinely per-week would mean threading `week` through simulation.py's
                        own call sites, the same category of change as the positional-tiers fix
                        below, but on code this session didn't write.)
    data/logs/      -- genuinely append-only, season-spanning. Currently just
                        PROJECTION_LOG_FILE, the one file this project tracks in git.
    data/weeks/week_NN/ -- one directory per simulated week (zero-padded so `week_02` sorts
                        before `week_10` in a plain file listing), holding every artifact a
                        weekly run produces: the engine's exports, its charts, and
                        fantasy_sim.positional_tiers's report/charts/table.

Every filename this project touches -- inputs from the sync pipeline, outputs from the
simulation engine, backtesting artifacts -- is named exactly once here, instead of ad-hoc
open()/json.load()/json.dump() calls or bare string literals scattered through the codebase.

BASENAME STABILITY: the four weekly JSON exports (live_season_forecast_path,
model_learning_report_path, syndicate_insights_path, syndicate_comprehensive_matrix_path) and
SIMULATION_AUDIT_LOG_FILE keep their pre-existing basenames (e.g. still
"live_season_forecast_week_3.json", now living under weeks/week_03/ instead of flat) even
though the week number is now redundant with the directory name. This is deliberate: golden
master's stage_b hash keys each save_json call by os.path.basename(path)
(tests/golden_master.py's capture_save), so renaming these five basenames would change stage_b
hashes and require its own regenerate-with-deltas cycle -- out of scope for a pure directory
migration. Everything else this module renames as part of the same move (PNG chart names, the
positional-tiers artifacts) is invisible to the golden master: charts are deliberately never
hashed, and positional_tiers.py's own save_json calls go through fantasy_sim.positional_tiers's
own imported name, never fantasy_sim.simulation.save_json, which is the only name the golden
master's sandbox patches -- so those basenames were free to simplify.

DIRECTORY CREATION: path CONSTRUCTION (_path/_current/_log/_week below) never touches the
filesystem -- it is pure string joining. Directories are created at WRITE time (ensure_dir_for,
called from save_json and every raw file writer), matching the pattern sync.py's
append_projection_log already used. This split matters because several module-level constants
here (PLAYER_CACHE_FILE etc.) are evaluated once, at import time, at whatever cwd is active
then -- but fantasy_sim.backtest_season chdirs into BACKTEST_WORKDIR and reuses these same
constants afterward to write there. Eagerly creating directories at import time would create
them next to the ORIGINAL cwd, not BACKTEST_WORKDIR, and the later real write would then fail.
Resolving the directory lazily, from the path string, at the moment of the actual write, is
correct regardless of any chdir in between.
"""
import json
import os

import matplotlib.pyplot as plt

DATA_DIR = "data"


def _path(*parts):
    return os.path.join(DATA_DIR, *parts)


def _current(filename):
    """A sync-input/current-state file: always overwritten, no season-long retention."""
    return _path("current", filename)


def _log(filename):
    """A genuinely append-only, season-spanning file."""
    return _path("logs", filename)


def _week_dir_name(week):
    return f"week_{int(week):02d}"


def _week(week, *parts):
    """A per-week artifact -- one directory per simulated week, so re-running in a later week
    never overwrites an earlier week's output (see module docstring). Pure string joining, like
    _path/_current/_log -- directory creation happens at WRITE time (ensure_dir_for), not here.

    An earlier version of this function created the directory eagerly, right here, reasoning
    that a plain plt.savefig(path) has no chance to create a missing directory itself the way
    save_json does. That reasoning was correct about the risk but wrong about the fix: it meant
    every PATH CONSTRUCTION touched the filesystem, including ones that never led to a write at
    all -- a golden-master or unit test that mocks matplotlib.pyplot.savefig still calls this
    function to build the path first, so running the suite alone left behind empty
    data/weeks/week_02, week_06, week_15 directories with nothing in them.

    The first fix attempt was ALSO wrong, caught before landing rather than assumed correct: it
    added an ensure_dir_for(path) call immediately before each plt.savefig(path, ...), mirroring
    save_json's internal call. That still touched disk under a mock, because
    @patch('matplotlib.pyplot.savefig') only replaces plt.savefig itself -- the ensure_dir_for
    call sitting one line above it in application code is untouched by that patch and still
    runs for real. save_json has no such gap because ensure_dir_for is called FROM INSIDE
    save_json, so patching save_json (as golden_master.py and every render test do) removes the
    directory-creation call too. The actual fix: save_chart (below) bundles ensure_dir_for with
    the real plt.savefig call the same way save_json bundles it with the real write, and every
    chart-producing call site + test mock was moved to use it instead of a bare plt.savefig."""
    return _path("weeks", _week_dir_name(week), *parts)


def ensure_dir_for(path):
    """Creates every directory in path's parent chain if it doesn't already exist. Called at
    WRITE time (not path-construction time -- see module docstring) by save_json and by every
    raw file writer that bypasses it (a logging.FileHandler, a raw open() for an HTML table)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)


# ==============================================================================
# Sync pipeline inputs/outputs (written by fantasy_sim.sync, read by fantasy_sim.simulation)
# ==============================================================================
PLAYER_CACHE_FILE = _current("sleeper_players_cache.json")
VEGAS_FILE = _current("vegas_totals.json")
BASELINES_FILE = _current("player_baselines.json")
TEAM_RATINGS_FILE = _current("nfl_team_power_ratings.json")
LEAGUE_SCHEDULE_FILE = _current("league_schedule.json")
NFL_SCHEDULE_FILE = _current("nfl_schedule.json")
DEFENSIVE_RATINGS_FILE = _current("nfl_defensive_ratings.json")
DEFENSIVE_TIERS_FILE = _current("nfl_defensive_tiers.json")
LEAGUE_STATE_FILE = _current("league_state.json")
LIVE_ROSTERS_FILE = _current("live_rosters.json")
LEAGUE_STANDINGS_FILE = _current("league_standings.json")
WEEKLY_ACTUALS_FILE = _current("weekly_actuals.json")
# F7 (AUDIT_PLAN.md): append-only log of the projections each sync used for rostered players,
# so next season's projection error -- what EPISTEMIC_ERROR_RATES actually is -- can be measured.
# Sleeper serves only the current week's projections; this file is the only record of them.
PROJECTION_LOG_FILE = _log("projection_log.jsonl")
# F3: Sleeper's winners bracket, resolved to team names at sync time (see sync.generate_playoff_bracket).
PLAYOFF_BRACKET_FILE = _current("playoff_bracket.json")

# ==============================================================================
# Simulation engine outputs (week-parameterized -- one set per week the sim is run for)
# ==============================================================================
def live_season_forecast_path(week):
    return _week(week, f"live_season_forecast_week_{week}.json")


def model_learning_report_path(week):
    return _week(week, f"model_learning_report_week_{week}.json")


def syndicate_insights_path(week):
    return _week(week, f"syndicate_insights_week_{week}.json")


def syndicate_comprehensive_matrix_path(week):
    return _week(week, f"syndicate_comprehensive_matrix_week_{week}.json")


# Ephemeral despite the name -- see module docstring. Lives in current/, not weeks/, because it
# carries no week-retention today; that would be a separate fix (thread `week` through
# simulation.py's own write site), not a rename.
SIMULATION_AUDIT_LOG_FILE = _current("simulation_audit_log_sim0.json")
SYNDICATE_WARNINGS_LOG_FILE = _current("syndicate_warnings.log")


def power_rankings_chart_path(week):
    return _week(week, "Power_Rankings.png")


def season_outcomes_chart_path(week):
    return _week(week, "Season_Outcomes.png")


def all_teams_trajectories_chart_path(week):
    return _week(week, "All_Teams_Trajectories.png")


def expected_wins_chart_path(week):
    return _week(week, "Expected_Wins.png")


def h2h_heatmap_chart_path(week):
    return _week(week, "H2H_Heatmap.png")


def seeding_distribution_path(week):
    return _week(week, "Seeding_Distribution.png")


def weekly_scoring_density_path(week):
    return _week(week, "Weekly_Scoring_Density.png")


# ==============================================================================
# Positional-tier report (fantasy_sim.positional_tiers) -- week-parameterized like the exports
# above and for the same reason: BASELINES_FILE is overwritten fresh by every sync with that
# week's projections, so a tier report derived from it is exactly as week-specific as
# live_season_forecast_path et al.
# ==============================================================================
def positional_tiers_report_path(week):
    return _week(week, "positional_tiers.json")


def tier_chart_path(position, week):
    return _week(week, "tiers", f"{position}.png")


def positional_tiers_table_path(position, week):
    return _week(week, "tiers", f"{position}_Table.html")


# ==============================================================================
# Strength-of-schedule report (fantasy_sim.strength_of_schedule) -- week-parameterized for the
# same reason as the positional-tier report: the underlying power/defensive ratings and
# nfl_schedule.json move week to week (real games played, updated empirical defense), so a
# report derived from them is exactly as week-specific as everything else under weeks/.
# ==============================================================================
def sos_report_path(week):
    return _week(week, "strength_of_schedule.json")


def sos_team_grid_chart_path(week):
    return _week(week, "Strength_of_Schedule_By_Team.png")


def sos_team_summary_chart_path(week):
    return _week(week, "Strength_of_Schedule_Team_Ranking.png")


def sos_roster_chart_path(week):
    return _week(week, "Strength_of_Schedule_By_Roster.png")


# ==============================================================================
# Player-variance report (fantasy_sim.player_variance -- boom/bust, floor/ceiling). Reads
# FantasySimulationEngine.player_weekly_scores, populated only during a real run_simulation()
# call, so this is generated alongside the engine's own weekly exports, not standalone like
# positional_tiers/strength_of_schedule (see that module's docstring).
# ==============================================================================
def player_variance_report_path(week):
    return _week(week, "player_variance.json")


def _safe_team_filename(fantasy_team):
    return fantasy_team.replace(' ', '_')


def boom_bust_chart_path(fantasy_team, week):
    return _week(week, "boom_bust", f"{_safe_team_filename(fantasy_team)}.png")


def floor_ceiling_chart_path(fantasy_team, week):
    return _week(week, "floor_ceiling", f"{_safe_team_filename(fantasy_team)}.png")


# ==============================================================================
# Win-trajectory chart (fantasy_sim.win_trajectory) -- re-visualizes
# expected_cumulative_wins_by_week, already exported inside syndicate_comprehensive_matrix_path
# for this same week, as one overlay chart across all teams instead of the engine's own
# per-team-faceted percentile-band chart. No new computation; week-parameterized only because
# its source file is.
# ==============================================================================
def win_trajectory_chart_path(week):
    return _week(week, "Win_Trajectory.png")


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
    """Writes data as JSON to path, creating its directory first if needed."""
    ensure_dir_for(path)
    with open(path, 'w') as f:
        json.dump(data, f, indent=indent)


def save_chart(path, **savefig_kwargs):
    """Saves the CURRENT matplotlib figure to path, creating its directory first -- the chart
    equivalent of save_json, and for the same reason: bundling ensure_dir_for with the real
    write means mocking this ONE function (by its module-qualified name, e.g.
    "fantasy_sim.simulation.save_chart", exactly how save_json is already mocked in
    golden_master.py) skips both the directory creation and the render together. Calling
    ensure_dir_for and matplotlib.pyplot.savefig as two separate statements at each chart's
    call site does NOT achieve this -- see _week()'s docstring for why that was tried first and
    didn't work. Chart-producing code should call this, never matplotlib.pyplot.savefig
    directly."""
    ensure_dir_for(path)
    plt.savefig(path, **savefig_kwargs)
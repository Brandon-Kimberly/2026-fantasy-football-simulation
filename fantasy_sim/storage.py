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
                        place; never historical. (One exception lives here despite its
                        "log"-sounding name: SYNDICATE_WARNINGS_LOG_FILE is a process-level
                        console mirror opened in overwrite mode at import time -- verified by
                        reading its write site, not assumed from the filename -- so it behaves
                        exactly like the rest of this bucket, not like logs/. The sim-0 audit
                        log used to be a second such exception until F10 moved it to weeks/.
                        Originally flagged as a pre-existing retention gap, not fixed: making them
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
model_learning_report_path, syndicate_insights_path, syndicate_comprehensive_matrix_path) keep
their pre-existing basenames, and simulation_audit_log_path (F10) follows the same
"_week_N" convention (e.g. still
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


def decisions_week_path(week, filename, canonical=False):
    """A weekly decision artifact (the seven tools' JSON, the weekly digests), F9's layout
    applied to decisions: canonical runs -- the scheduled Tuesday/Sunday reports, or a
    deliberate re-run after a real roster move, marked with --canonical -- live at
    data/decisions/week_NN/; everything else (exploratory, mid-week checks, development)
    defaults into week_NN/archive/. Intent is the CALLER'S flag: it is not inferrable from
    the artifact, so exploratory is the cheap default and canonical a deliberate act."""
    parts = ["decisions", _week_dir_name(week)] + ([] if canonical else ["archive"]) + [filename]
    return _path(*parts)


def decisions_season_path(filename):
    """A season-spanning one-off (draft review, season retrospective): data/decisions/season/."""
    return _path("decisions", "season", filename)


def decisions_adhoc_path(filename):
    """Ad-hoc tool output tied to a moment rather than a report run (compare_players,
    evaluate_move/evaluate_trade): data/decisions/adhoc/. The durable record of the moves
    themselves is the decision log, not these files."""
    return _path("decisions", "adhoc", filename)


def decisions_path(filename):
    """A decision-support output (fantasy_sim.decisions): one file per tool invocation under
    data/decisions/, timestamped by the caller. Never read by the engine or the season
    exports; ignored by git like the rest of data/."""
    return _path("decisions", filename)


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
# The decision log (2026-09-01): every completed league transaction (add/drop/waiver/trade),
# auto-ingested at sync from Sleeper's /transactions endpoint, append-only, deduped by
# transaction_id, with each involved player's model projection at ingestion time. Tracked in
# git for the same reason the projection log is: the projection snapshots cannot be
# reconstructed after the fact.
DECISION_LOG_FILE = _log("decision_log.jsonl")
# F15 ingestion row: one immutable document per season -- the league's real completed draft,
# picks resolved to team names at ingestion (sync.ingest_drafts; a file that exists is never
# rewritten). Tracked in git like the other logs: Sleeper ages drafts out, so the on-disk
# record is the historical source once that happens.
def draft_log_file(season):
    return _log(f"draft_{season}.json")


# Season-retrospective bundle: one immutable document per completed season -- league
# metadata (roster_positions, scoring-format settings), the roster map resolved to team
# names, final standings, and every week's matchups with per-player realized points
# (sync.ingest_season; a file that exists is never rewritten). Tracked in git like the
# other logs: Sleeper ages seasons out, after which this is the only record.
def season_log_file(season):
    return _log(f"season_{season}.json")


# The weekly prediction record F18/F19 read from (weekly_report.append_predictions_log):
# one JSON line per week -- the season-outcome table, the week's matchup win probabilities
# and P(>= median), commit hash and sync-manifest timestamps. Tracked in git, unlike
# data/weeks/, so the pre-season baseline survives a machine loss. Append-only; a re-run
# within a week appends again and consumers keep the last row per (season, week).
def predictions_log_file(season):
    return _log(f"predictions_{season}.jsonl")


# F3: Sleeper's winners bracket, resolved to team names at sync time (see sync.generate_playoff_bracket).
PLAYOFF_BRACKET_FILE = _current("playoff_bracket.json")

# Written LAST by sync.sync_all (weekly orchestrator, 2026-09-01): started_at / finished_at,
# season, current_week, every WARNING/ERROR logged during the run (`degraded`), and the mtimes
# of the sync outputs at finish. A manifest whose started_at matches a run exists iff that run
# completed -- the one-glance answer to "has sync run this week, and did it succeed"
# (scripts.check_freshness) and the orchestrator's gate before anything runs downstream.
SYNC_MANIFEST_FILE = _current("sync_manifest.json")
# PLAYER_CACHE_FILE is deliberately NOT here: clients.sleeper.update_player_cache refreshes it
# on a one-day TTL, so on most syncs it is legitimately older than the sync (its age is
# recorded in the manifest separately).
SYNC_OUTPUT_FILES = (
    VEGAS_FILE, BASELINES_FILE, TEAM_RATINGS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_STATE_FILE,
    LIVE_ROSTERS_FILE, LEAGUE_STANDINGS_FILE, WEEKLY_ACTUALS_FILE, PLAYOFF_BRACKET_FILE,
)

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


# F10 (2026-08-31): the sim-0 audit log is retained per week like the four weekly JSON exports
# above. It was SIMULATION_AUDIT_LOG_FILE = _current("simulation_audit_log_sim0.json") -- a
# single always-overwritten path -- until F10 threaded `week` through its one write site.
# Basename carries the week (redundant with the directory, deliberately -- see BASENAME
# STABILITY in the module docstring; golden master keys stage_b by basename).
def simulation_audit_log_path(week):
    return _week(week, f"simulation_audit_log_sim0_week_{week}.json")


# Process-level console mirror of the root logger -- NOT a per-run artefact and not a source
# of truth for any single run's warnings. Opened in overwrite mode at fantasy_sim.simulation
# import time, so it holds whatever PROCESS last imported that module (a test run overwrites
# the last real run's; run_sync never imports simulation, so sync's warnings never reach it).
# A run's own warnings are exported inside its per-week audit log (F10, commit 2).
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
#
# Encoding is explicit on both sides: Windows' locale default is cp1252, so without it
# the first non-ASCII player name in a UTF-8 file would decode as mojibake with no
# error raised. All current files are pure ASCII (json.dump's ensure_ascii), which is
# why adding this changed no bytes -- the goldens are the proof (2026-09-04).
# ==============================================================================
def load_json(path):
    """Reads and parses a JSON file. Raises FileNotFoundError with a clear message pointing at
    the sync entrypoint if the file doesn't exist -- this is the exact behavior previously
    embedded directly in FantasySimulationEngine's module scope."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing required file: '{path}'. Run `python -m fantasy_sim.sync` first.")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path, data, indent=2):
    """Writes data as JSON to path, creating its directory first if needed."""
    ensure_dir_for(path)
    with open(path, 'w', encoding='utf-8') as f:
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
"""
fantasy_sim.freshness

"Has sync run this week, and did it succeed?" in one glance (scripts.check_freshness), and
the orchestrator's gate (scripts.weekly_report). assess() is a pure function over what is read
from disk and, optionally, from Sleeper; the readers below do the I/O.

Verdicts:
  OK        a completed sync on record for the current NFL week, every sync output at least as
            new as that sync's start, Vegas stamped for the week, the week's simulation export
            newer than the sync.
  DEGRADED  everything above holds, but the sync tolerated failures (its `degraded` list is
            non-empty: ESPN blend, odds, weather, a schedule week ...). Usable, but say so.
  STALE     anything else -- and the reasons say exactly what. STALE outranks DEGRADED.

Derived from the F11 lesson: a partial failure must never look like success.
"""
import datetime as _dt
import os

from fantasy_sim.storage import (
    SYNC_MANIFEST_FILE, SYNC_OUTPUT_FILES, VEGAS_FILE, LEAGUE_STATE_FILE, load_json,
    syndicate_comprehensive_matrix_path,
)

OK, DEGRADED, STALE = "OK", "DEGRADED", "STALE"
EXIT_CODES = {OK: 0, DEGRADED: 2, STALE: 1}


def parse_stamp(text):
    """'YYYY-MM-DDTHH:MM:SSZ' -> epoch seconds (UTC); None when absent/malformed."""
    try:
        return _dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc).timestamp()
    except Exception:
        return None


def assess(manifest, sync_start, file_mtimes, vegas_week, export_mtime, nfl_week, check_export=True):
    """Pure. manifest: dict or None; sync_start: epoch seconds of manifest.started_at or None;
    file_mtimes: {basename: mtime or None} for the sync outputs; vegas_week: the week stamped in
    vegas_totals._meta (or None); export_mtime: mtime of the current week's simulation export
    (None if absent); nfl_week: Sleeper's current week, or None when not checked (--offline)."""
    reasons = []
    if not manifest or not manifest.get("ok") or sync_start is None:
        return STALE, ["no completed sync on record (no sync_manifest.json, or it is not ok) -- run scripts.run_sync"]
    week = manifest.get("current_week")
    for name, mtime in sorted(file_mtimes.items()):
        if mtime is None:
            reasons.append(f"partial: {name} is missing since the sync")
        elif mtime < sync_start - 1.0:
            reasons.append(f"partial: {name} is older than the sync (rewritten or never written)")
    if vegas_week != week:
        reasons.append(f"vegas_totals.json is stamped for week {vegas_week}, sync is for week {week}")
    # check_export=False: the F36 gate assesses the SYNC alone, after sync and before
    # the report -- at that moment the export ALWAYS predates the sync, and gating on it
    # would abort every unattended run by construction (found live, 2026-09-04).
    if check_export:
        if export_mtime is None:
            reasons.append(f"simulation export for week {week} is missing -- run scripts.run_simulation")
        elif export_mtime < sync_start:
            reasons.append(f"simulation export for week {week} predates the sync -- re-run scripts.run_simulation")
    if nfl_week is not None and nfl_week != week:
        reasons.append(f"week rolled: sync is for week {week}, Sleeper reports week {nfl_week} -- re-run the sync")
    degraded = list(manifest.get("degraded") or [])
    if reasons:
        # STALE outranks DEGRADED, but the tolerated failures are still worth seeing.
        return STALE, reasons + [f"degraded: {d}" for d in degraded]
    if degraded:
        return DEGRADED, degraded
    return OK, []


# ---------------------------------------------------------------------------- readers
def read_manifest():
    if not os.path.exists(SYNC_MANIFEST_FILE):
        return None, None
    m = load_json(SYNC_MANIFEST_FILE)
    return m, parse_stamp(m.get("started_at", ""))


def read_file_mtimes():
    return {os.path.basename(p): (os.path.getmtime(p) if os.path.exists(p) else None) for p in SYNC_OUTPUT_FILES}


def read_vegas_week():
    if not os.path.exists(VEGAS_FILE):
        return None
    return (load_json(VEGAS_FILE).get("_meta") or {}).get("week")


def read_export_mtime(week):
    p = syndicate_comprehensive_matrix_path(week)
    return os.path.getmtime(p) if os.path.exists(p) else None


def read_nfl_week(timeout=8):
    """Sleeper's current week, or None if unreachable (reported separately by the caller)."""
    import requests
    from fantasy_sim.config import BASE_URL
    try:
        state = requests.get(f"{BASE_URL}/state/nfl", timeout=timeout).json()
        return 1 if state.get("season_type") == "pre" else int(state.get("week", 1))
    except Exception:
        return None


def check(offline=False):
    """Read everything and assess. Returns (status, reasons, details)."""
    manifest, sync_start = read_manifest()
    week = (manifest or {}).get("current_week") or (load_json(LEAGUE_STATE_FILE).get("current_week", 1)
                                                    if os.path.exists(LEAGUE_STATE_FILE) else None)
    nfl_week = None if offline else read_nfl_week()
    status, reasons = assess(manifest, sync_start, read_file_mtimes(), read_vegas_week(),
                             read_export_mtime(week) if week else None, nfl_week)
    if not offline and nfl_week is None:
        reasons = list(reasons) + ["(Sleeper unreachable: week roll not checked)"]
    details = {"manifest": manifest, "week": week, "nfl_week": nfl_week, "offline": offline}
    return status, reasons, details


# ------------------------------------------------------------------- log-push state
def logs_git_state(porcelain, ahead_text):
    """Pure. The irreplaceable logs under data/logs are git-tracked so a machine loss
    (AUDIT_PLAN.md R1) cannot destroy them -- but only if appends actually get committed
    and pushed. porcelain: `git status --porcelain -- data/logs` output (modified tracked
    files AND new files matching the .gitignore exceptions, e.g. a new season's
    predictions log, both count). ahead_text: `git rev-list --count @{u}..HEAD --
    data/logs` output, or None when there is no upstream. Returns (sorted uncommitted
    paths, unpushed commit count or None for unknown)."""
    uncommitted = []
    for line in (porcelain or "").splitlines():
        if len(line) > 3 and line[:2].strip():
            uncommitted.append(line[3:].strip())
    try:
        ahead = int((ahead_text or "").strip()) if ahead_text is not None else None
    except ValueError:
        ahead = None
    return sorted(uncommitted), ahead


def read_logs_git_state():
    """Reader for logs_git_state. Never raises: git absent or not a repo reads as
    (unknown, unknown) rather than breaking a freshness check."""
    import subprocess

    def git(args):
        try:
            out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=15)
            return (out.stdout if out.returncode == 0 else None)
        except Exception:
            return None

    porcelain = git(["status", "--porcelain", "--", "data/logs"])
    ahead = git(["rev-list", "--count", "@{u}..HEAD", "--", "data/logs"])
    if porcelain is None:
        return None, None
    return logs_git_state(porcelain, ahead)

"""webui.sync -- syncing from the UI, behind three safeguards (docs/WEB_UI.md W4, reopened
2026-09-27 at the owner's request).

W4 recorded "sync from the UI: no" because of C3: a sync run with a dead ODDS_API_KEY
silently overwrote the real betting lines with the flat 21.5 fallback, and there was no
restore. The three things that made a terminal sync safe are now done here, in this
order, before anything is written:

1. PREFLIGHT -- the key is read from the Windows USER scope (the registry), not from
   this process's environment, which can hold a pre-rotation value for as long as the
   shell that started it lives (H5's lesson). It is probed against the-odds-api and the
   launch goes ahead only on `ok`. Rejected, absent, or unreachable all stop here; the
   `--allow-fallback` path stays a deliberate terminal act.
2. BACKUP -- every file in data/current/ is copied to data/local/webui/backups/<stamp>/
   (local, gitignored) with a manifest, so a sync that turns out wrong has a restore
   button. The ten newest backups are kept.
3. INJECTION -- the verified key is placed in the subprocess environment only; it never
   enters the job record, the log, a template, or this module's return values.

What sync touches, for the owner's caveat: data/current/ (the model's inputs, rewritten
by design -- that is what "sync" means) and the append-only season logs it ADDS rows to
(decision log transactions, first scores, designations, projection log + provenance).
It never rewrites a log row, never touches data/weeks/, data/decisions/ or the
predictions log, and the engine's own H5 check runs again inside the subprocess.

The probe logic is copied from fantasy_sim.sync.verify_odds_key rather than imported:
fantasy_sim.sync is one of the three modules the web process must never import.
"""
import datetime as _dt
import json
import os
import shutil
import sys

ODDS_PROBE_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
KEEP_BACKUPS = 10
MODES = {
    "sync": {"module": "scripts.run_sync", "label": "Sync rosters, standings, lines and projections",
             "what": "pulls live data into data/current/ and appends the new transactions to the decision log"},
    "refresh": {"module": "scripts.weekly_report", "label": "Sync, then refresh everything",
                "what": "sync, then the full run: simulation, charts, roster grades, lineup, matchup, waivers and a new digest (non-canonical, filed under archive)"},
}


def _read_user_scope_windows(name):
    """The value of `name` in the Windows User environment (HKCU\\Environment), or None."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            value, _kind = winreg.QueryValueEx(k, name)
            return str(value).strip() or None
    except OSError:
        return None


def user_scope_key(name="ODDS_API_KEY", read_user=None):
    """(key, source): the User-scope value on Windows when there is one, else this
    process's environment. `source` is what the page shows; the key never is."""
    reader = read_user if read_user is not None else _read_user_scope_windows
    v = reader(name) if sys.platform.startswith("win") or read_user is not None else None
    if v:
        return v, "the Windows User scope"
    v = (os.environ.get(name) or "").strip()
    return (v or None), ("this server's environment" if v else "nowhere")


def probe_key(key, fetch=None):
    """{verdict, detail, remaining, used} -- H5's verdicts, and NEVER the key."""
    if not (key or "").strip():
        return {"verdict": "absent", "detail": "no ODDS_API_KEY is configured; a sync now would write the flat 21.5 fallback "
                                               "over the real lines (C3). Set the key, or sync from a terminal with --allow-fallback on purpose.",
                "remaining": None, "used": None}
    getter = fetch
    if getter is None:
        import requests
        getter = requests.get
    try:
        resp = getter(ODDS_PROBE_URL, params={"apiKey": key, "regions": "us", "markets": "totals"}, timeout=15)
        status = getattr(resp, "status_code", None)
        headers = getattr(resp, "headers", {}) or {}
    except Exception as ex:
        return {"verdict": "unreachable", "detail": f"could not reach the-odds-api ({type(ex).__name__}); nothing was written. Try again in a minute.",
                "remaining": None, "used": None}
    remaining, used = headers.get("x-requests-remaining"), headers.get("x-requests-used")
    if status in (401, 403):
        return {"verdict": "rejected", "detail": f"the-odds-api REJECTED the key (HTTP {status}). That is not the API being down: the key "
                                                  "has been rotated or revoked. Nothing was written. Fix the key in the Windows User scope "
                                                  "(setx ODDS_API_KEY ...) and restart this server.", "remaining": remaining, "used": used}
    if status == 200:
        return {"verdict": "ok", "detail": "the key is accepted by the-odds-api", "remaining": remaining, "used": used}
    return {"verdict": "unreachable", "detail": f"the-odds-api answered HTTP {status}; nothing was written. Try again in a minute.",
            "remaining": remaining, "used": used}


# ---------------------------------------------------------------------------- backups
def backups_dir(root):
    return os.path.join(root.local, "webui", "backups")


def backup(root, reason="before sync"):
    """Copy every file directly under data/current/ into a stamped backup folder; return
    its name. Prunes to the newest KEEP_BACKUPS afterwards."""
    src = os.path.join(root.data, "current")
    # microseconds in the name: names sort chronologically even for two backups in one
    # second, and pruning the oldest can never free a name a newer backup then reuses
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    name = f"current_{stamp}"
    n = 2
    while os.path.exists(os.path.join(backups_dir(root), name)):     # belt and braces: never overwrite
        name = f"current_{stamp}-{n:02d}"
        n += 1
    dst = os.path.join(backups_dir(root), name)
    os.makedirs(dst, exist_ok=True)
    files = []
    if os.path.isdir(src):
        for fn in sorted(os.listdir(src)):
            p = os.path.join(src, fn)
            if os.path.isfile(p):
                shutil.copy2(p, os.path.join(dst, fn))
                files.append({"name": fn, "size": os.path.getsize(p)})
    week = None
    try:
        with open(os.path.join(src, "sync_manifest.json"), encoding="utf-8") as fh:
            week = (json.load(fh) or {}).get("current_week")
    except (OSError, ValueError):
        pass
    with open(os.path.join(dst, "backup.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": name, "taken_at": stamp, "reason": reason, "week": week, "files": files,
                   "bytes": sum(f["size"] for f in files)}, fh, indent=1)
    prune(root)
    return name


def list_backups(root):
    out = []
    base = backups_dir(root)
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base), reverse=True):
        meta_p = os.path.join(base, name, "backup.json")
        try:
            with open(meta_p, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            continue
    return out


def prune(root, keep=KEEP_BACKUPS):
    names = [b["name"] for b in list_backups(root)]
    for name in names[keep:]:
        shutil.rmtree(os.path.join(backups_dir(root), name), ignore_errors=True)


def restore(root, name):
    """Copy a backup's files back over data/current/. Returns how many files came back.
    The caller must refuse this while a job is running."""
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError("bad backup name")
    src = os.path.join(backups_dir(root), name)
    if not os.path.isfile(os.path.join(src, "backup.json")):
        raise FileNotFoundError(name)
    dst = os.path.join(root.data, "current")
    os.makedirs(dst, exist_ok=True)
    n = 0
    for fn in sorted(os.listdir(src)):
        if fn == "backup.json":
            continue
        shutil.copy2(os.path.join(src, fn), os.path.join(dst, fn))
        n += 1
    return n


def argv_for(mode, python=None):
    """The command a mode runs. `refresh` is the weekly report WITHOUT --skip-sync -- the
    repository's primary entry point, exactly as a hand run -- so it syncs first."""
    if mode not in MODES:
        raise KeyError(mode)
    return [python or sys.executable, "-m", MODES[mode]["module"]]

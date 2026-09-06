#!/usr/bin/env python3
"""Localize downloaded runner reports to REAL team names -- the owner's private copies
(F37 follow-up, 2026-09-06).

  py -3.10 -m scripts.localize_reports --fetch      # the weekly one-liner: download
                                                    # every new successful canonical-run
                                                    # artifact via gh, file by week,
                                                    # localize everything
  py -3.10 -m scripts.localize_reports              # localize data/results/ in place
  py -3.10 -m scripts.localize_reports data/results/week_03

Workflow: download a canonical run's artifact zip from the Actions run page, drop it
(zip or extracted) into data/results/week_NN/, run this. Every .html and .md under the
target is rewritten fictional -> real and gains a visible PRIVATE COPY banner; dropped
artifact zips are extracted in place first. Idempotent: run it as often as you like.

Privacy design, in the same pattern as scripts.migrate_identity: this script is
committed MECHANICS ONLY. The real names come from a live Sleeper fetch (league id from
the environment -- current names, tracking mid-season renames) with the owner's
untracked data/local/identity_map.json as offline fallback; no identity ever appears in
the repository. The target must live under data/ (blanket-gitignored, never part of the
Pages artifact); anything else -- docs/, the repo root -- is refused hard, because
docs/ deploys to the public site. Chart images keep their baked-in fictional labels
(re-rendering them would need a local engine run); every table, header, and log line
converts.
"""
import json
import os
import sys
import zipfile

BANNER_MARKER = "PRIVATE COPY -- real team names"
_BANNER_HTML = ('<div style="background:#7a2e2e;color:#fff;padding:.5em .8em;'
                f'font-weight:bold">{BANNER_MARKER}; do not share or publish.</div>')
_BANNER_MD = f"> **{BANNER_MARKER}; do not share or publish.**\n\n"


def refuse_unsafe_root(root):
    """Hard-refuses any target outside data/. docs/ is the Pages publish root and the
    repo root is committable; real-name copies belong only where the blanket data/*
    gitignore makes accidental publication structurally impossible."""
    data = os.path.realpath("data")
    target = os.path.realpath(root)
    if not (target == data or target.startswith(data + os.sep)):
        raise SystemExit(f"REFUSED: {root} is outside data/ -- localized reports carry "
                         "real names and must never sit anywhere publishable.")


def localize_text(s, mapping, kind):
    for fict, real in mapping.items():
        s = s.replace(fict, real)
    if BANNER_MARKER in s:
        return s
    if kind == "html":
        i = s.find("<h1")
        if i >= 0:
            s = s[:i] + _BANNER_HTML + s[i:]
        else:
            s = _BANNER_HTML + s
    else:
        s = _BANNER_MD + s
    return s


def extract_zips(root):
    """Extracts each dropped artifact zip into a sibling directory named after it;
    a zip whose directory already exists is skipped (already extracted)."""
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(".zip"):
                continue
            dest = os.path.join(dirpath, name[:-4])
            if os.path.isdir(dest):
                continue
            with zipfile.ZipFile(os.path.join(dirpath, name)) as z:
                z.extractall(dest)
            print(f"  extracted {os.path.join(dirpath, name)}")


def real_mapping():
    """{fictional: real}, live-first. Live uses each manager's CURRENT Sleeper team
    name (metadata team_name, else display name); the untracked local identity map is
    the offline fallback (its real names are the 2026-08 labels, which may lag a
    mid-season rename -- stale-but-real is stated, not hidden)."""
    try:
        import requests
        from fantasy_sim.config import BASE_URL, LEAGUE_ID, TEAM_NAME_MAP
        if not LEAGUE_ID:
            raise RuntimeError("SLEEPER_LEAGUE_ID not set")
        users = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/users", timeout=10).json() or []
        rosters = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/rosters", timeout=10).json() or []
        by_user = {u["user_id"]: (u.get("metadata") or {}).get("team_name")
                   or u.get("display_name", "?") for u in users}
        out = {}
        for r in rosters:
            fict = TEAM_NAME_MAP.get(str(r.get("roster_id")))
            if fict:
                out[fict] = by_user.get(r.get("owner_id"), "?")
        if len(out) == 8:
            return out, "live"
        raise RuntimeError(f"only {len(out)} teams resolved")
    except Exception as ex:
        path = os.path.join("data", "local", "identity_map.json")
        with open(path, encoding="utf-8") as f:
            ident = json.load(f)
        inv = {fict: real for real, fict in ident["real_to_fictional"].items()}
        print(f"[NOTE] live fetch unavailable ({ex}); using the local map's 2026-08 "
              "names, which may lag a mid-season rename.")
        return inv, "local map"


def missing_runs(run_ids, existing_dirnames):
    """Pure: run ids whose artifact directory is not already on disk."""
    return [r for r in run_ids if f"weekly-report-{r}" not in existing_dirnames]


def file_artifact(src_dir, root, name):
    """Files a downloaded artifact into data/results/: each contained week_NN goes to
    root/week_NN/<name>/ (keeping the artifact's own layout inside); anything with no
    week directory lands under root/unsorted/<name>/."""
    import shutil
    weeks = [d for d in os.listdir(src_dir)
             if d.startswith("week_") and os.path.isdir(os.path.join(src_dir, d))]
    if weeks:
        for wk in weeks:
            dest = os.path.join(root, wk, name, wk)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(os.path.join(src_dir, wk), dest)
        # anything left over (loose files next to the week dirs) rides along
        leftovers = os.listdir(src_dir)
        if leftovers:
            dest = os.path.join(root, weeks[0], name)
            for item in leftovers:
                shutil.move(os.path.join(src_dir, item), os.path.join(dest, item))
    else:
        dest = os.path.join(root, "unsorted", name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src_dir, dest)


def _run_gh(args):
    """gh seam: (returncode, stdout). Finds gh on PATH or at the default Windows
    install location; a missing gh reads as a failure, never an exception."""
    import shutil
    import subprocess
    exe = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=300)
        return out.returncode, out.stdout
    except Exception as ex:
        return 1, str(ex)


def fetch_artifacts(root, gh=_run_gh):
    """Downloads every SUCCESSFUL canonical-run's report artifact not already under
    root, filed by week. Failed runs are deliberately excluded (their artifacts exist
    for debugging via the remediation issue, not for the archive). Fetch problems warn
    and return; localizing what is already on disk must never be blocked by GitHub
    being unreachable."""
    import shutil
    import tempfile
    rc, out = gh(["run", "list", "--workflow", "canonical-run", "--status", "success",
                  "--limit", "50", "--json", "databaseId",
                  "--jq", ".[].databaseId"])
    if rc != 0:
        print(f"[NOTE] artifact fetch unavailable (gh: {out.strip() or 'failed'}); "
              "localizing what is already on disk.")
        return 0
    run_ids = [x.strip() for x in out.splitlines() if x.strip()]
    existing = {d for _dp, dirs, _f in os.walk(root) for d in dirs}
    todo = missing_runs(run_ids, existing)
    fetched = 0
    for rid in todo:
        name = f"weekly-report-{rid}"
        tmp = tempfile.mkdtemp(prefix="fetch_")
        rc, out = gh(["run", "download", rid, "--name", name, "--dir", tmp])
        if rc != 0:
            print(f"[NOTE] run {rid}: no downloadable artifact ({out.strip() or 'skipped'})")
            shutil.rmtree(tmp, ignore_errors=True)
            continue
        file_artifact(tmp, root, name)
        shutil.rmtree(tmp, ignore_errors=True)
        fetched += 1
        print(f"  fetched {name}")
    print(f"{fetched} artifact(s) fetched ({len(run_ids) - len(todo)} already present)")
    return fetched


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    fetch = "--fetch" in argv
    argv = [a for a in argv if a != "--fetch"]
    root = argv[0] if argv else os.path.join("data", "results")
    refuse_unsafe_root(root)
    if fetch:
        os.makedirs(root, exist_ok=True)
        fetch_artifacts(root)
    if not os.path.isdir(root):
        os.makedirs(root, exist_ok=True)
        print(f"created empty {root} -- drop downloaded artifact zips (or folders) "
              "into week_NN/ subfolders and re-run.")
        return 0
    mapping, source = real_mapping()
    extract_zips(root)
    n = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            kind = "html" if name.lower().endswith((".html", ".htm")) else (
                "md" if name.lower().endswith(".md") else None)
            if kind is None:
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as f:
                s = f.read()
            out = localize_text(s, mapping, kind)
            if out != s:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(out)
                n += 1
                print(f"  localized {path}")
    print(f"{n} file(s) localized (names: {source}); already-localized files untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

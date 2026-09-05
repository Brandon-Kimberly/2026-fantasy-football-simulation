#!/usr/bin/env python3
"""F37 (2026-09-05): the one-time league-identity migration, mechanics only.

  py -3.10 -m scripts.migrate_identity data/local/identity_map.json

Rewrites every git-tracked text file so the public repository carries ONLY the
pseudonymized league identity: real team names -> the fictional map (the same one the
sanitized sample has always shown), Sleeper usernames and owner user_ids -> the
fictional team names (both resolve to real identities through Sleeper's public API),
and the raw league IDs -> emptied in data files (consumers fall back to the
environment) or a placeholder token in prose.

The mapping itself deliberately lives OUTSIDE the repository (data/local/, gitignored):
committing a forbidden-strings list would re-publish exactly what it removes. This
script is committed so the migration is reviewable and re-runnable; its input is not.
Git history retains the pre-migration record -- by design, not oversight (this project
does not rewrite history; HEAD is the presentation, history is the record, F37).
"""
import json
import os
import subprocess
import sys

TEXT_EXT = {".py", ".md", ".yml", ".yaml", ".json", ".jsonl", ".txt", ".cff", ".toml",
            ".gitignore", ".gitattributes", ".ps1", ""}
SKIP_PREFIXES = ("data/local/", "scripts/migrate_identity.py")   # never rewrite itself: its own token literals match the prose pass


def build_replacements(ident):
    reps = []
    fict = ident["real_to_fictional"]
    # longest-first so no partial-name collision can misfire
    for real, f in sorted(fict.items(), key=lambda kv: -len(kv[0])):
        reps.append((real, f))
    # owner_id -> roster_id -> fictional name (for draft-log picked_by fields etc.)
    # identity map stores usernames keyed by owner_id and roster_ids keyed by roster_id.
    # usernames and owner ids both map to the fictional team of that manager; derive via
    # the owner_team map when supplied alongside
    ot_path = os.path.join(os.path.dirname(sys.argv[1]), "owner_team_map.json")
    with open(ot_path, encoding="utf-8") as f:
        owner_team = json.load(f)
    for oid, username in ident["usernames"].items():
        team = owner_team[oid]
        reps.append((oid, team))
        reps.append((username, team))
    for lid in (ident["league_id"], ident.get("league_id_2025", ""), ident["espn_league_id"]):
        if lid:
            reps.append((lid, "LEAGUE_ID_TOKEN"))
    return reps


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        raise SystemExit("usage: migrate_identity <identity_map.json>")
    with open(argv[0], encoding="utf-8") as f:
        ident = json.load(f)
    reps = build_replacements(ident)

    files = subprocess.check_output(["git", "ls-files"]).decode().splitlines()
    changed = 0
    for rel in files:
        if any(rel.startswith(p) for p in SKIP_PREFIXES):
            continue
        ext = os.path.splitext(rel)[1].lower()
        if ext not in TEXT_EXT:
            continue
        try:
            with open(rel, encoding="utf-8") as f:
                s = f.read()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        orig = s
        for old, new in reps:
            s = s.replace(old, new)
        # league-id tokens: emptied in data files, placeholder in prose/code
        if rel.endswith((".json", ".jsonl")):
            s = s.replace('"LEAGUE_ID_TOKEN"', '""')
            s = s.replace("LEAGUE_ID_TOKEN", "")
        else:
            s = s.replace("LEAGUE_ID_TOKEN", "<league-id: env>")
        if s != orig:
            with open(rel, "w", encoding="utf-8", newline="") as f:
                f.write(s)
            changed += 1
            print(f"  rewrote {rel}")
    print(f"{changed} files rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())

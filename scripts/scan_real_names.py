#!/usr/bin/env python3
"""
Does any tracked file contain a real identity from this league? -- run before a push.

  py -3.10 -m scripts.scan_real_names              # scan every tracked text file
  py -3.10 -m scripts.scan_real_names --verbose    # also print the token counts
  py -3.10 -m scripts.scan_real_names --paths a.py b.md

The standing rule is absolute: no real team name and no real username in any file in this
repo, ever -- not in code, tests, fixtures, docs, or commit messages. Fictional names only.
This tool exists because a LITERAL-match scan on 2026-09-22 passed while four real-identity
strings were sitting in tracked files, and a tokenising scan the next day found all four:
a username built from a team name, a variable named after a team, one word of a team name
merged into a fictional one, and a manager `style` string equal to the first word of a real
team name. A literal scan cannot see any of those. This one is the tokenising scan, kept in
the repo instead of in a session transcript.

LOCAL ONLY, BY CONSTRUCTION.
  * refuses to run on GITHUB_ACTIONS -- it would have to fetch real names on a runner;
  * refuses unless SHOW_REAL_TEAM_NAMES is set, the same gate the digest's legend uses;
  * fetches the names live and holds them in memory only. It NEVER writes a file, never
    logs the fetched list, and never prints a whole name -- only the matched token, which
    is what a human needs to tell a leak from a coincidence;
  * contains no real name itself, which is why there is no committed stop-word list below:
    a list of "ordinary words to ignore" assembled from real team names would BE a partial
    leak. Adjudicated false positives go in a local, gitignored allowlist instead
    (--allow, default .real_name_scan_allow), one token per line.

FALSE POSITIVES ARE EXPECTED AND ARE NOT LEAKS. NFL player names are domain data: a
projections fixture holding a cornerback whose surname matches a token is a coincidence,
not an identity. That is why every hit prints its token and its line, and why the exit
status is a prompt to look rather than a verdict.

Exit 0 clean, 1 hits found, 2 refused (wrong environment, or names unavailable).
"""
import argparse
import os
import re
import subprocess
import sys

_SPLIT = re.compile(r"[^a-z0-9]+")
_BINARY_SNIFF = 8192


def _refuse(msg):
    print(f"scan_real_names: REFUSED -- {msg}", file=sys.stderr)
    return 2


def identity_tokens(names, min_len=5):
    """Lowercase search tokens for a list of display names and team names.

    Each name contributes its words, its words with a plural ending stripped, and the whole
    name with separators removed -- the last one catches a real name merged into a longer
    fictional string, which is how one of the four 2026-09-22 misses was written. Tokens
    shorter than `min_len` are dropped: below that they match ordinary English constantly
    and the output becomes noise nobody reads.
    """
    out = set()
    for raw in names:
        if not raw:
            continue
        low = str(raw).lower()
        words = [w for w in _SPLIT.split(low) if w]
        joined = "".join(words)
        for w in words + ([joined] if len(words) > 1 else []):
            if len(w) >= min_len:
                out.add(w)
            # ferrets -> ferret, walruses -> walrus. Stems are allowed one character
            # shorter than a raw token: a five-letter plural whose stem is four letters
            # is exactly the case a username is built from, and requiring min_len here
            # would drop it. Each ending is stripped only when it is actually THERE --
            # an earlier version applied both slices to anything ending in "s" and turned
            # a six-letter name into a four-letter fragment that matched half the repo.
            stems = ([w[:-2]] if w.endswith("es") else []) + ([w[:-1]] if w.endswith("s") else [])
            for stem in stems:
                if len(stem) >= min_len - 1:
                    out.add(stem)
    return out


def _is_binary(path):
    try:
        with open(path, "rb") as f:
            return b"\0" in f.read(_BINARY_SNIFF)
    except OSError:
        return True


def bounded(line, start, end):
    """Does `line[start:end]` sit at a word boundary, counting camelCase as one?

    This is the whole difference between a tool that gets run and one that gets ignored.
    A plain substring match over these tokens produced **13,313 hits** on this repo --
    `fall` inside `fallback`, `kill` inside `killed`, on every page of the audit -- and a
    report nobody reads protects nothing. A boundary is: the edge of the line, any
    non-letter (so `walrus_fan_99` and `"style": "quantum"` both hit), or a case change
    (so `NeonWalrusCats` hits). `fallback` has a letter on the right in the same case, so
    it does not.

    The alternative the backlog suggested -- a committed list of ordinary words to ignore
    -- was rejected: assembled from real team names, that list would itself be a partial
    leak of the thing this tool exists to keep out of the repo.
    """
    left_ok = start == 0 or not line[start - 1].isalpha() or line[start].isupper()
    right_ok = end >= len(line) or not line[end].isalpha() or line[end].isupper()
    return left_ok and right_ok


def scan_paths(tokens, paths, root="."):
    """[(path, lineno, token, line, strict)] for every token found in the given files.

    Case-insensitive substring match; `strict` says whether it sat at a word boundary.
    Both are returned because the caller decides: a strict hit fails the run, a loose one
    is a count you can ask for. Dropping loose matches entirely would lose the
    all-lowercase merge (`neonwalruscats`), which is a real shape.
    """
    hits = []
    if not tokens:
        return hits
    ordered = sorted(tokens)
    for rel in paths:
        full = os.path.join(root, rel)
        if not os.path.isfile(full) or _is_binary(full):
            continue
        try:
            with open(full, encoding="utf-8-sig", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    raw = line.rstrip("\n")
                    low = raw.lower()
                    for tok in ordered:
                        at = low.find(tok)
                        while at != -1:
                            hits.append((rel, i, tok, raw[:160],
                                         bounded(raw, at, at + len(tok))))
                            at = low.find(tok, at + 1)
        except OSError:
            continue
    return hits


def tracked_files(root="."):
    out = subprocess.check_output(["git", "-C", root, "ls-files", "-z"])
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def _load_allow(path):
    """Adjudicated false positives, one token per line. Local and gitignored: an allowlist
    that shipped with the repo would be the leak this tool exists to prevent."""
    try:
        with open(path, encoding="utf-8") as f:
            return {ln.strip().lower() for ln in f
                    if ln.strip() and not ln.lstrip().startswith("#")}
    except OSError:
        return set()


def fetch_names():
    """Every display name and team name in the league's renewal chain. Live, in memory.

    The chain matters: a manager who changed their team name still has the old one sitting
    in last season's league, and a file naming it is just as much a leak.
    """
    import requests
    from fantasy_sim.config import BASE_URL, LEAGUE_ID, KNOWN_LEAGUE_IDS
    from fantasy_sim.league_chain import resolve_chain

    def get(url):
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return r.json() or []

    ids = {str(LEAGUE_ID)}
    try:
        for _season, lid in resolve_chain(
                LEAGUE_ID, fetch=lambda x: get(f"{BASE_URL}/league/{x}"),
                known=KNOWN_LEAGUE_IDS):
            ids.add(str(lid))
    except Exception as ex:
        print(f"  note: renewal chain unavailable ({type(ex).__name__}); current league only")
    names = []
    for lid in sorted(ids):
        try:
            for u in get(f"{BASE_URL}/league/{lid}/users"):
                names.append(u.get("display_name"))
                names.append((u.get("metadata") or {}).get("team_name"))
        except Exception as ex:
            print(f"  note: league {lid} users unavailable ({type(ex).__name__})")
    return [n for n in names if n]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paths", nargs="*", default=None,
                    help="scan these paths instead of every tracked file")
    ap.add_argument("--allow", default=".real_name_scan_allow",
                    help="local gitignored allowlist of adjudicated false positives")
    ap.add_argument("--min-len", type=int, default=5)
    ap.add_argument("--loose", action="store_true",
                    help="also list mid-word matches (not counted as hits)")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)

    if os.environ.get("GITHUB_ACTIONS"):
        return _refuse("this is a LOCAL tool; it would have to fetch real names on a runner")
    from fantasy_sim.weekly_report import real_names_enabled
    if not real_names_enabled():
        return _refuse("SHOW_REAL_TEAM_NAMES is not set, so there is nothing to scan for. "
                       "Set it in this shell only; never in a committed file or a secret.")

    names = fetch_names()
    if not names:
        return _refuse("no names could be fetched; a scan against an empty list would "
                       "report CLEAN and mean nothing")
    tokens = identity_tokens(names, min_len=a.min_len) - _load_allow(a.allow)
    paths = a.paths if a.paths is not None else tracked_files()
    print(f"scan_real_names: {len(names)} names -> {len(tokens)} tokens, "
          f"{len(paths)} tracked paths")
    if a.verbose:
        print(f"  (token lengths {min(map(len, tokens))}-{max(map(len, tokens))}; "
              f"tokens are not printed unless they match)")

    hits = scan_paths(tokens, paths)
    strict = [h for h in hits if h[4]]
    loose = [h for h in hits if not h[4]]
    seen = set()
    deduped = [h for h in strict if not (h[:3] in seen or seen.add(h[:3]))]

    if loose:
        counts = {}
        for h in loose:
            counts[h[2]] = counts.get(h[2], 0) + 1
        top = ", ".join(f"{t}x{n}" for t, n in sorted(counts.items(), key=lambda kv: -kv[1])[:8])
        print(f"  {len(loose)} mid-word match(es) not counted as hits ({top}). "
              f"{'Listed below.' if a.loose else 'Pass --loose to see them.'}")

    if not deduped:
        print("CLEAN -- no tracked file contains a real identity token at a word boundary.")
    else:
        print(f"\n{len(deduped)} HIT(S). Each line shows the TOKEN that matched, so you can "
              f"tell a leak from a coincidence (NFL player names are domain data, not "
              f"identities):\n")
        for rel, ln, tok, line, _s in deduped:
            print(f"  {rel}:{ln}: [{tok}] {line}")
        print("\nIf a hit is a genuine coincidence, add its token to "
              f"{a.allow} (local, gitignored). If it is a leak, rewrite the file -- and "
              "check git history and commit messages for the same string.")
    if a.loose and loose:
        print("\n  mid-word matches:")
        for rel, ln, tok, line, _s in loose[:400]:
            print(f"    {rel}:{ln}: [{tok}] {line}")
    return 1 if deduped else 0


if __name__ == "__main__":
    raise SystemExit(main())

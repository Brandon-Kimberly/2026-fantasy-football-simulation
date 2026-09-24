#!/usr/bin/env python3
"""
Does running the test suite modify real synced data? Run it when you add a boundary test.

  py -3.10 -m scripts.check_test_isolation            # snapshot, run the suite, diff
  py -3.10 -m scripts.check_test_isolation --dir data/current --pattern "*.json"

WHY THIS EXISTS. A boundary test added on 2026-09-24 patched `requests.get` but not
`save_json`, and `sync.generate_league_schedule` WRITES `data/current/league_schedule.json`
as a side effect while returning the list of failed weeks. Running the suite replaced the
real fourteen-week schedule with a two-team, five-week fixture. It was caught by accident,
within minutes, and the file was recoverable by re-syncing -- but that is exactly the
shape of F11, where a defect silently truncated real data on every suite run and was found
only by accident. Once is an incident; twice is a pattern nobody is measuring.

The suite is hermetic BY DESIGN (`CLAUDE.md`: it needs none of the league identifiers), so
the correct result is always NONE. A single changed file is a real finding, not noise:
either a test is writing where it should be patching, or a library function has grown a
side effect its callers do not expect.

This is not a unit test. A test cannot observe what the whole suite did to the filesystem
while it is itself part of that suite, and a per-test fixture guard would have to be
remembered by the author of the next boundary test -- which is the thing that failed here.
It is a standalone check, like `run_behavior_check`, run deliberately.

Exit 0 clean, 1 if anything under the watched directory changed, 2 if the suite itself
failed (in which case the diff is not trustworthy and is not reported as a verdict).
"""
import argparse
import glob
import hashlib
import os
import subprocess
import sys


def snapshot(directory, pattern):
    out = {}
    for p in sorted(glob.glob(os.path.join(directory, pattern))):
        try:
            with open(p, "rb") as fh:
                out[p] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            continue
    return out


def diff(before, after):
    """(changed, added, removed) -- all three matter. A test that DELETES a synced file is
    just as damaging as one that rewrites it, and an added file is a stray artifact."""
    changed = sorted(p for p in before if p in after and before[p] != after[p])
    return changed, sorted(set(after) - set(before)), sorted(set(before) - set(after))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=os.path.join("data", "current"))
    ap.add_argument("--pattern", default="*.json")
    a = ap.parse_args(argv)

    before = snapshot(a.dir, a.pattern)
    if not before:
        print(f"nothing to watch under {a.dir}/{a.pattern} -- run a sync first, or the "
              f"check proves nothing.")
        return 0
    print(f"watching {len(before)} file(s) under {a.dir}; running the suite ...")
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
                          capture_output=True, text=True)
    tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [""]
    print(f"  suite: {tail[0]}")

    changed, added, removed = diff(before, snapshot(a.dir, a.pattern))
    if proc.returncode != 0:
        print("  the suite FAILED, so the diff below is not a verdict -- fix the suite first.")
    for label, rows in (("CHANGED", changed), ("ADDED", added), ("REMOVED", removed)):
        for p in rows:
            print(f"  {label}: {p}")
    if changed or added or removed:
        print("\nA test is writing to real synced data. Patch the WRITER too, not just the\n"
              "transport: several sync functions save as a side effect and return something\n"
              "else entirely (generate_league_schedule returns the FAILED WEEKS, not the\n"
              "schedule). Re-run a sync to restore whatever moved.")
        return 2 if proc.returncode != 0 else 1
    print("CLEAN -- the suite left every watched file byte-identical.")
    return 2 if proc.returncode != 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())

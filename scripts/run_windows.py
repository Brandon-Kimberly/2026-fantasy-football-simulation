#!/usr/bin/env python3
"""
Canonical-run window assistant -- READ-ONLY, safe to run unattended from Task Scheduler.
Not a runner: R1 makes unattended engine runs unsafe on this machine (no void-and-re-run
judgment, and a scheduled run could fire alongside an ad-hoc one -- the exact multi-process
load that produces silent corruption). This reports; a human runs
`py -3.10 -m scripts.weekly_report --canonical` inside a window.

  py -3.10 -m scripts.run_windows            # human-readable report
  py -3.10 -m scripts.run_windows --json     # machine-readable (the future post-RMA
                                             # auto-runner consumes this; see
                                             # fantasy_sim.run_windows's module docstring)

Windows (America/Los_Angeles): run 1 before the week's earliest real kickoff (from
nfl_schedule._meta.kickoffs, live-fetched from ESPN if a pre-migration sync has not stored
them); run 2 Sunday before 10:00; run 3 Tuesday before the Wednesday 00:00 waiver clear.
Coverage = a canonical weekly digest in data/decisions/week_NN/ stamped inside the window.
"""
import argparse
import json
import os
from datetime import datetime, timezone

from fantasy_sim.freshness import check as freshness_check
from fantasy_sim.run_windows import PT, compute_windows, load_kickoffs, parse_canonical_digest, release_advice
from fantasy_sim.storage import decisions_week_path


def _release_advice_live(week):
    """Thin git wiring for run_windows.release_advice: latest local tag and whether the
    golden fixtures changed since it. Degrades to the fetch-tags hint when git or tags
    are unavailable."""
    import subprocess

    def git(*args):
        try:
            out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None

    tag = git("describe", "--tags", "--abbrev=0")
    changed = False
    if tag:
        log = git("log", "--oneline", f"{tag}..HEAD", "--", "tests/fixtures/golden/expected")
        changed = bool(log)
    return release_advice(tag, changed, week)


def _canonical_stamps(week):
    """Canonical weekly digests on disk for the week (both name shapes; _FAILED covers
    nothing -- see run_windows.parse_canonical_digest)."""
    week_dir = os.path.dirname(decisions_week_path(week, "x", canonical=True))
    stamps = []
    if os.path.isdir(week_dir):
        for name in sorted(os.listdir(week_dir)):
            dt = parse_canonical_digest(name, week)
            if dt is not None:
                stamps.append((name, dt))
    return stamps


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    kicks, kick_source = load_kickoffs()
    if not kicks:
        raise SystemExit("no kickoff data: schedule meta empty and ESPN unreachable")

    status, reasons, details = freshness_check()
    state_week = details.get("nfl_week") or details.get("week")

    target = compute_windows(now, kicks, [], state_week=state_week)["target_week"]
    stamps = _canonical_stamps(target) if target else []
    r = compute_windows(now, kicks, stamps, state_week=state_week)
    r["freshness"] = {"status": status, "reasons": reasons}
    r["kickoff_source"] = kick_source
    r["now"] = now

    if args.json:
        def enc(o):
            return o.isoformat() if isinstance(o, datetime) else o
        print(json.dumps(r, default=enc, indent=1, sort_keys=True))
        return r

    pt_now = now.astimezone(PT)
    print(f"\nCanonical run windows -- week {r['target_week']}  "
          f"(now {pt_now.strftime('%a %Y-%m-%d %H:%M %Z')}; kickoffs from {kick_source})")
    print(f"  freshness: {status}" + (f" -- {'; '.join(reasons)}" if reasons else ""))
    for w in r["windows"]:
        s_, d_ = w["start"].astimezone(PT), w["deadline"].astimezone(PT)
        line = (f"  {w['status']:8s} {w['name']:18s} "
                f"{s_.strftime('%a %m-%d %H:%M')} -> {d_.strftime('%a %m-%d %H:%M %Z')}")
        if w["covered_by"]:
            line += f"  [{w['covered_by']}]"
        elif w["status"] == "OPEN":
            remaining = w["deadline"] - now
            hrs = remaining.total_seconds() / 3600
            line += f"  ({hrs:.1f} h remaining)"
        print(line)
    if r["outside_windows"]:
        print("  canonical runs outside every window (covered nothing): "
              + ", ".join(r["outside_windows"]))
    for f in r["flags"]:
        print(f"  FLAG: {f}")
    for m in _release_advice_live(r["target_week"]):
        print(f"  RELEASE: {m}")
    if any(w["status"] == "MISSED" for w in r["windows"]):
        print("  >> a window was MISSED -- run `py -3.10 -m scripts.weekly_report --canonical` "
              "at the next opportunity")
    return r


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Stat corrections: what Sleeper first reported against what it reports now
(fantasy_sim.corrections).

  py -3.10 -m scripts.stat_corrections [--week N] [--min-delta 0.1] [--json]

`weekly_actuals.json` is regenerated on every sync, so a Tuesday correction overwrites
the number it corrected. `data/logs/first_recorded_scores.jsonl` is written once per
player per completed week and never touched again (B19); this diffs the two.

Why it matters beyond curiosity: every quoted-vs-realized comparison in January is
measured against the REALIZED side, and that side is mutable. Without the frozen copy a
calibration computed in January need not match the same calibration computed in
December, and neither would be identifiably wrong.

Reads data/logs/ and data/current/ only; writes nothing.
"""
import argparse
import json
import os

from fantasy_sim.corrections import diff_corrections, team_impact
from fantasy_sim.storage import FIRST_SCORES_FILE, LIVE_ROSTERS_FILE, WEEKLY_ACTUALS_FILE, load_json


def _first(path=FIRST_SCORES_FILE):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", type=int, default=None, help="only this completed week")
    ap.add_argument("--min-delta", type=float, default=0.1,
                    help="hide movements smaller than this (rounding noise)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows = _first()
    if args.week is not None:
        rows = [r for r in rows if r.get("week") == args.week]
    actuals = load_json(WEEKLY_ACTUALS_FILE) or {}
    r = diff_corrections(rows, actuals)
    shown = [c for c in r["corrections"] if abs(c["delta"]) >= args.min_delta]

    if args.json:
        print(json.dumps({**r, "corrections": shown}, indent=1, sort_keys=True))
        return r

    if not rows:
        print("\nNo first-recorded scores yet. The file is written on the first sync "
              "AFTER a week completes (B19):")
        print(f"  {FIRST_SCORES_FILE}")
        return r

    print(f"\nSTAT CORRECTIONS -- {r['n_recorded']} frozen score(s), "
          f"{r['n_corrected']} changed ({100 * r['rate']:.1f}%)")
    if r["n_corrected"]:
        print(f"  largest {r['largest']:.2f} pts, total absolute movement "
              f"{r['total_abs']:.2f}")
    if not shown:
        print(f"  nothing above {args.min_delta:.2f} -- no correction worth a second look.")
    else:
        print(f"\n  {'wk':>2s} {'player':26s} {'first':>7s} {'now':>7s} {'delta':>7s}")
        for c in shown:
            now = "GONE" if c["now"] is None else f"{c['now']:7.2f}"
            print(f"  {c['week']:2d} {str(c['name'])[:26]:26s} {c['first']:7.2f} "
                  f"{now:>7s} {c['delta']:+7.2f}")

        lr = load_json(LIVE_ROSTERS_FILE) or {}
        rosters = {t: [p["name"] for p in pl] for t, pl in lr.items()}
        ti = team_impact(shown, actuals, rosters)
        moved = [t for t in ti["teams"] if abs(t["delta"]) >= args.min_delta]
        if moved:
            print("\n  team totals moved (ROSTERED players only):")
            for t in moved:
                print(f"    wk{t['week']:2d} {t['team'][:22]:22s} {t['delta']:+7.2f}   "
                      f"{', '.join(t['players'][:3])}")
            print(f"    {ti['note']}")
    print(f"\n  {r['note']}")
    return r


if __name__ == "__main__":
    main()

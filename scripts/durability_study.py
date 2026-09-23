#!/usr/bin/env python3
"""
Does a Questionable tag predict a later DNP? -- B10's study (fantasy_sim.durability).

  py -3.10 -m scripts.durability_study
  py -3.10 -m scripts.durability_study --lookback 4 --json

Reads `data/logs/designations.jsonl` (B21) and `data/logs/first_recorded_scores.jsonl`
(B19) and reports the 2x2: designated in the trailing window vs DNP the following week.

IT REPORTS WHAT IS MISSING. As of 2026-09-23 there is no usable pair -- designations
begin at week 3 and completed-week scores end at week 2 -- so the honest output is the
inventory and the earliest week the study can run, not a number. The estimator is tested
against hand-computed fixtures (`tests/test_durability.py`); what it lacks is data.

MEASURES ONLY. B10: adoption of a durability multiplier is "a separate, later decision"
and requires a lift that is significant AND survives holdout. Nothing here writes to
`config.py` and nothing in the engine imports `fantasy_sim.durability`.

Reads data/logs/ only; writes nothing.
"""
import argparse
import json
import os

from fantasy_sim.durability import study
from fantasy_sim.storage import DESIGNATIONS_FILE, FIRST_SCORES_FILE, BASELINES_FILE, load_json


def _jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _positions():
    """{pid: pos} from the current baselines. Position is not in either log."""
    out = {}
    for entry in (load_json(BASELINES_FILE) or {}).values():
        if isinstance(entry, dict) and entry.get("player_id") and entry.get("pos"):
            out[str(entry["player_id"])] = entry["pos"]
    return out


def _roll_call(des_rows):
    """{week: {pid}} from B21's roll-call rows.

    Only weeks that actually carry a healthy row are returned: a week logged before F63
    has designated players only, and treating that as the roster would make every healthy
    man look undesignated-and-absent.
    """
    by_week, has_healthy = {}, set()
    for r in des_rows:
        try:
            wk = int(r.get("week"))
        except (TypeError, ValueError):
            continue
        by_week.setdefault(wk, set()).add(str(r.get("player_id")))
        if not r.get("injury_status"):
            has_healthy.add(wk)
    return {w: pids for w, pids in by_week.items() if w in has_healthy}


def _weeks(rows):
    ws = {int(r["week"]) for r in rows if r.get("week") is not None}
    return (min(ws), max(ws)) if ws else (None, None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lookback", type=int, default=3,
                    help="trailing weeks of designation history (default 3)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    des, scores = _jsonl(DESIGNATIONS_FILE), _jsonl(FIRST_SCORES_FILE)
    roll = _roll_call(des)
    r = study(des, scores, lookback=args.lookback, positions=_positions(),
              roll_call=roll or None)

    if args.json:
        print(json.dumps(r, indent=1, sort_keys=True, default=list))
        return r

    d_lo, d_hi = _weeks(des)
    s_lo, s_hi = _weeks(scores)
    healthy = sum(1 for x in des if not x.get("injury_status"))
    print("\nB10 -- does a designation predict a later DNP?\n")
    print(f"  designations   {len(des):5d} rows, weeks {d_lo}-{d_hi}"
          f"   ({healthy} roll-call rows)")
    print(f"  scores         {len(scores):5d} rows, weeks {s_lo}-{s_hi}")
    print(f"  usable pairs   {r['pairs'] or 'NONE'}   lookback {r['lookback']}w"
          f"   population from {r['population_source']}")

    if not r["pairs"]:
        nxt = (d_lo + 1) if d_lo else "?"
        print(f"\n  NO STUDY YET. A lift needs a designation in week W and a completed-week")
        print(f"  score in W+1. Designations start at week {d_lo}; completed scores end at")
        print(f"  week {s_hi}. The first usable pair is week {d_lo} -> {nxt}, so this runs")
        print(f"  once week {nxt} completes. Reporting a number before then would be")
        print(f"  inventing one.")
        if not healthy:
            print(f"\n  NOTE: no roll-call rows yet (F63 landed 2026-09-23). Weeks logged")
            print(f"  before that carry the designated only, and their denominator would")
            print(f"  have to come from the league-wide scored feed -- a lower bound, not")
            print(f"  an estimate. Weeks from the next sync on are clean.")
        return r

    for label in ("designated", "undesignated"):
        a = r[label]
        rate = "-" if a["rate"] is None else f"{a['rate']:.3f}"
        print(f"  {label:<14} n {a['n']:5d}   DNP {a['dnp']:4d}   rate {rate:>6s}"
              f"   95% CI [{a['ci'][0]:.3f}, {a['ci'][1]:.3f}]")
    lift = "undefined" if r["lift"] is None else f"{r['lift']:.2f}x"
    rd = "-" if r["risk_difference"] is None else f"{r['risk_difference']:+.3f}"
    print(f"\n  lift {lift}   risk difference {rd}   n {r['n']}")
    for pos, pr in sorted(r["by_position"].items()):
        pl = "-" if pr["lift"] is None else f"{pr['lift']:.2f}x"
        print(f"    {pos:<4} designated {pr['designated']['n']:4d} "
              f"undesignated {pr['undesignated']['n']:4d}   lift {pl}")
    print(f"\n  adoptable: {r['adoptable']} -- {r['reason']}")
    return r


if __name__ == "__main__":
    main()

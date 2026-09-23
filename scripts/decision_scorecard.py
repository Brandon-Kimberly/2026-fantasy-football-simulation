#!/usr/bin/env python3
"""
Did we make bad calls? -- the week's start/sit decisions, scored (fantasy_sim.scorecard).

  py -3.10 -m scripts.decision_scorecard --week 1
  py -3.10 -m scripts.decision_scorecard --week 1 --live-scores   # allow mutable actuals

Reads the PRE-KICKOFF lineup record for that week (data/decisions/week_NN/lineup_*.json)
and reports one row per distinct ALTERNATIVE, paired with the starter it was closest to
beating.

WHY THE DEDUPLICATION MATTERS. The hand-built week-1 answer listed eight decisions. Five
of them were the same bench receiver, eligible at five slots. One player is ONE call, and
counting him five times turns a single judgement into a pattern of failure.

SCORES COME FROM B19's FROZEN RECORD by default -- `first_recorded_scores.jsonl`, written
once per completed week and never updated. `weekly_actuals.json` is regenerated every
sync, so a Tuesday stat correction can silently change last week's verdict; --live-scores
uses it anyway and says so.

NO LOOKAHEAD: nothing here re-solves a lineup. Scoring a decision against a lineup rebuilt
with the week's results in hand is the leakage CLAUDE.md forbids.

Reads data/decisions/ and data/logs/ only; writes nothing.
"""
import argparse
import glob
import json
import os

from fantasy_sim.config import MY_TEAM as DEFAULT_TEAM
from fantasy_sim.scorecard import decisions_from_lineup, score_decisions, summarise
from fantasy_sim.storage import FIRST_SCORES_FILE, WEEKLY_ACTUALS_FILE, load_json


def _earliest(candidates):
    """The (path, record) with the earliest `timestamp_utc`.

    BY TIME, NOT BY PATH. The first version sorted the candidate FILES, and
    "week_01/archive/lineup_..." sorts before "week_01/lineup_...", so a later archived
    run was selected over an earlier top-level one. For a tool whose premise is "score
    against the PRE-KICKOFF record" that is the wrong rule: mid-week runs are a real
    thing since B3, and picking one would be the lookahead CLAUDE.md forbids.

    A record with no timestamp sorts LAST. It cannot be shown to be pre-kickoff, so it
    must not win by default.
    """
    if not candidates:
        return None, None
    path, rec = min(candidates,
                    key=lambda pr: (pr[1].get("timestamp_utc") is None,
                                    pr[1].get("timestamp_utc") or ""))
    return path, rec


def _lineup_record(week, team):
    """The earliest recorded lineup for the week -- the pre-kickoff one."""
    pats = [os.path.join("data", "decisions", f"week_{week:02d}", "lineup_*.json"),
            os.path.join("data", "decisions", f"week_{week:02d}", "archive", "lineup_*.json")]
    cands = []
    for pat in pats:
        for f in glob.glob(pat):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if d.get("team") == team:
                cands.append((f, d))
    path, rec = _earliest(cands)
    return rec, path


def _frozen_scores(week, path=FIRST_SCORES_FILE):
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("week") == week:
                out[r.get("name")] = float(r.get("points") or 0.0)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--live-scores", action="store_true",
                    help="use weekly_actuals.json instead of B19's frozen record")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    record, path = _lineup_record(args.week, args.team)
    if record is None:
        raise SystemExit(f"no pre-kickoff lineup record for {args.team} in week "
                         f"{args.week}. This scores what was DECIDED, so without that "
                         f"record there is nothing to score.")

    if args.live_scores:
        wa = (load_json(WEEKLY_ACTUALS_FILE) or {}).get(f"week_{args.week}") or {}
        scores, source = (wa.get("player_scores") or {}), "weekly_actuals (MUTABLE)"
    else:
        scores, source = _frozen_scores(args.week), "first_recorded_scores (frozen, B19)"

    rows = score_decisions(decisions_from_lineup(record), scores)
    s = summarise(rows)

    if args.json:
        print(json.dumps({"week": args.week, "team": args.team, "source": source,
                          "decisions": rows, "summary": s}, indent=1, sort_keys=True))
        return s

    print(f"\n{args.team} -- week {args.week} decision scorecard")
    print(f"  from {os.path.basename(path)}  (pre-kickoff)   scores: {source}")
    if not rows:
        print("  No start/sit decisions: every slot had one eligible player.")
        return s

    print(f"\n  {'alternative':22s} {'over':20s} {'slot':5s} {'margin':>7s} "
          f"{'started':>8s} {'alt':>6s} {'outcome':>10s} {'cost':>6s}  also")
    for r in rows:
        sp = "-" if r["started_points"] is None else f"{r['started_points']:8.2f}"
        ap_ = "-" if r["alternative_points"] is None else f"{r['alternative_points']:6.2f}"
        cost = "-" if r["cost"] is None else f"{r['cost']:6.2f}"
        also = (f"+{r['n_slots'] - 1} more slot(s)" if r["n_slots"] > 1 else "")
        print(f"  {r['alternative'][:22]:22s} {str(r['started'])[:20]:20s} {r['slot']:5s} "
              f"{r['margin']:+7.2f} {sp:>8s} {ap_:>6s} {r['outcome']:>10s} {cost:>6s}  {also}")

    hr = "-" if s["hit_rate"] is None else f"{100 * s['hit_rate']:.0f}%"
    print(f"\n  {s['decisions']} decision(s): {s['right']} right, {s['wrong']} wrong, "
          f"{s['unresolved']} unresolved   hit rate {hr} (of resolved)")
    if s["points_left_behind"]:
        print(f"  points left on the bench: {s['points_left_behind']:.2f}")
    print(f"  {s['note']}")
    return s


if __name__ == "__main__":
    main()

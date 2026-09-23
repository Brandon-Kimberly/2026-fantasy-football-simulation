"""
fantasy_sim.scorecard

Did we make bad calls? -- scored against the pre-kickoff record, with the duplicates
collapsed.

B24. Asked twice in one week and answered by hand both times. The week-1 answer listed
EIGHT decisions; the lineup record shows Christian Watson as the alternative in five
different slots, Tony Pollard in two, Fred Warner in one. **Three** decisions. One bench
player eligible at five slots is ONE call, and counting him five times turns a single
judgement into a pattern of failure -- which is precisely the reading that makes a bad
week feel like a bad process.

**WHICH STARTER THE ALTERNATIVE PAIRS WITH** is the non-obvious part. It is the SMALLEST
margin: Watson against Breece Hall at +0.71, not against McConkey at +4.24. The smallest
margin is the swap that was actually available; pairing him against the largest would
score a decision nobody was ever close to making.

**NO LOOKAHEAD.** Everything here reads the PRE-KICKOFF lineup record
(`data/decisions/week_NN/lineup_*.json`). Re-solving a lineup with the week's results in
hand is the leakage `CLAUDE.md`'s statistical conventions forbid outright, and a test
refuses `optimize_lineup`, `roster_gaps` and `_solve_optimal_assignment` anywhere in this
module.

**UNRESOLVED IS NOT CORRECT.** An alternative who never played cannot settle anything.
Those rows are marked and excluded from the hit rate; counting them as right is how a
scorecard flatters itself.

Pure: records in, dicts out.
"""
from collections import defaultdict


def decisions_from_lineup(record):
    """One row per distinct ALTERNATIVE, paired with the closest starter.

    `record` is a `scripts.optimize_lineup` JSON record. Slots with no alternative are
    not decisions -- nothing was chosen between.
    """
    by_alt = defaultdict(list)
    for row in (record or {}).get("lineup", []):
        alt = row.get("alternative")
        if alt:
            by_alt[alt].append(row)

    out = []
    for alt, rows in by_alt.items():
        closest = min(rows, key=lambda r: float(r.get("margin") or 0.0))
        out.append({
            "alternative": alt,
            "started": closest.get("name"),
            "slot": closest.get("slot"),
            "margin": float(closest.get("margin") or 0.0),
            "expected": float(closest.get("expected") or 0.0),
            # Collapsed, not discarded: the owner should still see it was five slots.
            "n_slots": len(rows),
            "slots": sorted({r.get("slot") for r in rows}),
            "also_considered_over": sorted(r.get("name") for r in rows
                                           if r.get("name") != closest.get("name")),
        })
    out.sort(key=lambda d: d["margin"])
    return out


def score_decisions(decisions, actual_scores):
    """Attach the outcome. `actual_scores` is {name: points}.

    Prefer B19's frozen first-recorded scores where the caller has them: they are the
    contemporaneous record and cannot be moved by a Tuesday stat correction.
    """
    scores = actual_scores or {}
    out = []
    for d in decisions or []:
        started, alt = d.get("started"), d.get("alternative")
        sp, ap = scores.get(started), scores.get(alt)
        row = dict(d, started_points=sp, alternative_points=ap)
        if ap is None or sp is None:
            row["outcome"] = "unresolved"
            row["cost"] = None
        elif float(ap) > float(sp):
            row["outcome"] = "wrong"
            row["cost"] = float(ap) - float(sp)
        else:
            row["outcome"] = "right"
            row["cost"] = 0.0
        out.append(row)
    # Worst first among the wrong ones; unresolved last, since they say nothing.
    out.sort(key=lambda r: (r["outcome"] == "unresolved", -(r["cost"] or 0.0)))
    return out


def summarise(scored):
    """Counts and a hit rate over RESOLVED calls only."""
    rows = list(scored or [])
    right = sum(1 for r in rows if r["outcome"] == "right")
    wrong = sum(1 for r in rows if r["outcome"] == "wrong")
    unresolved = sum(1 for r in rows if r["outcome"] == "unresolved")
    resolved = right + wrong
    return {
        "decisions": len(rows), "right": right, "wrong": wrong, "unresolved": unresolved,
        "hit_rate": (right / resolved) if resolved else None,
        "points_left_behind": float(sum(r["cost"] or 0.0 for r in rows)),
        "note": ("one alternative eligible at several slots is ONE decision, not several. "
                 "The hit rate is over RESOLVED calls: an alternative who never played "
                 "settles nothing and is excluded rather than counted correct. Scored "
                 "against the PRE-KICKOFF record, never a re-solved lineup."),
    }

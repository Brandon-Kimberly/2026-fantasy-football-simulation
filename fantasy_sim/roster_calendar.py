"""Bye-week exposure and the roster-crunch forecast -- both answered by hand, both arithmetic.

T6 (docs/SCOPED_BACKLOG_2.md). Two questions drove real decisions and neither has a tool:

  "Which weeks am I short at a position?"   -> one hole all season, a week-7 DL
  "When the IR'd QB returns I am at 20 active and must cut someone -- who?"

The first is why every RB-for-WR offer got declined: two of the RBs leave three bye holes
and the RB wire is barren. That reasoning lived in a chat window.

TWO SECTIONS.

`calendar` -- per remaining week: who is on bye, which required slots are still fillable,
and, for every starter whose bye it is, which bench piece steps into his slot. A week whose
slot cannot be filled by anyone on the roster is a HOLE, and holes are what the waiver plan
is built around.

`crunch` -- per IR player: the active count on the week he returns, whether that breaks the
roster limit, and which bench pieces are droppable. A bench piece is droppable when it
covers NO starter's bye in any remaining week; one that does is load-bearing even though it
never starts today, and that is exactly the distinction the hand-built version kept getting
wrong.

WHO COUNTS AS OUT. `SIM_CONFIG['INITIAL_ABSENCE_STATUSES']` decides, the same set the
engine uses. **Questionable is deliberately not in it (F51)** and a Questionable starter is
NOT a hole -- the Sleeper projection his baseline derives from already reflects expected
usage, so treating him as absent here would double-count and would invent a hole the owner
would then go and spend FAAB on.

Nothing here changes a number. Slots come from the engine's own Hungarian assignment
(`decisions.roster_gaps`) on baseline means, so this module and `waiver_targets` cannot
disagree about what a hole is.
"""
from fantasy_sim.config import REGULAR_SEASON_WEEKS
from fantasy_sim.decisions import (ACTIVE_ROSTER_LIMIT, _entry, _opts, _unavailable_now,
                                   roster_gaps)


def remaining_weeks(engine, through=None):
    """The regular-season weeks still to play, current week included."""
    last = int(through or REGULAR_SEASON_WEEKS)
    return list(range(int(engine.current_week), last + 1))


def _bye(engine, name):
    b = _entry(engine, name).get("bye")
    try:
        return int(b) if b else None
    except (TypeError, ValueError):
        return None


def bye_map(engine, team, weeks):
    """({week: [names on bye]}, {week: [names on bye who are ALREADY OUT]}).

    The split matters and the first live run showed why: an IR'd player's bye landed in
    the same list as two real absences and the week read as "three men out" when one of
    them had been out all along. He is still worth naming -- his bye matters the moment he
    is activated -- so he is listed apart rather than dropped.
    """
    out = {w: [] for w in weeks}
    out_ir = {w: [] for w in weeks}
    for n in engine.rosters.get(team, []):
        b = _bye(engine, n)
        if b not in out:
            continue
        (out_ir if _unavailable_now(_entry(engine, n)) else out)[b].append(n)
    for w in out:
        out[w].sort()
        out_ir[w].sort()
    return out, out_ir


def calendar(engine, team, weeks=None, through=None):
    """Per remaining week: byes, unfillable slots, and who covers each bye-week starter.

    "Covers" is answered by comparing the assignment WITH the bye against the assignment
    the same roster would produce if nobody were on bye -- the man who appears in the slot
    only in the first is the cover. That is a fact about the solved lineups, not a guess
    from position strings, so a dual-eligible cover is found the same way the engine would
    find him.
    """
    weeks = list(weeks) if weeks is not None else remaining_weeks(engine, through)
    gaps = roster_gaps(engine, team, weeks)
    byes, byes_ir = bye_map(engine, team, weeks)

    # The counterfactual: the same roster with nobody on bye. One solve, reused.
    full = []
    for n in engine.rosters.get(team, []):
        e = _entry(engine, n)
        if _unavailable_now(e):
            continue
        full.append((n, _opts(engine, n), float(e.get("mean", 4.0))))
    baseline_assigned, _uf = engine._solve_optimal_assignment(full)
    baseline_starters = {n for n, _v, _s in baseline_assigned}

    rows = []
    for w in weeks:
        g = gaps[w]
        started = {n for entries in g["starters"].values() for n, _v in entries}
        on_bye = byes[w]
        bye_starters = [n for n in on_bye if n in baseline_starters]
        covers = []
        for slot, entries in g["starters"].items():
            for n, _v in entries:
                if n not in baseline_starters:
                    covers.append({"slot": slot, "name": n})
        rows.append({
            "week": w,
            "on_bye": on_bye,
            "on_bye_ir": byes_ir[w],
            "bye_starters": bye_starters,
            "unfilled": list(g["unfilled"]),
            "covers": sorted(covers, key=lambda c: (c["slot"], c["name"])),
            "n_startable": len(started),
        })
    return {"team": team, "weeks": weeks, "rows": rows,
            "holes": {w: rows[i]["unfilled"] for i, w in enumerate(weeks) if rows[i]["unfilled"]}}


def covering_bench(engine, team, weeks=None, through=None):
    """{bench name: [weeks where he fills a slot a bye-week starter vacated]}.

    A bench piece with an empty list is droppable; one with entries is load-bearing even
    though it never starts this week, which is the distinction the hand-built answer kept
    losing.
    """
    cal = calendar(engine, team, weeks=weeks, through=through)
    out = {}
    for row in cal["rows"]:
        for c in row["covers"]:
            out.setdefault(c["name"], []).append(row["week"])
    return out


def crunch(engine, team, weeks=None, through=None, limit=ACTIVE_ROSTER_LIMIT):
    """For each IR'd player: the active count when he returns, and who could be cut.

    The return week is UNKNOWN -- Sleeper publishes a designation, not a date -- so this
    reports the arithmetic conditional on a return rather than inventing a week. Saying
    "when he returns" is the honest form of the question the owner actually asked.
    """
    weeks = list(weeks) if weeks is not None else remaining_weeks(engine, through)
    roster = list(engine.rosters.get(team, []))
    ir = [n for n in roster if bool(_entry(engine, n).get("on_ir", False))]
    active = [n for n in roster if n not in ir]
    covering = covering_bench(engine, team, weeks=weeks)

    # Today's starters, so "bench" means what the owner means by it.
    gaps = roster_gaps(engine, team, [weeks[0]] if weeks else [engine.current_week])
    this_week = gaps[weeks[0] if weeks else engine.current_week]
    starters = {n for entries in this_week["starters"].values() for n, _v in entries}
    bench = [n for n in active if n not in starters]

    droppable = sorted(
        ({"name": n, "pos": _opts(engine, n), "mean": float(_entry(engine, n).get("mean", 0.0))}
         for n in bench if not covering.get(n)),
        key=lambda d: d["mean"])
    load_bearing = sorted(
        ({"name": n, "covers_weeks": covering[n]} for n in bench if covering.get(n)),
        key=lambda d: d["name"])

    returns = []
    for n in ir:
        returns.append({
            "name": n,
            "active_on_return": len(active) + 1,
            "over_limit": (len(active) + 1) > limit,
            "must_drop": max(0, (len(active) + 1) - limit),
        })
    return {"team": team, "limit": limit, "active_now": len(active), "ir": ir,
            "returns": returns, "droppable": droppable, "load_bearing": load_bearing,
            "note": ("Sleeper publishes an IR designation, not a return date, so the count "
                     "below is conditional on a return rather than dated. A bench piece is "
                     "'droppable' only when it covers no starter's bye in any remaining "
                     "week -- one that does is load-bearing even though it never starts "
                     "today.")}


def render_lines(cal, cr, name_of=None):
    """Both sections as plain lines."""
    def T(x):
        return (name_of or {}).get(x, x) if isinstance(name_of, dict) else x

    out = [f"ROSTER CALENDAR -- {T(cal['team'])}, weeks "
           f"{cal['weeks'][0]}-{cal['weeks'][-1]}" if cal["weeks"] else
           f"ROSTER CALENDAR -- {T(cal['team'])}"]
    # One line per week plus indented continuation lines. A fixed-width column that CUTS a
    # name is worse than no column: the first live run printed "Chris Olave, Eddy Pineiro,
    # F" and the reader cannot tell who the third man is, which is the one thing the row
    # exists to say.
    for row in cal["rows"]:
        holes = ", ".join(row["unfilled"])
        out.append(f"  week {row['week']:<3d}" + (f"  HOLES: {holes}" if holes else ""))
        if row["on_bye"]:
            out.append("      on bye:  " + ", ".join(row["on_bye"]))
        if row["on_bye_ir"]:
            out.append("      on bye but already out (IR): " + ", ".join(row["on_bye_ir"]))
        if row["covers"]:
            out.append("      covered by: "
                       + "; ".join(f"{c['name']} -> {c['slot']}" for c in row["covers"]))
    if cal["holes"]:
        for w, slots in sorted(cal["holes"].items()):
            out.append(f"  HOLE: week {w} has no eligible player for {', '.join(slots)} "
                       f"-- plan a claim before it, not during it.")
    else:
        out.append("  No unfillable slot in any remaining week on the current roster.")

    out.append("")
    out.append(f"  ROSTER CRUNCH -- {cr['active_now']} active of {cr['limit']}, "
               f"{len(cr['ir'])} on IR")
    for r in cr["returns"]:
        verdict = (f"you are at {r['active_on_return']} active and must drop "
                   f"{r['must_drop']}") if r["over_limit"] else \
                  f"you are at {r['active_on_return']} active, inside the limit"
        out.append(f"  when {r['name']} returns: {verdict}.")
    if cr["load_bearing"]:
        out.append("  load-bearing bench (covers a bye, do not drop): "
                   + "; ".join(f"{d['name']} (wk {', '.join(str(w) for w in d['covers_weeks'])})"
                               for d in cr["load_bearing"]))
    if cr["droppable"]:
        out.append("  droppable bench (covers no bye -- NOT a value ranking), cheapest "
                   "first: "
                   + ", ".join(f"{d['name']} ({d['mean']:.1f})" for d in cr["droppable"]))
    else:
        out.append("  NO droppable bench piece: every bench player covers a bye somewhere.")
    out.append("  " + cr["note"])
    return out

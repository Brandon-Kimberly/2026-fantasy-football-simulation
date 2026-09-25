"""
fantasy_sim.durability

B10. Does an injury designation predict a later DNP, above the base rate?

`INJURY_RATES` keys by POSITION. Every WR carries the same onset hazard, so the model
cannot know that a receiver has been listed Questionable four weeks running. B10 asks
whether that history carries signal, and is explicit that **adoption is a separate, later
decision**: this module measures, it does not price anything. Nothing here is imported by
the engine.

**THE TRAP, from the item itself: do not proxy durability from scores.** A low score is
not an injury. Only a designation makes a player designated, and only an exact 0.0 counts
as a DNP -- 1.2 points is a bad game, not an absence. A player who never appears in the
feed is UNKNOWN and is dropped, because reading his silence as a DNP would manufacture
the signal the study is trying to detect.

**THE DENOMINATOR DECIDES WHAT THE NUMBER MEANS** (F63). "Above the base rate" is a
comparison against the UNDESIGNATED, and `designations.jsonl` records only the designated.
So the undesignated must come from somewhere, and there are two somewheres:

  * `roll_call` -- {week: {pid, ...}} of who was actually rostered. Exact. This is what
    B21's log provides once it records the roll call, and `population_source` says
    "roll_call" when it was used.
  * the scored feed -- everyone with a score in both weeks. ~800 players rather than the
    ~152 rostered, and a player nobody tracked may have carried a designation that was
    never written down. Those men land in the UNDESIGNATED group, which inflates its DNP
    rate and biases the lift DOWNWARD. `population_source` says "scored_feed" and the
    result is a lower bound, not an estimate.

**Wilson intervals, not normal ones.** At the sample sizes this study will have for
months, a normal approximation puts the lower bound below zero and reads as precision
that is not there. Wilson stays inside [0, 1] and blows up honestly on small n.

**`lift` is None when the undesignated rate is zero** -- a ratio with a zero denominator
is undefined, and "infinite lift" from a handful of healthy men is exactly the overclaim
this module exists to avoid. `risk_difference` stays defined and is reported alongside.

Pure: rows in, dicts out. No I/O, no config, no engine.
"""
import math
from collections import defaultdict

# Below this many observations in EITHER arm, the study is not evidence for adoption. Set
# where a Wilson interval on a proportion near 0.1 is still roughly +/- 0.1 wide -- that
# is the point at which a lift of 2 could still be a lift of 1. It is a floor on
# seriousness, not a significance test: clearing it does not make a result adoptable.
MIN_ARM_N = 50


def designation_counts(rows, through_week, lookback=3):
    """{pid: number of DISTINCT weeks designated} within (through_week - lookback, through_week].

    Weeks, not rows. B21 dedupes on (week, pid, status), so a Friday Questionable that
    became a Sunday Out is two rows in one week -- one designated week, not two.

    A row with a falsy `injury_status` is a ROLL-CALL row (the player was rostered and
    healthy) and is not a designation.
    """
    lo = int(through_week) - int(lookback)
    weeks_by_pid = defaultdict(set)
    for r in rows or []:
        if not r.get("injury_status"):
            continue
        try:
            wk = int(r.get("week"))
        except (TypeError, ValueError):
            continue
        if lo < wk <= int(through_week):
            weeks_by_pid[str(r.get("player_id"))].add(wk)
    return {pid: len(weeks) for pid, weeks in weeks_by_pid.items()}


def dnp_flags(score_rows, week):
    """{pid: True if the player scored exactly 0.0 in `week`}.

    Absent from the feed means absent from the dict -- unknown, not a DNP.

    FIRST row wins per pid (F87). These rows come from `first_recorded_scores.jsonl`, which
    freezes the first score seen, and that file is `merge=union`: when two syncs race, both
    captures survive in the file. Taking the last one would silently re-score a frozen
    observation -- the opposite of what the log is for.
    """
    out = {}
    for r in score_rows or []:
        try:
            if int(r.get("week")) != int(week):
                continue
        except (TypeError, ValueError):
            continue
        pid = str(r.get("player_id"))
        if pid in (None, "None", ""):
            continue
        if pid in out:
            continue
        try:
            pts = float(r.get("points") or 0.0)
        except (TypeError, ValueError):
            continue
        out[pid] = (pts == 0.0)
    return out


def wilson(successes, n, z=1.96):
    """Wilson score interval for a proportion. (0.0, 1.0) when n is 0 -- total ignorance,
    which is the honest interval for no data."""
    n = int(n)
    if n <= 0:
        return (0.0, 1.0)
    p = float(successes) / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


def _arm(n, dnp):
    return {"n": n, "dnp": dnp, "rate": (dnp / n) if n else None,
            "ci": wilson(dnp, n)}


def _summarise(designated, undesignated):
    d_n, d_dnp = designated
    u_n, u_dnp = undesignated
    d, u = _arm(d_n, d_dnp), _arm(u_n, u_dnp)
    lift = None
    if d["rate"] is not None and u["rate"]:          # zero denominator -> undefined
        lift = d["rate"] / u["rate"]
    rd = None
    if d["rate"] is not None and u["rate"] is not None:
        rd = d["rate"] - u["rate"]
    return d, u, lift, rd


def study(designation_rows, score_rows, lookback=3, positions=None, roll_call=None,
          min_arm_n=MIN_ARM_N):
    """The 2x2: designated in the trailing window vs DNP in the following week.

    A usable PAIR is a week W with at least one designation row and a week W+1 with
    scores. The population is the players observable in BOTH weeks -- a man who appears
    only in W+1 was not observable at W and cannot be classified.
    """
    des_weeks = sorted({int(r["week"]) for r in (designation_rows or [])
                        if r.get("week") is not None})
    score_weeks = {int(r["week"]) for r in (score_rows or []) if r.get("week") is not None}

    pairs = [(w, w + 1) for w in des_weeks if w in score_weeks and (w + 1) in score_weeks]

    cells = defaultdict(lambda: [0, 0])          # key -> [n, dnp]
    for w, nxt in pairs:
        counts = designation_counts(designation_rows, through_week=w, lookback=lookback)
        seen_w = set(dnp_flags(score_rows, w))
        out = dnp_flags(score_rows, nxt)
        eligible = seen_w & set(out)
        if roll_call:
            eligible &= (set(roll_call.get(w) or ()) | set(roll_call.get(nxt) or ()))
        for pid in eligible:
            arm = "designated" if counts.get(pid) else "undesignated"
            cells[arm][0] += 1
            cells[arm][1] += int(bool(out[pid]))
            if positions:
                pos = positions.get(pid)
                if pos:
                    cells[(pos, arm)][0] += 1
                    cells[(pos, arm)][1] += int(bool(out[pid]))

    d, u, lift, rd = _summarise(cells["designated"], cells["undesignated"])

    by_position = {}
    if positions:
        for pos in sorted({p for p in positions.values() if p}):
            pd, pu, plift, prd = _summarise(cells[(pos, "designated")],
                                            cells[(pos, "undesignated")])
            if pd["n"] or pu["n"]:
                by_position[pos] = {"designated": pd, "undesignated": pu,
                                    "lift": plift, "risk_difference": prd}

    n = d["n"] + u["n"]
    adoptable, reason = _adoptability(d, u, lift, min_arm_n)
    return {
        "pairs": pairs, "lookback": lookback, "n": n,
        "designated": d, "undesignated": u,
        "lift": lift, "risk_difference": rd,
        "by_position": by_position,
        "population_source": "roll_call" if roll_call else "scored_feed",
        "adoptable": adoptable, "reason": reason,
    }


def _adoptability(designated, undesignated, lift, min_arm_n):
    """Whether this result could support putting a multiplier in `config.py`.

    Deliberately conservative and deliberately not a p-value. B10 requires a lift that is
    "significant and survives holdout", and a holdout does not exist yet -- so the most
    this can ever return today is False with a reason.
    """
    if not designated["n"] or not undesignated["n"]:
        return False, ("no usable designation/outcome pair: a lift needs a designation in "
                       "week W and a score in week W+1")
    if designated["n"] < min_arm_n or undesignated["n"] < min_arm_n:
        return False, (f"arms too small ({designated['n']} designated, "
                       f"{undesignated['n']} undesignated; floor {min_arm_n} each)")
    if lift is None:
        return False, "undesignated DNP rate is zero, so the ratio is undefined"
    if designated["ci"][0] <= undesignated["rate"]:
        return False, ("the designated interval still contains the undesignated rate: "
                       "the lift is not separated from the base rate")
    return False, ("separated in-sample, but B10 requires a HOLDOUT and 2026 is the only "
                   "season with designations at all -- there is nothing to hold out")

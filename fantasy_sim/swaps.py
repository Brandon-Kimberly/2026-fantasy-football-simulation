"""
fantasy_sim.swaps

The exhaustive swap scan: every 1-for-1 and (bounded) 2-for-2 across the league, scored
on the engine's own values.

B15. `decisions.find_trade_targets` is NEED-DRIVEN -- it looks for *their bench player
who starts at my weakest slot*. That shape missed three real deals in week 3, because
the piece worth having was often **their starter**, displaced by something they need
more. This scan has no such blind spot: it tries everything.

**RANKING FOLLOWS B2, NOT B15.** B15 says to filter on both sides gaining. B2, written
later off four measured reversals, says `their_screen_gain` is the least reliable number
the tool computes and must never decide which candidates the simulation sees -- a screen
that misranks filters the good ones out before the sim gets a look. Those cannot both
hold, so: ranking is by MY screen gain only; their screen gain is reported and labelled;
and the mutual-gain filter is an explicit `require_mutual` opt-in for when the list needs
cutting. Every row carries `simulated: False` until a paired evaluation says otherwise.

**ROSTER SIZES ARE MEASURED, NEVER ASSUMED** (B15's named trap). The first version of
this scan silently skipped a rival because a hardcoded 19-man cap rejected their 20-man
roster -- a team with someone on IR carries 20, since reserve slots sit on top of the 19
active spots (B16). Sides are size-matched to each other, and nothing in this module
knows a roster limit.

**HOW EXHAUSTIVE, HONESTLY.** 1-for-1 is complete: every player of mine against every
player of theirs, across every rival. 2-for-2 is NOT -- it is bounded to the top
`pair_pool` players by mean on each side, because the full product is
C(19,2) x C(20,2) x 7 = ~227,000 pairs and each one costs two Hungarian solves. The bound
is stated in the result's `note` rather than left for the reader to discover.
"""
import itertools

from fantasy_sim.decisions import _entry

# 2-for-2 is explored over the top N by mean on each side. 10 gives C(10,2)^2 = 2,025
# pairs per rival (~14k league-wide), which runs in seconds; the unbounded product is
# ~227,000 and does not. UNVERIFIED as a tuning choice -- it bounds SEARCH, not any
# measured quantity, and a deal outside the top 10 by mean on both sides is not the deal
# anyone is looking for.
PAIR_POOL = 10


def _mean(engine, name):
    return float((_entry(engine, name) or {}).get("mean") or 0.0)


def roster_value(engine, names):
    """The screen's value of a roster: the engine's own acceptance rule.

    This is `get_optimal_score`, optimal lineup plus 0.1 x bench -- deliberately
    unchanged (B2's trap: the weight is not wrong for a week, only as a season proxy).
    What changed is that its output is labelled a SCREEN number everywhere it appears.
    """
    return float(engine.get_optimal_score(list(names)))


def exhaustive_swaps(engine, team, week=None, max_side=2, min_gain=0.0,
                     require_mutual=False, top_n=25, pair_pool=PAIR_POOL, exclude=None):
    """Every 1-for-1 and bounded 2-for-2 swap, ranked by MY screen gain.

    Returns a list of dicts: `with`, `i_give`, `i_get`, `my_screen_gain`,
    `their_screen_gain`, `simulated`, `sim_champ_delta`.

    `require_mutual=True` keeps only swaps the screen thinks the other side also gains
    from. OFF by default, and the cost of turning it on is B2's: the screen's view of
    someone else's roster is the least reliable thing here, so this can discard a deal
    the simulation would have liked.
    """
    if team not in engine.rosters:
        raise KeyError(f"unknown team {team!r}")
    # T3: `exclude` drops players committed to a PENDING trade from the candidate pools.
    # The pools only -- `engine.rosters` is never modified and both sides' baseline roster
    # values below still count every man they actually own today, because pending is not
    # complete and a vetoed trade returns them.
    skip = frozenset(exclude or ())
    mine = list(engine.rosters[team])
    base_me = roster_value(engine, mine)

    out, seen = [], set()
    for other in engine.rosters:
        if other == team:
            continue
        theirs = list(engine.rosters[other])
        base_them = roster_value(engine, theirs)

        offerable_mine = [n for n in mine if n not in skip]
        offerable_theirs = [n for n in theirs if n not in skip]

        # Sizes are read off the rosters themselves. No literal cap lives here (B15).
        my_pool = sorted(offerable_mine, key=lambda n: -_mean(engine, n))[:pair_pool]
        their_pool = sorted(offerable_theirs, key=lambda n: -_mean(engine, n))[:pair_pool]

        combos = [(1, offerable_mine, offerable_theirs)]
        if max_side >= 2:
            combos.append((2, my_pool, their_pool))

        for k, give_pool, get_pool in combos:
            for give in itertools.combinations(give_pool, k):
                for get in itertools.combinations(get_pool, k):
                    key = (other, tuple(sorted(give)), tuple(sorted(get)))
                    if key in seen:
                        continue
                    seen.add(key)
                    new_me = [n for n in mine if n not in give] + list(get)
                    new_them = [n for n in theirs if n not in get] + list(give)
                    # Size-matched by construction, so neither roster can overflow --
                    # which is why no limit constant is needed or wanted here.
                    d_me = roster_value(engine, new_me) - base_me
                    if d_me <= min_gain:
                        continue
                    d_them = roster_value(engine, new_them) - base_them
                    if require_mutual and d_them <= 0:
                        continue
                    out.append({
                        "with": other, "i_give": list(give), "i_get": list(get),
                        "my_screen_gain": d_me, "their_screen_gain": d_them,
                        "simulated": False, "sim_champ_delta": None,
                    })

    # B2: MY gain only. Never their_screen_gain.
    out.sort(key=lambda s: -s["my_screen_gain"])
    return out[:top_n]


def describe(result, pair_pool=PAIR_POOL):
    """The honesty line that travels with the numbers."""
    return (f"SCREEN numbers (optimal lineup + 0.1 x bench), not measurements -- run "
            f"scripts.evaluate_trade before acting (B2). 1-for-1 is complete; 2-for-2 is "
            f"bounded to the top {pair_pool} by mean on each side, because the full "
            f"product is ~227,000 pairs. Ranked by MY gain only: the screen's view of "
            f"another roster is the least reliable number here, so it never decides what "
            f"gets simulated. {len(result)} candidate(s).")

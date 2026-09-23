"""
fantasy_sim.market

The market sweep: every starting slot measured against the best free agent available for
it, the resulting upgrades ranked, and the roster's dead weight named.

B12, promoting a session scratchpad (`sweep3.py`) that produced real decisions. Pure --
dicts in, dicts out, no fetching and no printing -- so the tests are hermetic and the
script layer stays a renderer.

THREE THINGS THIS GETS RIGHT THAT THE SCRATCHPAD DID NOT, or had to be kept right:

1. **The slot-losing starter comes from the engine's own optimal assignment**, not from a
   fixed per-position count. The scratchpad used `SLOTS = {"RB": 2, "WR": 2, ...}` and so
   ignored the three FLEX slots: a third WR who genuinely starts at FLEX was counted as
   bench, and the position's "weakest starter" was the wrong man.

2. **The DROP is not the slot-losing starter.** The weakest man who starts and the worst
   man you own are different people the moment a position is more than one deep. The
   scratchpad printed the former as the drop. Both are reported here, separately.

3. **Engine values only.** `engine.baselines[name]['mean']` is the corrected post-F54
   number for every player, rostered or not. Nothing here re-blends it. The
   count-weighted `(4*prior + obs)/6` arithmetic that got applied by hand during week 3
   over-credits volatile producers and reversed four recommendations.

CURRENCY. Everything is in SEASON mean points per week, because that is the unit
`engine.replacement_levels` is expressed in and VORP is the question a roster move asks.
A week-specific start/sit call is `decisions.optimize_lineup`, which works in
`week_expectation` units instead; mixing the two in one table would be meaningless.

KEYING. This module works on engine keys, which `sync.resolve_player_keys` has already
disambiguated -- a colliding name is either the rostered player or is stored as
"Name (pid)". It never reads the raw player cache, which is where the collisions live;
`fantasy_sim.player_ids.resolve_pid` (B17) is the helper for that path.
"""
from collections import defaultdict

from fantasy_sim.config import normalize_position
from fantasy_sim.decisions import _entry, free_agents, roster_gaps

# Positions a roster move can target. DEF is excluded: this league starts none.
SWEEP_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DL", "LB", "DB")

# A player this far below replacement is not depth, he is a roster spot (the phrase is
# grade_roster's). UNVERIFIED as a threshold -- it is a display cutoff for a human
# reading a list, not an input to any decision, and nothing downstream consumes it.
DEAD_WEIGHT_VORP = -1.5


def _mean(engine, name):
    return float((_entry(engine, name) or {}).get("mean") or 0.0)


def _pos(engine, name):
    return normalize_position((_entry(engine, name) or {}).get("pos") or "")


def _on_ir(engine, name):
    """B16/F60: reserve slots sit ON TOP of the active roster, and _active_count excludes
    anyone on_ir. So an IR'd player is not a drop candidate -- cutting him frees no
    active spot and buys nothing -- and he is not dead weight either: he is stashed, not
    carried. Found by running this tool live, which named an IR'd QB as the drop."""
    return bool((_entry(engine, name) or {}).get("on_ir", False))


def starters_by_position(engine, team, week):
    """{position: [names]} for the players the engine's optimal assignment actually
    starts. FLEX is resolved to the player's own position, which is the whole point: a
    third WR starting at FLEX is a WR starter."""
    gaps = roster_gaps(engine, team, weeks=(week,))[week]
    out = defaultdict(list)
    for _slot, entries in gaps["starters"].items():
        for name, _value in entries:
            out[_pos(engine, name)].append(name)
    return dict(out)


def market_sweep(engine, team, week):
    """Every position: the weakest starter, the best free agent, the gain, and the drop.

    Returns {"team", "week", "replacement_levels", "rows", "upgrades", "dead_weight"}.
    `rows` carries one entry per position in SWEEP_POSITIONS; `upgrades` is the subset
    with a positive gain, ranked; `dead_weight` is every rostered player below
    DEAD_WEIGHT_VORP.
    """
    if team not in engine.rosters:
        raise KeyError(f"unknown team {team!r}")

    rep = engine.replacement_levels
    roster = list(engine.rosters[team])
    started = starters_by_position(engine, team, week)

    owned = defaultdict(list)
    for n in roster:
        owned[_pos(engine, n)].append(n)

    pool = defaultdict(list)
    for n in free_agents(engine):
        p = _pos(engine, n)
        if p in SWEEP_POSITIONS and _mean(engine, n) > 0:
            pool[p].append(n)

    rows = []
    for pos in SWEEP_POSITIONS:
        mine = sorted((n for n in owned.get(pos, []) if not _on_ir(engine, n)),
                      key=lambda n: _mean(engine, n))
        starting = sorted(started.get(pos, []), key=lambda n: _mean(engine, n))
        fas = sorted(pool.get(pos, []), key=lambda n: -_mean(engine, n))

        # The weakest man who actually starts. If nobody at this position starts, the
        # slot is effectively empty and any usable free agent is an upgrade over zero.
        slot_loser = starting[0] if starting else None
        slot_loser_mean = _mean(engine, slot_loser) if slot_loser else 0.0
        best = fas[0] if fas else None
        best_mean = _mean(engine, best) if best else 0.0
        # NOT the slot loser (B12's trap): the worst man owned at this position.
        drop = mine[0] if mine else None

        rows.append({
            "pos": pos,
            "slot_loser": slot_loser, "slot_loser_mean": slot_loser_mean,
            "n_owned": len(mine), "n_starting": len(starting),
            "best_available": best, "best_available_mean": best_mean,
            "gain": (best_mean - slot_loser_mean) if best else 0.0,
            "drop": drop, "drop_mean": _mean(engine, drop) if drop else 0.0,
            "replacement": float(rep.get(pos, 0.0)),
        })

    upgrades = sorted((r for r in rows if r["best_available"] and r["gain"] > 0),
                      key=lambda r: -r["gain"])
    dead = sorted(
        ({"name": n, "pos": _pos(engine, n), "mean": _mean(engine, n),
          "vorp": _mean(engine, n) - float(rep.get(_pos(engine, n), 0.0)),
          "starting": n in started.get(_pos(engine, n), [])}
         for n in roster
         if not _on_ir(engine, n)
         and _mean(engine, n) - float(rep.get(_pos(engine, n), 0.0)) < DEAD_WEIGHT_VORP),
        key=lambda d: d["vorp"])

    return {"team": team, "week": week, "replacement_levels": rep,
            "rows": rows, "upgrades": upgrades, "dead_weight": dead,
            "note": ("season mean points per week, the unit replacement_levels is in. "
                     "The slot-losing starter is the weakest man the engine's optimal "
                     "assignment actually starts at that position (FLEX included); the "
                     "drop is the worst ACTIVE man owned there (IR players occupy no active "
                     "slot, so cutting one frees nothing), which is usually someone "
                     "else.")}

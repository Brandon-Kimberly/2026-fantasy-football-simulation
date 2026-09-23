"""
fantasy_sim.leverage

Trade bait, two ways: whose PUBLIC price sits above the model's, and who needs what I
have spare.

B12, promoting a session scratchpad (`bait.py`). Pure -- the caller supplies the external
maps and gets dicts back -- so the fetching lives in `scripts/` and the tests are
hermetic.

**JOIN BY player_id, ALWAYS.** This module's inputs come from OUTSIDE the engine: the
league's draft (`/draft/{id}/picks`) and the F7 projection log. Both identify people the
way Sleeper does, and the raw player cache carries **220 colliding names**, seven of them
involving a player rostered in this league. A name-keyed join hands a cornerback's draft
pick to the wide receiver who shares his name -- which is precisely what the scratchpad
did. `engine.baselines[name]['player_id']` is the bridge, and every join here crosses it.

(Contrast `fantasy_sim.market`, which stays entirely on engine keys that
`sync.resolve_player_keys` has already disambiguated, and so is safe by construction. The
rule is not "always use pids"; it is "use pids wherever data enters from outside".)

**Starters come from the optimal assignment**, via `market.starters_by_position`, not from
a fixed per-position count. The scratchpad's `SLOTS = {"RB": 2, "WR": 2, ...}` ignored the
three FLEX slots, so a rival starting a below-replacement player at FLEX was invisible --
exactly the hole worth trading into.
"""
from collections import defaultdict

from fantasy_sim.config import normalize_position
from fantasy_sim.decisions import _entry
from fantasy_sim.market import starters_by_position

# A player taken this early is a premium asset in these eight managers' revealed
# preference. 60 is five rounds of an 8-team draft. UNVERIFIED: a display cutoff for
# ranking a list a human reads, not an input to any measured quantity.
EARLY_PICK = 60
SELL_HIGH_MARKDOWN = 1.5       # points/week the model has cut him by
NAME_OVER_PRODUCTION = 2.5     # a bigger cut, worth flagging even if he went late


def _pid(engine, name):
    v = (_entry(engine, name) or {}).get("player_id")
    return str(v) if v is not None else None


def _mean(engine, name):
    return float((_entry(engine, name) or {}).get("mean") or 0.0)


def _pos(engine, name):
    return normalize_position((_entry(engine, name) or {}).get("pos") or "")


def sell_high(engine, team, draft_picks, preseason):
    """One row per rostered player: what the market paid, what the model says now.

    `draft_picks` is {player_id: pick_no} and `preseason` is {player_id: mean}. Both are
    keyed by PID because both come from outside the engine -- see the module docstring.

    A player with no preseason row is returned with `preseason`/`markdown` None rather
    than dropped: silently omitting people is how a roster hole becomes invisible.
    """
    if team not in engine.rosters:
        raise KeyError(f"unknown team {team!r}")
    rep = engine.replacement_levels
    picks = {str(k): v for k, v in (draft_picks or {}).items()}
    pre = {str(k): float(v) for k, v in (preseason or {}).items()}

    rows = []
    for name in engine.rosters[team]:
        pid = _pid(engine, name)
        pos, now = _pos(engine, name), _mean(engine, name)
        p0 = pre.get(pid) if pid else None
        pick = picks.get(pid) if pid else None
        markdown = (p0 - now) if p0 is not None else None
        rows.append({
            "name": name, "player_id": pid, "pos": pos,
            "pick": pick, "preseason": p0, "now": now, "markdown": markdown,
            "vs_replacement": now - float(rep.get(pos, 0.0)),
            "sell_high": bool(markdown is not None and markdown > SELL_HIGH_MARKDOWN
                              and pick is not None and pick <= EARLY_PICK),
            "name_over_production": bool(markdown is not None
                                         and markdown > NAME_OVER_PRODUCTION),
        })
    rows.sort(key=lambda r: (r["markdown"] is None, -(r["markdown"] or 0.0)))
    return rows


def leverage(engine, team, week):
    """Per rival: the starting slots they fill BELOW replacement, and what I could send.

    A "hole" is a player the rival's own optimal assignment STARTS whose mean is under
    the replacement level for his position -- the position they need badly enough to
    overpay in kind. `i_could_send` is my best SURPLUS man there: someone I own and do
    NOT start, who is better than the man they are starting. Offering one of my own
    starters is not leverage, it is a downgrade.
    """
    if team not in engine.rosters:
        raise KeyError(f"unknown team {team!r}")
    rep = engine.replacement_levels

    my_starters = starters_by_position(engine, team, week)
    my_surplus = defaultdict(list)
    for n in engine.rosters[team]:
        p = _pos(engine, n)
        if n not in my_starters.get(p, []):
            my_surplus[p].append(n)
    for p in my_surplus:
        my_surplus[p].sort(key=lambda n: -_mean(engine, n))

    out = []
    for rival in engine.rosters:
        if rival == team:
            continue
        starting = starters_by_position(engine, rival, week)
        holes = []
        for pos, names in starting.items():
            r = float(rep.get(pos, 0.0))
            for n in names:
                mu = _mean(engine, n)
                if mu >= r:
                    continue
                spare = my_surplus.get(pos, [])
                best = spare[0] if spare and _mean(engine, spare[0]) > mu else None
                holes.append({
                    "pos": pos, "their_starter": n, "their_mean": mu,
                    "replacement": r, "deficit": r - mu,
                    "i_could_send": best,
                    "my_mean": _mean(engine, best) if best else None,
                    "upgrade_for_them": (_mean(engine, best) - mu) if best else None,
                })
        holes.sort(key=lambda h: -h["deficit"])
        out.append({"team": rival, "holes": holes})
    out.sort(key=lambda g: -(g["holes"][0]["deficit"] if g["holes"] else 0.0))
    return out

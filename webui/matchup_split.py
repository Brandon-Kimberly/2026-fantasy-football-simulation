"""webui.matchup_split -- where the matchup is decided, slot by slot (docs/WEB_UI_ROADMAP.md UI-M3).

The two lineups from the matchup record are paired slot by slot -- within a slot the owner
has twice (RB, WR, FLEX), highest projection against highest -- and each pair gets:

  p      the chance the owner's player outscores the other: a normal curve for each, with this
         week's projection as the mean (the matchup record) and the simulation's own spread for
         that player (player_variance.json's std), the two taken as independent
  share  the pair's share of the variance of the final margin, (sd_mine^2 + sd_theirs^2) over
         the sum across pairs: where the game's uncertainty actually sits. Shares sum to one.

The pairing is a presentation choice, not a real contest -- nobody's QB plays the other
QB -- and the page says so. A pair missing a spread on either side is left out and counted.
This is the pre-game read; during the games the live panel carries the remaining spread.
"""
import math

from webui.lineups import record as matchup_record


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _by_slot(lineup):
    out = {}
    for e in lineup or []:
        out.setdefault(e.get("slot"), []).append(e)
    for s in out:
        out[s].sort(key=lambda e: -(e.get("expected") or 0.0))
    return out


def split(mine, theirs, sds):
    """mine, theirs: [{slot, name, expected}]; sds: {name: sd or None}."""
    a, b = _by_slot(mine), _by_slot(theirs)
    order = []
    for e in mine or []:
        if e.get("slot") not in order:
            order.append(e.get("slot"))
    rows, left_out = [], 0
    for slot in order:
        for pm, pt in zip(a.get(slot, []), b.get(slot, [])):
            sm, st = sds.get(pm.get("name")), sds.get(pt.get("name"))
            if not sm or not st:
                left_out += 1
                continue
            mm, mt = float(pm.get("expected") or 0.0), float(pt.get("expected") or 0.0)
            var = float(sm) ** 2 + float(st) ** 2
            rows.append({"slot": slot, "mine": pm.get("name"), "theirs": pt.get("name"), "mine_mean": mm, "theirs_mean": mt,
                         "mine_sd": float(sm), "theirs_sd": float(st), "p": _phi((mm - mt) / math.sqrt(var)), "var": var})
    total = sum(r["var"] for r in rows)
    for r in rows:
        r["share"] = r["var"] / total if total else None
    return {"rows": rows, "left_out": left_out}


def for_week(root, week, my_team):
    """The split for the newest matchup record of `week`, with the spreads from that week's
    forecast (or the newest one before it); None without a record."""
    from webui.glance import odds_now
    from webui.objects import _ranges
    got = matchup_record(root, week, my_team)
    if not got:
        return None
    rec, stamp = got
    cons = rec.get("constructions") or {}
    mine = (cons.get("max_mean") or next(iter(cons.values()), {})).get("lineup") or []
    theirs = rec.get("opponent_lineup") or []
    fw = odds_now(root)["week"]
    sds = {}
    for team, lineup in ((my_team, mine), (rec.get("opponent"), theirs)):
        rg = _ranges(root, fw, team) if team else {}
        for e in lineup:
            sds[e.get("name")] = (rg.get(e.get("name")) or {}).get("std")
    return dict(split(mine, theirs, sds), opponent=rec.get("opponent"), stamp=stamp)

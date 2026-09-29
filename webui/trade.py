"""
webui.trade -- the trade builder's instant estimate (docs/WEB_UI_ROADMAP.md UI-T1).

The paired simulation (scripts.evaluate_trade) takes minutes and prices playoff and title
odds. This answers the first question at once: what the deal does to each side's best
starting lineup, week by week over the rest of the regular season.

  - The best lineup is an exact assignment of players to the league's 13 starting slots
    (config.REQUIRED_STARTING_SLOTS; eligibility from config.eligible_slots, Sleeper's own
    fantasy_positions; FLEX takes RB/WR/TE), solved with scipy's linear_sum_assignment --
    not a greedy fill, which can strand a dual-eligible player.
  - Each week's lineup leaves out players on bye that week and players on IR.
  - A side that ends above the active limit must drop; the likely cut is its weakest
    non-starter, and his season average is the cost of the roster spot.
  - Values are season averages (player_baselines). It is an ESTIMATE and the page says so:
    no injuries to come, no matchups, no rest-of-league response, no odds. The page links
    the same deal to the paired simulation for those.

Reads only; config is the one fantasy_sim module imported, and only for the roster format.
"""
from urllib.parse import urlencode

from fantasy_sim.config import REQUIRED_STARTING_SLOTS, eligible_slots

# docs/WAIVER_MECHANICS.md: roster_positions is 19 active spots (13 starters + 6 bench; the two
# IR slots are separate). The same number as fantasy_sim.decisions.ACTIVE_ROSTER_LIMIT, which the
# web UI may not import; tests.test_webui_wave4 pins the two equal.
ACTIVE_ROSTER_LIMIT = 19
FLEX_POSITIONS = ("RB", "WR", "TE")      # FLEX eligibility, as the engine's decisions._SLOT_POSITIONS
LAST_REGULAR_WEEK = 14                   # docs/WAIVER_MECHANICS.md: playoff_week_start 15


def _slots(name, entry):
    s = list(eligible_slots(name, entry))
    if any(p in FLEX_POSITIONS for p in s):
        s.append("FLEX")
    return s


def _assign(players, week=None, slots=REQUIRED_STARTING_SLOTS):
    """[(slot index, name, mean)] for the best assignment: the highest total expected points
    an assignment of {name: baseline entry} to `slots` can reach, leaving out anyone on IR
    and, when `week` is given, anyone on bye that week."""
    from scipy.optimize import linear_sum_assignment
    pool = [(n, e) for n, e in players.items()
            if isinstance(e, dict) and not e.get("on_ir") and not (week is not None and e.get("bye") == week)]
    if not pool:
        return []
    big = 1e6
    cost = []
    for slot in slots:
        row = []
        for n, e in pool:
            ok = slot in _slots(n, e)
            row.append(-float(e.get("mean") or 0.0) if ok else big)
        cost.append(row)
    r, c = linear_sum_assignment(cost)
    return [(int(i), pool[j][0], -cost[i][j]) for i, j in zip(r, c) if cost[i][j] < big]


def best_lineup(players, week=None, slots=REQUIRED_STARTING_SLOTS):
    """(total, [names starting], empty slots) for {name: baseline entry} -- see _assign."""
    got = _assign(players, week, slots)
    total, used = 0.0, []
    for _i, name, mean in got:
        total += mean
        used.append(name)
    return round(total, 2), used, len(slots) - len(used)


def _slot_labels(slots=REQUIRED_STARTING_SLOTS):
    """QB, RB 1, RB 2, WR 1, WR 2, FLEX 1... -- a slot the lineup has twice is numbered."""
    seen, out = {}, []
    for sl in slots:
        seen[sl] = seen.get(sl, 0) + 1
        out.append(f"{sl} {seen[sl]}" if slots.count(sl) > 1 else sl)      # "WR 1": "WR1" reads as an audit code (R1)
    return out


def factors(before, after, weeks):
    """UI-T3: why a side's number moves, computed rather than narrated -- the starters that go
    out and come in, the starting line slot by slot (season averages), the weeks two or more
    of the new starters share a bye that fewer did before, and each position's active depth."""
    labels = _slot_labels()
    b_slots = {i: (n, m) for i, n, m in _assign(before)}
    a_slots = {i: (n, m) for i, n, m in _assign(after)}
    b_names = {n for n, _m in b_slots.values()}
    a_names = {n for n, _m in a_slots.values()}
    slots = []
    for i, label in enumerate(labels):
        bn, bm = b_slots.get(i, (None, 0.0))
        an, am = a_slots.get(i, (None, 0.0))
        if bn != an:
            slots.append({"slot": label, "before": bn, "after": an, "before_mean": round(bm, 2) if bn else None,
                          "after_mean": round(am, 2) if an else None, "delta": round(am - bm, 2)})
    byes = []
    for w in weeks:
        a_off = sorted(n for n in a_names if (after.get(n) or {}).get("bye") == w)
        b_off = [n for n in b_names if (before.get(n) or {}).get("bye") == w]
        if len(a_off) >= 2 and len(a_off) > len(b_off):
            byes.append({"week": w, "players": a_off})
    depth = {}
    for side, roster in (("before", before), ("after", after)):
        for n, e in roster.items():
            if e.get("on_ir"):
                continue
            pos = e.get("pos") or "?"
            depth.setdefault(pos, {"before": 0, "after": 0})[side] += 1
    return {"out": sorted(b_names - a_names), "in": sorted(a_names - b_names), "slots": slots, "byes": byes,
            "depth": dict(sorted(depth.items()))}


def _active(roster, base):
    return {e["name"]: (base.get(e["name"]) or {"pos": e.get("pos"), "mean": 0.0, "on_ir": e.get("on_ir")})
            for e in roster or [] if e.get("name")}


def estimate(root, team_a, a_gives, team_b, b_gives):
    """Both sides of a deal: per-week lineup before and after, the average change, the weeks
    the deal leaves a slot empty that was filled before, and the forced drops."""
    from webui.glance import freshness_report
    base = root.read_json("current/player_baselines.json", {}) or {}
    rosters = root.read_json("current/live_rosters.json", {}) or {}
    cur = freshness_report(root)["week"]
    cur = int(cur) if cur else 1
    weeks = list(range(cur, LAST_REGULAR_WEEK + 1))
    a_gives, b_gives = [n for n in a_gives if n], [n for n in b_gives if n]
    sides = {}
    for team, gives, gets in ((team_a, a_gives, b_gives), (team_b, b_gives, a_gives)):
        before = _active(rosters.get(team), base)
        after = {n: e for n, e in before.items() if n not in gives}
        for n in gets:
            after[n] = base.get(n) or {"pos": "?", "mean": 0.0}
        rows, holes = [], []
        for w in weeks:
            b_total, _bu, b_empty = best_lineup(before, w)
            a_total, _au, a_empty = best_lineup(after, w)
            rows.append({"week": w, "before": b_total, "after": a_total, "delta": round(a_total - b_total, 2),
                         "empty_before": b_empty, "empty_after": a_empty})
            if a_empty > b_empty:
                holes.append(w)
        active_after = sum(1 for e in after.values() if not e.get("on_ir"))
        over = max(0, active_after - ACTIVE_ROSTER_LIMIT)
        _t, starters, _e = best_lineup(after)
        bench = sorted(((n, float(e.get("mean") or 0.0)) for n, e in after.items()
                        if n not in starters and not e.get("on_ir")), key=lambda x: x[1])
        sides[team] = {"team": team, "gives": gives, "gets": gets, "weeks": rows,
                       "per_week": round(sum(r["delta"] for r in rows) / len(rows), 2) if rows else 0.0,
                       "total": round(sum(r["delta"] for r in rows), 1), "new_holes": holes,
                       "active_after": active_after, "over": over,
                       "drops": [{"name": n, "mean": m} for n, m in bench[:over]],
                       "factors": factors(before, after, weeks)}                                    # UI-T3
    simulate = "/tools/evaluate_trade?" + urlencode({"team_a": team_a, "a_gives": ", ".join(a_gives),
                                                      "team_b": team_b, "b_gives": ", ".join(b_gives)})
    return {"sides": sides, "weeks": weeks, "simulate": simulate, "a": team_a, "b": team_b}


def needs(root):
    """UI-T6: each team's positional need -- the position whose starters (its best lineup from
    season averages) fall furthest below the league's average for that position. None for a
    team at or above the league everywhere."""
    base = root.read_json("current/player_baselines.json", {}) or {}
    rosters = root.read_json("current/live_rosters.json", {}) or {}
    by_team = {}
    for team, roster in rosters.items():
        active = _active(roster, base)
        tot = {}
        for _i, name, mean in _assign(active):
            pos = (active.get(name) or {}).get("pos") or "?"
            tot[pos] = tot.get(pos, 0.0) + float(mean)
        by_team[team] = tot
    positions = {p for t in by_team.values() for p in t}
    avg = {p: sum(t.get(p, 0.0) for t in by_team.values()) / max(1, len(by_team)) for p in positions}
    out = {}
    for team, tot in by_team.items():
        short = {p: avg[p] - tot.get(p, 0.0) for p in positions}
        worst = max(short, key=lambda p: short[p]) if short else None
        out[team] = worst if worst is not None and short[worst] > 0 else None
    return out

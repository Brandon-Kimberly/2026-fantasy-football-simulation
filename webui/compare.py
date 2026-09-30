"""
webui.compare -- "start A or B?" answered at once (docs/WEB_UI_ROADMAP.md UI-P4).

The compare tool runs the joint simulation and takes about a minute. The common case can be
answered immediately from each player's own distribution: priced exactly as the live panel
prices a starter (webui.live.expectations -- this week's lineup or matchup record where one
priced him, else the season baseline; sd from the aleatoric and epistemic parts), each
treated as an independent Normal. For two players that is P(A > B); for three or four, each
one's chance of the top score, integrated on a grid.

It is an ESTIMATE and says so: it ignores same-game links (a quarterback and his own
receiver move together) and the chance a player sits, both of which the joint simulation
prices. Where a joint answer for the same pair and week is on file it is returned too, and
the page shows it in place of the estimate. Pure reading; nothing is written.
"""
import glob
import math
import os

from webui import idp as idpmod
from webui.live import expectations
from webui.players import PlayerIndex

Z80 = 1.2815515655446004          # the 90th percentile of the standard Normal: an 80% range


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def p_top(players, steps=4000):
    """Each player's chance of the highest score, independent Normals, by integrating
    f_i(x) * prod_j F_j(x) on a grid. Sums to 1 up to the grid's error."""
    sds = [max(float(p["sd"]), 0.01) for p in players]
    means = [float(p["mean"]) for p in players]
    lo = min(m - 7 * s for m, s in zip(means, sds))
    hi = max(m + 7 * s for m, s in zip(means, sds))
    dx = (hi - lo) / steps
    out = [0.0] * len(players)
    for k in range(steps + 1):
        x = lo + k * dx
        cdf = [_phi((x - m) / s) for m, s in zip(means, sds)]
        w = 0.5 if k in (0, steps) else 1.0
        for i, (m, s) in enumerate(zip(means, sds)):
            prod = 1.0
            for j, c in enumerate(cdf):
                if j != i:
                    prod *= c
            out[i] += w * _pdf((x - m) / s) / s * prod * dx
    total = sum(out) or 1.0
    return [v / total for v in out]


def joint_answer(root, a, b, week):
    """The newest joint compare record for {a, b} in `week`, as P(a beats b); None without one."""
    best = None
    for rel in _compare_records(root):
        d = root.read_json(rel, {}) or {}
        if d.get("tool") != "compare_players" or str(d.get("week")) != str(week):
            continue
        pair = (d.get("a_name"), d.get("b_name"))
        if set(pair) != {a, b} or d.get("p_a") is None:
            continue
        stamp = d.get("timestamp_utc") or ""
        if best is None or stamp > best[0]:
            p_first = float(d["p_a"]) if pair[0] == a else float(d.get("p_b") if d.get("p_b") is not None else 1 - float(d["p_a"]))
            best = (stamp, {"p_first": round(p_first, 4), "se": d.get("se_p"), "n": d.get("n"), "stamp": stamp,
                            "rel": rel, "path": d.get("path")})
    return best[1] if best else None


def _compare_records(root):
    """Every compare record's data-relative path: ad-hoc ones and any filed under a week."""
    base = str(root.data)
    out = []
    for pat in (os.path.join(base, "decisions", "adhoc", "compare_*.json"),
                os.path.join(base, "decisions", "week_*", "compare_*.json")):
        for full in glob.glob(pat):
            out.append(os.path.relpath(full, base).replace(os.sep, "/"))
    return out


def estimate(root, names, week=None):
    """{week, players: [{name, pos, nfl, owner, mean, sd, p10, p90, source, p_top} | {name,
    unknown}], p_first_beats_second, joint}."""
    if week is None:
        from webui.glance import freshness_report
        week = freshness_report(root)["week"]
    week = int(week) if week else None
    idx = PlayerIndex.for_root(root)
    by_name = {r["name"]: dict(r, pid=pid) for pid, r in (expectations(root, week) if week else {}).items()}
    rows = []
    for n in names:
        p = idx._by_fold.get(str(n or "").strip().casefold())
        e = by_name.get(p["name"]) if p else None
        if not p or not e:
            rows.append({"name": (p or {}).get("name") or str(n or "").strip(), "unknown": True})
            continue
        mean, sd = float(e["mean"]), float(e["sd"])
        rows.append({"name": p["name"], "pos": p.get("pos"), "nfl": p.get("nfl"), "owner": p.get("owner"),
                     "mean": round(mean, 2), "sd": round(sd, 2), "p10": round(max(0.0, mean - Z80 * sd), 1),
                     "p90": round(mean + Z80 * sd, 1), "source": e.get("source") or "baseline", "unknown": False})
    known = [r for r in rows if not r["unknown"]]
    pair = None
    if len(known) >= 2 and len(known) == len(rows):
        for r, q in zip(known, p_top(known)):
            r["p_top"] = round(q, 4)
        if len(known) == 2:
            a, b = known
            pair = round(_phi((a["mean"] - b["mean"]) / math.sqrt(max(a["sd"] ** 2 + b["sd"] ** 2, 1e-9))), 4)
    joint = joint_answer(root, rows[0]["name"], rows[1]["name"], week) if len(rows) == 2 and week else None
    idp = idpmod.table(root, [{"name": r["name"], "pid": (by_name.get(r["name"]) or {}).get("pid")} for r in known]) \
        if len(known) >= 2 and len(known) == len(rows) else None               # UI-P8: defenders side by side
    return {"week": week, "players": rows, "p_first_beats_second": pair, "joint": joint, "idp": idp}

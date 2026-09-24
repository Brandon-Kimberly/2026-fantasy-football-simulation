"""Is the engine's streamer worth what the actual free-agent pool is worth? (C5)

The engine fills a slot nobody on a roster can cover with a STREAMER, scored
`max(0, N(m_str, 2.2))` where

    m_str = max(replacement_level[pos] * 0.8, BASE_STREAMER_MEANS[pos])

Every evaluation of a hole is priced against that number: `evaluate_move` on an add at
that position, the season simulation for a team carrying the hole, and (since C2) the
matchup tool and the weekly League table. If `m_str` sits below what is genuinely claimable
for $0, filling the hole looks more valuable than it is.

WHAT THIS MODULE DOES AND DOES NOT DO. It measures and reports. It changes no constant:
`BASE_STREAMER_MEANS` is a sync-time/init constant read by every hole evaluation, so moving
it is a MAJOR release (the model's predictions change materially) and is the owner's call,
not a side effect of a study.

THE CAP IS NOT OPTIONAL. Phase 4 capped won streamers at the replacement level precisely so
a streamer can never out-project a rostered starter -- otherwise a HOLE becomes worth more
than a player and every tool starts recommending you empty a slot. So the derived
alternative below is `min(pool_top3_mean, replacement_level)`, and the cap is reported
separately wherever it binds.

FLEX IS A SLOT, NOT A POSITION, and the naive reading misses it. An unfilled FLEX streams at
`BASE_STREAMER_MEANS['FLEX']`, but no free agent normalises to "FLEX" -- the pool that can
actually fill it is RB, WR and TE together. Measuring FLEX against an empty pool would
report a meaningless -8.5 gap; it is measured against the union.

n IS SMALL AND THAT IS STATED, NOT HIDDEN. The backlog assumed `projection_log.jsonl`
carries the free-agent history. It does not: that log is one line per ROSTERED player, so a
player who has been free all season never appears in it and the pool cannot be
reconstructed. `record()` appends one row per run so the four-sync comparison the item
wanted becomes possible going forward.
"""
from fantasy_sim.config import BASE_STREAMER_MEANS, normalize_position
from fantasy_sim.decisions import free_agents

# The slots the league actually starts, plus FLEX. `_SLOT_POSITIONS`-style eligibility.
FLEX_POSITIONS = ("RB", "WR", "TE")
TOP_N = 3


def _mean(engine, name):
    e = engine.baselines.get(name) or {}
    return float(e.get("mean", 0.0)) if isinstance(e, dict) else 0.0


def free_agent_pool(engine):
    """{position: [means, descending]} over players on no roster.

    FLEX is synthesised from RB/WR/TE, because that is what can legally fill the slot.
    """
    pool = {}
    for n in free_agents(engine):
        e = engine.baselines.get(n) or {}
        pool.setdefault(normalize_position(e.get("pos", "FLEX")), []).append((_mean(engine, n), n))
    flex = [x for p in FLEX_POSITIONS for x in pool.get(p, [])]
    if flex:
        pool["FLEX"] = flex
    for p in pool:
        pool[p].sort(reverse=True)
    return pool


def streamer_gap(engine, top_n=TOP_N):
    """One row per position in BASE_STREAMER_MEANS. Measurement only."""
    pool = free_agent_pool(engine)
    rows = []
    for pos in sorted(BASE_STREAMER_MEANS):
        lst = pool.get(pos, [])
        rep = float(engine.replacement_levels.get(pos, 4.0))
        base = float(BASE_STREAMER_MEANS[pos])
        m_str = max(rep * 0.8, base)
        top = [v for v, _n in lst[:top_n]]
        top_mean = (sum(top) / len(top)) if top else None
        # Phase 4's cap: a streamer may never out-project the replacement level, or a hole
        # becomes worth more than a starter.
        derived = min(top_mean, rep) if top_mean is not None else None
        rows.append({
            "pos": pos, "base": base, "replacement": round(rep, 2),
            "m_str": round(m_str, 2),
            "pool_n": len(lst),
            "top1": round(lst[0][0], 2) if lst else None,
            "top_names": [n for _v, n in lst[:top_n]],
            "top_mean": round(top_mean, 2) if top_mean is not None else None,
            "gap": round(top_mean - m_str, 2) if top_mean is not None else None,
            "derived_capped": round(derived, 2) if derived is not None else None,
            "cap_binds": bool(top_mean is not None and top_mean > rep),
            "derived_gap": round(derived - m_str, 2) if derived is not None else None,
        })
    return {"rows": rows, "top_n": top_n,
            "note": ("MEASUREMENT ONLY -- no constant is changed here. `gap` is the raw "
                     "pool premium; `derived_capped` is what a pool-derived streamer would "
                     "be under Phase 4's cap (never above the replacement level, so a hole "
                     "can never be worth more than a starter). Changing "
                     "BASE_STREAMER_MEANS is a MAJOR release.")}


def render_lines(study):
    out = ["STREAMER LEVELS vs the live free-agent pool  (top "
           f"{study['top_n']} free agents per position)",
           f"  {'pos':5s} {'BASE':>6s} {'repl':>6s} {'m_str':>6s} {'poolTop':>8s} "
           f"{'gap':>7s} {'capped':>7s} {'vs m_str':>9s} {'n':>5s}  best available"]
    for r in study["rows"]:
        if r["top_mean"] is None:
            out.append(f"  {r['pos']:5s} {r['base']:6.2f} {r['replacement']:6.2f} "
                       f"{r['m_str']:6.2f}   -- no free agent at this position --")
            continue
        out.append(f"  {r['pos']:5s} {r['base']:6.2f} {r['replacement']:6.2f} {r['m_str']:6.2f} "
                   f"{r['top_mean']:8.2f} {r['gap']:+7.2f} {r['derived_capped']:7.2f} "
                   f"{r['derived_gap']:+9.2f} {r['pool_n']:5d}  "
                   + ", ".join(r["top_names"]) + ("   [cap binds]" if r["cap_binds"] else ""))
    worst = sorted((r for r in study["rows"] if r["derived_gap"] is not None),
                   key=lambda r: -r["derived_gap"])[:3]
    if worst:
        out.append("  biggest understatements: "
                   + "; ".join(f"{r['pos']} {r['derived_gap']:+.2f}" for r in worst))
    out.append("  " + study["note"])
    return out

#!/usr/bin/env python3
"""
Head-to-head start/sit comparator: P(A > B) for one NFL week from the players' simulated
distributions (fantasy_sim.decisions.compare_players), not a mean comparison.

  py -3.10 -m scripts.compare_players "Player A" "Player B" [--week N] [--sims 2000]
                                      [--seed S] [--light]

Both rostered: a reduced simulation (default 2,000 seasons, ~2 min) and a sim-by-sim
comparison on the week's column -- the joint distribution (copula, shared environment and
injury state). A free agent is sampled from his baseline parameters through the engine's own
transform, independently. --light samples both that way and skips the simulation.

Reads data/current/ only. Writes one JSON record under data/decisions/. Never touches the
season exports.
"""
import argparse
import datetime as _dt
import json
import re

from fantasy_sim.decisions import compare_players
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_adhoc_path, save_json


def _resolve(engine, query):
    """Exact key first; otherwise a unique case-insensitive match on the baseline pool."""
    if query in engine.baselines:
        return query
    hits = [n for n in engine.baselines if n.lower() == query.lower()]
    if not hits:
        hits = [n for n in engine.baselines if query.lower() in n.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"no player matching {query!r} in the baseline pool")
    raise SystemExit(f"{query!r} is ambiguous: {hits[:8]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--week", type=int, default=None, help="NFL week (default: current week)")
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--light", action="store_true", help="sample both from baseline parameters; no simulation")
    args = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    a, b = _resolve(engine, args.a), _resolve(engine, args.b)
    week = args.week or engine.current_week
    r = compare_players(engine, a, b, week=week, sims=args.sims, seed=args.seed, light=args.light)

    def row(tag, name, s):
        return (f"  {tag} {name:28s} mean {s['mean']:5.1f}  p10 {s['p10']:5.1f}  p50 {s['p50']:5.1f}  "
                f"p90 {s['p90']:5.1f}  zero {100 * s['p_zero']:4.1f}%")
    print(f"\nWeek {week} -- {a} vs {b}  [{r['path']}, n={r['n']}]")
    print(row("A", a, r['a'])); print(row("B", b, r['b']))
    print(f"  P(A > B) = {100 * r['p_a']:.1f}%   P(B > A) = {100 * r['p_b']:.1f}%   tie {100 * r['p_tie']:.1f}%   "
          f"(+-{100 * r['se_p']:.1f} pts)   mean diff {r['mean_diff']:+.2f}")
    print(f"  note: {r['note']}")

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{a}_vs_{b}")[:60]
    out = decisions_adhoc_path(f"compare_{stamp}_{slug}.json")
    save_json(out, {"timestamp_utc": stamp, "tool": "compare_players", **r})
    print(f"  logged -> {out}")
    return r


if __name__ == "__main__":
    main()

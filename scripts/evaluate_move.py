#!/usr/bin/env python3
"""
Single-roster move evaluator: what is adding X (and dropping Y) worth, in the same paired
Champ%/Playoff%/expected-wins terms tool 2 uses for trades
(fantasy_sim.decisions.evaluate_add_drop)? One roster changes; there is no counterparty.

  py -3.10 -m scripts.evaluate_move --add "Player X" [--drop "Player Y"] [--team T]
        [--batches 10] [--sims 300]
  py -3.10 -m scripts.evaluate_move --log-tx TRANSACTION_ID     # a move already in the decision log

Adds resolve against the free-agent pool (the baseline file), drops against the team's
roster. --log-tx evaluates a logged add/drop or waiver claim (or trade -- it dispatches on
type) and appends the evaluation record to data/logs/decision_log.jsonl. Reads data/current/
only; hypothetical evaluations write one JSON record under data/decisions/.
"""
import argparse
import datetime as _dt
import platform
import subprocess
import sys

from fantasy_sim.config import MY_TEAM
from fantasy_sim.decisions import evaluate_add_drop, evaluate_logged_transaction
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_adhoc_path, save_json


def _git(*args):
    try:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def _resolve(pool, query, what):
    if query in pool:
        return query
    hits = [n for n in pool if query.lower() in n.lower()]
    if len(hits) == 1:
        return hits[0]
    raise SystemExit(f"{query!r} ({what}): {'no match' if not hits else 'ambiguous ' + str(hits[:6])}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-tx", default=None, metavar="TRANSACTION_ID")
    ap.add_argument("--team", default=MY_TEAM)
    ap.add_argument("--add", default="", help="comma-separated free agents to add")
    ap.add_argument("--drop", default="", help="comma-separated rostered players to drop")
    ap.add_argument("--bid", type=int, default=None, help="FAAB bid: adds the separated budget-cost block")
    ap.add_argument("--batches", type=int, default=10)
    ap.add_argument("--sims", type=int, default=300)
    args = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    if args.log_tx:
        r = evaluate_logged_transaction(engine, args.log_tx, batches=args.batches, sims=args.sims)
        if r.get("skipped"):
            print(f"[SKIP] transaction {args.log_tx}: {r['skipped']}")
            return r
        m = r.get("move") or r.get("trade")
        print(f"\nLOGGED {('MOVE' if 'move' in r else 'TRADE')} {args.log_tx}: {m}")
        focus = [m["team"]] if "move" in r else [m["team_a"], m["team_b"]]
        for t in focus:
            d = r["teams"][t]
            print(f"  {t:18s} Champ {d['champ_pct']['delta']:+6.2f}+-{d['champ_pct']['se']:.2f}  "
                  f"Playoff {d['playoff_pct']['delta']:+6.2f}+-{d['playoff_pct']['se']:.2f}  "
                  f"ExpW {d['expected_wins']['delta']:+6.3f}+-{d['expected_wins']['se']:.3f}")
        print(f"  {r['note']}")
        print("  evaluation record appended to the decision log.")
        return r

    rostered = {n for ros in engine.rosters.values() for n in ros}
    pool = [n for n in engine.baselines if n not in rostered]
    adds = [_resolve(pool, q.strip(), "free agent") for q in args.add.split(",") if q.strip()]
    drops = [_resolve(engine.rosters[args.team], q.strip(), f"on {args.team}") for q in args.drop.split(",") if q.strip()]
    if not adds and not drops:
        raise SystemExit("nothing to evaluate: give --add and/or --drop, or --log-tx")

    print(f"\nMOVE: {args.team} adds {adds or 'nothing'}, drops {drops or 'nothing'}   "
          f"{args.batches} x {args.sims} = {args.batches * args.sims} paired seasons per arm ...")
    r = evaluate_add_drop(engine, args.team, adds, drops, batches=args.batches, sims=args.sims,
                          faab_bid=args.bid)
    if args.bid is not None:
        from fantasy_sim.decisions import faab_context
        r["faab"] = faab_context(engine, args.bid, args.team)
    print(f"\n  {'team':18s} {'side':9s} | {'Champ%':>7s} {'delta':>7s} {'+-SE':>5s} | "
          f"{'Playoff%':>8s} {'delta':>7s} {'+-SE':>5s} | {'ExpW':>5s} {'delta':>6s} {'+-SE':>5s}")
    order = sorted(r["teams"], key=lambda t: r["teams"][t]["side"] != "team")
    for t in order:
        d = r["teams"][t]; c, pp, w = d["champ_pct"], d["playoff_pct"], d["expected_wins"]
        print(f"  {t:18s} {d['side']:9s} | {c['without']:7.2f} {c['delta']:+7.2f} {c['se']:5.2f} | "
              f"{pp['without']:8.2f} {pp['delta']:+7.2f} {pp['se']:5.2f} | {w['without']:5.2f} {w['delta']:+6.3f} {w['se']:5.3f}")
    print(f"  {r['note']}")
    if r.get("faab"):
        fb = r["faab"]
        print(f"\n  BUDGET COST (separate from the roster-change value above):")
        print(f"  bid {fb['bid']}  |  {args.team} remaining FAAB {fb['remaining_faab']:.0f} "
              f"(league avg {fb['league_avg_faab']:.0f})")
        for c in fb["comparables"]:
            retro = " (retro snapshot)" if c.get("snapshot_is_retroactive") else ""
            print(f"    comparable: bid {c['bid']:>3} -- {c['player']} ({c['pos']}, proj {c['proj_mean']:.1f}, "
                  f"VORP {c['vorp']:+.1f}) by {c['team']}, week {c['week']}{retro}")
        if fb["market"]:
            m = fb["market"]
            per = f", median bid/VORP {m['median_bid_per_vorp']:.2f}" if "median_bid_per_vorp" in m else ""
            print(f"  market: median bid {m['median_bid']}{per} (n={m['n']})")
        else:
            print(f"  market: {fb['market_note']}")

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record = {"timestamp_utc": stamp, "tool": "evaluate_move", "git_commit": _git("rev-parse", "HEAD"),
               "git_dirty": bool(_git("status", "--porcelain")), "python": sys.version.split()[0],
               "python_executable": sys.executable, "machine": platform.node(), **r}
    out = decisions_adhoc_path(f"move_{stamp}.json")
    save_json(out, record)
    print(f"  commit {record['git_commit']}{' (dirty)' if record['git_dirty'] else ''}  python {record['python']}\n  logged -> {out}")
    return r


if __name__ == "__main__":
    main()

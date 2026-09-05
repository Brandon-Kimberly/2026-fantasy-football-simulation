#!/usr/bin/env python3
"""
Hypothetical trade evaluator: two PAIRED full simulations (with and without the trade, same
seeds) and the real Champ_Pct / Playoff_Pct / expected-wins delta for both sides and every
bystander, each with a paired-batch standard error (fantasy_sim.decisions.evaluate_trade).

  py -3.10 -m scripts.evaluate_trade --team-a "Quantum Ferrets" --a-gives "Tony Pollard"
        --team-b "Neon Walruses" --b-gives "Player X, Player Y"
        [--a-drops "..."] [--b-drops "..."] [--batches 10] [--sims 300]

Default 10 x 300 = 3,000 seasons per arm (~3 min each at production speed). Reads
data/current/ only; writes one JSON record under data/decisions/, stamped with the git commit
and Python interpreter (F12 is open). Never touches the season exports.
"""
import argparse
import datetime as _dt
import platform
import subprocess
import sys

from fantasy_sim.decisions import evaluate_trade
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_adhoc_path, save_json


def _git(*args):
    try:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def _names(engine, team, text):
    out = []
    for q in [x.strip() for x in (text or "").split(",") if x.strip()]:
        if q in engine.rosters[team]:
            out.append(q); continue
        hits = [n for n in engine.rosters[team] if q.lower() in n.lower()]
        if len(hits) != 1:
            raise SystemExit(f"{q!r} on {team}: {'no match' if not hits else 'ambiguous ' + str(hits)}")
        out.append(hits[0])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-tx", default=None, metavar="TRANSACTION_ID",
                    help="evaluate a trade already in data/logs/decision_log.jsonl and append the "
                         "evaluation record to it (see the weekly digest's Housekeeping list)")
    ap.add_argument("--team-a", default=None); ap.add_argument("--a-gives", default="")
    ap.add_argument("--team-b", default=None); ap.add_argument("--b-gives", default="")
    ap.add_argument("--a-drops", default=""); ap.add_argument("--b-drops", default="")
    ap.add_argument("--a-faab", type=int, default=0, help="FAAB team A sends to team B (recorded, unpriced -- F31)")
    ap.add_argument("--b-faab", type=int, default=0, help="FAAB team B sends to team A (recorded, unpriced -- F31)")
    ap.add_argument("--batches", type=int, default=10); ap.add_argument("--sims", type=int, default=300)
    args = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    if args.log_tx:
        from fantasy_sim.decisions import evaluate_logged_transaction
        r = evaluate_logged_transaction(engine, args.log_tx, batches=args.batches, sims=args.sims)
        if r.get("skipped"):
            print(f"[SKIP] transaction {args.log_tx}: {r['skipped']}")
            return r
        if "trade" in r:
            head = (f"LOGGED TRADE {args.log_tx}: {r['trade']['team_a']} gives {r['trade']['a_gives']} <-> "
                    f"{r['trade']['team_b']} gives {r['trade']['b_gives']}")
            focus = (r['trade']['team_a'], r['trade']['team_b'])
        else:
            m = r["move"]
            bid = f", bid {m['faab_bid']}" if m.get("faab_bid") is not None else ""
            head = f"LOGGED MOVE {args.log_tx}: {m['team']} adds {m['adds']} drops {m['drops']}{bid}"
            focus = (m["team"],)
        print(f"\n{head}  ({r['n_sims']} paired seasons per arm)")
        for t in focus:
            d = r["teams"][t]
            print(f"  {t:18s} Champ {d['champ_pct']['delta']:+6.2f}+-{d['champ_pct']['se']:.2f}  "
                  f"Playoff {d['playoff_pct']['delta']:+6.2f}+-{d['playoff_pct']['se']:.2f}  "
                  f"ExpW {d['expected_wins']['delta']:+6.3f}+-{d['expected_wins']['se']:.3f}")
        print(f"  {r['note']}")
        print("  evaluation record appended to the decision log.")
        return r
    if not args.team_a or not args.team_b:
        raise SystemExit("either --log-tx, or both --team-a and --team-b, are required")
    for t in (args.team_a, args.team_b):
        if t not in engine.rosters:
            raise SystemExit(f"unknown team {t!r}; teams: {sorted(engine.rosters)}")
    a_gives, b_gives = _names(engine, args.team_a, args.a_gives), _names(engine, args.team_b, args.b_gives)
    drops = {}
    # drops are taken from the POST-trade rosters, so a received player may be dropped too
    if args.a_drops: drops[args.team_a] = [x.strip() for x in args.a_drops.split(",") if x.strip()]
    if args.b_drops: drops[args.team_b] = [x.strip() for x in args.b_drops.split(",") if x.strip()]

    print(f"\nTRADE: {args.team_a} gives {a_gives or 'nothing'}  <->  {args.team_b} gives {b_gives or 'nothing'}"
          + (f"   drops {drops}" if drops else "") + f"\n  {args.batches} x {args.sims} = {args.batches * args.sims} paired seasons per arm ...")
    faab_net = int(args.a_faab) - int(args.b_faab)
    r = evaluate_trade(engine, args.team_a, a_gives, args.team_b, b_gives, drops=drops or None,
                       batches=args.batches, sims=args.sims, faab_a_to_b=faab_net or None)

    print(f"\n  {'team':18s} {'side':9s} | {'Champ%':>7s} {'delta':>7s} {'+-SE':>5s} | {'Playoff%':>8s} {'delta':>7s} {'+-SE':>5s} | {'ExpW':>5s} {'delta':>6s} {'+-SE':>5s}")
    order = sorted(r["teams"], key=lambda t: {"A": 0, "B": 1}.get(r["teams"][t]["side"], 2))
    for t in order:
        d = r["teams"][t]; c, p, w = d["champ_pct"], d["playoff_pct"], d["expected_wins"]
        print(f"  {t:18s} {d['side']:9s} | {c['without']:7.2f} {c['delta']:+7.2f} {c['se']:5.2f} | "
              f"{p['without']:8.2f} {p['delta']:+7.2f} {p['se']:5.2f} | {w['without']:5.2f} {w['delta']:+6.3f} {w['se']:5.3f}")
    print(f"  {r['note']}")
    if r.get("faab_note"):
        print(f"\n  FAAB (unpriced): {r['faab_note']}")

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record = {"timestamp_utc": stamp, "tool": "evaluate_trade", "git_commit": _git("rev-parse", "HEAD"),
              "git_dirty": bool(_git("status", "--porcelain")), "python": sys.version.split()[0],
              "python_executable": sys.executable, "machine": platform.node(), **r}
    out = decisions_adhoc_path(f"trade_{stamp}.json")
    save_json(out, record)
    print(f"  commit {record['git_commit']}{' (dirty)' if record['git_dirty'] else ''}  python {record['python']}\n  logged -> {out}")
    return r


if __name__ == "__main__":
    main()

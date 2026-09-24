#!/usr/bin/env python3
"""
Running bid calibration: what I suggested, what I bid, what it cost (fantasy_sim.bid_ledger).

  py -3.10 -m scripts.bid_review                     # the ledger, reconciled and scored
  py -3.10 -m scripts.bid_review --add "Player" --bid 25 [--week N]   # record a claim
  py -3.10 -m scripts.bid_review --record-rivals "Player" --rivals 21,20   # the losing bids

RECORD THE LOSING BIDS AFTER EVERY RUN (T2). Sleeper shows them, and best-rival + 1 is the
EXACT clearing price. Without it a claim I WON gives only an upper bound on the price, and
the scoring has to excuse any suggestion below my own bid (B13's censoring). With it,
"would this suggestion have won?" is answerable whether I won or lost. The `paid` and
`clears` columns are different questions -- Sleeper is a first-price auction, so the winner
pays their own bid -- and both are kept.

Record a claim when you PLACE it, not after. The decision log only ever contains
COMPLETED transactions -- a claim you lost never becomes one -- so waiting until the
waiver run has happened loses exactly the half of the data that calibrates a bid.

WHAT THIS IS FOR. F61 measured, on all 26 logged 2026 claims, a correlation between a
player's VORP and his winning bid of **-0.136**: this league does not bid on model value.
Neither `suggest_bid` (v1) nor `suggest_bid_v2` can therefore be scored against those
prices, and both ship labelled unvalidated. This ledger is the only route to settling it,
and it deliberately names no winner until enough claims have resolved.

Reads data/logs/ only; --add appends one line.
"""
import argparse
import json

from fantasy_sim.bid_ledger import (
    MIN_CLAIMS_FOR_A_VERDICT, calibration, clearing_price, live_rows, load, reconcile,
    record_bid, record_rival_bids, superseded_rows, supersession_reasons,
)
from fantasy_sim.config import MY_TEAM as DEFAULT_TEAM, normalize_position
from fantasy_sim.decisions import _entry, _rivals_needing, suggest_bid, suggest_bid_v2
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import DECISION_LOG_FILE


def _decisions():
    out = []
    try:
        with open(DECISION_LOG_FILE, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--add", default=None, help="record a claim you are placing now")
    ap.add_argument("--bid", type=int, default=None, help="the FAAB you are bidding")
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--record-rivals", default=None, dest="record_rivals",
                    help="a claim already recorded; supply the LOSING bids with --rivals. "
                         "Sleeper shows every bid after a run, and best-rival + 1 is the "
                         "EXACT clearing price rather than the censored upper bound (T2)")
    ap.add_argument("--rivals", default=None,
                    help="comma-separated rival bids, e.g. 21,20")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.add:
        if args.bid is None:
            raise SystemExit("--add needs --bid: the point is recording what you PLACED.")
        engine = FantasySimulationEngine()
        week = args.week or engine.current_week
        e = _entry(engine, args.add) or {}
        if not e:
            raise SystemExit(f"{args.add!r} is not in the baseline pool -- check the spelling.")
        pos = normalize_position(e.get("pos") or "")
        vorp = float(e.get("mean") or 0.0) - float(engine.replacement_levels.get(pos, 0.0))
        rivals = _rivals_needing(engine, args.team, pos)
        remaining = float(engine.current_faab.get(args.team, 100.0))
        v2 = suggest_bid_v2(vorp, 0.0, len(rivals), [r["faab"] for r in rivals], remaining,
                            rival_aggression=[r["aggression"] for r in rivals])
        n = record_bid({
            "season": "2026", "week": week, "player": args.add,
            "player_id": str(e.get("player_id")), "pos": pos,
            "bid_placed": int(args.bid),
            "suggested_v1": suggest_bid(vorp, "upgrade", remaining, remaining),
            "suggested_v2_point": v2["point"], "suggested_v2_low": v2["low"],
            "suggested_v2_high": v2["high"],
            "vorp_at_bid": round(vorp, 2), "rivals_needing": len(rivals),
            "remaining_faab": remaining,
        })
        print(f"recorded {n} claim: {args.add} at ${args.bid} "
              f"(v1 said {suggest_bid(vorp, 'upgrade', remaining, remaining)}, "
              f"v2 {v2['low']}-{v2['high']})")
        return n

    if args.record_rivals:
        if not args.rivals:
            raise SystemExit("--record-rivals needs --rivals, e.g. --rivals 21,20")
        engine = FantasySimulationEngine()
        week = args.week or engine.current_week
        e = _entry(engine, args.record_rivals) or {}
        if not e:
            raise SystemExit(f"{args.record_rivals!r} is not in the baseline pool.")
        bids = [x.strip() for x in args.rivals.split(",") if x.strip()]
        try:
            # Matching is by player_id, never by name: the raw cache carries 220 colliding
            # names, seven of them involving a player rostered in this league (B17).
            n = record_rival_bids(str(e.get("player_id")), week, bids)
        except ValueError as ex:
            raise SystemExit(str(ex))
        price = clearing_price({"rival_bids": bids})
        print(f"recorded {n} amendment: {args.record_rivals} week {week}, rivals "
              f"{', '.join(bids)} -> exact clearing price {price:.0f}. "
              f"What it cost me is unchanged (Sleeper is a first-price auction).")
        return n

    all_rows = reconcile(load(), _decisions())
    cal = calibration(all_rows)
    # F64: a bid raised before the waiver run is ONE claim. The earlier row stays in the
    # file -- it is true that the bid was that much at that hour -- but it is not a claim
    # and must not be listed or scored as one.
    rows = sorted(live_rows(all_rows),
                  key=lambda r: (r.get("week") or 0, str(r.get("player"))))
    superseded = superseded_rows(all_rows)
    if args.json:
        print(json.dumps({"rows": rows, "superseded": superseded, "calibration": cal},
                         indent=1, sort_keys=True))
        return cal

    print(f"\nBID LEDGER -- {len(rows)} claim(s) recorded")
    if not rows:
        print("  empty. Record a claim when you place it:")
        print('    py -3.10 -m scripts.bid_review --add "Patrick Mahomes" --bid 25')
        return cal
    print(f"  {'wk':>2s} {'player':24s} {'pos':4s} {'v1':>4s} {'v2':>7s} {'bid':>4s} "
          f"{'won':>4s} {'paid':>6s} {'clears':>6s}  note")
    for r in rows:
        won = "-" if r["won"] is None else ("yes" if r["won"] else "no")
        price = "-" if r["winning_bid_if_visible"] is None else str(r["winning_bid_if_visible"])
        v2 = f"{r.get('suggested_v2_low')}-{r.get('suggested_v2_high')}"
        # T2: two different questions. `paid` is what it cost (first-price), `clears` is
        # what would have won -- best recorded rival + 1, exact rather than a bound.
        cp = clearing_price(r)
        clears = "-" if cp is None else f"{cp:.0f}"
        # F65: the ledger records what I meant to bid, Sleeper records what it charged.
        note = ("" if r.get("bid_mismatch") is None
                else f"recorded ${r.get('bid_placed')}, CHARGED ${r['bid_mismatch']}")
        print(f"  {r.get('week', 0):2d} {str(r.get('player'))[:24]:24s} {str(r.get('pos')):4s} "
              f"{r.get('suggested_v1', 0):4d} {v2:>7s} {r.get('bid_placed', 0):4d} "
              f"{won:>4s} {price:>6s} {clears:>6s}  {note}")

    if superseded:
        # T2: an amendment recording rival bids also supersedes its original, so calling
        # every superseded row a raised bid is false the moment rivals are recorded. The
        # reason is read per ROW from its own successor, because one claim can have both.
        why = supersession_reasons(all_rows)
        print(f"\n  {len(superseded)} superseded row(s) -- a bid RAISED before the waiver "
              f"run is one claim, scored once at the price that was live (F64); an "
              f"amendment recording rival bids supersedes its original the same way:")
        for r in superseded:
            print(f"    wk {r.get('week', 0):2d} {str(r.get('player'))[:24]:24s} "
                  f"${r.get('bid_placed', 0)} -> {why.get(id(r), 'superseded')}")

    print(f"\n  resolved {cal['n']} ({cal['n_exact']} with an EXACT clearing price from "
          f"recorded rival bids), unresolved {cal['unresolved']}")
    for k in ("v1", "v2"):
        c = cal[k]
        print(f"    {k}: {c['errors']} error(s), total miss ${c['total_miss']:.0f}, "
              f"mean ${c['mean_miss']:.1f}")
    print(f"  VERDICT: {cal['verdict']}")
    if cal["n"] < MIN_CLAIMS_FOR_A_VERDICT:
        print(f"  ({MIN_CLAIMS_FOR_A_VERDICT} resolved claims needed before naming one.)")
    print(f"  {cal['note']}")
    return cal


if __name__ == "__main__":
    main()

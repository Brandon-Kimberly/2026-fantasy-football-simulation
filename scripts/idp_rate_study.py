#!/usr/bin/env python3
"""
What SHOULD the IDP epistemic rate be? -- B1's measurement (fantasy_sim.epistemic_fit).

  py -3.10 -m scripts.idp_rate_study
  py -3.10 -m scripts.idp_rate_study --season 2025 --top-k 24 --json

`EPISTEMIC_ERROR_RATES` sets DL/LB/DB to 0.15 against QB 0.30, RB 0.63, WR 0.55. The IDP
value is a carried number (CLAUDE.md: "less rigorously sourced"). This runs Phase 7's own
instrument -- between-player variance of season means MINUS the within-player sampling
term, over the population mean -- on real weekly data, for IDP and for the offensive
positions side by side, so the IDP figure can be read against the rates already shipped.

SCORED UNDER THIS LEAGUE'S CURRENT SETTINGS. F49's sack cut went live 2026-09-23
(docs/EVALUATION_BOUNDARIES.md, boundary 1), so "current" now means post-cut -- which is
the right basis for a constant governing 2026 predictions. B1's trap says "fit on 2025
pre-change scoring"; that was written while the change was pending and is superseded.

MEASURES ONLY. Adoption is MAJOR, is conditional on the points backtest, and is NOT done
here. Phase 7 built, gated and REVERTED a change that adopted its own fitted values.

Reads Sleeper's public stats feed; writes nothing except an optional --cache file.
"""
import argparse
import json
import os
import sys

from fantasy_sim.epistemic_fit import (
    holdout_rate_scan, score_stat_line, top_k_by_total, variance_components,
)
from fantasy_sim.simulation import normalize_position
from fantasy_sim.config import EPISTEMIC_ERROR_RATES, REGULAR_SEASON_WEEKS

# Phase 7's published figures, for the side-by-side that makes the IDP number readable.
PHASE7 = {"QB": 0.07, "RB": 0.28, "WR": 0.22, "TE": 0.20, "K": 0.25}


def _weekly_rows(season, weeks, cache=None):
    """[(week, rows)] from Sleeper's stats feed, one unfiltered call per week."""
    if cache and os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            return [(int(w), r) for w, r in json.load(fh)]
    import requests
    out = []
    for wk in weeks:
        url = f"https://api.sleeper.app/stats/nfl/{season}/{wk}?season_type=regular"
        resp = requests.get(url, timeout=60)
        if resp.status_code != 200:
            print(f"  week {wk}: HTTP {resp.status_code} -- skipped", file=sys.stderr)
            continue
        out.append((wk, resp.json() or []))
    if cache:
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(out, fh)
    return out


def histories_by_position(weekly, scoring):
    """{normalised position: {player_id: [weekly score, ...]}}, this league's scoring."""
    out = {}
    for _wk, rows in weekly:
        for row in rows:
            pid = row.get("player_id")
            stats = row.get("stats") or {}
            if pid is None or not stats:
                continue
            player = row.get("player") or {}
            raw = player.get("position") or player.get("fantasy_positions") or row.get("position")
            if isinstance(raw, list):
                raw = raw[0] if raw else None
            pos = normalize_position(raw) if raw else None
            if pos is None:
                continue
            pts = score_stat_line(stats, scoring)
            if pts:
                out.setdefault(pos, {}).setdefault(str(pid), []).append(pts)
    return out


def fit(histories, top_k, min_games):
    pool = {p: histories.get(p, {}) for p in histories}
    results = {}
    for pos, hist in pool.items():
        keep = set(top_k_by_total(hist, top_k))
        results[pos] = variance_components({k: v for k, v in hist.items() if k in keep},
                                           min_games=min_games)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", default="2025")
    ap.add_argument("--top-k", type=int, default=24,
                    help="population size per position (default 24: the engine's own "
                         "replacement level is the 24th-best at a position)")
    ap.add_argument("--min-games", type=int, default=6)
    ap.add_argument("--cache", default=None, help="cache the raw feed to this path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    import requests
    from fantasy_sim.config import BASE_URL, LEAGUE_ID
    scoring = (requests.get(f"{BASE_URL}/league/{LEAGUE_ID}", timeout=20).json()
               or {}).get("scoring_settings") or {}
    if not scoring:
        raise SystemExit("could not read this league's scoring settings")

    weekly = _weekly_rows(args.season, range(1, REGULAR_SEASON_WEEKS + 1), cache=args.cache)
    hist = histories_by_position(weekly, scoring)
    results = fit(hist, args.top_k, args.min_games)

    if args.json:
        print(json.dumps({"season": args.season, "top_k": args.top_k,
                          "min_games": args.min_games, "results": results},
                         indent=1, sort_keys=True, default=list))
        return results

    print(f"\nB1 -- epistemic rate by position, {args.season}, top {args.top_k} per "
          f"position, >= {args.min_games} scoring games")
    print(f"  scored under this league's CURRENT settings (post-F49 sack cut, "
          f"idp_sack={scoring.get('idp_sack')}, idp_qb_hit={scoring.get('idp_qb_hit')})\n")
    print(f"  {'pos':<5}{'n':>4}{'gms':>6}{'mean':>8}{'sd_obs':>8}{'sd_true':>9}"
          f"{'FITTED':>8}{'config':>8}{'phase7':>8}  note")
    order = ["QB", "RB", "WR", "TE", "K", "DL", "LB", "DB"]
    for pos in [p for p in order if p in results]:
        r = results[pos]
        rate = "-" if r["rate"] is None else f"{r['rate']:.3f}"
        cfg = EPISTEMIC_ERROR_RATES.get(pos)
        p7 = PHASE7.get(pos)
        note = "DEGENERATE: noise >= spread" if r["degenerate"] else ""
        print(f"  {pos:<5}{r['n_players']:>4}{r['games_per_player']:>6.1f}"
              f"{r['mean']:>8.2f}{r['sd_observed']:>8.2f}{r['sd_true']:>9.2f}"
              f"{rate:>8}{cfg if cfg is not None else '-':>8}"
              f"{(f'{p7:.2f}' if p7 is not None else '-'):>8}  {note}")
    # B1's stated acceptance is a HELD-OUT score. Variance components say how much true
    # spread exists; only this says which rate PREDICTS the unseen half best.
    rates = [0.05, 0.10, 0.15, 0.20, 0.30, 0.45, 0.63, 1.00]
    split = REGULAR_SEASON_WEEKS // 2
    print(f"\n  HELD-OUT: fit on weeks 1-{split}, predict {split + 1}+; "
          f"mean squared error, lower is better, * = best")
    print(f"  {'pos':<5}{'n':>4}  " + "".join(f"{r:<8}" for r in rates) + " best  config")
    for pos in [p for p in order if p in results]:
        pool = hist.get(pos, {})
        keep = set(top_k_by_total(pool, args.top_k))
        scan = holdout_rate_scan({k: v for k, v in pool.items() if k in keep},
                                 rates, split=split)
        if scan["degenerate"]:
            print(f"  {pos:<5}{scan['n_players']:>4}  (too few players either side)")
            continue
        cells = "".join(
            f"{('*' if r == scan['best_rate'] else ' ') + format(scan['by_rate'][round(r, 4)], '.2f'):<8}"
            for r in rates)
        print(f"  {pos:<5}{scan['n_players']:>4}  {cells} {scan['best_rate']:<5} "
              f"{EPISTEMIC_ERROR_RATES.get(pos)}")
        results[pos]["holdout"] = scan

    print("\n  FITTED is sd_true / mean -- the same quantity Phase 7's survey reported, so "
          "\n  the offensive rows double as a check that this instrument reproduces it."
          "\n  config rates were tuned at roughly 2x the survey values (n_0 = 4 quadruples"
          "\n  prior precision, so the pair is calibrated together, not independently).")
    return results


if __name__ == "__main__":
    main()

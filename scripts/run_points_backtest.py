#!/usr/bin/env python3
"""
Points-level backtest against the real 2025 season -- the reconstruction of the scratch
`bt_points.py` procedure that AUDIT_PLAN.md's absence-modelling arc and F2's acceptance
criterion (c) cite, now committed so "changed by <= 0.5 pts in mean bias and <= 0.05 in mean z
versus the commit immediately before" is a diff of two logged lines, not a memory.

Definition (AUDIT_PLAN.md, "Absence modelling -- the arc, consolidated"): paired, seeded
(run_simulation reseeds np.random.seed(1000 + batch) itself), 300 sims per checkpoint,
checkpoints 3/6/9/12. For every (team, week) with week >= checkpoint:
    bias    = simulated mean weekly team points - real weekly team points   (mean over all)
    z       = (real - simulated mean) / simulated sd                        (mean over all)
    cover80 = share of real scores inside the simulated 10th-90th percentile band
    cover50 = share inside the 25th-75th band
Per-checkpoint figures are reported too (the "gradient" the arc tracks).

Every run appends one JSON line to data/logs/points_backtest.jsonl stamped with the git commit
it ran on (plus a dirty-tree flag), the Python interpreter, the machine, and the settings --
F12 (an unexplained SystemError, once, on this machine) is still open, so a result must be
attributable to the exact code and interpreter that produced it.

Usage:
    py -3.10 -m scripts.run_points_backtest [--sims 300] [--checkpoints 3,6,9,12] [--label TEXT]
"""
import argparse
import datetime as _dt
import json
import platform
import subprocess
import sys

import numpy as np

from fantasy_sim.backtest_season import run_backtest_checkpoint, DEFAULT_CHECKPOINT_WEEKS
from fantasy_sim.storage import ensure_dir_for, _log

POINTS_BACKTEST_LOG = _log("points_backtest.jsonl")


def _git(*args):
    try:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def real_optimal_points(bundle, player_positions):
    """{team: {week: realized optimal-lineup points}} from the season bundle's own per-week
    matchups -- each week's ACTUAL roster (the era roster) with realized players_points,
    solved through the engine's Hungarian assignment on the bundle's slot list. The same
    machinery and semantics as season_retrospective's lineup-efficiency optimal."""
    from fantasy_sim.simulation import FantasySimulationEngine
    roster_map = {str(k): v for k, v in (bundle.get("roster_map") or {}).items()}
    cutoff = (bundle.get("settings") or {}).get("playoff_week_start")
    slots = [sl for sl in (bundle.get("roster_positions") or []) if sl != "BN"]
    out = {}
    for wk_s, entries in (bundle.get("matchups") or {}).items():
        wk = int(wk_s)
        if cutoff is not None and wk >= int(cutoff):
            continue
        for e in entries:
            team = roster_map.get(str(e.get("roster_id")))
            if team is None:
                continue
            cands = []
            for pid, pts in (e.get("players_points") or {}).items():
                pos = player_positions.get(str(pid))
                if pos:
                    cands.append((str(pid), list(pos), float(pts)))
            assigned, _ = FantasySimulationEngine._solve_optimal_assignment(cands, slots=slots)
            out.setdefault(team, {})[wk] = round(sum(v for _n, v, _s in assigned), 2)
    return out


def score_checkpoint(raw):
    """Pure scoring of one checkpoint's raw payload -> per-(team, week) records."""
    cp = raw["checkpoint_week"]
    rows = []
    for team, sims in raw["weekly_scores"].items():
        sims = np.asarray(sims, dtype=float)
        for wk, real in raw["real_weekly_points"].get(team, {}).items():
            col = sims[:, wk - 1]
            if col.size == 0 or not np.isfinite(real):
                continue
            mu, sd = float(col.mean()), float(col.std(ddof=1))
            p10, p25, p75, p90 = (float(x) for x in np.percentile(col, (10, 25, 75, 90)))
            # F25's corrected target: realized OPTIMAL-lineup points on the week's actual
            # roster. The sim never claimed to predict managers' start/sit errors (measured
            # var ~144 pts^2 of the coverage gap), so calibration is ALSO scored against the
            # optimal target; the hindsight-selection premium in that target shows up as a
            # negative bias_opt, which is why summarise() reports RECENTRED opt coverage.
            real_opt = (raw.get("real_optimal_points") or {}).get(team, {}).get(wk)
            naive = (raw.get("naive_weekly_forecast") or {}).get(team, {}).get(wk)
            rows.append({
                "checkpoint": cp, "team": team, "week": wk, "real": real, "sim_mean": mu, "sim_sd": sd,
                "bias": mu - real, "z": (real - mu) / sd if sd > 0 else float("nan"),
                "in80": p10 <= real <= p90, "in50": p25 <= real <= p75,
                "p10": p10, "p25": p25, "p75": p75, "p90": p90,
                "real_opt": real_opt,
                "z_opt": ((real_opt - mu) / sd if (real_opt is not None and sd > 0) else None),
                "naive": naive,
            })
    return rows


def summarise(rows):
    if not rows:
        return {"n": 0}
    bias = np.array([r["bias"] for r in rows]); z = np.array([r["z"] for r in rows]); real = np.array([r["real"] for r in rows])
    out = {
        "n": len(rows),
        "bias": round(float(bias.mean()), 3),
        "bias_pct": round(float(100 * bias.mean() / real.mean()), 2),
        "mean_z": round(float(np.nanmean(z)), 4),
        "cover80": round(float(np.mean([r["in80"] for r in rows])), 4),
        "cover50": round(float(np.mean([r["in50"] for r in rows])), 4),
    }
    # The projections-only baseline (2026-09-05): same rows, same target -- the full
    # simulation's mean forecast has to beat a static Hungarian-on-means forecast (byes
    # excluded, nothing else) or its machinery is unpriced. MAE on both, deliberately:
    # bias can cancel; MAE cannot.
    nv = [r for r in rows if r.get("naive") is not None]
    if nv:
        naive_err = np.array([r["naive"] - r["real"] for r in nv])
        engine_err = np.array([r["bias"] for r in nv])
        out.update({
            "naive_n": len(nv),
            "naive_bias": round(float(naive_err.mean()), 3),
            "naive_mae": round(float(np.abs(naive_err).mean()), 3),
            "engine_mae": round(float(np.abs(engine_err).mean()), 3),
        })
    opt = [r for r in rows if r.get("real_opt") is not None]
    if opt:
        bias_opt = np.array([r["sim_mean"] - r["real_opt"] for r in opt])
        z_opt = np.array([r["z_opt"] for r in opt if r["z_opt"] is not None])
        shift = float(bias_opt.mean())
        # recentre: the optimal target carries the hindsight-selection premium as a mean
        # offset; calibration is about SPREAD, so coverage is scored after removing the
        # mean offset (equivalently, shifting every band by the mean bias).
        out.update({
            "bias_opt": round(shift, 3),
            "sd_z_opt": round(float(z_opt.std(ddof=1)), 4) if len(z_opt) > 1 else None,
            "cover80_opt_centered": round(float(np.mean(
                [r["p10"] - shift <= r["real_opt"] <= r["p90"] - shift for r in opt])), 4),
            "cover50_opt_centered": round(float(np.mean(
                [r["p25"] - shift <= r["real_opt"] <= r["p75"] - shift for r in opt])), 4),
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sims", type=int, default=300)
    ap.add_argument("--checkpoints", default=",".join(str(w) for w in DEFAULT_CHECKPOINT_WEEKS))
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)
    checkpoints = [int(w) for w in args.checkpoints.split(",") if w.strip()]

    # the corrected target, built once from the season bundle (F25)
    import json as _json
    from fantasy_sim.storage import PLAYER_CACHE_FILE, load_json, season_log_file
    _optimal_target = {}
    try:
        with open(season_log_file("2025"), encoding="utf-8") as _f:
            _bundle = _json.load(_f)
        _pdb = load_json(PLAYER_CACHE_FILE)
        _positions = {}
        for _pid, _e in _pdb.items():
            _pos = _e.get("fantasy_positions") or ([_e.get("position")] if _e.get("position") else None)
            if _pos:
                _positions[str(_pid)] = [x for x in _pos if x]
        _optimal_target = real_optimal_points(_bundle, _positions)
    except Exception as ex:
        print(f"[NOTE] optimal target unavailable ({ex}); scoring started-lineup target only")

    all_rows, per_cp = [], {}
    for wk in checkpoints:
        print(f"\n{'=' * 70}\nPOINTS BACKTEST -- checkpoint week {wk}, {args.sims} sims\n{'=' * 70}")
        out = run_backtest_checkpoint(wk, num_batches=1, sims_per_batch=args.sims, return_raw=True)
        if not out:
            print(f"[SKIP] checkpoint {wk} produced no output")
            continue
        _results, raw = out
        raw["real_optimal_points"] = _optimal_target
        rows = score_checkpoint(raw)
        per_cp[str(wk)] = summarise(rows)
        all_rows.extend(rows)
        s = per_cp[str(wk)]
        line = (f"  cp{wk}: n={s['n']} bias {s['bias']:+.2f} pts ({s['bias_pct']:+.1f}%)  mean z {s['mean_z']:+.3f}  "
                f"cover80 {s['cover80']:.2f}  cover50 {s['cover50']:.2f}")
        if "sd_z_opt" in s:
            line += (f"\n        OPT target: bias {s['bias_opt']:+.2f}  sd(z) {s['sd_z_opt']:.2f}  "
                     f"cover80c {s['cover80_opt_centered']:.2f}  cover50c {s['cover50_opt_centered']:.2f}")
        print(line)

    overall = summarise(all_rows)
    record = {
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "label": args.label,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "machine": platform.node(),
        "platform": platform.platform(),
        "sims_per_checkpoint": args.sims,
        "checkpoints": checkpoints,
        "overall": overall,
        "per_checkpoint": per_cp,
    }
    ensure_dir_for(POINTS_BACKTEST_LOG)
    with open(POINTS_BACKTEST_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    print(f"\n{'=' * 70}\nOVERALL ({overall.get('n', 0)} team-weeks): bias {overall.get('bias', float('nan')):+.2f} pts "
          f"({overall.get('bias_pct', float('nan')):+.1f}%)  mean z {overall.get('mean_z', float('nan')):+.3f}  "
          f"cover80 {overall.get('cover80', float('nan')):.2f}  cover50 {overall.get('cover50', float('nan')):.2f}")
    if "sd_z_opt" in overall:
        print(f"OPT TARGET (hindsight-optimal lineups, recentred): bias {overall['bias_opt']:+.2f}  "
              f"sd(z) {overall['sd_z_opt']:.2f}  cover80c {overall['cover80_opt_centered']:.2f}  "
              f"cover50c {overall['cover50_opt_centered']:.2f}")
    if overall.get("naive_mae") is not None:
        print(f"NAIVE BASELINE (projections-only, byes excluded): MAE {overall['naive_mae']:.2f} vs "
              f"engine MAE {overall['engine_mae']:.2f}; naive bias {overall['naive_bias']:+.2f} "
              f"(n={overall['naive_n']})")
    print(f"commit {record['git_commit']}{' (dirty)' if record['git_dirty'] else ''}  python {record['python']} "
          f"({record['python_executable']})\nlogged -> {POINTS_BACKTEST_LOG}\n{'=' * 70}")
    return record


if __name__ == "__main__":
    main()

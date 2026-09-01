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
import os
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
            rows.append({
                "checkpoint": cp, "team": team, "week": wk, "real": real, "sim_mean": mu, "sim_sd": sd,
                "bias": mu - real, "z": (real - mu) / sd if sd > 0 else float("nan"),
                "in80": p10 <= real <= p90, "in50": p25 <= real <= p75,
            })
    return rows


def summarise(rows):
    if not rows:
        return {"n": 0}
    bias = np.array([r["bias"] for r in rows]); z = np.array([r["z"] for r in rows]); real = np.array([r["real"] for r in rows])
    return {
        "n": len(rows),
        "bias": round(float(bias.mean()), 3),
        "bias_pct": round(float(100 * bias.mean() / real.mean()), 2),
        "mean_z": round(float(np.nanmean(z)), 4),
        "cover80": round(float(np.mean([r["in80"] for r in rows])), 4),
        "cover50": round(float(np.mean([r["in50"] for r in rows])), 4),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sims", type=int, default=300)
    ap.add_argument("--checkpoints", default=",".join(str(w) for w in DEFAULT_CHECKPOINT_WEEKS))
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)
    checkpoints = [int(w) for w in args.checkpoints.split(",") if w.strip()]

    all_rows, per_cp = [], {}
    for wk in checkpoints:
        print(f"\n{'=' * 70}\nPOINTS BACKTEST -- checkpoint week {wk}, {args.sims} sims\n{'=' * 70}")
        out = run_backtest_checkpoint(wk, num_batches=1, sims_per_batch=args.sims, return_raw=True)
        if not out:
            print(f"[SKIP] checkpoint {wk} produced no output")
            continue
        _results, raw = out
        rows = score_checkpoint(raw)
        per_cp[str(wk)] = summarise(rows)
        all_rows.extend(rows)
        s = per_cp[str(wk)]
        print(f"  cp{wk}: n={s['n']} bias {s['bias']:+.2f} pts ({s['bias_pct']:+.1f}%)  mean z {s['mean_z']:+.3f}  "
              f"cover80 {s['cover80']:.2f}  cover50 {s['cover50']:.2f}")

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
    print(f"commit {record['git_commit']}{' (dirty)' if record['git_dirty'] else ''}  python {record['python']} "
          f"({record['python_executable']})\nlogged -> {POINTS_BACKTEST_LOG}\n{'=' * 70}")
    return record


if __name__ == "__main__":
    main()

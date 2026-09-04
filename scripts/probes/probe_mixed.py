"""R1 targeted probe: the native combination present at the original crash -- assignment-heavy
work (scipy linear_sum_assignment through the real _solve_optimal_assignment, plus the
exhaustive brute force) interleaved with REAL matplotlib/seaborn rendering (Agg backend,
kdeplot + fill_between + savefig to a temp file, figures closed) and pandas frames, under
faulthandler, for a fixed number of rounds. usage: probe_mixed.py ROUNDS"""
import faulthandler, os, sys, tempfile, time
faulthandler.enable()
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, seaborn as sns
from fantasy_sim.simulation import FantasySimulationEngine
sys.path.insert(0, os.getcwd())
from tests.test_lineup_optimality import brute_force_best, POSITIONS
rng = np.random.default_rng(int(time.time()) % 100000)
rounds = int(sys.argv[1]); tmp = tempfile.mkdtemp(); t0 = time.time(); calls = 0
for r in range(rounds):
    # 1) assignment-heavy: 400 Hungarian solves, 60 of them cross-checked by brute force (<= 7 players)
    for i in range(400):
        k = int(rng.integers(1, 21 if i % 7 else 8))
        cands = [("p%d" % j, list(rng.choice(POSITIONS, 2, replace=False)) if rng.random() < 0.3 else [str(rng.choice(POSITIONS))],
                  float(rng.uniform(0, 30))) for j in range(k)]
        assigned, _ = FantasySimulationEngine._solve_optimal_assignment(cands); calls += 1
        if k <= 7 and i % 7 == 0:
            assert abs(sum(v for _, v, _ in assigned) - brute_force_best(cands)) < 1e-9
    # 2) rendering: the export's own plot types on realistic shapes
    scores = rng.normal(110, 25, size=(8, 300, 14)); traj = np.cumsum(rng.random((8, 300, 14)) < 0.5, axis=2)
    plt.figure(figsize=(10, 5))
    for t in range(8):
        sns.kdeplot(scores[t].flatten(), linewidth=1.5, label="T%d" % t)
    plt.axvline(float(np.median(scores)), linestyle="--"); plt.legend(); plt.savefig(os.path.join(tmp, "kde.png"), dpi=60); plt.close()
    plt.figure(figsize=(10, 5))
    for t in range(8):
        p25, p75, p50 = np.percentile(traj[t], 25, axis=0), np.percentile(traj[t], 75, axis=0), np.percentile(traj[t], 50, axis=0)
        plt.fill_between(range(14), p25, p75, alpha=0.3); plt.plot(range(14), p50)
    plt.savefig(os.path.join(tmp, "traj.png"), dpi=60); plt.close("all")
    df = pd.DataFrame(scores.mean(axis=2)); df.describe(); (df / (300 * 14) * 100).round(2).to_dict(orient="index")
    if r % 5 == 0:
        print("round %d ok  (%d solves, %.0fs)" % (r, calls, time.time() - t0), flush=True)
print("MIXED PROBE OK: %d rounds, %d assignment calls, %.0fs, python %s" % (rounds, calls, time.time() - t0, sys.version.split()[0]))

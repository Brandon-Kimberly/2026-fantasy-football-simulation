import faulthandler, time, sys
faulthandler.enable()
import numpy as np, pandas as pd
from fantasy_sim.simulation import FantasySimulationEngine
POS = ["QB","RB","WR","TE","K","DL","LB","DB"]
rng = np.random.default_rng(123)
t0 = time.time(); n = 0
for trial in range(300000):
    k = int(rng.integers(1, 21))
    cands = []
    for i in range(k):
        opts = list(rng.choice(POS, 2, replace=False)) if rng.random() < 0.3 else [str(rng.choice(POS))]
        cands.append(("p%d" % i, opts, float(rng.uniform(0, 30))))
    assigned, unfilled = FantasySimulationEngine._solve_optimal_assignment(cands)
    n += 1
    if trial % 20000 == 0:
        arr = rng.normal(100, 20, size=(300, 14)); np.percentile(arr.flatten(), [10, 90]); pd.DataFrame(arr).mean()
print("STRESS OK: %d assignment calls in %.0fs, python %s" % (n, time.time() - t0, sys.version.split()[0]))

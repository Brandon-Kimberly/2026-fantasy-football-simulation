"""
tests.golden_master

The Phase 0 reproducibility harness: machinery for running FantasySimulationEngine against a
committed fixture set and reducing everything it produces to a set of hashes.

WHY THIS EXISTS
---------------
`run_simulation` (~445 lines) and `export_and_visualize` (~333 lines) have no test that pins
their aggregate behaviour. Until they do, any "this refactor preserves behaviour" claim about
them is unfalsifiable -- which is precisely how a previous refactor widened the blast radius of
two latent bugs before they were found. This module makes such claims falsifiable: change
either method, run the suite, and either the hashes match or you changed the numbers.

WHAT IS HASHED, AND WHY IN THREE STAGES
---------------------------------------
The two methods are hashed separately so a failure localises to one of them:

  stage_a -- the 17 arguments run_simulation passes to export_and_visualize. This is the
             complete output of run_simulation: win/point arrays, trajectories, weekly scores,
             per-batch rates, the H2H matrix, seed matrix, championship shares, audit log.
             A stage_a break means run_simulation's numbers moved.

  stage_b -- the payloads export_and_visualize hands to save_json. A stage_b break with
             stage_a intact means the export/derivation layer moved, not the engine.

  stage_c -- export_and_visualize re-run on the same stage_a arguments with championship
             appearance counts scaled up. See COVERAGE GAPS below.

Charts are deliberately NOT hashed. PNG bytes vary with matplotlib/freetype versions and with
platform font rasterisation, so hashing them would produce failures carrying no information
about the model. The DATA behind every chart is covered instead: trajectories, weekly scores,
the H2H matrix and the seed matrix are all in stage_a, and the exported JSON in stage_b.

DETERMINISM
-----------
run_simulation calls np.random.seed(1000 + batch) at the top of every batch and nothing draws
from the global stream before that, so its output does not depend on ambient RNG state. That
is a property, not an assumption -- test_golden_master.py asserts it directly by perturbing
the global stream before a run and confirming the hashes are unchanged.

FLOAT EXACTNESS
---------------
Floats are canonicalised via float.hex(), an exact round-trip-stable rendering of the
underlying double, so the hashes catch bit-level drift and not merely visible drift. The flip
side is that they are exact to THIS platform's libm: a different OS or numpy build can
legitimately differ in the last ulp of exp/log and break a hash with no real behaviour change.
That is why every hash is stored alongside a `summary` -- on failure the test prints stored vs.
current moments, so a one-ulp difference is immediately distinguishable from a distribution
that actually moved. The generating platform is recorded in each golden file's _meta block.

SUMMARY SIZE VS. INTERPRETIVE PRECISION (learned in Phase 2)
-------------------------------------------------------------
The summaries are for telling ulp-noise from real movement. They are NOT precise enough to
SIZE an effect. At 2 x 15 sims a scenario holds 30 seasons per team; the per-run standard
error of the weekly team mean is ~1.6 points (~0.9%), and a change that also reshuffles the
RNG stream makes two runs independent samples, so their difference carries ~1.3% of noise.
Phase 2 read a -4.6% mean shift off these summaries and explained it; the true effect,
isolated properly, was -2.4%. The rest was noise.

To size an effect: (1) run at >= 400 seasons, and (2) isolate one change at a time on the
SAME RNG stream -- e.g. patch the new code path back to the old constant so every draw is
paired -- rather than comparing two golden runs. See AUDIT_PHASE_2_FINDINGS.md finding 1.
Conservation SUMS (wins, all-play, seed counts) being identical across a change is evidence
that the Phase 1 invariants held, not that outcomes did: the arrays behind them move whenever
different seasons win.

COVERAGE GAPS (stated explicitly rather than papered over)
----------------------------------------------------------
1. export_and_visualize gates its championship-share ranking behind
   MIN_CHAMP_APPEARANCES_FOR_RANKING = 50. A player accrues at most one appearance per
   simulation his team wins, so at any sim count a fast test can afford, that block only ever
   executes its empty-result branch. stage_c closes this for export_and_visualize by re-running
   the export with appearance counts scaled past the threshold. It does NOT close it for
   run_simulation; the accumulation side there is covered only as part of stage_a's hash of
   champ_players. Verified: at 2 x 15 the stage_b ranking holds 0 entries and stage_c holds 20.
   stage_c's `championship_lineup_appearance_pct` values exceed 100% because appearances are
   scaled while total_sims is not -- an artifact of the synthetic boost, not a defect. Read the
   stage_c golden as a branch-coverage hash only; its numbers carry no meaning.
2. The fixtures are two league states -- preseason, and mid-season with completed weeks. They
   do not cover a current_week past the end of the 14-week regular season, nor a league size
   other than 8, nor MEDIAN_SCORING_ENABLED = False.
3. Chart RENDERING is unhashed, per above. A refactor that broke only the plotting calls,
   without touching the data behind them, would pass. Charts remain uncovered.
4. These are characterisation hashes. They pin what the engine DOES; they say nothing about
   whether what it does is correct. That is what Phases 1-7 are for.

REGENERATING
------------
    python -m tests.golden_master --regenerate

Regenerate ONLY when a change to the numbers is intended. Read the failure output first: it
names the stage and prints the magnitude of the move. A regeneration commit that silently
shifts the distribution is exactly the failure this harness exists to prevent, so regenerate
in its own commit with the reason in the message.
"""
import copy
import hashlib
import json
import logging
import os
import platform
import sys
from contextlib import contextmanager
from unittest.mock import patch

import numpy as np

from fantasy_sim.config import SIM_CONFIG
from fantasy_sim.simulation import FantasySimulationEngine

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_ROOT = os.path.join(HERE, "fixtures", "golden")
EXPECTED_DIR = os.path.join(FIXTURE_ROOT, "expected")

# Batch/sim counts for a golden run. NUM_BATCHES must stay > 1: export_and_visualize takes a
# different branch for the standard-error column when it is 1, and the branch that matters is
# the one the real 10-batch production configuration takes.
GOLDEN_BATCHES = 2
GOLDEN_SIMS_PER_BATCH = 15

SCENARIOS = ("week01", "week06", "week15")   # week15: F3, seeded from banked standings

# Every file FantasySimulationEngine reads, keyed by the basename the storage layer resolves to.
FIXTURE_INPUTS = (
    "league_state.json", "league_standings.json", "vegas_totals.json", "live_rosters.json",
    "player_baselines.json", "nfl_team_power_ratings.json", "nfl_defensive_ratings.json",
    "nfl_defensive_tiers.json", "league_schedule.json", "nfl_schedule.json",
    "weekly_actuals.json", "playoff_bracket.json",
)

# The names of the 17 positional arguments run_simulation passes to export_and_visualize.
STAGE_A_ARG_NAMES = (
    "wins", "points", "b_playoffs", "b_champs", "b_toilets", "trajectories", "h2h",
    "pts_against", "all_play", "champ_players", "max_score", "max_team", "max_wk",
    "audit_log", "total_sims", "global_weekly_scores", "seed_matrix",
)

CHAMP_APPEARANCE_SCALE = 40  # lifts stage_c past MIN_CHAMP_APPEARANCES_FOR_RANKING = 50


# ---------------------------------------------------------------------------- canonicalisation
def canonical(obj):
    """Reduces an arbitrary engine output to a JSON-serialisable form that is byte-stable for
    identical values. Floats become float.hex() (exact); dict keys become strings and are
    sorted, so insertion order cannot leak into the hash."""
    if isinstance(obj, np.ndarray):
        return {"__ndarray__": {"shape": list(obj.shape), "dtype": obj.dtype.str,
                                "data": [canonical(v) for v in obj.ravel().tolist()]}}
    if isinstance(obj, dict):
        return {str(k): canonical(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, (list, tuple)):
        return [canonical(v) for v in obj]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        if f != f:
            return "__nan__"
        return f.hex()
    if obj is None or isinstance(obj, str):
        return obj
    raise TypeError("golden master cannot canonicalise " + repr(type(obj)))


def digest(obj):
    blob = json.dumps(canonical(obj), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _numbers(obj, out):
    """Flattens every float/int reachable in obj into out, for summary statistics."""
    if isinstance(obj, np.ndarray):
        out.extend(np.asarray(obj, dtype=float).ravel().tolist())
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, out)
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float, np.integer, np.floating)):
        out.append(float(obj))


def summarise(obj):
    """Moments of everything numeric in obj. Purely diagnostic -- never asserted on -- so that
    a hash mismatch can be read as 'last-ulp noise' or 'the distribution moved' without
    having to guess which."""
    vals = []
    _numbers(obj, vals)
    if not vals:
        return {"n": 0}
    a = np.asarray(vals, dtype=float)
    finite = a[np.isfinite(a)]
    if finite.size == 0:
        return {"n": int(a.size), "all_nonfinite": True}
    return {
        "n": int(a.size),
        "sum": round(float(np.sum(finite)), 6),
        "mean": round(float(np.mean(finite)), 6),
        "std": round(float(np.std(finite)), 6),
        "min": round(float(np.min(finite)), 6),
        "max": round(float(np.max(finite)), 6),
    }


def entry(obj):
    return {"hash": digest(obj), "summary": summarise(obj)}


# ------------------------------------------------------------------------------------ sandbox
@contextmanager
def _sandbox(scenario, batches, sims_per_batch):
    """Isolates a golden run from the real data/ directory, from matplotlib output, and from
    SIM_CONFIG's production batch sizes."""
    scenario_dir = os.path.join(FIXTURE_ROOT, scenario)
    cache = {}
    for name in FIXTURE_INPUTS:
        with open(os.path.join(scenario_dir, name)) as f:
            cache[name] = json.load(f)

    def fixture_load(path):
        name = os.path.basename(path)
        if name not in cache:
            raise FileNotFoundError("Missing required file: '" + str(path) + "'.")
        # Deep copy on every read: the engine mutates self.baselines in _apply_bayesian_updates,
        # and that mutation must not leak into a later run in the same process.
        return copy.deepcopy(cache[name])

    saved = {}

    def capture_save(path, data, indent=2):
        saved[os.path.basename(path)] = data

    prev_level = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.ERROR)
    orig_batches = SIM_CONFIG["NUM_BATCHES"]
    orig_sims = SIM_CONFIG["SIMS_PER_BATCH"]
    SIM_CONFIG["NUM_BATCHES"] = batches
    SIM_CONFIG["SIMS_PER_BATCH"] = sims_per_batch
    try:
        with patch("fantasy_sim.simulation.load_json", side_effect=fixture_load), \
             patch("fantasy_sim.simulation.save_json", side_effect=capture_save), \
             patch("fantasy_sim.simulation.save_chart"), \
             patch("fantasy_sim.simulation.read_faab_observations", return_value={}):
            # The observations patch is load-bearing hermeticity, not tidiness: F31's
            # profile updater reads the decision log via open(), which the fixture_load
            # seam does not intercept -- without this, a "hermetic" golden run silently
            # reads the LIVE, growing decision log and the goldens change with every
            # logged transaction (F11's contamination class, caught before it shipped).
            yield saved
    finally:
        SIM_CONFIG["NUM_BATCHES"] = orig_batches
        SIM_CONFIG["SIMS_PER_BATCH"] = orig_sims
        logging.getLogger().setLevel(prev_level)


def run_scenario(scenario, batches=GOLDEN_BATCHES, sims_per_batch=GOLDEN_SIMS_PER_BATCH):
    """Runs one scenario end to end and returns its three-stage hash record."""
    real_export = FantasySimulationEngine.export_and_visualize
    stage_a_args = {}

    def recording_export(self, *args):
        if not stage_a_args:  # the genuine call from run_simulation, not stage_c's re-run
            stage_a_args.update(zip(STAGE_A_ARG_NAMES, args))
        return real_export(self, *args)

    with _sandbox(scenario, batches, sims_per_batch) as saved:
        engine = FantasySimulationEngine()
        with patch.object(FantasySimulationEngine, "export_and_visualize", recording_export):
            engine.run_simulation()
        stage_b = dict((name, entry(payload)) for name, payload in saved.items())

        # stage_c: re-run the export with championship appearances scaled past
        # MIN_CHAMP_APPEARANCES_FOR_RANKING so the ranking block executes for real instead of
        # falling through its empty branch. See COVERAGE GAPS in the module docstring.
        boosted = copy.deepcopy(stage_a_args)
        for share in boosted["champ_players"].values():
            share["appearances"] *= CHAMP_APPEARANCE_SCALE
            share["total_points"] *= CHAMP_APPEARANCE_SCALE
        saved.clear()
        real_export(engine, *[boosted[n] for n in STAGE_A_ARG_NAMES])
        stage_c = dict((name, entry(payload)) for name, payload in saved.items())

    stage_a = dict((name, entry(value)) for name, value in stage_a_args.items())
    return {
        "stage_a__run_simulation": stage_a,
        "stage_b__export_and_visualize": stage_b,
        "stage_c__export_champ_ranking": stage_c,
    }


def expected_path(scenario):
    return os.path.join(EXPECTED_DIR, scenario + ".json")


def load_expected(scenario):
    with open(expected_path(scenario)) as f:
        return json.load(f)


def regenerate():
    os.makedirs(EXPECTED_DIR, exist_ok=True)
    for scenario in SCENARIOS:
        record = run_scenario(scenario)
        record["_meta"] = {
            "scenario": scenario,
            "batches": GOLDEN_BATCHES,
            "sims_per_batch": GOLDEN_SIMS_PER_BATCH,
            "python": platform.python_version(),
            "numpy": np.__version__,
            "platform": platform.platform(),
            "note": "Regenerate only when a change to the numbers is INTENDED. "
                    "See tests/golden_master.py.",
        }
        with open(expected_path(scenario), "w") as f:
            json.dump(record, f, indent=2, sort_keys=True)
        count = sum(len(v) for k, v in record.items() if k.startswith("stage"))
        print("[OK] " + scenario + ": " + str(count) + " hashed outputs -> "
              + expected_path(scenario))


if __name__ == "__main__":
    if "--regenerate" in sys.argv:
        regenerate()
    else:
        print(__doc__)
        print("Nothing done. Pass --regenerate to rewrite the golden files.")

"""
fantasy_sim.behavior_check

The behavioral-plausibility harness: measures the SIMULATED rates of every in-season
mechanic on the hermetic golden fixtures and reports each against (a) the real 2025
league's measured rates and (b) a committed baseline of the sim's own accepted rates.

WHY THIS EXISTS (2026-09-03 re-audit): F31 found FAAB spending running at ~31% of the
real league's rate, and the readiness audit found trades fully inert (0 completions vs
11 real) -- both by MANUAL measurement against 2025, because nothing in the apparatus
checks behavioral plausibility: the goldens pin bytes, the gate checks score
calibration, and neither can see a mechanism running at the wrong rate. This module
makes that measurement a permanent instrument instead of a scratchpad artifact.

TWO COMPARISONS, TWO SEMANTICS (deliberate -- see the 2026-09-03 design decision):
  * vs REAL 2025: report-only, three-way (IN-BAND / UNDER / OVER). Known, filed gaps
    (trades ~0 vs 11 -> F2/F34; the missing free-add channel -> F34) are annotated with
    their F-numbers, not failed on -- a check that fails every run on a known gap
    becomes wallpaper.
  * vs the COMMITTED BASELINE (tests/fixtures/behavior/): a drift CHECK. The instrumented
    run is deterministic on the seeded golden fixtures, so the sim's rates only move when
    engine behavior moves. An intended change regenerates the baseline in its own commit
    with the deltas explained (the golden-master discipline applied to rates); an
    unintended drift exits nonzero.

The REAL_2025 constants below are the readiness audit's measurements (2026-09-03) from
the league's full transaction and matchup history; each carries its derivation.
Instrumentation is passive delegating wrappers -- verified drift-free by the baseline's
own double-run determinism check at regeneration time.
"""
import json
import os
from unittest.mock import patch

import numpy as np

from fantasy_sim.simulation import FantasySimulationEngine

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE_DIR = os.path.join(os.path.dirname(HERE), "tests", "fixtures", "behavior")

# Real 2025 rates, measured 2026-09-03 from the 2025 league's complete Sleeper history
# (99 completed waiver claims, 152 free-agent adds, 11 trades, weeks 1-17 transactions;
# lineup changes from 128 consecutive team-week starter diffs). League totals are per
# season for the whole 8-team league.
REAL_2025 = {
    "faab_spent":        {"value": 728.0, "band": (650.0, 800.0),
                          "note": "F31's acceptance band; five of eight teams spent ~all 100"},
    "waiver_claims":     {"value": 99.0, "band": (74.0, 124.0),
                          "note": "+-25%; the sim's claims are its only named-churn analog"},
    "bid_mean":          {"value": 7.35, "band": (5.5, 9.2), "note": "+-25% of the real mean"},
    "bid_median":        {"value": 4.0, "band": (2.5, 5.5),
                          "note": "the real median bid; the lognormal was fitted to it"},
    "bid_p95":           {"value": 21.2, "band": (14.0, 28.0),
                          "note": "the conviction tail F31 existed to produce"},
    "early_claim_share": {"value": 0.394, "band": (0.25, 0.55),
                          "note": "weeks 1-4 carried 39 of 99 real claims; the sim is known "
                                  "modestly flat here (readiness audit)"},
    "trade_completions": {"value": 11.0, "band": (5.0, 20.0), "filed": "F2/F34",
                          "note": "real 2025 trades; the sim is known INERT here -- 0 "
                                  "completions, tracked, not a regression"},
    "lineup_changes_mean": {"value": 2.76, "band": (2.0, 3.5),
                            "note": "starters changed per team-week, real mean"},
    "lineup_zero_share": {"value": 0.10, "band": (0.03, 0.25),
                          "note": "share of team-weeks with an unchanged lineup"},
}

WEEKS_PER_SEASON = 16   # 14 regular + 2 playoff weeks simulated from a week-1 start


def measure(scenario="week01"):
    """One instrumented hermetic run on the golden fixtures; returns the sim's per-season
    behavioral rates. All wrappers delegate to the real implementations (passive), and the
    run goes through tests.golden_master's _sandbox, which also severs the F31 profile
    updater's live decision-log read -- the hermeticity hole the 2026-09-03 audit flagged
    is closed HERE by construction, not by each caller remembering a patch."""
    from tests import golden_master as gm

    week = [0]
    bids = []
    bid_weeks = []
    offers_events = []
    trade_scores = []
    score_watch = [0]
    completions = [0]
    lineups_by_week = {}
    solves_since_boundary = [99]

    real_solve = FantasySimulationEngine._solve_optimal_assignment
    real_offers = FantasySimulationEngine._construct_trade_offers
    real_apportion = FantasySimulationEngine._apportion_vacated_volume
    real_faab = FantasySimulationEngine._compute_faab_bid
    real_score = FantasySimulationEngine.get_optimal_score

    def solve(c):
        a, u = real_solve(c)
        if solves_since_boundary[0] < 8:
            lineups_by_week.setdefault(week[0], []).append(frozenset(x[0] for x in a))
            solves_since_boundary[0] += 1
        return a, u

    def apportion(engine, *a):
        week[0] += 1
        solves_since_boundary[0] = 0
        return real_apportion(engine, *a)

    def faab(remaining, *rest):
        b = real_faab(remaining, *rest)
        bids.append(b)
        bid_weeks.append(((week[0] - 1) % WEEKS_PER_SEASON) + 1)
        return b

    def offers(self_, d_list, r_list):
        out = real_offers(self_, d_list, r_list)
        offers_events.append(len(out))
        if out:
            score_watch[0] = 2 + 2 * len(out)
            trade_scores.append([])
        return out

    def score(self_, roster):
        v = real_score(self_, roster)
        if score_watch[0] > 0:
            trade_scores[-1].append(v)
            score_watch[0] -= 1
            if score_watch[0] == 0:
                seq = trade_scores[-1]
                for i in range(2, len(seq) - 1, 2):
                    if seq[i] > seq[0] and seq[i + 1] > seq[1]:
                        completions[0] += 1
                        break
        return v

    with patch.object(FantasySimulationEngine, "_solve_optimal_assignment", staticmethod(solve)), \
         patch.object(FantasySimulationEngine, "_construct_trade_offers", offers), \
         patch.object(FantasySimulationEngine, "_apportion_vacated_volume", apportion), \
         patch.object(FantasySimulationEngine, "_compute_faab_bid", staticmethod(faab)), \
         patch.object(FantasySimulationEngine, "get_optimal_score", score):
        gm.run_scenario(scenario)

    n_sims = gm.GOLDEN_BATCHES * gm.GOLDEN_SIMS_PER_BATCH
    b = np.array(bids, dtype=float)
    early = sum(1 for w in bid_weeks if w <= 4)

    diffs = []
    weeks_sorted = sorted(lineups_by_week)
    for a_, b_ in zip(weeks_sorted, weeks_sorted[1:]):
        if (b_ - a_) == 1 and len(lineups_by_week[a_]) == 8 == len(lineups_by_week[b_]) \
                and ((b_ - 1) % WEEKS_PER_SEASON) != 0:
            for la, lb in zip(lineups_by_week[a_], lineups_by_week[b_]):
                diffs.append(len(lb - la))
    d = np.array(diffs, dtype=float)

    return {
        "scenario": scenario, "n_sims": n_sims,
        "faab_spent": round(float(b.sum()) / n_sims, 2),
        "waiver_claims": round(len(b) / n_sims, 2),
        "bid_mean": round(float(b.mean()), 3) if len(b) else 0.0,
        "bid_median": round(float(np.median(b)), 3) if len(b) else 0.0,
        "bid_p95": round(float(np.quantile(b, 0.95)), 3) if len(b) else 0.0,
        "early_claim_share": round(early / len(b), 4) if len(b) else 0.0,
        "trade_offer_events": round(len(offers_events) / n_sims, 2),
        "trade_completions": round(completions[0] / n_sims, 3),
        "lineup_changes_mean": round(float(d.mean()), 3) if len(d) else 0.0,
        "lineup_zero_share": round(float((d == 0).mean()), 4) if len(d) else 0.0,
    }


def classify(metric, sim_value):
    """Three-way verdict against the real-2025 band; filed gaps keep their F-number."""
    ref = REAL_2025[metric]
    lo, hi = ref["band"]
    if lo <= sim_value <= hi:
        verdict = "IN-BAND"
    elif sim_value < lo:
        verdict = "UNDER"
    else:
        verdict = "OVER"
    if verdict != "IN-BAND" and ref.get("filed"):
        verdict += f" (filed: {ref['filed']})"
    return verdict


def compare_to_baseline(metrics, baseline, rel_tol=0.02):
    """The drift check: every metric must match the committed baseline within rel_tol
    (the run is deterministic; the tolerance only absorbs float formatting). Returns the
    list of drifted metrics -- empty means no behavioral drift since the baseline."""
    drifted = []
    for k, v in metrics.items():
        if k in ("scenario", "n_sims"):
            continue
        base = baseline.get(k)
        if base is None:
            drifted.append((k, "missing from baseline", v))
            continue
        denom = max(abs(base), 1e-9)
        if abs(v - base) / denom > rel_tol and abs(v - base) > 1e-9:
            drifted.append((k, base, v))
    return drifted


def baseline_path(scenario):
    return os.path.join(BASELINE_DIR, f"baseline_{scenario}.json")


def load_baseline(scenario):
    with open(baseline_path(scenario), encoding="utf-8") as f:
        return json.load(f)


def render_report(metrics, drifted, baseline_exists):
    lines = [f"BEHAVIORAL PLAUSIBILITY -- scenario {metrics['scenario']}, "
             f"{metrics['n_sims']} simulated seasons (hermetic golden fixtures)",
             "",
             f"{'mechanic':<22} {'sim':>9} {'real 2025':>10} {'band':>16}  verdict"]
    for k, ref in REAL_2025.items():
        sim_v = metrics.get(k)
        band = f"[{ref['band'][0]:g}, {ref['band'][1]:g}]"
        lines.append(f"{k:<22} {sim_v:>9g} {ref['value']:>10g} {band:>16}  {classify(k, sim_v)}")
    lines.append("")
    lines.append(f"(trade offer events, context only: {metrics['trade_offer_events']}/season "
                 "-- the mechanism runs; completions are what reality counts)")
    lines.append("")
    if not baseline_exists:
        lines.append("BASELINE: none committed for this scenario -- run with --regenerate.")
    elif drifted:
        lines.append("DRIFT vs committed baseline (an engine behavior change -- regenerate "
                     "deliberately, in its own commit, with the deltas explained):")
        for k, base, cur in drifted:
            lines.append(f"  {k}: baseline {base} -> current {cur}")
    else:
        lines.append("No drift vs the committed baseline: behavioral rates are exactly the "
                     "accepted ones (known gaps included -- they are the baseline, not failures).")
    return "\n".join(lines)

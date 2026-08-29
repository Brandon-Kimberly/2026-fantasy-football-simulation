"""
tests.test_lineup_optimality

AUDIT_PLAN.md Phase 4 -- decision logic.

    Invariant: decisions are optimal given information legitimately available at decision time.

Optimality questions are answered by brute force and closed-form cross-checks, not by the
real-data backtest: that backtest is the gate for a decision-logic change that might touch
baseline computation, and nothing here does.

Passing tests lock what was verified to hold: the Hungarian assignment is exactly optimal
(1,700 random rosters vs exhaustive search, including dual-eligible players and FLEX), the
lineup criterion sees no realised score (no lookahead), and the streamer-need count agrees
with the assignment's unfilled slots week by week. Failing tests characterise the Phase 4
findings in AUDIT_PHASE_4_FINDINGS.md; they are red on purpose until remediation is decided.

WHAT IS NOT COVERED
-------------------
1. FAAB conservation (spend + remaining == start) per team: _compute_faab_bid does not
   receive the team, so per-team spend is not observable from outside run_simulation.
   Non-negativity is covered in test_invariants; the bid curve in test_simulation.
2. The 2-week deficit lookahead is a no-op today (byes are unmodelled -- Phase 1 finding 7 --
   and next week's injuries are unknown at decision time), so its inputs are identical to this
   week's. Nothing to assert until byes exist.
"""
import itertools
import logging
import os
import unittest
from unittest.mock import patch

import numpy as np

from fantasy_sim.config import REQUIRED_STARTING_SLOTS, SIM_CONFIG
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)
from tests.golden_master import STAGE_A_ARG_NAMES, _sandbox
from tests.test_distributions import controlled_season

SLOTS = list(REQUIRED_STARTING_SLOTS)
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DL", "LB", "DB"]


# ----------------------------------------------------------------- Hungarian vs brute force
def _eligible_slot_indices(opts):
    return [j for j, s in enumerate(SLOTS)
            if s in opts or (s == "FLEX" and any(o in ("RB", "WR", "TE") for o in opts))]


def brute_force_best(candidates):
    """Exhaustive search over every eligibility-respecting injective assignment (including
    benching). Feasible for <= 7 players."""
    per_player = [_eligible_slot_indices(opts) + [None] for _, opts, _ in candidates]
    best = 0.0
    for choice in itertools.product(*per_player):
        used = [c for c in choice if c is not None]
        if len(used) != len(set(used)):
            continue
        best = max(best, sum(v for (_, _, v), c in zip(candidates, choice) if c is not None))
    return best


class TestHungarianOptimality(unittest.TestCase):
    def test_assignment_matches_exhaustive_search_on_random_rosters(self):
        """1,500 random rosters of 1-6 players, ~70% containing a dual-eligible player, plus
        200 seven-player FLEX-heavy rosters. The Hungarian total must equal the exhaustive
        maximum exactly."""
        rng = np.random.default_rng(4)
        for trial in range(1500):
            k = int(rng.integers(1, 7))
            cands = []
            for i in range(k):
                opts = list(rng.choice(POSITIONS, 2, replace=False)) if rng.random() < 0.3 else [str(rng.choice(POSITIONS))]
                cands.append(("p%d" % i, opts, float(rng.uniform(0, 30))))
            assigned, _ = FantasySimulationEngine._solve_optimal_assignment(cands)
            self.assertAlmostEqual(sum(v for _, v, _ in assigned), brute_force_best(cands), places=9,
                                   msg="trial %d: %r" % (trial, cands))
        for trial in range(200):
            cands = [("p%d" % i, list(rng.choice(["RB", "WR", "TE", "QB", "DB"], size=int(rng.integers(1, 3)), replace=False)),
                      float(rng.uniform(0, 30))) for i in range(7)]
            assigned, _ = FantasySimulationEngine._solve_optimal_assignment(cands)
            self.assertAlmostEqual(sum(v for _, v, _ in assigned), brute_force_best(cands), places=9)

    def test_dual_eligible_player_is_placed_where_the_lineup_gains_most(self):
        """A WR/DB with the only DB-eligible slot open must take DB even if WR is listed
        first, when that frees a WR slot for a better pure WR."""
        cands = [("Hunter", ["WR", "DB"], 15.0), ("WR_A", ["WR"], 14.0), ("WR_B", ["WR"], 13.0)]
        assigned, _ = FantasySimulationEngine._solve_optimal_assignment(cands)
        placed = {name: slot for name, _v, slot in assigned}
        self.assertEqual(placed["Hunter"], "DB")
        self.assertAlmostEqual(sum(v for _, v, _ in assigned), brute_force_best(cands))


# ------------------------------------------------------------------------- no lookahead
class TestNoLookahead(unittest.TestCase):
    def test_lineup_criterion_never_sees_the_realised_draw(self):
        """Controlled engine (std_epistemic 0, injuries off, all FA: environment multiplier
        exactly 1, no contingency). Every candidate value handed to the assignment must then
        equal the baseline mean exactly, every week, every season -- while realised scores
        vary. Any leakage of final_score, z, or env_var into the criterion breaks equality."""
        real_solve = FantasySimulationEngine._solve_optimal_assignment
        seen = []

        def spy(cands):
            seen.extend(v for _, _, v in cands)
            return real_solve(cands)
        with patch.object(FantasySimulationEngine, "_solve_optimal_assignment", staticmethod(spy)):
            W = controlled_season(12.0, 5.0, 0.0, sims=20)
        seen = np.array(seen)
        self.assertGreater(seen.size, 10000)
        self.assertEqual(float(np.abs(seen - 12.0).max()), 0.0)
        self.assertGreater(float(W.std()), 5.0)   # the draws really did vary


# ---------------------------------------------------------- streamer needs vs real holes
class _FixtureRun(object):
    """One instrumented fixture run: per-week bid count and per-week unfilled-slot count from
    the assignment (trade evaluations also call the assignment, so solves are attributed to
    weeks by the apportion boundary, which every week crosses exactly once)."""
    _cache = {}

    def __init__(self, scenario, batches=2, sims=10):
        real_solve = FantasySimulationEngine._solve_optimal_assignment
        real_faab = FantasySimulationEngine._compute_faab_bid
        real_apportion = FantasySimulationEngine._apportion_vacated_volume
        self.bids_by_week, self.solves, self.marks, self.args = [], [], [], {}
        week = [0]

        def solve(c):
            a, u = real_solve(c)
            self.solves.append(len(u))
            return a, u

        def faab(*a):
            self.bids_by_week.append(week[0])
            return real_faab(*a)

        def apportion(engine, *a):
            self.marks.append(len(self.solves))
            week[0] += 1
            return real_apportion(engine, *a)

        def export(engine, *a):
            self.args.update(zip(STAGE_A_ARG_NAMES, a))
        with _sandbox(scenario, batches, sims):
            with patch.object(FantasySimulationEngine, "_solve_optimal_assignment", staticmethod(solve)), \
                 patch.object(FantasySimulationEngine, "_compute_faab_bid", staticmethod(faab)), \
                 patch.object(FantasySimulationEngine, "_apportion_vacated_volume", apportion), \
                 patch.object(FantasySimulationEngine, "export_and_visualize", export):
                engine = FantasySimulationEngine()
                engine.run_simulation()
        self.n_teams = len(engine.team_names)
        self.n_sims = batches * sims
        self.replacement_levels = dict(engine.replacement_levels)
        self.weeks = week[0]

    @classmethod
    def get(cls, scenario):
        if scenario not in cls._cache:
            cls._cache[scenario] = cls(scenario)
        return cls._cache[scenario]

    def unfilled_in_week(self, w):
        start = self.marks[w]
        return sum(self.solves[start:start + self.n_teams])

    def bids_in_week(self, w):
        return sum(1 for b in self.bids_by_week if b == w)


class TestStreamerNeedsMatchRealHoles(unittest.TestCase):
    """The greedy need counter (positions in a fixed order, then FLEX) and the Hungarian
    assignment must agree on how many slots are open, or FAAB is spent on phantom holes /
    real holes go unbid.

    Until bye modelling (2026-08-28) this was `bids == unfilled` every week, because the
    need counter's next-week lookahead was a no-op: injuries do not split the two scans
    (both read the same clocks), only byes do, and no player had one. With byes live, a
    team bids this week for next week's bye hole and carries the won streamer one week
    (Phase 4 finding 4, fixed) -- so the exact statement is now three-part."""

    @staticmethod
    def _rostered_bye_weeks(scenario):
        import json
        here = os.path.dirname(os.path.abspath(__file__))
        fx = os.path.join(here, "fixtures", "golden", scenario)
        rosters = json.load(open(os.path.join(fx, "live_rosters.json")))
        base = json.load(open(os.path.join(fx, "player_baselines.json")))
        state = json.load(open(os.path.join(fx, "league_state.json")))
        byes = {base[p["name"]].get("bye", 0) for t in rosters.values() for p in t if p["name"] in base}
        return byes - {0}, int(state["current_week"])

    @unittest.expectedFailure
    def test_bids_equal_unfilled_slots_except_next_to_a_bye(self):
        """CHARACTERISATION -- fails at week 16 of the week01 fixture (2 bids, 1 hole, no bye
        within a week). Cause: the need scan looks ahead with `min(14, week_num + 1)`, so every
        week >= 15 re-scans WEEK 14 and counts each rostered bye-14 player as next week's hole.
        Latent while every bye was 0; live since the fixtures carry byes. Remove the
        expectedFailure with the fix (bye-modelling step 6b).

        On every week with no rostered bye in w-1, w or w+1, bids == unfilled exactly (the
        pre-bye invariant, still holding where the lookahead cannot bind). On every other week
        the two may differ -- and wherever they DO differ, a bye must be adjacent: byes are the
        only thing that separates the lookahead from this week's holes."""
        for scenario in ("week01", "week06"):
            run = _FixtureRun.get(scenario)
            byes, cw = self._rostered_bye_weeks(scenario)
            wps = run.weeks // run.n_sims
            exact = diverged = 0
            for w in range(run.weeks):
                wk = cw + (w % wps)
                near_bye = bool({wk - 1, wk, wk + 1} & byes)
                b, u = run.bids_in_week(w), run.unfilled_in_week(w)
                if not near_bye:
                    self.assertEqual(b, u, "%s week %d (index %d): bids %d vs unfilled %d with no bye nearby"
                                     % (scenario, wk, w, b, u))
                    exact += 1
                elif b != u:
                    diverged += 1
            self.assertGreater(exact, 0, scenario + ": no lookahead-free weeks were checked")
            self.assertGreater(diverged, 0, scenario + ": the lookahead never bound next to a bye -- "
                                            "byes are not reaching the need counter")

    def test_every_real_hole_is_coverable_every_week(self):
        """Per team, need = max(0, max(holes_w, holes_w+1) - carried) and every bid wins, so
        bids_w + carried_w >= holes_w; carried_w <= bids_w-1 (one-week persistence, same sim).
        League-wide: bids_w + bids_w-1 >= unfilled_w, with bids_w-1 = 0 at a sim's first week."""
        for scenario in ("week01", "week06"):
            run = _FixtureRun.get(scenario)
            wps = run.weeks // run.n_sims
            for w in range(run.weeks):
                prev = run.bids_in_week(w - 1) if w % wps else 0
                self.assertGreaterEqual(run.bids_in_week(w) + prev, run.unfilled_in_week(w),
                                        "%s week-index %d: %d holes, %d bids + %d carried-at-most"
                                        % (scenario, w, run.unfilled_in_week(w), run.bids_in_week(w), prev))


class TestStreamerValueBound(unittest.TestCase):
    def test_a_won_streamer_is_never_worth_more_than_the_replacement_level(self):
        """Regression guard for Phase 4 finding 3. Won streamers used to take their mean from
        a league-wide bid ladder (12.0, 11.5, 11.0, ... floor 4.0) regardless of position.
        Replacement level is 8.4 at DL, 7.7 at TE, 8.8 at DB, 10.7 at K; a rank-1 streamer at
        12.0 out-projected 105 of 156 rostered players, so a roster hole at those positions
        was an UPGRADE for a ~3.5 FAAB bid. A won streamer is now capped at the position's
        replacement level where it fills the slot. Observed through the audit log (sim 0),
        whose starters carry the streamer's expected value."""
        run = _FixtureRun.get("week01")
        worst = []
        for wk, wd in run.args["audit_log"]["weeks"].items():
            for team, td in wd["teams"].items():
                for s in td["starters"]:
                    if s["name"].startswith("STREAMER_"):
                        pos = s["name"].split("_")[1]
                        cap = run.replacement_levels.get(pos, 4.0)
                        if s["expected"] > cap + 1e-9:
                            worst.append((wk, team, s["name"], s["expected"], round(cap, 2)))
        self.assertEqual(worst, [], "streamers valued above their position's replacement level: %r" % worst[:6])


# ------------------------------------------------------------------------------- trades
class TestTradeLogic(unittest.TestCase):
    @staticmethod
    def _reconstruct(calls):
        """Evaluations are 3 or 4 get_optimal_score calls (the acceptance test short-circuits
        after the desperate side fails). Returns [(accepted, len_d, len_r, len_tent_r)]."""
        out, i = [], 0
        while i + 2 < len(calls):
            (d, vd), (r, vr), (td, vtd) = calls[i:i + 3]
            p1, sd, sr, std_ = d[0], set(d), set(r), set(td)
            if not (p1 not in std_ and std_ - sd <= sr and len(std_) == len(sd)):
                i += 1
                continue
            if vtd > vd and i + 3 < len(calls):
                tr, vtr = calls[i + 3]
                out.append((vtr > vr, len(d), len(r), len(tr)))
                i += 4
            else:
                out.append((False, len(d), len(r), None))
                i += 3
        return out

    def _run_fixture(self, scenario, batches, sims):
        real_gos = FantasySimulationEngine.get_optimal_score
        calls = []

        def gos(engine, roster):
            v = real_gos(engine, roster)
            calls.append((tuple(roster), v))
            return v
        with _sandbox(scenario, batches, sims):
            with patch.object(FantasySimulationEngine, "get_optimal_score", gos), \
                 patch.object(FantasySimulationEngine, "export_and_visualize", lambda s, *a: None):
                FantasySimulationEngine().run_simulation()
        return self._reconstruct(calls)

    @unittest.expectedFailure
    def test_trades_are_live_on_the_preseason_fixture(self):
        """CHARACTERISATION, deliberately still failing -- Phase 4 finding 1, tracked as
        AUDIT_PLAN.md F2 (sized, not implemented). Over 40 simulated seasons of the week01
        fixture, 0 of ~220 evaluated trades are accepted (0 of 548 over 100 seasons in the
        probe). The rich team gives its 6th- and 7th-best players -- both STARTERS in a 13-slot
        lineup (medians 12.7 and 12.2) -- for the desperate team's best, a QB 99% of the time;
        its optimal score falls every time (max gain -3.17). MANAGER_PROFILES['trade_will']
        therefore has no observable effect. F2's acceptance criterion is >= 1.0 completed
        trades per season on this fixture; remove the expectedFailure when it lands and this
        test becomes the guard."""
        evals = self._run_fixture("week01", 2, 20)
        self.assertGreater(len(evals), 50, "trade block did not run")
        self.assertGreater(sum(1 for e in evals if e[0]), 0,
                           "0 of %d evaluated trades accepted" % len(evals))

    def test_a_completed_trade_conserves_roster_sizes(self):
        """Regression guard for Phase 4 finding 2. The desperate side drops its worst player
        after receiving two, so its roster size was conserved; the rich side gave two and
        received one, never dropping or adding, so it shrank by one on every completed trade
        (observed 19 -> 18 on all 16 completions in 100 week06 seasons). The trade is now
        2-for-2 -- the desperate side's dropped player goes to the rich side as a throw-in --
        so both rosters are conserved. Reproduced on a crafted league where the trade is
        favourable to both sides."""
        teams = ["Rich1", "Rich2", "M3", "M4", "M5", "M6", "M7", "Poor8"]
        slots = ["QB", "K", "DB", "DL", "LB", "RB", "RB", "RB", "WR", "WR", "WR", "TE", "TE"]

        def roster(prefix, starter_means, bench_means):
            names = []
            for i, (pos, m) in enumerate(zip(slots, starter_means)):
                names.append(("%s_%s%d" % (prefix, pos, i), pos, m))
            for i, m in enumerate(bench_means):
                names.append(("%s_B%d" % (prefix, i), "WR", m))
            return names

        specs = {}
        # rich: five elite starters, a weak QB (5.9 -- strictly below the 6.0 starters, so it
        # is NOT among the 6th/7th best that get offered), seven weak starters, junk bench.
        # Its 6th/7th best are then two 6.0 starters, and a 25-point QB for them is a
        # genuine upgrade (+19 at QB, -8 from replacing two 6s with 2s).
        for t in ("Rich1", "Rich2"):
            specs[t] = roster(t, [5.9] + [30.0] * 5 + [6.0] * 7, [2.0] * 6)
        for t in ("M3", "M4", "M5", "M6", "M7"):
            specs[t] = roster(t, [10.0] * 13, [3.0] * 6)
        # poor: one star QB, a second good QB on the bench, everything else weak -> giving the
        # star for two 6s improves its own lineup (two 4s become 6s, QB slot barely moves).
        specs["Poor8"] = roster("Poor8", [25.0] + [4.0] * 12, [24.0] + [1.0] * 5)
        specs["Poor8"][13] = ("Poor8_QB2", "QB", 24.0)

        rosters = {t: [{"name": n, "pos": p, "team": "FA"} for n, p, _ in ps] for t, ps in specs.items()}
        baselines = {n: {"mean": m, "std_aleatoric": 2.0, "std_epistemic": 0.0, "pos": p, "team": "FA"}
                     for ps in specs.values() for n, p, m in ps}
        fs = {
            LEAGUE_STATE_FILE: {"current_week": 1},
            LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in teams},
            VEGAS_FILE: {"_meta": {"week": 1, "source": "odds_api", "fetched_at": "x"}},
            LIVE_ROSTERS_FILE: rosters, BASELINES_FILE: baselines,
            TEAM_RATINGS_FILE: {}, DEFENSIVE_RATINGS_FILE: {},
            DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
            LEAGUE_SCHEDULE_FILE: [[["Rich1", "Poor8"], ["Rich2", "M3"], ["M4", "M5"], ["M6", "M7"]]] * 14,
            NFL_SCHEDULE_FILE: {}, WEEKLY_ACTUALS_FILE: {},
        }
        real_gos = FantasySimulationEngine.get_optimal_score
        calls = []

        def gos(engine, r):
            v = real_gos(engine, r)
            calls.append((tuple(r), v))
            return v
        willing = {t: {"faab_agg": 0.0, "trade_will": 1.0} for t in teams}
        prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        orig = SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"]
        SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = 1, 6
        try:
            with patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]), \
                 patch.dict(SIM_CONFIG["INJURY_RATES"], {k: 0.0 for k in SIM_CONFIG["INJURY_RATES"]}), \
                 patch.dict("fantasy_sim.simulation.MANAGER_PROFILES", willing, clear=True), \
                 patch.object(FantasySimulationEngine, "get_optimal_score", gos), \
                 patch.object(FantasySimulationEngine, "export_and_visualize", lambda s, *a: None):
                FantasySimulationEngine().run_simulation()
        finally:
            SIM_CONFIG["NUM_BATCHES"], SIM_CONFIG["SIMS_PER_BATCH"] = orig
            logging.getLogger().setLevel(prev)
        evals = self._reconstruct(calls)
        accepted = [e for e in evals if e[0]]
        self.assertGreater(len(accepted), 0, "crafted league produced no completed trade; %d evaluated" % len(evals))
        for _ok, len_d, len_r, len_tr in accepted:
            self.assertEqual(len_tr, len_r,
                             "rich roster %d -> %d on a completed trade (desperate stays %d)" % (len_r, len_tr, len_d))


if __name__ == "__main__":
    unittest.main()

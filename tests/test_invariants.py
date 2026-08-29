"""
tests.test_invariants

AUDIT_PLAN.md Phase 1 -- conservation and invariants.

    Invariant: nothing is created or destroyed that shouldn't be.

WHY THESE TESTS LOOK LIKE THIS
------------------------------
Phase 0 pinned what the engine DOES (golden_master.py hashes its outputs). It says nothing
about whether what it does is right. This module asks the complementary question: for each
quantity the engine conserves by construction, is it actually conserved?

Every historical defect in this codebase was found by asking a property question rather than
reading code linearly, so the tests are organised by property, not by file:

    conservation  -- total out == total in           (vacated volume, wins, all-play)
    bounds        -- values stay inside their range  (injury clocks, FAAB, slot counts)
    normalisation -- probabilities sum to 1          (playoff/champ/toilet shares)
    consistency   -- exported == computed            (h2h matrix, luck index, percentiles)
    liveness      -- does this field ever hold data? (bye weeks)

HOW THE ENGINE IS OBSERVED
--------------------------
`run_simulation` is a single ~445-line method, so most of these quantities are local variables
with no public accessor. Rather than refactor it -- Phase 8's job, and premature here -- the
tests observe it through the seams that already exist: the extracted helper methods
(`_solve_optimal_assignment`, `_apportion_vacated_volume`, `_record_vacated_volume`,
`_compute_faab_bid`) are wrapped with recorders that delegate to the real implementation and
change no number, and the 17 arguments run_simulation hands to export_and_visualize are
captured wholesale.

Because the wrappers are pure pass-throughs, an instrumented run is numerically identical to a
production one. `test_instrumentation_does_not_perturb_the_engine` asserts exactly that rather
than leaving it as a claim.

The fixtures are Phase 0's committed league states, reused deliberately: week01 (preseason, all
14 weeks simulated) and week06 (mid-season, 9 weeks simulated, 5 weeks of banked results). That
pairing is what makes the Phase 1 findings legible -- four of them are invisible at week 1 and
appear only once part of the season is already in the books.

WHAT IS NOT COVERED
-------------------
1. Bye weeks. The plan's invariant is "a player on bye never scores and never absorbs vacated
   volume". It cannot be exercised end to end, because no player in any fixture -- or in
   production -- has a bye week at all. See TestByeWeekLiveness for why, and treat the
   scoring/absorption half of that invariant as an open coverage gap, not as verified.
2. current_week > 14. run_simulation raises IndexError there (top4 is never populated), so
   there is nothing to assert about conservation in that regime. Phase 5 owns the week-indexing
   question.
3. League sizes other than 8 and MEDIAN_SCORING_ENABLED=False, inherited from the Phase 0
   fixture set's own gaps.
"""
import collections
import copy
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import numpy as np

from fantasy_sim.config import REQUIRED_STARTING_SLOTS, SIM_CONFIG
from fantasy_sim.simulation import FantasySimulationEngine
from tests.golden_master import STAGE_A_ARG_NAMES, _sandbox

try:
    from hypothesis import HealthCheck, assume, given, settings
    from hypothesis import strategies as st
    HAS_HYPOTHESIS = True
except ImportError:
    # Optional, handled the same way as espn_api: the suite must still run without it. The
    # @given/@settings decorators are evaluated when the class body executes, which happens
    # regardless of skipUnless, so no-op stand-ins are needed for the module to import at all.
    HAS_HYPOTHESIS = False

    def _noop_decorator(*_args, **_kwargs):
        def wrap(func):
            return func
        return wrap

    given = settings = _noop_decorator

    def assume(_condition):  # pragma: no cover - never reached; the tests are skipped
        pass

    class HealthCheck(object):  # pragma: no cover - referenced only inside @settings
        too_slow = None

    class _NoStrategies(object):
        """Absorbs the st.<strategy>(...) calls made inside the skipped classes' decorators."""

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    st = _NoStrategies()

# Small enough to keep the suite fast, large enough that every branch under test fires:
# at 16 sims x 14 weeks x 8 teams the injury path alone draws ~1,200 onsets per scenario.
BATCHES = 2
SIMS_PER_BATCH = 8
REGULAR_SEASON_WEEKS = 14


# --------------------------------------------------------------------------- run harness
@contextmanager
def pristine_config():
    """Restores any config constant a run might mutate.

    `SIM_CONFIG['KNOWN_MISSING_ASSETS']` used to be corrupted in place by any run whose fixture
    had completed weeks; the engine now deepcopies it on import, and
    TestConfigConstantsSurviveARun is the regression guard. This wrapper is kept as defence in
    depth rather than removed: every run in this module goes through it, so a future
    reintroduction of that aliasing shows up as a single focused failure in
    TestConfigConstantsSurviveARun instead of as unexplained red in whatever module happens to
    run next."""
    saved = copy.deepcopy(SIM_CONFIG["KNOWN_MISSING_ASSETS"])
    try:
        yield
    finally:
        SIM_CONFIG["KNOWN_MISSING_ASSETS"] = copy.deepcopy(saved)


class ScenarioRun(object):
    """One instrumented end-to-end run of a fixture scenario, cached per process.

    Holds three things: `args` (what run_simulation passed to export_and_visualize),
    `saved` (what export_and_visualize passed to save_json), and `rec` (what the wrapped
    helper methods observed on the way through)."""

    _cache = {}

    def __init__(self, scenario, batches=BATCHES, sims=SIMS_PER_BATCH):
        self.scenario = scenario
        self.total_sims = batches * sims
        rec = {
            "slot_totals": collections.Counter(),
            "duplicate_assignments": 0,
            "ineligible_assignments": 0,
            "clock_max": 0,
            "clock_min": 0,
            "clock_observations": 0,
            "vacated_in": 0.0,
            "vacated_out": 0.0,
            "apportion_overshoots": 0,
            "faab_remaining_min": float("inf"),
            "faab_calls": 0,
        }
        self.rec = rec

        real_solve = FantasySimulationEngine._solve_optimal_assignment
        real_apportion = FantasySimulationEngine._apportion_vacated_volume
        real_record = FantasySimulationEngine._record_vacated_volume
        real_faab = FantasySimulationEngine._compute_faab_bid
        real_export = FantasySimulationEngine.export_and_visualize

        def solve(candidates):
            assigned, unfilled = real_solve(candidates)
            rec["slot_totals"][len(assigned) + len(unfilled)] += 1
            names = [a[0] for a in assigned]
            if len(names) != len(set(names)):
                rec["duplicate_assignments"] += 1
            eligibility = dict((c[0], c[1]) for c in candidates)
            for name, _value, slot in assigned:
                opts = eligibility[name]
                ok = slot in opts or (
                    slot == "FLEX" and any(o in ("RB", "WR", "TE") for o in opts)
                )
                if not ok:
                    rec["ineligible_assignments"] += 1
            return assigned, unfilled

        def record_vacated(pools, p_pos, nfl_team, season_mean):
            before = sum(sum(d.values()) for d in pools.values())
            real_record(pools, p_pos, nfl_team, season_mean)
            rec["vacated_in"] += sum(sum(d.values()) for d in pools.values()) - before

        def apportion(engine, pools, clocks, newly_injured):
            # Called exactly once per simulated week, after every injury for that week is
            # known -- the only point at which the whole league's injury_clocks dict is
            # visible from outside run_simulation.
            values = list(clocks.values())
            if values:
                rec["clock_observations"] += 1
                rec["clock_max"] = max(rec["clock_max"], max(values))
                rec["clock_min"] = min(rec["clock_min"], min(values))
            out = real_apportion(engine, pools, clocks, newly_injured)
            pool_total = sum(sum(d.values()) for d in pools.values())
            given = sum(out.values())
            rec["vacated_out"] += given
            if given > pool_total + 1e-9:
                rec["apportion_overshoots"] += 1
            return out

        def faab_bid(remaining, raw_draw, aggression, needs, deflation, avg_league_faab):
            rec["faab_calls"] += 1
            rec["faab_remaining_min"] = min(rec["faab_remaining_min"], remaining)
            return real_faab(remaining, raw_draw, aggression, needs, deflation, avg_league_faab)

        captured = {}

        def capturing_export(engine, *args):
            if not captured:
                captured.update(zip(STAGE_A_ARG_NAMES, args))
            return real_export(engine, *args)

        with pristine_config(), _sandbox(scenario, batches, sims) as saved:
            with patch.object(FantasySimulationEngine,
                              "_solve_optimal_assignment", staticmethod(solve)), \
                 patch.object(FantasySimulationEngine,
                              "_record_vacated_volume", staticmethod(record_vacated)), \
                 patch.object(FantasySimulationEngine,
                              "_compute_faab_bid", staticmethod(faab_bid)), \
                 patch.object(FantasySimulationEngine,
                              "_apportion_vacated_volume", apportion), \
                 patch.object(FantasySimulationEngine,
                              "export_and_visualize", capturing_export):
                engine = FantasySimulationEngine()
                engine.run_simulation()
            self.saved = dict(saved)

        self.args = captured
        self.teams = list(engine.team_names)
        self.current_week = engine.current_week
        self.actual_points = dict(engine.actual_points)
        self.weeks_simulated = REGULAR_SEASON_WEEKS - (engine.current_week - 1)

    @classmethod
    def get(cls, scenario):
        if scenario not in cls._cache:
            cls._cache[scenario] = cls(scenario)
        return cls._cache[scenario]

    def payload(self, fragment):
        """The save_json payload whose filename contains `fragment`."""
        for name, data in self.saved.items():
            if fragment in name:
                return data
        raise KeyError("no saved payload matching " + fragment)


class ScenarioTestCase(unittest.TestCase):
    """Base class running both fixture scenarios through one assertion body."""

    SCENARIOS = ("week01", "week06")

    def each_scenario(self):
        for scenario in self.SCENARIOS:
            yield scenario, ScenarioRun.get(scenario)


# ------------------------------------------------------------------- instrumentation sanity
class TestInstrumentationFidelity(unittest.TestCase):
    def test_instrumentation_does_not_perturb_the_engine(self):
        """The recorders wrapped around the engine's helper methods must be pure
        pass-throughs. If they weren't, every other test in this module would be measuring an
        engine that does not exist in production.

        Runs a fully instrumented simulation and a completely unpatched one back to back on
        identical fixtures and seeds -- each from a pristine config, so this measures the
        wrappers and nothing else -- and asserts bit-level equality of the season win and
        point arrays."""
        with pristine_config():
            instrumented = ScenarioRun("week01")

        clean = {}

        def capture(engine, *args):
            if not clean:
                clean.update(zip(STAGE_A_ARG_NAMES, args))
            return None

        with pristine_config():
            with _sandbox("week01", BATCHES, SIMS_PER_BATCH):
                with patch.object(FantasySimulationEngine, "export_and_visualize", capture):
                    FantasySimulationEngine().run_simulation()

        for team in instrumented.teams:
            np.testing.assert_array_equal(
                instrumented.args["wins"][team], clean["wins"][team],
                err_msg="instrumentation perturbed wins for " + team)
            np.testing.assert_array_equal(
                instrumented.args["points"][team], clean["points"][team],
                err_msg="instrumentation perturbed points for " + team)


# ------------------------------------------------------------------------------- bounds
class TestRosterSlotBounds(ScenarioTestCase):
    def test_every_lineup_fills_exactly_thirteen_slots(self):
        """assigned + unfilled == 13, every team, every week, every simulation.

        This is what makes 'team weekly total == sum of its 13 starters' true: run_simulation
        injects exactly one streamer per unfilled slot, so any drift in this count would
        silently change a team's weekly score by a whole player."""
        expected = len(REQUIRED_STARTING_SLOTS)
        self.assertEqual(expected, 13)
        for scenario, run in self.each_scenario():
            observed = dict(run.rec["slot_totals"])
            self.assertEqual(
                observed, {expected: sum(observed.values())},
                msg=scenario + ": lineups did not always total 13 slots -- " + repr(observed))

    def test_no_player_is_assigned_to_two_slots(self):
        for scenario, run in self.each_scenario():
            self.assertEqual(run.rec["duplicate_assignments"], 0,
                             msg=scenario + ": a player filled more than one starting slot")

    def test_no_player_is_assigned_to_an_ineligible_slot(self):
        for scenario, run in self.each_scenario():
            self.assertEqual(run.rec["ineligible_assignments"], 0,
                             msg=scenario + ": a player started at an ineligible position")


class TestInjuryClockBounds(ScenarioTestCase):
    def test_injury_clocks_stay_within_zero_and_sixteen(self):
        """Clocks are set to min(16, weeks_missed) on onset and decremented once per week,
        never below zero. The severe-injury component draws Exponential(scale=12.3), which is
        unbounded, so the min(16, ...) clamp is load-bearing -- without it a clock could
        outlive the season."""
        for scenario, run in self.each_scenario():
            self.assertGreater(run.rec["clock_observations"], 0,
                               msg=scenario + ": no injury clocks were observed at all")
            self.assertGreaterEqual(run.rec["clock_min"], 0,
                                    msg=scenario + ": an injury clock went negative")
            self.assertLessEqual(run.rec["clock_max"], 16,
                                 msg=scenario + ": an injury clock exceeded 16 weeks")


class TestFaabBounds(ScenarioTestCase):
    def test_remaining_faab_is_never_negative(self):
        """A bid is always sized against the bidder's real remaining budget, and the deduction
        is clamped, so a manager can never be modelled as spending money they do not have."""
        for scenario, run in self.each_scenario():
            self.assertGreater(run.rec["faab_calls"], 0,
                               msg=scenario + ": no FAAB bids were placed")
            self.assertGreaterEqual(
                run.rec["faab_remaining_min"], 0.0,
                msg=scenario + ": a bid was sized against a negative FAAB balance")


# -------------------------------------------------------------------------- conservation
class TestVacatedVolumeConservation(ScenarioTestCase):
    def test_apportioned_volume_never_exceeds_vacated_volume(self):
        """The defect this locks down: contingency_pts was once a bare pool lookup, so every
        rostered player sharing an injured player's team and position received the FULL
        vacated amount -- one injury injecting 3x its volume into the league.

        Asserted end to end on the real engine, summing what _record_vacated_volume put into
        the pools against what _apportion_vacated_volume handed out."""
        for scenario, run in self.each_scenario():
            self.assertGreater(run.rec["vacated_in"], 0.0,
                               msg=scenario + ": no volume was vacated; test is vacuous")
            self.assertEqual(run.rec["apportion_overshoots"], 0,
                             msg=scenario + ": a week apportioned more volume than it vacated")
            self.assertLessEqual(
                run.rec["vacated_out"], run.rec["vacated_in"] + 1e-6,
                msg=scenario + ": total apportioned (%.4f) exceeded total vacated (%.4f)"
                    % (run.rec["vacated_out"], run.rec["vacated_in"]))

    def test_vacated_volume_is_fully_apportioned_when_a_healthy_teammate_exists(self):
        """The other side of conservation: volume must not silently evaporate either. It is
        allowed to vanish only when no healthy member of the real NFL position group remains,
        which does not occur in either fixture -- so here the pools should balance exactly."""
        for scenario, run in self.each_scenario():
            self.assertAlmostEqual(
                run.rec["vacated_out"], run.rec["vacated_in"], places=6,
                msg=scenario + ": vacated volume leaked (in=%.6f out=%.6f)"
                    % (run.rec["vacated_in"], run.rec["vacated_out"]))


class TestDecisionConservation(ScenarioTestCase):
    def test_league_wide_wins_are_conserved_every_simulated_season(self):
        """Each week awards exactly one decision per team under H2H (8 teams -> 4 matchups,
        2 decisions each, or a split) plus one per team for the median beat. Summed across
        the league, every simulated season must therefore award the identical total, with no
        variance whatsoever -- banked results included."""
        for scenario, run in self.each_scenario():
            totals = np.zeros(run.total_sims)
            for team in run.teams:
                totals += run.args["wins"][team]

            n_teams = len(run.teams)
            per_week = n_teams * (2 if SIM_CONFIG.get("MEDIAN_SCORING_ENABLED", True) else 1)
            banked = sum(run.args["wins"][t][0] for t in run.teams) - run.weeks_simulated * per_week
            expected = run.weeks_simulated * per_week + banked

            self.assertEqual(
                float(totals.min()), float(totals.max()),
                msg=scenario + ": league-wide decisions varied between simulations "
                    "(%.1f..%.1f)" % (totals.min(), totals.max()))
            self.assertAlmostEqual(
                float(totals[0]), expected, places=6,
                msg=scenario + ": league awarded %.1f decisions, expected %.1f"
                    % (totals[0], expected))

    def test_all_play_wins_equal_the_h2h_matrix_total(self):
        """all_play and h2h_matrix are incremented in the same branch of the same loop, so
        they are two views of one quantity. If they ever diverge, one of the two charts they
        back is reading a different season than the other."""
        for scenario, run in self.each_scenario():
            all_play = sum(run.args["all_play"].values())
            matrix = sum(sum(row.values()) for row in run.args["h2h"].values())
            self.assertEqual(all_play, matrix,
                             msg=scenario + ": all_play (%d) != h2h matrix total (%d)"
                                 % (all_play, matrix))

    def test_cumulative_win_trajectories_never_decrease(self):
        """Wins are banked, never revoked. A decreasing trajectory would mean a later week
        overwrote rather than accumulated."""
        for scenario, run in self.each_scenario():
            for team in run.teams:
                live = run.args["trajectories"][team][:, run.current_week - 1:]
                steps = np.diff(live, axis=1)
                self.assertGreaterEqual(
                    float(steps.min()), -1e-9,
                    msg=scenario + ": " + team + " lost cumulative wins between weeks")


# ------------------------------------------------------------------------- normalisation
class TestProbabilityNormalisation(ScenarioTestCase):
    def test_playoff_champion_and_toilet_shares_sum_to_their_totals(self):
        """Exactly 4 playoff berths, 1 championship and 1 last place are awarded per simulated
        season, so summed across teams these must be 400%, 100% and 100% -- exactly, not
        approximately, since they are counts divided by a known denominator."""
        for scenario, run in self.each_scenario():
            for key, expected in (("b_playoffs", 400.0), ("b_champs", 100.0),
                                  ("b_toilets", 100.0)):
                total = sum(float(np.mean(run.args[key][t])) for t in run.teams) * 100
                self.assertAlmostEqual(
                    total, expected, places=9,
                    msg="%s: %s summed to %.9f, expected %.1f"
                        % (scenario, key, total, expected))


# --------------------------------------------------------------------------- consistency
#
# The four tests below are the Phase 1 findings. Each holds at week01 and fails at week06:
# the quantities are normalised against a hardcoded 14-week season rather than against the
# number of weeks actually simulated, so they are correct only for a preseason run.
#
class TestExportedRatesMatchWeeksSimulated(ScenarioTestCase):
    def test_h2h_win_probability_matrix_pairs_sum_to_one_hundred_percent(self):
        """'Any Given Sunday' compares every team against every other, every week. For any
        pair, P(A beats B) + P(B beats A) must be 100% less the tie rate.

        The numerator counts only weeks actually simulated; the denominator is hardcoded
        total_sims * 14. Mid-season those disagree and every cell in the exported heatmap is
        scaled by weeks_simulated/14 -- the same class of silent deflation the all-play fix
        was written to remove, surviving in the divisor."""
        for scenario, run in self.each_scenario():
            matrix = run.payload("comprehensive_matrix")["h2h_win_probability_matrix"]
            pair_sums = [matrix[a][b] + matrix[b][a]
                         for i, a in enumerate(run.teams) for b in run.teams[i + 1:]]
            self.assertAlmostEqual(
                float(np.mean(pair_sums)), 100.0, delta=1.0,
                msg="%s: h2h pairs sum to %.2f%%, not ~100%% (weeks_simulated=%d, "
                    "divisor assumes 14)" % (scenario, float(np.mean(pair_sums)),
                                             run.weeks_simulated))

    def test_schedule_luck_is_zero_sum(self):
        """luck_rating = actual expected win% - all-play win%. Both terms average to the same
        thing across the league, so the ratings must sum to zero: one team's good schedule is
        another's bad one. A non-zero sum means every team is being reported as lucky, which
        is not a thing that can happen."""
        for scenario, run in self.each_scenario():
            luck = run.payload("syndicate_insights")["schedule_luck_index"]
            total = sum(v["luck_rating"] for v in luck.values())
            self.assertAlmostEqual(
                total, 0.0, delta=0.5,
                msg="%s: schedule luck summed to %+.2f, not zero (weeks_simulated=%d)"
                    % (scenario, total, run.weeks_simulated))

    def test_points_against_per_game_matches_the_weeks_actually_played(self):
        """avg_points_against_per_game divides by 14 regardless of how many weeks contributed
        to the numerator, so mid-season it reports a per-game average no team ever faced."""
        for scenario, run in self.each_scenario():
            luck = run.payload("syndicate_insights")["schedule_luck_index"]
            for team in run.teams:
                expected = (run.args["pts_against"][team] / run.total_sims
                            / run.weeks_simulated)
                self.assertAlmostEqual(
                    luck[team]["avg_points_against_per_game"], expected, delta=0.5,
                    msg="%s: %s points-against/game reported %.2f, actual %.2f"
                        % (scenario, team, luck[team]["avg_points_against_per_game"],
                           expected))


class TestWeeklyScoreStatisticsExcludeUnplayedWeeks(ScenarioTestCase):
    def test_weekly_score_percentiles_are_not_diluted_by_completed_weeks(self):
        """global_weekly_scores is allocated as a full 14-week array but written to only for
        weeks the simulation actually runs. On a mid-season run the leading columns stay at
        their initialised zero, and the exported percentiles are computed over the flattened
        array -- so p10_floor is exactly 0.0 for every team, and the mean is pulled down by
        the fraction of the season already played. The KDE chart is fit over the same zeros."""
        for scenario, run in self.each_scenario():
            stats = run.payload("comprehensive_matrix")["weekly_score_percentiles"]
            for team in run.teams:
                played = run.args["global_weekly_scores"][team][:, run.current_week - 1:]
                self.assertAlmostEqual(
                    stats[team]["mean"], float(played.mean()), delta=1.0,
                    msg="%s: %s weekly mean reported %.2f, actual %.2f"
                        % (scenario, team, stats[team]["mean"], float(played.mean())))
                self.assertGreater(
                    stats[team]["p10_floor"], 0.0,
                    msg="%s: %s p10 scoring floor is %.2f -- a team cannot score zero"
                        % (scenario, team, stats[team]["p10_floor"]))


class TestExpectedPointsCoversTheRegularSeason(ScenarioTestCase):
    def test_expected_points_excludes_the_playoff_weeks(self):
        """sim_points accumulates for all 8 teams through weeks 15 and 16, but only 4 teams
        play a semi-final and only 2 play the final. Expected_Points is reported next to
        Expected_Wins, which covers the 14-week regular season, so the pair is inconsistent:
        every team is credited with two extra weeks of scoring, including the four eliminated
        at week 14 and the team that finished last."""
        for scenario, run in self.each_scenario():
            outcomes = run.payload("comprehensive_matrix")["season_outcomes"]
            for row in outcomes:
                team = row["Team"]
                regular = float(run.args["global_weekly_scores"][team].sum(axis=1).mean())
                expected = run.actual_points[team] + regular
                self.assertAlmostEqual(
                    row["Expected_Points"], expected, delta=1.0,
                    msg="%s: %s Expected_Points %.2f exceeds banked + regular season %.2f "
                        "by %.2f (weeks 15-16 are being counted)"
                        % (scenario, team, row["Expected_Points"], expected,
                           row["Expected_Points"] - expected))


class TestConfigConstantsSurviveARun(unittest.TestCase):
    """Constructing an engine must not rewrite the config module's constants.

    Regression guard for a real defect. `__init__` fills gaps in the loaded baselines from the
    whitelist, and used to do it by binding the config's own dict object into self.baselines
    rather than a copy:

        self.baselines[p_name] = SIM_CONFIG["KNOWN_MISSING_ASSETS"][p_name]

    `_apply_bayesian_updates` writes posterior values straight into the entries of
    self.baselines, so the whitelisted player's sourced constants were overwritten in the config
    module for the remainder of the process.

    Three consequences, in ascending order of seriousness:
      - a constant whose provenance is documented in config.py silently stopped holding the
        documented value;
      - simulation results became order-dependent -- the same fixture gave different answers
        depending on what ran before it in the same process, which is exactly the property the
        Phase 0 harness exists to guarantee. Verified at the time: running this module before
        tests.test_golden_master failed all six golden-master tests. The suite was green only
        because test_golden_master sorts first alphabetically and SCENARIOS happens to put
        week01 (no completed weeks, so _apply_bayesian_updates returns early) ahead of week06.
        That ordering was load-bearing by accident;
      - the corruption compounded. Each run treated the previous run's posterior as its prior
        and re-applied the same evidence, so uncertainty collapsed on repetition rather than
        converging. Five successive constructions on the week06 fixture drove std_epistemic
        1.17 -> 0.51 -> 0.25 -> 0.16, an 87% collapse built entirely on double-counted
        evidence. Anything running the engine in a loop -- both backtest harnesses do -- was
        exposed.

    Fixed by deepcopying the whitelist entry at the point of imputation. Because the mutation
    only ever landed on the config module and never on the loaded baselines the engine actually
    simulates from, the fix moved no exported number: the golden hashes are unchanged."""

    def test_known_missing_assets_is_unchanged_by_constructing_an_engine(self):
        before = copy.deepcopy(SIM_CONFIG["KNOWN_MISSING_ASSETS"])
        try:
            with _sandbox("week06", 1, 1):
                with patch.object(FantasySimulationEngine,
                                  "export_and_visualize", lambda self, *a: None):
                    FantasySimulationEngine()
            self.assertEqual(
                SIM_CONFIG["KNOWN_MISSING_ASSETS"], before,
                msg="constructing an engine rewrote SIM_CONFIG['KNOWN_MISSING_ASSETS']")
        finally:
            SIM_CONFIG["KNOWN_MISSING_ASSETS"] = before

    def test_repeated_runs_do_not_ratchet_the_whitelisted_priors(self):
        """The compounding half of the same defect, asserted separately: whatever the value
        after one run, a second identical run must not move it again."""
        before = copy.deepcopy(SIM_CONFIG["KNOWN_MISSING_ASSETS"])
        try:
            snapshots = []
            for _ in range(3):
                with _sandbox("week06", 1, 1):
                    with patch.object(FantasySimulationEngine,
                                      "export_and_visualize", lambda self, *a: None):
                        FantasySimulationEngine()
                snapshots.append(copy.deepcopy(SIM_CONFIG["KNOWN_MISSING_ASSETS"]))
            self.assertEqual(
                snapshots[0], snapshots[-1],
                msg="whitelisted priors drifted across identical repeated runs: "
                    + repr(snapshots[0]) + " -> " + repr(snapshots[-1]))
        finally:
            SIM_CONFIG["KNOWN_MISSING_ASSETS"] = before


# ------------------------------------------------------------------------------ liveness
class TestByeWeekLiveness(unittest.TestCase):
    """AUDIT_PLAN Phase 1: 'a player on bye never scores and never absorbs vacated volume.'

    Phase 1 finding 7 characterised this as unreachable: Sleeper's payload has no bye field, so
    every baseline carried `bye: 0` and the engine's three `week_num == bye` guards were dead
    code. Bye modelling (steps 1-6, 2026-08-28) derives each team's bye from the NFL schedule
    at sync time (`config.derive_bye_weeks`: the one usable week a team appears in no pairing),
    writes it to `nfl_schedule.json["_meta"]["byes"]`, and stamps every baseline's `bye` from
    its team. The fixtures now carry exactly that (populated from their own pairings).

    These are the inverted characterisation tests: they pin that the fixtures are live and
    self-consistent. The engine-level consequences (no score, no onset, no vacated volume, a
    streamer bid per bye hole, one-week persistence) are pinned in tests/test_byes.py."""

    def _fixture(self, scenario, name):
        import json
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "fixtures", "golden", scenario, name)) as handle:
            return json.load(handle)

    def test_every_nfl_team_has_one_bye_in_the_fixture_schedule(self):
        """32/32 teams, each with a single derivable bye in weeks 5-14, recorded in _meta and
        identical to what derive_bye_weeks yields from the pairings (single derivation point)."""
        from fantasy_sim.config import NFL_TEAMS, derive_bye_weeks
        for scenario in ("week01", "week06"):
            sched = self._fixture(scenario, "nfl_schedule.json")
            byes = sched.get("_meta", {}).get("byes", {})
            self.assertEqual(sorted(byes), sorted(NFL_TEAMS), msg=scenario + ": teams with a bye")
            self.assertTrue(all(5 <= w <= 14 for w in byes.values()), msg=scenario + ": " + repr(byes))
            self.assertEqual(byes, derive_bye_weeks(sched, sched["_meta"].get("failed_weeks", [])),
                             msg=scenario + ": _meta.byes disagrees with the pairings it was derived from")

    def test_every_baseline_carries_its_team_bye(self):
        """Every player with an NFL team carries that team's bye; a player with no team carries
        0 (never on bye, never absent). So the engine's guards are reachable for essentially the
        whole roster pool -- the exact inverse of the Phase 1 characterisation."""
        for scenario in ("week01", "week06"):
            byes = self._fixture(scenario, "nfl_schedule.json")["_meta"]["byes"]
            baselines = self._fixture(scenario, "player_baselines.json")
            reachable = 0
            for name, data in baselines.items():
                expected = byes.get(data.get("team"), 0)
                self.assertEqual(data.get("bye"), expected,
                                 msg="%s: %s (%s) carries bye %r, team bye is %r"
                                     % (scenario, name, data.get("team"), data.get("bye"), expected))
                reachable += 1 <= (data.get("bye") or 0) <= 16
            self.assertGreater(reachable, 0.95 * len(baselines),
                               msg=scenario + ": only %d of %d baselines carry a real bye" % (reachable, len(baselines)))


# ------------------------------------------------------------------------ property-based
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DL", "LB", "DB")

if HAS_HYPOTHESIS:
    _PLAYER = st.tuples(
        st.text(min_size=1, max_size=6),
        st.lists(st.sampled_from(POSITIONS), min_size=1, max_size=2, unique=True),
        st.floats(min_value=0.0, max_value=60.0, allow_nan=False, allow_infinity=False),
    )
    # Roster shapes from the empty list up to a deep bench, with duplicate names collapsed --
    # _solve_optimal_assignment keys its eligibility lookup by name, so a repeated name would
    # be a malformed input rather than an interesting one.
    CANDIDATES = st.lists(_PLAYER, min_size=0, max_size=25).map(
        lambda ps: list(dict((p[0], p) for p in ps).values()))
else:  # pragma: no cover - the decorators below are never evaluated
    CANDIDATES = None


@unittest.skipUnless(HAS_HYPOTHESIS, "hypothesis not installed")
class TestAssignmentProperties(unittest.TestCase):
    """Property tests over the extracted helpers whose invariants are pure functions of their
    inputs. The end-to-end tests above sample whatever roster shapes the fixtures happen to
    produce; these search for the shapes that break them."""

    @settings(max_examples=150, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(candidates=CANDIDATES)
    def test_slots_are_always_conserved(self, candidates):
        """Filled + unfilled == 13 for any roster, including the empty one and rosters with
        no eligible player for a required position."""
        assigned, unfilled = FantasySimulationEngine._solve_optimal_assignment(candidates)
        self.assertEqual(len(assigned) + len(unfilled), len(REQUIRED_STARTING_SLOTS))

    @settings(max_examples=150, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(candidates=CANDIDATES)
    def test_assignment_uses_each_player_at_most_once_and_only_where_eligible(self, candidates):
        assigned, _unfilled = FantasySimulationEngine._solve_optimal_assignment(candidates)
        names = [a[0] for a in assigned]
        self.assertEqual(len(names), len(set(names)))
        eligibility = dict((c[0], c[1]) for c in candidates)
        for name, _value, slot in assigned:
            opts = eligibility[name]
            self.assertTrue(
                slot in opts or (slot == "FLEX"
                                 and any(o in ("RB", "WR", "TE") for o in opts)),
                msg=name + " started at " + slot + " but is eligible only for " + repr(opts))


@unittest.skipUnless(HAS_HYPOTHESIS, "hypothesis not installed")
class TestVacatedVolumeProperties(unittest.TestCase):
    @settings(max_examples=200, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(
        means=st.lists(st.floats(min_value=0.0, max_value=30.0,
                                 allow_nan=False, allow_infinity=False),
                       min_size=1, max_size=8),
        injured=st.lists(st.booleans(), min_size=1, max_size=8),
        vacated=st.floats(min_value=0.0, max_value=200.0,
                          allow_nan=False, allow_infinity=False),
    )
    def test_apportionment_never_creates_volume(self, means, injured, vacated):
        """For any position group and any pattern of injuries, the total handed out is at most
        the size of the pool -- and is either the whole pool or nothing, never a fraction."""
        assume(len(means) == len(injured))
        engine = FantasySimulationEngine.__new__(FantasySimulationEngine)
        names = ["p%d" % i for i in range(len(means))]
        engine.nfl_position_groups = {("RB", "DET"): list(zip(names, means))}

        clocks = dict((n, 3 if hurt else 0) for n, hurt in zip(names, injured))
        pools = {"RB": {"DET": vacated}, "WR": {}, "TE": {}}
        out = engine._apportion_vacated_volume(pools, clocks, set())

        given_out = sum(out.values())
        self.assertLessEqual(given_out, vacated + 1e-6)

        healthy_weight = sum(m for n, m in zip(names, means)
                             if clocks[n] <= 0 and m > 0.0)
        if healthy_weight > 0.0 and vacated > 0.0:
            self.assertAlmostEqual(given_out, vacated, places=6)
        else:
            self.assertEqual(given_out, 0.0)

    @settings(max_examples=200, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(
        means=st.lists(st.floats(min_value=0.1, max_value=30.0,
                                 allow_nan=False, allow_infinity=False),
                       min_size=2, max_size=8),
        vacated=st.floats(min_value=0.1, max_value=200.0,
                          allow_nan=False, allow_infinity=False),
    )
    def test_newly_injured_players_never_inherit_their_own_vacated_volume(self, means, vacated):
        """The player whose injury created the pool must be excluded from it, or an injury
        would partially pay itself back."""
        engine = FantasySimulationEngine.__new__(FantasySimulationEngine)
        names = ["p%d" % i for i in range(len(means))]
        engine.nfl_position_groups = {("RB", "DET"): list(zip(names, means))}
        clocks = dict((n, 0) for n in names)
        out = engine._apportion_vacated_volume(
            {"RB": {"DET": vacated}, "WR": {}, "TE": {}}, clocks, {names[0]})
        self.assertNotIn(names[0], out)


@unittest.skipUnless(HAS_HYPOTHESIS, "hypothesis not installed")
class TestFaabBidProperties(unittest.TestCase):
    @settings(max_examples=300, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    @given(
        remaining=st.floats(min_value=0.0, max_value=100.0,
                            allow_nan=False, allow_infinity=False),
        raw_draw=st.floats(min_value=6.0, max_value=22.0,
                           allow_nan=False, allow_infinity=False),
        aggression=st.floats(min_value=0.0, max_value=1.0,
                             allow_nan=False, allow_infinity=False),
        needs=st.integers(min_value=0, max_value=13),
        deflation=st.floats(min_value=0.0, max_value=1.5,
                            allow_nan=False, allow_infinity=False),
        avg_faab=st.floats(min_value=0.0, max_value=100.0,
                           allow_nan=False, allow_infinity=False),
    )
    def test_bid_is_bounded_by_budget_and_never_negative(self, remaining, raw_draw,
                                                         aggression, needs, deflation,
                                                         avg_faab):
        bid = FantasySimulationEngine._compute_faab_bid(
            remaining, raw_draw, aggression, needs, deflation, avg_faab)
        self.assertGreaterEqual(bid, 0.0)
        self.assertLessEqual(bid, remaining + 1e-9)
        self.assertLessEqual(bid, max(1.0, avg_faab * 1.5) + 1e-9)


if __name__ == "__main__":
    unittest.main()

"""
Tests for fantasy_sim.decisions -- decision-support tools (head-to-head comparator first).
Written before the module existed (CLAUDE.md rule 1): the first run failed on ImportError.

The comparator has two paths. Rostered-vs-rostered reads FantasySimulationEngine's
player_weekly_scores accumulator after a (reduced) run_simulation(), so P(A>B) is computed
within sim on the current week's column -- joint, respecting the copula, shared environment and
injury state. The light path samples one player's single-week score from his baseline
parameters through the engine's own extracted transform (_weekly_score_from_z), with
independent z -- for free agents, who never enter a simulation.
"""
import logging
import unittest
from unittest.mock import patch

import numpy as np

from fantasy_sim.simulation import FantasySimulationEngine, SIM_CONFIG
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)
from fantasy_sim.decisions import (
    prob_a_beats_b, sample_week_scores, summarise_scores, compare_players, run_reduced_simulation,
    roster_gaps, free_agents, rank_waiver_targets, suggest_bid, INDEPENDENCE_CAVEAT,
    apply_trade, run_paired_capture, evaluate_trade, ACTIVE_ROSTER_LIMIT,
    grade_roster, roster_grades, week_expectation, optimize_lineup,
    sample_week_matrix, weekly_scores_vectorised, matchup_lineups,
    find_trade_targets, league_week_outlook,
)


def _fixture_engine():
    """A 4-team league with one rostered QB each plus three UNROSTERED players in the
    baselines: a healthy WR, a WR on bye in week 1, and an IR'd RB."""
    teams = ['Legion of Coom', 'Femboy Cats', 'Year of Jarvis', 'Drunk Cats']
    fs = {
        LEAGUE_STATE_FILE: {"current_week": 1},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in teams},
        VEGAS_FILE: {"_meta": {"week": 1, "source": "odds_api", "fetched_at": "x"},
                     "DET": {"total": 24.0, "spread": -4.0, "opponent": "CHI"},
                     "CHI": {"total": 20.0, "spread": 4.0, "opponent": "DET"}},
        LIVE_ROSTERS_FILE: {
            teams[0]: [{"name": "QB_1", "pos": "QB", "team": "DET"}],
            teams[1]: [{"name": "QB_2", "pos": "QB", "team": "CHI"}],
            teams[2]: [{"name": "QB_3", "pos": "QB", "team": "FA"}],
            teams[3]: [{"name": "QB_4", "pos": "QB", "team": "FA"}],
        },
        BASELINES_FILE: {
            "QB_1": {"mean": 20.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "DET", "bye": 0},
            "QB_2": {"mean": 15.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "CHI", "bye": 0},
            "QB_3": {"mean": 15.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "FA", "bye": 0},
            "QB_4": {"mean": 15.0, "std_aleatoric": 2.0, "std_epistemic": 1.5, "pos": "QB", "team": "FA", "bye": 0},
            "FA_WR_healthy": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 0.0, "pos": "WR", "team": "DET", "bye": 9},
            "FA_WR_bye": {"mean": 12.0, "std_aleatoric": 4.0, "std_epistemic": 0.0, "pos": "WR", "team": "CHI", "bye": 1},
            "FA_RB_ir": {"mean": 10.0, "std_aleatoric": 4.0, "std_epistemic": 0.0, "pos": "RB", "team": "DET", "bye": 9,
                         "injury_status": "IR", "on_ir": True},
            "FA_WR_weak": {"mean": 5.0, "std_aleatoric": 3.0, "std_epistemic": 0.0, "pos": "WR", "team": "CHI", "bye": 9},
            "FA_DEF_unit": {"mean": 8.0, "std_aleatoric": 3.0, "std_epistemic": 0.0, "pos": "DEF", "team": "DET", "bye": 9},
        },
        TEAM_RATINGS_FILE: {"DET": {"off_rating": 25}, "CHI": {"off_rating": 20}},
        DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                 "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[teams[0], teams[1]], [teams[2], teams[3]]]] * 14,
        NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: {},
    }
    return fs


class _EngineCase(unittest.TestCase):
    def setUp(self):
        self.fs = _fixture_engine()
        self.prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        self.p_exists = patch('os.path.exists', side_effect=lambda p: p in self.fs)
        self.p_load = patch('fantasy_sim.simulation.load_json', side_effect=lambda p: self.fs[p])
        self.p_exists.start(); self.p_load.start()
        self.engine = FantasySimulationEngine()

    def tearDown(self):
        self.p_exists.stop(); self.p_load.stop()
        logging.getLogger().setLevel(self.prev)


class TestProbAbeatsB(unittest.TestCase):
    def test_counts_strict_wins_ties_and_treats_structural_absence_as_zero(self):
        a = np.array([10.0, 5.0, np.nan, 7.0, 0.0])
        b = np.array([8.0, 5.0, 3.0, np.nan, np.nan])
        r = prob_a_beats_b(a, b)
        # wins: 10>8, 7>nan(0); ties: 5=5, 0=nan(0); loss: nan(0)<3
        self.assertEqual(r['n'], 5)
        self.assertAlmostEqual(r['p_a'], 2 / 5)
        self.assertAlmostEqual(r['p_b'], 1 / 5)
        self.assertAlmostEqual(r['p_tie'], 2 / 5)
        self.assertAlmostEqual(r['p_a'] + r['p_b'] + r['p_tie'], 1.0)

    def test_rejects_misaligned_samples(self):
        with self.assertRaises(ValueError):
            prob_a_beats_b(np.zeros(3), np.zeros(4))


class TestLightSampler(_EngineCase):
    def test_bye_week_is_all_zeros(self):
        s = sample_week_scores(self.engine, "FA_WR_bye", week=1, n=200, seed=1)
        self.assertEqual(s.size, 200)
        self.assertTrue(np.all(s == 0.0))

    def test_player_on_ir_is_absent_in_the_first_week_with_certainty(self):
        # F4: the first week of an initial absence is certain (the return hazard applies from
        # week two), so an IR'd player sampled for the current week scores zero every time.
        s = sample_week_scores(self.engine, "FA_RB_ir", week=1, n=300, seed=2)
        self.assertTrue(np.all(s == 0.0))

    def test_healthy_player_mean_matches_the_engine_expectation_within_tolerance(self):
        # std_epistemic = 0 so the only randomness is the weekly lognormal (E = mean), the
        # environment draw (E = v_tot/env_norm) and the onset hazard (a zero with probability
        # INJURY_RATES['WR'] * starter exposure). Expected mean = 12 * ratio * script * (1 - h).
        veg = self.engine._compute_week_environment(1, "DET")
        ratio = veg['total'] / self.engine._compute_environment_normaliser()
        script = self.engine._script_multiplier("WR", veg)
        h = SIM_CONFIG['INJURY_RATES']['WR'] * SIM_CONFIG['ONSET_EXPOSURE_STARTER']
        expected = 12.0 * ratio * script * (1 - h)
        s = sample_week_scores(self.engine, "FA_WR_healthy", week=1, n=40000, seed=3)
        self.assertAlmostEqual(float(s.mean()), expected, delta=0.15)
        self.assertAlmostEqual(float((s == 0.0).mean()), h, delta=0.01)
        self.assertLessEqual(float(s.max()), SIM_CONFIG['MAX_REALISTIC_WEEKLY_SCORE'])

    def test_same_seed_same_draws_different_seed_different_draws(self):
        a = sample_week_scores(self.engine, "FA_WR_healthy", week=1, n=50, seed=7)
        b = sample_week_scores(self.engine, "FA_WR_healthy", week=1, n=50, seed=7)
        c = sample_week_scores(self.engine, "FA_WR_healthy", week=1, n=50, seed=8)
        np.testing.assert_array_equal(a, b)
        self.assertFalse(np.array_equal(a, c))

    def test_unknown_player_is_a_loud_error(self):
        with self.assertRaises(KeyError):
            sample_week_scores(self.engine, "Nobody", week=1, n=10, seed=1)

    def test_summary_reports_percentiles_and_zero_share(self):
        s = summarise_scores(np.array([0.0, 0.0, 10.0, 20.0, 30.0]))
        self.assertAlmostEqual(s['p_zero'], 0.4)
        self.assertAlmostEqual(s['p50'], 10.0)
        self.assertEqual(s['n'], 5)


class TestComparePlayers(_EngineCase):
    def test_rostered_pair_uses_the_joint_accumulator_column(self):
        original = SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH']
        try:
            with patch('fantasy_sim.simulation.save_chart'), patch('fantasy_sim.simulation.save_json') as sj:
                r = compare_players(self.engine, "QB_1", "QB_2", week=1, sims=20, seed=1)
            sj.assert_not_called()   # a decision run must never overwrite the season exports
        finally:
            SIM_CONFIG['NUM_BATCHES'], SIM_CONFIG['SIMS_PER_BATCH'] = original
        self.assertEqual(SIM_CONFIG['NUM_BATCHES'], original[0], "batch settings must be restored")
        self.assertEqual(r['path'], 'joint')
        self.assertEqual(r['n'], 20)
        self.assertAlmostEqual(r['p_a'] + r['p_b'] + r['p_tie'], 1.0)
        self.assertGreater(r['p_a'], r['p_b'], "a 20-point QB beats a 15-point QB more often than not")
        for k in ('a', 'b'):
            self.assertIn('p50', r[k]); self.assertIn('p_zero', r[k])

    def test_free_agent_versus_rostered_uses_the_light_path_for_the_free_agent(self):
        with patch('fantasy_sim.simulation.save_chart'), patch('fantasy_sim.simulation.save_json'):
            r = compare_players(self.engine, "FA_WR_healthy", "QB_2", week=1, sims=20, seed=1)
        self.assertEqual(r['path'], 'mixed')
        self.assertEqual(r['n'], 20)
        self.assertIn('independent', r['note'].lower())

    def test_light_flag_samples_both_independently_without_a_simulation(self):
        with patch.object(FantasySimulationEngine, 'run_simulation') as run:
            r = compare_players(self.engine, "QB_1", "QB_2", week=1, sims=500, seed=1, light=True)
        run.assert_not_called()
        self.assertEqual(r['path'], 'light')
        self.assertGreater(r['p_a'], 0.8)

    def test_reduced_simulation_populates_the_current_week_column_only_from_current_week(self):
        with patch('fantasy_sim.simulation.save_chart'), patch('fantasy_sim.simulation.save_json'):
            scores = run_reduced_simulation(self.engine, sims=10)
        col = scores["QB_1"][:, 0]
        self.assertEqual(col.shape, (10,))
        self.assertTrue(np.all(np.isfinite(col)), "week-1 column is populated for a healthy starter")


class TestWaiverTargets(_EngineCase):
    """Tool 3. The fixture's Legion of Coom rosters one QB, so every other starting slot is a
    hard hole; the free-agent pool holds a healthy WR (12), a WR on bye in week 1 (12), a weak
    WR (5, which sets the WR replacement level in this tiny pool), an IR'd RB and a team-DEF
    unit this league has no slot for."""

    def test_roster_gaps_lists_unfilled_slots_and_respects_bye_and_absence(self):
        gaps = roster_gaps(self.engine, "Legion of Coom", weeks=(1, 2))
        self.assertEqual(set(gaps), {1, 2})
        self.assertNotIn("QB", gaps[1]["unfilled"])
        for slot in ("K", "DB", "DL", "LB", "RB", "WR", "TE", "FLEX"):
            self.assertIn(slot, gaps[1]["unfilled"])
        self.assertEqual(gaps[1]["unfilled"].count("RB"), 2)
        self.assertEqual(gaps[1]["unfilled"].count("FLEX"), 3)
        # starters carry their expected value so an upgrade threshold exists per slot
        self.assertAlmostEqual(gaps[1]["starters"]["QB"][0][1], 20.0)
        # a QB on bye in week 2 is a week-2 hole only
        self.engine.baselines["QB_1"]["bye"] = 2
        gaps = roster_gaps(self.engine, "Legion of Coom", weeks=(1, 2))
        self.assertNotIn("QB", gaps[1]["unfilled"]); self.assertIn("QB", gaps[2]["unfilled"])
        # an IR'd player is unavailable this week
        self.engine.baselines["QB_1"].update({"bye": 0, "injury_status": "IR", "on_ir": True})
        self.assertIn("QB", roster_gaps(self.engine, "Legion of Coom", weeks=(1,))[1]["unfilled"])

    def test_free_agents_excludes_rostered_players_and_positions_without_a_slot(self):
        fa = free_agents(self.engine)
        self.assertNotIn("QB_1", fa); self.assertNotIn("QB_4", fa)
        self.assertIn("FA_WR_healthy", fa); self.assertIn("FA_RB_ir", fa)
        self.assertNotIn("FA_DEF_unit", fa, "team defense has no slot in this league")

    def test_ranking_is_by_vorp_at_needed_positions_and_skips_players_unavailable_that_week(self):
        r = rank_waiver_targets(self.engine, "Legion of Coom", week=1, sims=200, seed=1)
        names = [x["name"] for x in r["targets"]]
        self.assertEqual(names[0], "FA_WR_healthy")
        self.assertNotIn("FA_WR_bye", names, "on bye in the target week: cannot fill a week-1 hole")
        self.assertNotIn("FA_RB_ir", names, "absent with certainty in the first week (F4)")
        top = r["targets"][0]
        self.assertAlmostEqual(top["vorp"], 12.0 - self.engine.replacement_levels["WR"])
        self.assertEqual(top["fills"], "hole")
        self.assertIn("p50", top["week"]); self.assertIn("bid", top)
        self.assertGreaterEqual(top["bid"]["suggested"], 1)
        self.assertEqual(r["caveat"], INDEPENDENCE_CAVEAT)
        # secondary joint-style display is present only when there is an incumbent to beat
        self.assertIsNone(top.get("p_beats_incumbent"))

    def test_upgrade_targets_compare_against_the_incumbent_with_the_caveat(self):
        # give the team a weak WR starter so the healthy FA is an upgrade, not a hole-filler
        self.engine.rosters["Legion of Coom"].append("WR_weak_starter")
        self.engine.meta["Legion of Coom"]["WR_weak_starter"] = {"pos": "WR", "team": "CHI"}
        self.engine.baselines["WR_weak_starter"] = {"mean": 6.0, "std_aleatoric": 3.0, "std_epistemic": 0.0,
                                                    "pos": "WR", "team": "CHI", "bye": 9}
        r = rank_waiver_targets(self.engine, "Legion of Coom", week=1, sims=300, seed=1)
        top = next(x for x in r["targets"] if x["name"] == "FA_WR_healthy")
        self.assertIn(top["fills"], ("hole", "upgrade"))
        # there are still open WR/FLEX holes, so the FA fills a hole; force the upgrade path by
        # filling every WR/FLEX-eligible slot with weak starters
        for i in range(5):
            n = f"weak_{i}"
            self.engine.rosters["Legion of Coom"].append(n)
            self.engine.meta["Legion of Coom"][n] = {"pos": "WR", "team": "CHI"}
            self.engine.baselines[n] = {"mean": 6.0, "std_aleatoric": 3.0, "std_epistemic": 0.0, "pos": "WR", "team": "CHI", "bye": 9}
        r = rank_waiver_targets(self.engine, "Legion of Coom", week=1, sims=300, seed=1)
        top = next(x for x in r["targets"] if x["name"] == "FA_WR_healthy")
        self.assertEqual(top["fills"], "upgrade")
        self.assertIsNotNone(top["p_beats_incumbent"])
        self.assertGreater(top["p_beats_incumbent"]["p"], 0.5)
        self.assertEqual(top["p_beats_incumbent"]["caveat"], INDEPENDENCE_CAVEAT)

    def test_suggest_bid_is_bounded_and_monotone_in_value(self):
        lo = suggest_bid(vorp=1.0, fills="upgrade", remaining_faab=100.0, league_avg_faab=100.0)
        hi = suggest_bid(vorp=8.0, fills="hole", remaining_faab=100.0, league_avg_faab=100.0)
        self.assertGreaterEqual(lo, 1); self.assertGreater(hi, lo); self.assertLessEqual(hi, 100)
        # share is capped at 40% for a hole, so 7 remaining -> round(7 * 0.4) = 3, never above 7
        tight = suggest_bid(vorp=50.0, fills="hole", remaining_faab=7.0, league_avg_faab=100.0)
        self.assertEqual(tight, 3); self.assertLessEqual(tight, 7)
        self.assertEqual(suggest_bid(vorp=50.0, fills="hole", remaining_faab=2.0, league_avg_faab=100.0), 1)
        self.assertEqual(suggest_bid(vorp=-3.0, fills="upgrade", remaining_faab=100.0, league_avg_faab=100.0), 1)


class TestTradeEvaluator(_EngineCase):
    """Tool 2. A proposed trade is evaluated by two paired simulations -- the league as it is
    and the league with the trade applied -- on the same seeds (run_simulation reseeds every
    batch itself), reporting each team's Champ_Pct / Playoff_Pct / expected-wins delta with a
    paired-batch SE. Feasible here (one trade, on demand) where it was not for the engine's
    automatic trade block (~200k evaluations per run)."""

    def test_apply_trade_swaps_names_and_meta_and_leaves_the_original_engine_untouched(self):
        e2 = apply_trade(self.engine, "Legion of Coom", ["QB_1"], "Femboy Cats", ["QB_2"])
        self.assertEqual(e2.rosters["Legion of Coom"], ["QB_2"])
        self.assertEqual(e2.rosters["Femboy Cats"], ["QB_1"])
        self.assertEqual(e2.meta["Legion of Coom"]["QB_2"]["team"], "CHI")
        self.assertNotIn("QB_1", e2.meta["Legion of Coom"])
        self.assertEqual(self.engine.rosters["Legion of Coom"], ["QB_1"], "original engine must not be mutated")
        self.assertEqual(sum(len(r) for r in e2.rosters.values()), sum(len(r) for r in self.engine.rosters.values()))

    def test_apply_trade_rejects_players_not_on_the_stated_roster_and_unknown_teams(self):
        with self.assertRaises(ValueError):
            apply_trade(self.engine, "Legion of Coom", ["QB_2"], "Femboy Cats", ["QB_1"])
        with self.assertRaises(KeyError):
            apply_trade(self.engine, "Nobody FC", ["QB_1"], "Femboy Cats", ["QB_2"])

    def test_apply_trade_requires_a_drop_when_a_side_would_exceed_the_active_roster_limit(self):
        # pad Femboy Cats to the active limit, then a 2-for-1 in its favour needs a drop
        for i in range(ACTIVE_ROSTER_LIMIT - 1):
            n = f"pad_{i}"
            self.engine.rosters["Femboy Cats"].append(n)
            self.engine.meta["Femboy Cats"][n] = {"pos": "WR", "team": "CHI"}
            self.engine.baselines[n] = {"mean": 5.0, "std_aleatoric": 3.0, "std_epistemic": 0.0, "pos": "WR", "team": "CHI", "bye": 9}
        self.engine.rosters["Legion of Coom"].append("FA_WR_healthy")
        self.engine.meta["Legion of Coom"]["FA_WR_healthy"] = {"pos": "WR", "team": "DET"}
        with self.assertRaises(ValueError):
            apply_trade(self.engine, "Legion of Coom", ["QB_1", "FA_WR_healthy"], "Femboy Cats", ["QB_2"])
        e2 = apply_trade(self.engine, "Legion of Coom", ["QB_1", "FA_WR_healthy"], "Femboy Cats", ["QB_2"],
                         drops={"Femboy Cats": ["pad_0"]})
        self.assertEqual(len(e2.rosters["Femboy Cats"]), ACTIVE_ROSTER_LIMIT)
        self.assertNotIn("pad_0", e2.rosters["Femboy Cats"])

    def test_paired_capture_is_deterministic_and_never_writes(self):
        with patch('fantasy_sim.simulation.save_json') as sj, patch('fantasy_sim.simulation.save_chart'):
            a = run_paired_capture(self.engine, batches=2, sims=5)
            b = run_paired_capture(self.engine, batches=2, sims=5)
        sj.assert_not_called()
        for t in self.engine.team_names:
            np.testing.assert_array_equal(a["wins"][t], b["wins"][t])
        self.assertEqual(len(a["b_champs"]["Legion of Coom"]), 2, "one championship rate per batch")

    def test_evaluate_trade_reports_every_team_with_zero_sum_championship_deltas(self):
        with patch('fantasy_sim.simulation.save_json'), patch('fantasy_sim.simulation.save_chart'):
            r = evaluate_trade(self.engine, "Legion of Coom", ["QB_1"], "Femboy Cats", ["QB_2"], batches=2, sims=15)
        self.assertEqual(set(r["teams"]), set(self.engine.team_names))
        for t, d in r["teams"].items():
            for k in ("champ_pct", "playoff_pct", "expected_wins"):
                self.assertIn("delta", d[k]); self.assertIn("se", d[k]); self.assertIn("with", d[k]); self.assertIn("without", d[k])
        self.assertAlmostEqual(sum(d["champ_pct"]["delta"] for d in r["teams"].values()), 0.0, places=9)
        self.assertAlmostEqual(sum(d["playoff_pct"]["delta"] for d in r["teams"].values()), 0.0, places=9)
        self.assertEqual(r["n_sims"], 30)
        self.assertEqual(r["trade"]["a_gives"], ["QB_1"])
        self.assertIn("independent", r["note"].lower() + " independent")  # note exists


class TestRosterGrades(_EngineCase):
    """Roster-grade report: every rostered player's tier and VORP, rolled up per position and
    overall, composed from what exists (compute_tiers, engine.replacement_levels, the optimal
    assignment via roster_gaps). Numbers below are by hand: with a 17-point bench QB added to
    Legion of Coom the QB pool is [20, 17, 15, 15, 15], depth index min(10, 4) = 4 -> QB
    replacement 15.0; QB_1 VORP 5, bench QB VORP 2; every other team's lone QB is at 0."""

    def setUp(self):
        super().setUp()
        self.engine.rosters["Legion of Coom"].append("QB_bench")
        self.engine.meta["Legion of Coom"]["QB_bench"] = {"pos": "QB", "team": "DET"}
        self.engine.baselines["QB_bench"] = {"mean": 17.0, "std_aleatoric": 2.0, "std_epistemic": 1.0,
                                             "pos": "QB", "team": "DET", "bye": 0}
        self.engine.replacement_levels = self.engine._calc_replacement_levels()

    def test_per_player_rows_carry_role_tier_and_vorp(self):
        g = grade_roster(self.engine, "Legion of Coom", week=1)
        rows = {r["name"]: r for r in g["players"]}
        self.assertEqual(set(rows), {"QB_1", "QB_bench"})
        self.assertEqual(rows["QB_1"]["role"], "starter"); self.assertEqual(rows["QB_1"]["slot"], "QB")
        self.assertAlmostEqual(rows["QB_1"]["vorp"], 5.0)
        self.assertEqual(rows["QB_bench"]["role"], "bench"); self.assertIsNone(rows["QB_bench"]["slot"])
        self.assertAlmostEqual(rows["QB_bench"]["vorp"], 2.0)
        self.assertEqual(rows["QB_1"]["tier"], 1)
        self.assertIsInstance(rows["QB_bench"]["tier"], int)

    def test_rollups_by_hand(self):
        g = grade_roster(self.engine, "Legion of Coom", week=1)
        qb = g["by_position"]["QB"]
        self.assertAlmostEqual(qb["starters_vorp"], 5.0)
        self.assertAlmostEqual(qb["depth_vorp"], 2.0)
        self.assertEqual((qb["n_starters"], qb["n_bench"]), (1, 1))
        self.assertAlmostEqual(g["lineup_vorp"], 5.0, msg="12 empty slots contribute 0, not a negative")
        self.assertAlmostEqual(g["depth_vorp"], 2.0)
        self.assertEqual(len(g["holes"]), 12)
        self.assertAlmostEqual(g["optimal_score"], self.engine.get_optimal_score(self.engine.rosters["Legion of Coom"]))
        # best available free agent at the position is the replaceability reference
        self.assertIn("best_free_agent", g["by_position"]["WR"] if "WR" in g["by_position"] else {"best_free_agent": None})

    def test_negative_vorp_bench_does_not_count_as_depth(self):
        self.engine.baselines["QB_bench"]["mean"] = 12.0
        self.engine.replacement_levels = self.engine._calc_replacement_levels()   # pool [20,15,15,15,12] -> 12
        g = grade_roster(self.engine, "Legion of Coom", week=1)
        rows = {r["name"]: r for r in g["players"]}
        self.assertAlmostEqual(rows["QB_bench"]["vorp"], 0.0)
        self.assertAlmostEqual(rows["QB_1"]["vorp"], 8.0)
        self.assertAlmostEqual(g["depth_vorp"], 0.0)

    def test_league_table_ranks_by_lineup_vorp_and_covers_every_team(self):
        table = roster_grades(self.engine, week=1)
        self.assertEqual([t["team"] for t in table["teams"]][0], "Legion of Coom")
        self.assertEqual({t["team"] for t in table["teams"]}, set(self.engine.team_names))
        for t in table["teams"][1:]:
            self.assertAlmostEqual(t["lineup_vorp"], 0.0)
        self.assertEqual(table["teams"][0]["rank"], 1)


class TestLineupOptimizer(_EngineCase):
    """Tool 4. This-week expectations are deterministic (the engine's own expected_pre form:
    baseline mean x environment ratio x game-script multiplier, 0 when on bye or out now) and
    the lineup is the engine's optimal assignment on them; the light sampler adds each
    starter's p10/p90. Margin = starter's expectation minus the best available bench
    alternative eligible for that slot."""

    def setUp(self):
        super().setUp()
        for n, pos, team, mean, extra in (("QB_bench", "QB", "DET", 17.0, {}),
                                          ("WR_a", "WR", "DET", 12.0, {}),
                                          ("WR_b", "WR", "CHI", 9.0, {}),
                                          ("WR_bye", "WR", "CHI", 14.0, {"bye": 1}),
                                          ("RB_ir", "RB", "DET", 15.0, {"injury_status": "IR", "on_ir": True})):
            self.engine.rosters["Legion of Coom"].append(n)
            self.engine.meta["Legion of Coom"][n] = {"pos": pos, "team": team}
            self.engine.baselines[n] = {"mean": mean, "std_aleatoric": 3.0, "std_epistemic": 0.0,
                                        "pos": pos, "team": team, "bye": 9, **extra}

    def _exp(self, name):
        e = self.engine.baselines[name]
        veg = self.engine._compute_week_environment(1, e["team"])
        ratio = veg["total"] / self.engine._compute_environment_normaliser()
        return e["mean"] * ratio * self.engine._script_multiplier(e["pos"], veg)

    def test_week_expectation_is_the_engines_pre_game_form_and_zero_when_unavailable(self):
        self.assertAlmostEqual(week_expectation(self.engine, "QB_1", 1), self._exp("QB_1"), places=9)
        self.assertEqual(week_expectation(self.engine, "WR_bye", 1), 0.0)
        self.assertEqual(week_expectation(self.engine, "RB_ir", 1), 0.0)

    def test_lineup_starts_the_best_available_and_never_a_bye_or_ir_player(self):
        r = optimize_lineup(self.engine, "Legion of Coom", week=1, sims=200, seed=1)
        by_slot = {(row["slot"], row["name"]) for row in r["lineup"]}
        self.assertIn(("QB", "QB_1"), by_slot)
        started = {row["name"] for row in r["lineup"]}
        self.assertIn("WR_a", started); self.assertIn("WR_b", started)
        self.assertNotIn("WR_bye", started); self.assertNotIn("RB_ir", started); self.assertNotIn("QB_bench", started)
        self.assertEqual(sorted(r["unfilled"]).count("RB"), 2)
        for row in r["lineup"]:
            for k in ("expected", "p10", "p50", "p90", "p_zero", "margin"):
                self.assertIn(k, row)
        self.assertAlmostEqual(r["expected_total"], sum(row["expected"] for row in r["lineup"]), places=9)

    def test_margin_is_starter_minus_best_eligible_bench_alternative(self):
        r = optimize_lineup(self.engine, "Legion of Coom", week=1, sims=100, seed=1)
        qb = next(row for row in r["lineup"] if row["slot"] == "QB")
        self.assertAlmostEqual(qb["margin"], self._exp("QB_1") - self._exp("QB_bench"), places=9)
        self.assertEqual(qb["alternative"], "QB_bench")
        # both WRs start (two WR slots) and no other WR is available: the alternative is none
        wr = next(row for row in r["lineup"] if row["name"] == "WR_b")
        self.assertIsNone(wr["alternative"])
        bench = {b["name"] for b in r["bench"]}
        self.assertEqual(bench, {"QB_bench", "WR_bye", "RB_ir"})


class TestJointSampler(_EngineCase):
    """Tool 5's joint sampler: one Cholesky factor from the engine's own
    build_covariance_matrix over a whole player list (cross=True, spanning both rosters -- the
    correlation the engine itself omits, AUDIT_PLAN.md F16) or one per group (cross=False,
    the engine's current behaviour)."""

    def test_vectorised_transform_equals_the_engines_static_method_elementwise(self):
        rng = np.random.default_rng(1)
        n = 500
        mean, std = 12.0, 5.0
        z = rng.normal(size=n); env_var = rng.normal(1.05, 0.1, size=n)
        out = weekly_scores_vectorised(np.full(n, mean), std, z, 1.05, env_var, 1.06, 0.0)
        for i in range(0, n, 37):
            _, ref = FantasySimulationEngine._weekly_score_from_z(mean, std, z[i], 1.05, env_var[i], 1.06, 0.0)
            self.assertAlmostEqual(out[i], ref, places=12)

    def test_cross_roster_correlation_is_present_with_cross_and_absent_without(self):
        # QB_1 (DET) and FA_WR_healthy (DET, the only DET WR -> WR1: target 0.40), both with
        # std_epistemic 0. log(score) = mu_a + sigma_a*z + log(env_var) + const, so the
        # log-score correlation is corr(z) = 0.40 attenuated by each player's share of
        # log-variance that comes from z rather than the independent environment draw:
        # QB_1 (mean 20, aleatoric sd 2) has sigma_a ~ 0.10 against env noise ~ 0.09, a share
        # of only ~0.55; the WR (12, sd 4) ~ 0.93. Expected ~ 0.40 * sqrt(0.55 * 0.93) ~ 0.29.
        self.engine.baselines["QB_1"]["std_epistemic"] = 0.0
        veg = self.engine._compute_week_environment(1, "DET")
        ratio = veg['total'] / self.engine._compute_environment_normaliser()
        se2 = (0.10 / ratio) ** 2
        def share(mean, sd):
            sa2 = np.log(1 + (sd / mean) ** 2)
            return sa2 / (sa2 + se2)
        expected = SIM_CONFIG['CORRELATIONS']['QB_WR1'] * np.sqrt(share(20.0, 2.0) * share(12.0, 4.0))
        groups = [["QB_1"], ["FA_WR_healthy"]]
        m, names = sample_week_matrix(self.engine, groups, week=1, n=30000, seed=3, cross=True)
        self.assertEqual(names, ["QB_1", "FA_WR_healthy"]); self.assertEqual(m.shape, (30000, 2))
        ok = (m[:, 0] > 0) & (m[:, 1] > 0)
        r_cross = float(np.corrcoef(np.log(m[ok, 0]), np.log(m[ok, 1]))[0, 1])
        m2, _ = sample_week_matrix(self.engine, groups, week=1, n=30000, seed=3, cross=False)
        ok2 = (m2[:, 0] > 0) & (m2[:, 1] > 0)
        r_none = float(np.corrcoef(np.log(m2[ok2, 0]), np.log(m2[ok2, 1]))[0, 1])
        self.assertGreater(expected, 0.2)
        self.assertAlmostEqual(r_cross, float(expected), delta=0.03)
        self.assertLess(abs(r_none), 0.04)

    def test_bye_and_ir_columns_are_zero_and_seed_is_reproducible(self):
        m, names = sample_week_matrix(self.engine, [["FA_WR_bye", "FA_RB_ir", "FA_WR_healthy"]], week=1, n=200, seed=5)
        self.assertTrue(np.all(m[:, 0] == 0)); self.assertTrue(np.all(m[:, 1] == 0)); self.assertGreater(m[:, 2].mean(), 0)
        m2, _ = sample_week_matrix(self.engine, [["FA_WR_bye", "FA_RB_ir", "FA_WR_healthy"]], week=1, n=200, seed=5)
        np.testing.assert_array_equal(m, m2)


class TestMatchupLineups(_EngineCase):
    """Tool 5. Legion of Coom (QB 20 + six WRs of equal-ish means, three safe / three boom)
    plays Femboy Cats (QB 15) in week 1 of the fixture schedule."""

    def setUp(self):
        super().setUp()
        for i, (mean, sd) in enumerate([(12.0, 2.0), (12.0, 8.0), (11.9, 2.0), (11.9, 8.0), (11.8, 2.0), (11.8, 8.0)]):
            n = f"WR_{i}"
            self.engine.rosters["Legion of Coom"].append(n)
            self.engine.meta["Legion of Coom"][n] = {"pos": "WR", "team": "CHI"}
            self.engine.baselines[n] = {"mean": mean, "std_aleatoric": sd, "std_epistemic": 0.0, "pos": "WR", "team": "CHI", "bye": 9}

    def test_opponent_comes_from_the_schedule_and_can_be_overridden(self):
        r = matchup_lineups(self.engine, "Legion of Coom", week=1, sims=300, seed=1)
        self.assertEqual(r["opponent"], "Femboy Cats")
        r2 = matchup_lineups(self.engine, "Legion of Coom", week=1, opponent="Drunk Cats", sims=300, seed=1)
        self.assertEqual(r2["opponent"], "Drunk Cats")

    def test_constructions_are_ordered_by_sd_and_local_search_never_lowers_p_win(self):
        r = matchup_lineups(self.engine, "Legion of Coom", week=1, sims=3000, seed=2)
        c = r["constructions"]
        for key in ("max_mean", "safe", "stack", "p_max"):
            self.assertIn(key, c)
            self.assertEqual(len(c[key]["lineup"]), 6, "QB + two WR slots + three FLEX")
            self.assertTrue(0.0 <= c[key]["p_beat_opponent"] <= 1.0)
            self.assertIn("p_beat_median", c[key]); self.assertIn("se", c[key])
        self.assertLessEqual(c["safe"]["sd"], c["stack"]["sd"])
        self.assertGreaterEqual(c["p_max"]["p_beat_opponent"], c["max_mean"]["p_beat_opponent"] - 1e-12)
        self.assertGreater(c["max_mean"]["p_beat_opponent"], 0.7, "a 20-point QB plus five WRs beats a lone 15-point QB")
        self.assertEqual(r["n"], 3000); self.assertTrue(r["cross"])
        safe_names = {row["name"] for row in c["safe"]["lineup"]}
        self.assertTrue({"WR_0", "WR_2", "WR_4"} <= safe_names, "safe prefers the low-sd receivers")
        stack_names = {row["name"] for row in c["stack"]["lineup"]}
        self.assertTrue({"WR_1", "WR_3", "WR_5"} <= stack_names, "stack prefers the high-sd receivers")

    def test_no_cross_switch_is_honoured_and_reported(self):
        r = matchup_lineups(self.engine, "Legion of Coom", week=1, sims=200, seed=1, cross=False)
        self.assertFalse(r["cross"]); self.assertIn("engine", r["note"].lower())


class TestTradeTargetFinder(_EngineCase):
    """Trade-target finder: F2's offer constructor run with my roster as the desperate side
    against each other roster (buy: their BURIED bench player who starts for me, with the
    cheapest give-back that upgrades one of their starters) and the other way round (sell: my
    surplus that would start for them). Gains are the engine's own acceptance rule
    (get_optimal_score both sides). Hand numbers: Legion QB_1 20, QB_backup 18, LC_QB3 7,
    LC_K 6; Femboy QB_2 15, FC_K_star 12, FC_K_bench 9, FC_QB_bench 8. Buy package: give
    QB_backup (+ LC_K as the 2-for-2 throw-in), get FC_K_bench + FC_QB_bench: my optimal
    score 27.8 -> 29.8 (+2.0), theirs 28.7 -> 32.1 (+3.4)."""

    def _add(self, team, name, pos, mean):
        self.engine.rosters[team].append(name)
        self.engine.meta[team][name] = {"pos": pos, "team": "FA"}
        self.engine.baselines[name] = {"mean": mean, "std_aleatoric": 2.0, "std_epistemic": 0.0, "pos": pos, "team": "FA", "bye": 9}

    def setUp(self):
        super().setUp()
        for n, pos, m in (("QB_backup", "QB", 18.0), ("LC_QB3", "QB", 7.0), ("LC_K", "K", 6.0)):
            self._add("Legion of Coom", n, pos, m)
        for n, pos, m in (("FC_K_star", "K", 12.0), ("FC_K_bench", "K", 9.0), ("FC_QB_bench", "QB", 8.0)):
            self._add("Femboy Cats", n, pos, m)
        self.outcomes = {t: {"Playoff_Pct": 55.0, "Champ_Pct": 12.0, "Expected_Wins": 14.0} for t in self.engine.team_names}
        self.outcomes["Femboy Cats"] = {"Playoff_Pct": 20.0, "Champ_Pct": 2.0, "Expected_Wins": 10.0}

    def test_buy_side_finds_the_buried_player_with_the_correct_give_back_and_gains(self):
        r = find_trade_targets(self.engine, "Legion of Coom", outcomes=self.outcomes, week=1)
        buys = [b for b in r["buy"] if b["with"] == "Femboy Cats"]
        self.assertGreaterEqual(len(buys), 2)
        top = buys[0]
        self.assertEqual(top["i_give"], ["QB_backup", "LC_K"])
        self.assertEqual(sorted(top["i_get"]), ["FC_K_bench", "FC_QB_bench"])
        self.assertEqual(top["target"], "FC_K_bench")
        self.assertEqual(top["buried_behind"], "FC_K_star")
        self.assertEqual(top["fills_my_slot"], "K")
        self.assertAlmostEqual(top["my_gain"], 2.0, places=6)
        self.assertAlmostEqual(top["their_gain"], 3.4, places=6)
        self.assertTrue(top["acceptable"])
        self.assertTrue(top["seller"]); self.assertAlmostEqual(top["their_playoff_pct"], 20.0)
        self.assertIn("willingness", top)

    def test_ranking_puts_acceptable_packages_first_then_my_gain(self):
        r = find_trade_targets(self.engine, "Legion of Coom", outcomes=self.outcomes, week=1)
        buys = [b for b in r["buy"] if b["with"] == "Femboy Cats"]
        # giving QB_1 (20) instead of QB_backup leaves my optimal score unchanged (27.8 -> 27.8):
        # not acceptable, so it ranks below the acceptable package despite the same target
        worse = next(b for b in buys if b["i_give"][0] == "QB_1")
        self.assertFalse(worse["acceptable"]); self.assertAlmostEqual(worse["my_gain"], 0.0, places=6)
        self.assertLess(buys.index(next(b for b in buys if b["i_give"][0] == "QB_backup")), buys.index(worse))

    def test_sell_side_mirrors_what_each_opponent_would_want_from_my_bench(self):
        r = find_trade_targets(self.engine, "Legion of Coom", outcomes=self.outcomes, week=1)
        sells = [x for x in r["sell"] if x["buyer"] == "Femboy Cats"]
        self.assertTrue(sells)
        self.assertIn("QB_backup", sells[0]["they_want"])
        # the constructor offers their cheapest player that upgrades one of my starters
        # (FC_K_bench) and, as a second giver, FC_K_star; the list is then ranked by MY gain,
        # so the FC_K_star package leads: me 28.5 -> 33.3 (+4.8), them 28.7 -> 29.3 (+0.6).
        self.assertEqual({x["they_give"][0] for x in sells} & {"FC_K_bench", "FC_K_star"}, {"FC_K_bench", "FC_K_star"})
        self.assertEqual(sells[0]["they_give"][0], "FC_K_star")
        self.assertAlmostEqual(sells[0]["my_gain"], 4.8, places=6)
        self.assertAlmostEqual(sells[0]["their_gain"], 0.6, places=6)
        self.assertTrue(sells[0]["acceptable"])

    def test_no_outcomes_means_no_seller_flag_and_a_stated_reason(self):
        r = find_trade_targets(self.engine, "Legion of Coom", outcomes=None, week=1)
        self.assertTrue(all(b["seller"] is None for b in r["buy"]))
        self.assertIn("no season export", r["contention_note"].lower())

    def test_package_collapses_to_one_for_one_when_the_throw_in_is_a_received_player(self):
        """Found on real data (Legion week 1, 'Tyrone Tracy not on Legion of Coom's roster'): the
        engine's 2-for-2 throw-in is the lowest-mean player on the desperate side AFTER the
        two received players are added, so when their second piece is the cheapest of all it
        is 'dropped' straight back -- a 1-for-1 in substance. The package must say so: I give
        p1 only, I get the piece that stays, and the terms must be valid for tool 2."""
        self.engine.baselines["FC_QB_bench"]["mean"] = 5.0   # below all of mine: the throw-in is now theirs
        r = find_trade_targets(self.engine, "Legion of Coom", outcomes=self.outcomes, week=1)
        pkg = next(b for b in r["buy"] if b["with"] == "Femboy Cats" and b["i_give"][0] == "QB_backup")
        self.assertEqual(pkg["i_give"], ["QB_backup"])
        self.assertEqual(pkg["i_get"], ["FC_K_bench"])
        for n in pkg["i_give"]:
            self.assertIn(n, self.engine.rosters["Legion of Coom"])
        for n in pkg["i_get"]:
            self.assertIn(n, self.engine.rosters["Femboy Cats"])

    def test_evaluate_top_calls_tool_2_with_the_exact_terms(self):
        with patch("fantasy_sim.decisions.evaluate_trade", return_value={"teams": {}, "n_sims": 30}) as ev:
            r = find_trade_targets(self.engine, "Legion of Coom", outcomes=self.outcomes, week=1,
                                   evaluate_top=1, batches=2, sims=15)
        ev.assert_called_once()
        args, kwargs = ev.call_args
        self.assertEqual(args[1:4], ("Legion of Coom", ["QB_backup", "LC_K"], "Femboy Cats"))
        self.assertEqual(sorted(args[4]), ["FC_K_bench", "FC_QB_bench"])
        self.assertEqual((kwargs["batches"], kwargs["sims"]), (2, 15))
        self.assertIn("evaluation", r["buy"][0])


class TestLeagueWeekOutlook(_EngineCase):
    """League-wide 'this week': every pairing on the schedule, P(win) both ways and P(>= median)
    for all eight teams, on ONE joint sample through the copula -- the same machinery
    matchup_lineups uses for my matchup, applied to all pairings. Fixture schedule, week 1:
    [Legion (QB 20) v Femboy (QB 15)], [Year of Jarvis (15) v Drunk Cats (15)]."""

    def test_every_pairing_is_reported_with_probabilities_that_sum_to_one(self):
        r = league_week_outlook(self.engine, week=1, sims=2000, seed=1)
        self.assertEqual(r["week"], 1); self.assertEqual(r["n"], 2000); self.assertTrue(r["cross"])
        self.assertEqual([(m["a"], m["b"]) for m in r["matchups"]],
                         [("Legion of Coom", "Femboy Cats"), ("Year of Jarvis", "Drunk Cats")])
        for m in r["matchups"]:
            self.assertAlmostEqual(m["p_a"] + m["p_b"] + m["p_tie"], 1.0, places=9)
            for k in ("margin_mean", "margin_sd", "a_expected", "b_expected"):
                self.assertIn(k, m)
        legion = r["matchups"][0]
        self.assertGreater(legion["p_a"], 0.7, "a 20-point QB beats a 15-point QB's team most weeks")
        even = r["matchups"][1]
        self.assertAlmostEqual(even["p_a"], even["p_b"], delta=0.06, msg="identical rosters are a coin flip")

    def test_every_team_has_a_lineup_and_a_median_probability(self):
        r = league_week_outlook(self.engine, week=1, sims=500, seed=2)
        self.assertEqual(set(r["teams"]), set(self.engine.team_names))
        for t, d in r["teams"].items():
            self.assertTrue(0.0 <= d["p_beat_median"] <= 1.0)
            self.assertEqual(len(d["lineup"]), 1, "each fixture roster is one QB")
            row = d["lineup"][0]
            for k in ("slot", "name", "expected", "sd", "nfl_team"):
                self.assertIn(k, row)
            self.assertAlmostEqual(d["expected_pre_total"], row["expected"], places=9)
            # sampled mean prices absence/onset zeros, so it sits at or below the pre-game sum
            self.assertLessEqual(d["expected_total"], d["expected_pre_total"] * 1.05)
            self.assertIn("opponent", d)
        self.assertEqual(r["teams"]["Legion of Coom"]["opponent"], "Femboy Cats")
        # the median rule: on average half the league is at or above the median each week
        self.assertAlmostEqual(sum(d["p_beat_median"] for d in r["teams"].values()) / 4, 0.5, delta=0.1)

    def test_seed_reproducible_and_no_cross_honoured(self):
        a = league_week_outlook(self.engine, week=1, sims=300, seed=5)
        b = league_week_outlook(self.engine, week=1, sims=300, seed=5)
        self.assertEqual(a["matchups"][0]["p_a"], b["matchups"][0]["p_a"])
        c = league_week_outlook(self.engine, week=1, sims=300, seed=5, cross=False)
        self.assertFalse(c["cross"])


if __name__ == "__main__":
    unittest.main()

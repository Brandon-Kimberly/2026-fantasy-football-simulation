"""
tests.test_sync

Test suite for fantasy_sim.sync. Extracted from what was originally a unittest.TestCase
embedded directly in 2026_sleeper_sync.py.
"""
import math
import json
import sys
import unittest
from unittest.mock import patch, MagicMock, mock_open

from fantasy_sim.sync import (
    generate_nfl_schedule, generate_defensive_ratings, generate_nfl_power_ratings,
    fetch_espn_projections, generate_player_baselines, _extract_weekly_h2h_results,
    _extract_weekly_player_scores, _build_roster_player_entry, _normalize_player_name_for_matching,
    LEAGUE_AVG_PPG, NFL_TEAM_ABBREVIATIONS, PRESEASON_DEFENSIVE_PRIOR, VOLATILITY_CONSTANTS,
    EPISTEMIC_ERROR_RATES, WEEK_1_VERIFIED_VEGAS,
)


class TestSleeperSyncPipeline(unittest.TestCase):
    def test_power_rating_math(self):
        """Verifies off_rating satisfies the Vegas-implied-total math. def_rating is
        intentionally no longer produced here (see generate_nfl_power_ratings docstring) --
        real defensive strength now comes from generate_defensive_ratings, tested separately."""
        dummy_implied = {"SEA": {"total": 24.0, "spread": -4.0}} 
        with patch('builtins.open', mock_open()), patch('json.dump') as mock_json:
            generate_nfl_power_ratings(dummy_implied)
            args, kwargs = mock_json.call_args
            written_data = args[0]
            self.assertIn("SEA", written_data)
            self.assertAlmostEqual(written_data["SEA"]["off_rating"], 26.0)
            self.assertNotIn("def_rating", written_data["SEA"])

    @staticmethod
    def _fake_response(json_payload, status_code=200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_payload
        return resp

    def test_epistemic_aleatoric_variance_split_fresh_player(self):
        """Verifies generate_player_baselines computes std_aleatoric and std_epistemic as two
        independent, non-pre-combined fields for a player with no existing prior on disk.
        This is the exact computation that the simulation's persistent-per-season epistemic
        draw and the James-Stein Bayesian update both depend on -- it deserves a direct test
        in the file where it is actually produced, not only indirectly via the simulation."""
        players_db = {"1234": {"first_name": "Test", "last_name": "Player", "position": "WR", "team": "DET", "team_bye": 5}}
        scoring_settings = {"rec": 1.0, "rec_yd": 0.1}
        live_rosters = {"SomeTeam": [{"name": "Test Player", "pos": "WR", "team": "DET"}]}
        weekly_projection_payload = {"1234": {"stats": {"rec": 5, "rec_yd": 50}}}  # total = 5*1.0 + 50*0.1 = 10.0

        def fake_get(url, timeout=None):
            if "/projections/nfl/regular/2026/1" in url:
                return self._fake_response(weekly_projection_payload)
            return self._fake_response({}, status_code=404)

        with patch('os.path.exists', return_value=False), \
             patch('requests.get', side_effect=fake_get), \
             patch('fantasy_sim.sync.fetch_espn_projection_data', return_value=({}, {})), \
             patch('builtins.open', mock_open()), \
             patch('json.dump') as mock_json_dump:
            result = generate_player_baselines(scoring_settings, players_db, live_rosters, current_year="2026", week=1)

        self.assertIn("Test Player", result)
        entry = result["Test Player"]
        self.assertAlmostEqual(entry["mean"], 10.0)
        # Reference the live constants directly rather than hardcoding their values -- so this
        # test keeps passing (and keeps testing the real thing) across future recalibrations.
        self.assertAlmostEqual(entry["std_aleatoric"], round(VOLATILITY_CONSTANTS['WR'] * math.sqrt(10.0), 2))
        self.assertAlmostEqual(entry["std_epistemic"], round(EPISTEMIC_ERROR_RATES['WR'] * 10.0, 2))
        # The two components must be genuinely independent fields, not pre-combined into one.
        self.assertNotAlmostEqual(entry["std_aleatoric"], entry["std_epistemic"])
        self.assertNotIn("std", entry)

    def test_baseline_mean_blends_with_existing_prior(self):
        """Verifies the 60/40 blend between this week's fresh projection and last week's
        stored posterior mean is applied correctly when an existing baseline is on disk."""
        players_db = {"1234": {"first_name": "Test", "last_name": "Player", "position": "WR", "team": "DET", "team_bye": 5}}
        scoring_settings = {"rec": 1.0, "rec_yd": 0.1}
        live_rosters = {"SomeTeam": [{"name": "Test Player", "pos": "WR", "team": "DET"}]}
        weekly_projection_payload = {"1234": {"stats": {"rec": 5, "rec_yd": 50}}}  # fresh weekly_mean = 10.0
        existing_baselines_payload = {"Test Player": {"mean": 8.0}}

        def fake_get(url, timeout=None):
            if "/projections/nfl/regular/2026/2" in url:
                return self._fake_response(weekly_projection_payload)
            return self._fake_response({}, status_code=404)

        with patch('os.path.exists', return_value=True), \
             patch('requests.get', side_effect=fake_get), \
             patch('fantasy_sim.sync.fetch_espn_projection_data', return_value=({}, {})), \
             patch('builtins.open', mock_open(read_data=json.dumps(existing_baselines_payload))), \
             patch('json.load', return_value=existing_baselines_payload), \
             patch('json.dump'):
            result = generate_player_baselines(scoring_settings, players_db, live_rosters, current_year="2026", week=2)

        # final_mean = (10.0 * 0.6) + (8.0 * 0.4) = 9.2
        self.assertAlmostEqual(result["Test Player"]["mean"], 9.2)

    def test_nfl_schedule_falls_back_to_week1_when_espn_unreachable(self):
        """If ESPN's public scoreboard API is entirely unreachable, week 1 of the schedule must
        still be populated from the hardcoded, verified Week 1 opponent map rather than left
        empty -- an empty week-1 schedule would silently degrade every 'current week' matchup
        context lookup in the simulation."""
        with patch('requests.get', side_effect=ConnectionError("simulated total ESPN outage")), \
             patch('builtins.open', mock_open()), \
             patch('json.dump') as mock_json_dump, \
             self.assertLogs(level="WARNING") as logs:   # a total outage must be loud (Phase 3 finding 2)
            generate_nfl_schedule(current_nfl_week=1)

        self.assertTrue(any("verified preseason table" in m for m in logs.output), logs.output)
        args, kwargs = mock_json_dump.call_args
        written_schedule = args[0]
        self.assertEqual(written_schedule["_meta"]["failed_weeks"], list(range(1, 19)))
        self.assertIn("1", written_schedule)
        # Spot check a known pairing from WEEK_1_VERIFIED_VEGAS.
        self.assertEqual(written_schedule["1"].get("SEA"), "NE")
        self.assertEqual(written_schedule["1"].get("NE"), "SEA")

    def test_nfl_schedule_extracts_completed_game_scores(self):
        """Verifies generate_nfl_schedule correctly parses ESPN's real completed-game scores
        into (team, points_allowed) tuples -- this is the entire data source item 1's empirical
        defensive model depends on, so the parsing itself needs direct coverage."""
        def fake_get(url, timeout=None):
            if "week=1" in url:
                payload = {"events": [{
                    "competitions": [{
                        "status": {"type": {"completed": True}},
                        "competitors": [
                            {"team": {"abbreviation": "SEA"}, "score": "24"},
                            {"team": {"abbreviation": "NE"}, "score": "17"},
                        ]
                    }]
                }]}
                return self._fake_response(payload)
            return self._fake_response({"events": []})

        with patch('requests.get', side_effect=fake_get), \
             patch('builtins.open', mock_open()), \
             patch('json.dump'):
            # current_nfl_week=2 means week 1 (< 2) is treated as already completed.
            completed_results = generate_nfl_schedule(current_nfl_week=2)

        self.assertIn(("SEA", 17.0), completed_results)  # SEA allowed NE's 17 points
        self.assertIn(("NE", 24.0), completed_results)   # NE allowed SEA's 24 points

    def test_defensive_ratings_shrinkage_math(self):
        """Numerically verifies the pseudo-count shrinkage for defensive ratings with
        DEF_RATING_SHRINKAGE_N0 = 12 (derived from the 2025 season -- see config.py; it was an
        unsourced 4.0 until Phase 3's n_0 decision). Patches PRESEASON_DEFENSIVE_PRIOR to empty
        to isolate the math against a known flat prior, independent of today's real seeded
        prior data (tested separately)."""
        completed_results = [("SEA", 20.0), ("SEA", 30.0)]  # SEA allowed 20, then 30 -> avg 25.0
        with patch('builtins.open', mock_open()), \
             patch('json.dump') as mock_json_dump, \
             patch('fantasy_sim.sync.PRESEASON_DEFENSIVE_PRIOR', {}):
            generate_defensive_ratings(completed_results)

        # First call writes nfl_defensive_ratings.json, second writes nfl_defensive_tiers.json
        ratings_call = mock_json_dump.call_args_list[0]
        ratings = ratings_call.args[0]

        # estimate = (12.0*21.5 + 2*25.0) / (12.0+2) = 308.0/14.0 = 22.0
        # (was (4.0*21.5 + 2*25.0) / 6.0 = 22.67 under the unsourced n_0 = 4)
        self.assertAlmostEqual(ratings["SEA"]["points_allowed_estimate"], 22.0, places=2)
        self.assertEqual(ratings["SEA"]["games_sampled"], 2)
        # A team with zero real games sampled must fall back to the honest league-average prior.
        self.assertAlmostEqual(ratings["ARI"]["points_allowed_estimate"], LEAGUE_AVG_PPG)
        self.assertEqual(ratings["ARI"]["games_sampled"], 0)

    def test_defensive_ratings_use_real_preseason_prior_by_default(self):
        """Confirms the actual, currently-configured PRESEASON_DEFENSIVE_PRIOR (populated from
        real preseason data) is what a genuine, unpatched call to generate_defensive_ratings
        uses -- i.e. that the dict is actually wired in, not just present but unused."""
        with patch('builtins.open', mock_open()), patch('json.dump') as mock_json_dump:
            generate_defensive_ratings([])  # zero real games -> pure prior

        ratings_call = mock_json_dump.call_args_list[0]
        ratings = ratings_call.args[0]
        self.assertGreater(len(PRESEASON_DEFENSIVE_PRIOR), 0, "Expected a populated preseason prior.")
        for team, expected_prior in PRESEASON_DEFENSIVE_PRIOR.items():
            self.assertAlmostEqual(ratings[team]["points_allowed_estimate"], expected_prior)
            self.assertEqual(ratings[team]["games_sampled"], 0)

    def test_defensive_tiers_empty_before_any_real_games(self):
        """Cold-start honesty check: with NO preseason prior AND zero completed games, no team
        should be classified as a top or bottom defense -- fabricating a ranking with zero
        signal of any kind would be worse than admitting we don't have one. (Explicitly patches
        PRESEASON_DEFENSIVE_PRIOR to empty, since a real prior is now filled in by default --
        this test isolates the true zero-signal case, not today's actual configuration.)"""
        with patch('builtins.open', mock_open()), \
             patch('json.dump') as mock_json_dump, \
             patch('fantasy_sim.sync.PRESEASON_DEFENSIVE_PRIOR', {}):
            generate_defensive_ratings([])

        tiers_call = mock_json_dump.call_args_list[1]
        tiers = tiers_call.args[0]
        self.assertEqual(tiers["TOP_DEFENSE"], [])
        self.assertEqual(tiers["BOTTOM_DEFENSE"], [])

    def test_defensive_tiers_derived_once_enough_real_data_exists(self):
        """Once real games are sampled for enough teams, the top-5/bottom-5 defensive tiers
        should be derived from actual points-allowed data, replacing what used to be a static,
        hand-typed team list. Ranking is now over ALL 32 teams (unsampled teams fall back to
        the prior/league-average), so this gives every real NFL team a distinct sampled value
        to keep the top/bottom-5 unambiguous. Explicitly patches PRESEASON_DEFENSIVE_PRIOR to
        empty to isolate the empirical-only mechanism from today's real prior data, which would
        otherwise dominate this test's single-game (n=1) synthetic samples via shrinkage."""
        all_teams = list(NFL_TEAM_ABBREVIATIONS.values())
        # Distinct, clearly-ordered points-allowed value for every team: 10.0, 10.5, 11.0, ...
        completed_results = [(t, 10.0 + 0.5 * i) for i, t in enumerate(all_teams)]

        with patch('builtins.open', mock_open()), \
             patch('json.dump') as mock_json_dump, \
             patch('fantasy_sim.sync.PRESEASON_DEFENSIVE_PRIOR', {}):
            generate_defensive_ratings(completed_results)

        tiers_call = mock_json_dump.call_args_list[1]
        tiers = tiers_call.args[0]
        self.assertEqual(len(tiers["TOP_DEFENSE"]), 5)
        self.assertEqual(len(tiers["BOTTOM_DEFENSE"]), 5)
        # Lowest points allowed (best/stingiest defenses) -> TOP_DEFENSE.
        self.assertIn(all_teams[0], tiers["TOP_DEFENSE"])
        # Highest points allowed (worst defenses) -> BOTTOM_DEFENSE.
        self.assertIn(all_teams[-1], tiers["BOTTOM_DEFENSE"])

    def test_defensive_tiers_derived_from_preseason_prior_before_any_real_games(self):
        """Regression test for the user-requested improvement: with zero real games played but
        a preseason prior filled in for enough teams, tiers should be derivable immediately from
        that prior -- a reasonable preseason take is better than a totally uninformed flat
        default for every team, as long as we're honest that it's a preseason estimate."""
        all_teams = list(NFL_TEAM_ABBREVIATIONS.values())
        fake_prior = {t: 10.0 + 0.5 * i for i, t in enumerate(all_teams)}

        with patch('builtins.open', mock_open()), \
             patch('json.dump') as mock_json_dump, \
             patch('fantasy_sim.sync.PRESEASON_DEFENSIVE_PRIOR', fake_prior):
            generate_defensive_ratings([])  # zero real games

        ratings_call = mock_json_dump.call_args_list[0]
        ratings = ratings_call.args[0]
        tiers_call = mock_json_dump.call_args_list[1]
        tiers = tiers_call.args[0]

        self.assertAlmostEqual(ratings[all_teams[0]]["points_allowed_estimate"], 10.0)
        self.assertEqual(ratings[all_teams[0]]["games_sampled"], 0)
        self.assertEqual(len(tiers["TOP_DEFENSE"]), 5)
        self.assertIn(all_teams[0], tiers["TOP_DEFENSE"])
        self.assertIn(all_teams[-1], tiers["BOTTOM_DEFENSE"])

    def test_multi_source_blend_uses_disagreement_for_epistemic_uncertainty(self):
        """Verifies that when Sleeper and ESPN meaningfully disagree on a player's projection,
        that disagreement -- not just the hand-set positional error rate -- drives std_epistemic
        upward. Two independent estimators disagreeing is real, data-driven uncertainty evidence.
        Patches fetch_espn_projections directly -- this test is about the BLENDING math, not
        about how ESPN's data gets fetched (that has its own dedicated tests below)."""
        players_db = {"1234": {"first_name": "Test", "last_name": "Player", "position": "WR", "team": "DET", "team_bye": 5}}
        scoring_settings = {"rec": 1.0, "rec_yd": 0.1}
        live_rosters = {"SomeTeam": [{"name": "Test Player", "pos": "WR", "team": "DET"}]}
        sleeper_payload = {"1234": {"stats": {"rec": 5, "rec_yd": 50}}}  # sleeper_weekly_mean = 10.0

        def fake_get(url, timeout=None):
            if "/projections/nfl/regular/2026/1" in url:
                return self._fake_response(sleeper_payload)
            return self._fake_response({}, status_code=404)

        with patch('os.path.exists', return_value=False), \
             patch('requests.get', side_effect=fake_get), \
             patch('fantasy_sim.sync.fetch_espn_projection_data', return_value=({"test player": 40.0}, {})), \
             patch('builtins.open', mock_open()), \
             patch('json.dump'):
            result = generate_player_baselines(scoring_settings, players_db, live_rosters, current_year="2026", week=1)

        entry = result["Test Player"]
        # fresh_mean = (10.0 + 40.0) / 2 = 25.0; no existing prior -> final_mean = 25.0
        self.assertAlmostEqual(entry["mean"], 25.0)
        # disagreement/2 = |10-40|/2 = 15.0, vs the current floor EPISTEMIC_ERROR_RATES['WR']*25.0
        # (~13.75 at time of writing) -> disagreement wins. Uses a wide margin (ESPN=40 vs
        # sleeper=10, not a smaller gap) specifically so this keeps demonstrating the intended
        # "disagreement can exceed the floor" behavior across future recalibrations of the
        # floor rate, rather than needing readjustment every time that rate changes.
        self.assertAlmostEqual(entry["std_epistemic"], 15.0)
        self.assertGreater(15.0, EPISTEMIC_ERROR_RATES['WR'] * 25.0,
                            "Test no longer demonstrates disagreement exceeding the floor -- widen the gap above.")

    def test_multi_source_blend_falls_back_gracefully_when_espn_has_no_match(self):
        """When ESPN doesn't have (or doesn't match) a player, generate_player_baselines must
        behave exactly as Sleeper-only -- an unmatched player is never dropped or degraded."""
        players_db = {"1234": {"first_name": "Test", "last_name": "Player", "position": "WR", "team": "DET", "team_bye": 5}}
        scoring_settings = {"rec": 1.0, "rec_yd": 0.1}
        live_rosters = {"SomeTeam": [{"name": "Test Player", "pos": "WR", "team": "DET"}]}
        sleeper_payload = {"1234": {"stats": {"rec": 5, "rec_yd": 50}}}  # sleeper_weekly_mean = 10.0

        def fake_get(url, timeout=None):
            if "/projections/nfl/regular/2026/1" in url:
                return self._fake_response(sleeper_payload)
            return self._fake_response({}, status_code=404)

        with patch('os.path.exists', return_value=False), \
             patch('requests.get', side_effect=fake_get), \
             patch('fantasy_sim.sync.fetch_espn_projection_data', return_value=({}, {})), \
             patch('builtins.open', mock_open()), \
             patch('json.dump'):
            result = generate_player_baselines(scoring_settings, players_db, live_rosters, current_year="2026", week=1)

        entry = result["Test Player"]
        self.assertAlmostEqual(entry["mean"], 10.0)  # unchanged, Sleeper-only
        self.assertAlmostEqual(entry["std_epistemic"], round(EPISTEMIC_ERROR_RATES['WR'] * 10.0, 2))  # hand-set floor, no disagreement bump

    # --- Tests for fetch_espn_projections itself (the espn_api-based fetch) ---
    # These specifically need the real espn_api package importable (to patch
    # espn_api.football.League), so they skip cleanly in any environment where it isn't
    # installed rather than breaking the rest of the suite.

    def _make_fake_player(self, name, position, week_projected_points, week=1):
        """Builds a lightweight stand-in for espn_api's Player/BoxPlayer object, matching the
        real attribute names and stats shape confirmed via live diagnostic against the actual
        ESPN league (name, position, stats={week: {'projected_points': X}})."""
        import types
        return types.SimpleNamespace(
            name=name, position=position,
            stats={week: {"projected_points": week_projected_points}},
        )

    def test_fetch_espn_projections_extracts_weekly_projected_points(self):
        """Verifies fetch_espn_projections correctly pulls stats[week]['projected_points'] --
        the real, confirmed-live shape espn_api returns -- for an eligible offensive position."""
        try:
            import espn_api.football
        except ImportError:
            self.skipTest("espn_api not installed in this environment")

        fake_league = MagicMock()
        fake_league.free_agents.return_value = [self._make_fake_player("Josh Allen", "QB", 19.98, week=1)]
        fake_league.teams = []

        with patch('espn_api.football.League', return_value=fake_league):
            result = fetch_espn_projections("2026", 1)

        self.assertIn("josh allen", result)
        self.assertAlmostEqual(result["josh allen"], 19.98)

    def test_fetch_espn_projections_excludes_kicker_and_idp_positions(self):
        """Regression test for the explicit, user-requested business rule: kicker and IDP
        scoring couldn't be matched exactly between Sleeper and ESPN, so those positions must
        never be blended even when ESPN has real data for them -- Sleeper-only for K/DL/LB/DB."""
        try:
            import espn_api.football
        except ImportError:
            self.skipTest("espn_api not installed in this environment")

        fake_league = MagicMock()
        fake_league.free_agents.return_value = [
            self._make_fake_player("Brandon Aubrey", "K", 10.77, week=1),
            self._make_fake_player("Fred Warner", "LB", 12.17, week=1),
            self._make_fake_player("Maxx Crosby", "DE", 9.92, week=1),
            self._make_fake_player("Josh Allen", "QB", 19.98, week=1),  # eligible, should still appear
        ]
        fake_league.teams = []

        with patch('espn_api.football.League', return_value=fake_league):
            result = fetch_espn_projections("2026", 1)

        self.assertNotIn("brandon aubrey", result)
        self.assertNotIn("fred warner", result)
        self.assertNotIn("maxx crosby", result)
        self.assertIn("josh allen", result)

    def test_fetch_espn_projections_falls_back_gracefully_on_league_connection_failure(self):
        """If the real ESPN league can't be reached (auth failure, league deleted, network
        issue, etc.), fetch_espn_projections must return {} rather than raise -- callers depend
        on this to fall back to Sleeper-only data."""
        try:
            import espn_api.football
        except ImportError:
            self.skipTest("espn_api not installed in this environment")

        with patch('espn_api.football.League', side_effect=Exception("simulated auth failure")):
            result = fetch_espn_projections("2026", 1)

        self.assertEqual(result, {})

    def test_fetch_espn_projections_falls_back_gracefully_when_espn_api_not_installed(self):
        """If espn_api isn't installed at all, fetch_espn_projections must degrade the same way
        a network failure would (return {}), not crash the whole sync. Forces the import to fail
        regardless of whether espn_api is actually installed in this environment, so this test
        runs everywhere."""
        with patch.dict('sys.modules', {'espn_api': None, 'espn_api.football': None}):
            result = fetch_espn_projections("2026", 1)

        self.assertEqual(result, {})

    def test_build_roster_player_entry_handles_explicit_none_team(self):
        """Regression test for a real bug found via a live backtest run: Sleeper's player
        database commonly has 'team': null for anyone not currently on an active NFL roster.
        .get('team', 'FA') does not catch this (its default only applies when the key is
        MISSING, not when present with an explicit None) -- the None then propagated into the
        simulation engine and crashed a sorted() comparison the first time a real rostered
        player actually had this field."""
        players_db = {
            "1": {"first_name": "Free", "last_name": "Agent", "position": "WR", "team": None},
            "2": {"first_name": "Real", "last_name": "Player", "position": "QB", "team": "BUF"},
        }

        entry_none = _build_roster_player_entry("1", players_db)
        entry_normal = _build_roster_player_entry("2", players_db)

        self.assertEqual(entry_none["team"], "FA")  # not None
        self.assertEqual(entry_normal["team"], "BUF")

    def test_extract_weekly_h2h_results_computes_real_win_loss(self):
        """Regression test for a real, significant bug: h2h_win in weekly_actuals.json was
        hardcoded to 0 for every team, every week -- meaning self.actual_h2h_wins in the
        simulation engine has always summed to 0 regardless of real results, understating every
        team's real banked progress by roughly half (actual_wins_banked = actual_h2h_wins +
        actual_median_wins, and a normal week awards one decision of each kind)."""
        roster_map = {1: "Team A", 2: "Team B", 3: "Team C", 4: "Team D"}
        wk_matchups = [
            {"roster_id": 1, "matchup_id": 100, "points": 120.5},
            {"roster_id": 2, "matchup_id": 100, "points": 98.3},
            {"roster_id": 3, "matchup_id": 101, "points": 105.0},
            {"roster_id": 4, "matchup_id": 101, "points": 105.0},  # tie
        ]

        result = _extract_weekly_h2h_results(wk_matchups, roster_map)

        self.assertEqual(result["Team A"], 1.0)  # won
        self.assertEqual(result["Team B"], 0.0)  # lost
        self.assertEqual(result["Team C"], 0.5)  # tied
        self.assertEqual(result["Team D"], 0.5)  # tied

    def test_extract_weekly_h2h_results_skips_malformed_pairings(self):
        """A matchup_id with anything other than exactly 2 entries (e.g. a bye) must be skipped
        gracefully, never raise or corrupt other teams' results."""
        roster_map = {1: "Team A", 2: "Team B"}
        wk_matchups = [
            {"roster_id": 1, "matchup_id": 100, "points": 50.0},  # unpaired -- a bye
            {"roster_id": 2, "matchup_id": 101, "points": 60.0},
        ]

        result = _extract_weekly_h2h_results(wk_matchups, roster_map)

        self.assertEqual(result, {})

    def test_extract_weekly_player_scores_populates_real_data(self):
        """Regression test for a real, significant bug: player_scores in weekly_actuals.json
        was hardcoded to always be {}, meaning _apply_bayesian_updates' player-level posterior
        refinement in the simulation engine has never had real data to update against in
        production. Sleeper's matchup entries already include a 'players_points' field
        alongside the team-total 'points' field that was already being used -- this verifies
        it's now actually extracted, keyed by full name to match self.baselines' convention."""
        players_db = {
            "1234": {"first_name": "Test", "last_name": "QB"},
            "5678": {"first_name": "Test", "last_name": "WR"},
        }
        wk_matchups = [
            {"roster_id": 1, "points": 25.5, "players_points": {"1234": 18.2, "5678": 7.3}},
            {"roster_id": 2, "points": 20.0, "players_points": {"1234": 0.0}},  # e.g. a bye/DNP
        ]

        result = _extract_weekly_player_scores(wk_matchups, players_db)

        self.assertAlmostEqual(result["Test WR"], 7.3)
        # Second entry overwrites the first for the same player id in this synthetic example
        # (same player rostered on two different teams shouldn't happen in a real single
        # league, but the function must not crash on it either way -- last value wins).
        self.assertIn("Test QB", result)

    def test_extract_weekly_player_scores_skips_unknown_player_ids(self):
        """A player_id not present in the players_db (e.g. a stale/retired ID) must be skipped
        gracefully, never raise or silently corrupt other players' data."""
        players_db = {"1234": {"first_name": "Test", "last_name": "QB"}}
        wk_matchups = [{"roster_id": 1, "points": 10.0, "players_points": {"1234": 5.0, "9999": 3.0}}]

        result = _extract_weekly_player_scores(wk_matchups, players_db)

        self.assertEqual(result, {"Test QB": 5.0})

    def test_normalize_player_name_for_matching(self):
        """Verifies cross-source name normalization handles common suffix/punctuation cases."""
        self.assertEqual(_normalize_player_name_for_matching("Michael Pittman Jr."), "michael pittman")
        self.assertEqual(_normalize_player_name_for_matching("D'Andre Swift"), "dandre swift")
        self.assertEqual(_normalize_player_name_for_matching("Odell Beckham III"), "odell beckham")
        self.assertEqual(_normalize_player_name_for_matching(""), "")
        self.assertEqual(_normalize_player_name_for_matching(None), "")


class TestSyncManifest(unittest.TestCase):
    """The sync manifest (weekly orchestrator, 2026-09-01): sync_all writes
    data/current/sync_manifest.json LAST, so a manifest whose started_at matches the run exists
    iff the sync ran to completion. Every WARNING/ERROR logged during the run is captured into
    `degraded`, so a sync that tolerated failures (ESPN, odds, weather, ...) is distinguishable
    from a clean one without reading the log. Written before the wrapper existed."""

    def test_manifest_is_written_last_with_fields_and_captured_warnings(self):
        import logging
        from fantasy_sim import sync as syncmod
        from fantasy_sim.storage import SYNC_MANIFEST_FILE
        saved = []

        def body(sharp_polling):
            logging.warning("ODDS: no ODDS_API_KEY; using ratings model")
            logging.warning("NAME COLLISION: 'Kyle Murphy' is pid 3356 (OT, NO), pid 7377 (OL, NYG). None are rostered; "
                            "all are stored as 'Name (pid)' until one is rostered.")
            saved.append(("some_file", None))
            return 7, "2026"

        with patch.object(syncmod, "_sync_body", side_effect=body),              patch.object(syncmod, "save_json", side_effect=lambda p, d, indent=2: saved.append((p, d))):
            syncmod.sync_all(sharp_polling=True)
        self.assertEqual(saved[-1][0], SYNC_MANIFEST_FILE, "manifest must be the last write")
        m = saved[-1][1]
        for k in ("started_at", "finished_at", "season", "current_week", "sharp_polling", "degraded", "files", "ok"):
            self.assertIn(k, m)
        self.assertEqual((m["current_week"], m["season"], m["sharp_polling"], m["ok"]), (7, "2026", True, True))
        self.assertTrue(any("ODDS_API_KEY" in d for d in m["degraded"]))
        # a routine unrostered-collision notice is counted, not listed as a degradation
        self.assertFalse(any("NAME COLLISION" in d for d in m["degraded"]))
        self.assertEqual(m["notices_count"], 1)
        self.assertIn("player_cache_age_days", m)
        self.assertIn("league_state.json", m["files"])
        self.assertLessEqual(m["started_at"], m["finished_at"])
        self.assertNotIn(None, [h for h in logging.getLogger().handlers if type(h).__name__ == "_WarningCollector"],
                         "collector handler must be detached after the run")

    def test_manifest_is_not_written_when_the_body_raises(self):
        from fantasy_sim import sync as syncmod
        from fantasy_sim.storage import SYNC_MANIFEST_FILE
        saved = []
        with patch.object(syncmod, "_sync_body", side_effect=RuntimeError("Sleeper 503")),              patch.object(syncmod, "save_json", side_effect=lambda p, d, indent=2: saved.append(p)):
            with self.assertRaises(RuntimeError):
                syncmod.sync_all()
        self.assertNotIn(SYNC_MANIFEST_FILE, saved)


class TestMissingProjectionIsAnAbsence(unittest.TestCase):
    """A rostered player whose weekly projection is zero used to be DROPPED from the baselines
    file, and the engine aborted one stage later. For a player Sleeper marks absent (IR / PUP /
    NA / on the league IR slot) a zero projection is not "no data", it is "out now" -- exactly
    F4's case, which needs the absence signal (present) and a healthy-week mean for the return.
    The previous sync's stored baseline for the same pid IS that mean (Sleeper's own earlier
    projection), so it is carried, flagged, and warned about -- never invented. With no prior
    the player is still dropped (the whitelist remains the only path). Written before the
    carry existed: the first assertion failed with KeyError."""

    def _run(self, existing, reserve_pids=(), injury_status="PUP"):
        players_db = {"1234": {"first_name": "Test", "last_name": "Player", "position": "RB", "team": "SEA",
                               "injury_status": injury_status}}
        live_rosters = {"SomeTeam": [{"name": "Test Player", "pos": "RB", "team": "SEA"}]}
        zero_projection = {"1234": {"stats": {"rec": 0, "rec_yd": 0}}}

        def fake_get(url, timeout=None):
            if "/projections/nfl/regular/2026/2" in url:
                return self._resp(zero_projection)
            return self._resp({}, status_code=404)

        with (patch('os.path.exists', return_value=True),
              patch('requests.get', side_effect=fake_get),
              patch('fantasy_sim.sync.fetch_espn_projection_data', return_value=({}, {})),
              patch('builtins.open', mock_open(read_data=json.dumps(existing))),
              patch('json.load', return_value=existing),
              patch('json.dump'),
              self.assertLogs(level="WARNING") as logs):
            result = generate_player_baselines({"rec": 1.0, "rec_yd": 0.1}, players_db, live_rosters,
                                               current_year="2026", week=2, rostered_pids={"1234"},
                                               reserve_pids=set(reserve_pids))
        return result, chr(10).join(logs.output)

    @staticmethod
    def _resp(payload, status_code=200):
        m = MagicMock(); m.status_code = status_code; m.json.return_value = payload
        return m

    def test_zero_projection_with_a_prior_carries_the_prior_and_keeps_the_absence_signal(self):
        existing = {"Test Player": {"mean": 11.5, "std_aleatoric": 4.2, "std_epistemic": 2.1, "pos": "RB",
                                    "team": "SEA", "player_id": "1234", "bye": 8}}
        result, log = self._run(existing, reserve_pids={"1234"}, injury_status="PUP")
        e = result["Test Player"]
        self.assertAlmostEqual(e["mean"], 11.5); self.assertAlmostEqual(e["std_aleatoric"], 4.2)
        self.assertEqual(e["projection_source"], "carried_prior")
        self.assertEqual(e["injury_status"], "PUP"); self.assertTrue(e["on_ir"])
        self.assertEqual(e["player_id"], "1234")
        self.assertIn("carried", log.lower())

    def test_zero_projection_without_a_file_prior_falls_back_to_the_projection_log(self):
        """The baselines file can have lost the prior already (the sync that first saw the
        zero projection dropped him). F7's projection log retains every earlier Sleeper (and
        ESPN) projection per pid; its last non-zero row is the second data-sourced fallback,
        blended 50/50 with ESPN when ESPN was matched, exactly like the fresh path."""
        with patch('fantasy_sim.sync._last_logged_projections', return_value={"1234": (9.0, 11.0)}):
            result, log = self._run(existing={}, reserve_pids=(), injury_status="NA")
        e = result["Test Player"]
        self.assertAlmostEqual(e["mean"], 10.0)
        self.assertEqual(e["projection_source"], "carried_log")
        self.assertEqual(e["injury_status"], "NA"); self.assertFalse(e["on_ir"])
        self.assertIn("carried", log.lower())

    def test_zero_projection_without_a_prior_is_still_dropped_with_the_warning(self):
        with patch('fantasy_sim.sync._last_logged_projections', return_value={}):
            result, log = self._run(existing={}, reserve_pids=(), injury_status="NA")
        self.assertNotIn("Test Player", result)
        self.assertIn("NOT in baselines", log)


class TestDepthMeanWatchdog(unittest.TestCase):
    """F24's watchdog: the 2025 study cleared mean-weighted vacated-volume apportionment
    (it ties depth weighting and matches observed inheritance concentration), leaving one
    rare failure mode -- baseline means misordering a backfield's true depth. Rather than
    switching to the chart (which was WRONG in the one live disagreement: Josh Jacobs on
    the Commissioner Exempt list, charted depth 4 while being GB's lead), sync WARNS when
    the two signals disagree about a team's top backup RB, surfacing the case for human
    judgment. Warn-never-raise; the warning lands in the manifest like the rest. Written
    before warn_depth_mean_disagreements existed."""

    def _players_db(self):
        return {"1": {"player_id": "1", "position": "RB", "team": "SEA", "depth_chart_order": 1},
                "2": {"player_id": "2", "position": "RB", "team": "SEA", "depth_chart_order": 2,
                      "first_name": "True", "last_name": "Handcuff"},
                "3": {"player_id": "3", "position": "RB", "team": "SEA", "depth_chart_order": 3,
                      "first_name": "Satellite", "last_name": "Back"}}

    def _baselines(self, mean2, mean3):
        return {"Lead Guy": {"pos": "RB", "team": "SEA", "mean": 15.0, "player_id": "1"},
                "True Handcuff": {"pos": "RB", "team": "SEA", "mean": mean2, "player_id": "2"},
                "Satellite Back": {"pos": "RB", "team": "SEA", "mean": mean3, "player_id": "3"}}

    def test_disagreement_warns_with_both_players_named(self):
        import fantasy_sim.sync as syncmod
        with self.assertLogs(level="WARNING") as logs:
            n = syncmod.warn_depth_mean_disagreements(self._baselines(4.0, 7.0), self._players_db())
        self.assertEqual(n, 1)
        msg = "\n".join(logs.output)
        self.assertIn("SEA", msg)
        self.assertIn("True Handcuff", msg)
        self.assertIn("Satellite Back", msg)

    def test_agreement_and_missing_depth_data_stay_silent(self):
        import logging
        import fantasy_sim.sync as syncmod
        n = syncmod.warn_depth_mean_disagreements(self._baselines(7.0, 4.0), self._players_db())
        self.assertEqual(n, 0, "means agree with the chart: silence")
        db = self._players_db()
        for v in db.values():
            v["depth_chart_order"] = None
        n = syncmod.warn_depth_mean_disagreements(self._baselines(4.0, 7.0), db)
        self.assertEqual(n, 0, "no chart data: nothing to disagree with")


class TestSeasonIngestion(unittest.TestCase):
    """Season-retrospective ingestion: one immutable bundle per completed season at
    data/logs/season_{season}.json -- league metadata (roster_positions, the settings the
    retrospective needs), the roster map resolved to team names, final standings, and every
    week's matchups with per-player realized points. Cannot be reconstructed once Sleeper
    ages the season out -- same bucket as the draft and projection logs. Written before
    sync.ingest_season existed."""

    def _fake_get(self, fail_matchups=False):
        info = {"league_id": "L0", "season": "2025", "name": "Test League 2025", "status": "complete",
                "previous_league_id": None,
                "roster_positions": ["QB", "FLEX", "BN"],
                "settings": {"playoff_week_start": 3, "league_average_match": 0}}
        users = [{"user_id": "u1", "display_name": "brandon.kimberly"},
                 {"user_id": "u2", "display_name": "clanker_han"}]
        rosters = [{"roster_id": 1, "owner_id": "u1",
                    "settings": {"wins": 1, "losses": 1, "fpts": 200, "fpts_decimal": 50}},
                   {"roster_id": 2, "owner_id": "u2",
                    "settings": {"wins": 1, "losses": 1, "fpts": 190, "fpts_decimal": 0}}]
        matchups = {1: [{"roster_id": 1, "matchup_id": 1, "points": 100.5,
                         "players": ["11", "12"], "starters": ["11"],
                         "players_points": {"11": 100.5, "12": 0.0}, "custom_points": None},
                        {"roster_id": 2, "matchup_id": 1, "points": 90.0,
                         "players": ["21"], "starters": ["21"],
                         "players_points": {"21": 90.0}, "custom_points": None}],
                    2: [{"roster_id": 1, "matchup_id": 1, "points": 80.0,
                         "players": ["11"], "starters": ["11"],
                         "players_points": {"11": 80.0}, "custom_points": None},
                        {"roster_id": 2, "matchup_id": 1, "points": 95.0,
                         "players": ["21"], "starters": ["21"],
                         "players_points": {"21": 95.0}, "custom_points": None}]}

        def fake(url, timeout=None):
            m = MagicMock(); m.status_code = 200
            parts = url.rstrip("/").split("/")
            if parts[-2] == "matchups":
                if fail_matchups:
                    raise OSError("boom")
                m.json.return_value = matchups.get(int(parts[-1]), [])
            elif parts[-1] == "users":
                m.json.return_value = users
            elif parts[-1] == "rosters":
                m.json.return_value = rosters
            elif parts[-2] == "league":
                m.json.return_value = info
            else:
                m.status_code = 404; m.json.return_value = None
            return m
        return fake

    def _run(self, d, fake=None):
        import fantasy_sim.sync as syncmod, os as _os
        path_fn = lambda season: _os.path.join(d, f"season_{season}.json")
        name_map = {"brandon.kimberly": "Legion of Coom", "clanker_han": "Clankers"}
        with patch("requests.get", side_effect=fake or self._fake_get()), \
             patch.object(syncmod, "TEAM_NAME_MAP", name_map):
            return syncmod.ingest_season("L0", path_fn=path_fn)

    def test_the_bundle_carries_slots_map_standings_and_weekly_matchups(self):
        import json as _json, os as _os, tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run(d), 1)
            with open(_os.path.join(d, "season_2025.json"), encoding="utf-8") as f:
                b = _json.load(f)
        self.assertEqual(b["season"], "2025"); self.assertEqual(b["league_id"], "L0")
        self.assertEqual(b["roster_positions"], ["QB", "FLEX", "BN"],
                         "the slot list is IN the bundle -- the retrospective reads it, never hardcodes")
        self.assertEqual(b["settings"]["playoff_week_start"], 3)
        self.assertEqual(b["settings"]["league_average_match"], 0)
        self.assertEqual(b["roster_map"]["1"], "Legion of Coom")
        self.assertEqual(b["final_standings"]["Legion of Coom"]["wins"], 1)
        self.assertAlmostEqual(b["final_standings"]["Legion of Coom"]["points_scored"], 200.50)
        self.assertEqual(sorted(b["matchups"]), ["1", "2"], "only weeks that returned data")
        e = b["matchups"]["1"][0]
        self.assertEqual(e["roster_id"], 1); self.assertEqual(e["matchup_id"], 1)
        self.assertAlmostEqual(e["players_points"]["11"], 100.5)
        self.assertEqual(e["starters"], ["11"])
        self.assertNotIn("custom_points", e, "entries are trimmed to what the retrospective uses")

    def test_an_existing_bundle_is_never_rewritten(self):
        import json as _json, os as _os, tempfile
        with tempfile.TemporaryDirectory() as d:
            sentinel = {"sentinel": True}
            with open(_os.path.join(d, "season_2025.json"), "w", encoding="utf-8") as f:
                _json.dump(sentinel, f)
            self.assertEqual(self._run(d), 0)
            with open(_os.path.join(d, "season_2025.json"), encoding="utf-8") as f:
                kept = _json.load(f)
        self.assertEqual(kept, sentinel, "immutable once written")

    def test_a_fetch_failure_warns_and_writes_nothing(self):
        import os as _os, tempfile
        with tempfile.TemporaryDirectory() as d:
            n = self._run(d, fake=self._fake_get(fail_matchups=True))
            self.assertEqual(n, 0)
            self.assertEqual(_os.listdir(d), [], "no partial bundle on disk")


class TestDraftIngestion(unittest.TestCase):
    """F15 ingestion row: every completed draft in the league renewal chain, pulled at sync,
    one document per season at data/logs/draft_{season}.json, IMMUTABLE once written (a file
    that exists is never rewritten). roster_id resolves via the CURRENT roster map; the raw
    roster_id and picked_by user id survive on every pick so a cross-season mapping error is
    recoverable. Written before sync.ingest_drafts existed."""

    ROSTER_MAP = {1: "Legion of Coom", 2: "Femboy Cats"}

    def _pick(self, no, rnd, slot, rid, pid, first, last, pos, team, keeper=False):
        return {"pick_no": no, "round": rnd, "draft_slot": slot, "roster_id": rid,
                "picked_by": f"user_{rid}", "player_id": pid, "is_keeper": keeper,
                "metadata": {"first_name": first, "last_name": last, "position": pos, "team": team}}

    def _fake_get(self):
        leagues = {"L1": {"league_id": "L1", "season": "2026", "previous_league_id": "L0"},
                   "L0": {"league_id": "L0", "season": "2025", "previous_league_id": None}}
        drafts = {"L1": [{"draft_id": "D26", "status": "complete", "season": "2026",
                          "start_time": 1_755_850_000_000, "settings": {"rounds": 2, "teams": 2}}],
                  "L0": [{"draft_id": "D25", "status": "complete", "season": "2025",
                          "start_time": 1_724_300_000_000, "settings": {"rounds": 1, "teams": 2}},
                         {"draft_id": "DX", "status": "drafting", "season": "2025"}]}
        picks = {"D26": [self._pick(1, 1, 1, 1, "111", "Player", "A", "RB", "SEA"),
                         self._pick(2, 1, 2, 2, "222", "Player", "B", "WR", "DET", keeper=True)],
                 "D25": [self._pick(1, 1, 1, 2, "333", "Player", "C", "QB", "KC")]}

        def fake(url, timeout=None):
            m = MagicMock(); m.status_code = 200
            parts = url.rstrip("/").split("/")
            if parts[-1] == "drafts":
                m.json.return_value = drafts.get(parts[-2], [])
            elif parts[-1] == "picks":
                m.json.return_value = picks.get(parts[-2], [])
            elif parts[-2] == "league":
                m.json.return_value = leagues.get(parts[-1], {})
            else:
                m.status_code = 404; m.json.return_value = None
            return m
        return fake

    def _run(self, d, fake=None):
        import fantasy_sim.sync as syncmod, os as _os
        path_fn = lambda season: _os.path.join(d, f"draft_{season}.json")
        with patch("requests.get", side_effect=fake or self._fake_get()):
            return syncmod.ingest_drafts(self.ROSTER_MAP, league_id="L1", path_fn=path_fn)

    def test_both_seasons_written_with_resolved_teams_and_raw_ids(self):
        import json as _json, os as _os, tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run(d), 2)
            with open(_os.path.join(d, "draft_2026.json"), encoding="utf-8") as f:
                d26 = _json.load(f)
            with open(_os.path.join(d, "draft_2025.json"), encoding="utf-8") as f:
                d25 = _json.load(f)
        self.assertEqual(d26["draft_id"], "D26"); self.assertEqual(d26["season"], "2026")
        self.assertEqual(len(d26["picks"]), 2)
        p1 = d26["picks"][0]
        self.assertEqual(p1["team"], "Legion of Coom", "roster_id resolved via the roster map")
        self.assertEqual(p1["roster_id"], 1, "the raw id survives beside the resolved name")
        self.assertEqual(p1["picked_by"], "user_1")
        self.assertEqual(p1["name"], "Player A"); self.assertEqual(p1["pos"], "RB")
        self.assertEqual(p1["pick_no"], 1); self.assertEqual(p1["round"], 1)
        self.assertTrue(d26["picks"][1]["is_keeper"])
        self.assertEqual(d25["league_id"], "L0")
        self.assertEqual(d25["picks"][0]["team"], "Femboy Cats")

    def test_an_existing_draft_file_is_never_rewritten(self):
        import json as _json, os as _os, tempfile
        with tempfile.TemporaryDirectory() as d:
            sentinel = {"sentinel": True}
            with open(_os.path.join(d, "draft_2026.json"), "w", encoding="utf-8") as f:
                _json.dump(sentinel, f)
            self.assertEqual(self._run(d), 1, "only 2025 is new")
            with open(_os.path.join(d, "draft_2026.json"), encoding="utf-8") as f:
                kept = _json.load(f)
        self.assertEqual(kept, sentinel, "immutable once written")

    def test_incomplete_drafts_are_skipped_and_a_failed_picks_fetch_never_raises(self):
        import os as _os, tempfile
        base = self._fake_get()

        def flaky(url, timeout=None):
            if url.rstrip("/").endswith("picks") and "D26" in url:
                raise OSError("boom")
            return base(url, timeout=timeout)

        with tempfile.TemporaryDirectory() as d:
            n = self._run(d, fake=flaky)
            self.assertEqual(n, 1, "2026 picks failed, 2025 still lands; nothing raises")
            self.assertFalse(_os.path.exists(_os.path.join(d, "draft_2026.json")))
            self.assertTrue(_os.path.exists(_os.path.join(d, "draft_2025.json")))
            listing = sorted(_os.listdir(d))
        self.assertEqual(listing, ["draft_2025.json"], "the DX drafting-status draft wrote nothing")


class TestDecisionLogIngestion(unittest.TestCase):
    """The decision log (data/logs/decision_log.jsonl): every completed league transaction,
    auto-ingested at sync, append-only, deduped by transaction_id, with each involved player's
    model projection AT INGESTION TIME (the baseline record) and an explicit
    snapshot_is_retroactive flag when the snapshot postdates the move by more than a day -- so
    a later retrospective never treats a backfilled projection as if it were recorded at the
    moment of the click. Written before sync.ingest_transactions existed."""

    ROSTER_MAP = {1: "Legion of Coom", 2: "Femboy Cats"}
    BASELINES = {
        "Player A": {"mean": 10.0, "std_epistemic": 2.0, "pos": "RB", "team": "SEA", "player_id": "111",
                     "injury_status": None},
        "Player B": {"mean": 7.0, "std_epistemic": 1.0, "pos": "WR", "team": "DET", "player_id": "222",
                     "injury_status": "Questionable"},
    }
    PLAYERS_DB = {"111": {"first_name": "Player", "last_name": "A"},
                  "222": {"first_name": "Player", "last_name": "B"},
                  "333": {"first_name": "Player", "last_name": "C"}}

    def _tx(self, txid, created_ms, tx_type="waiver", adds=None, drops=None, status="complete",
            roster_ids=(1,), bid=3):
        return {"transaction_id": txid, "type": tx_type, "status": status, "leg": 1,
                "created": created_ms, "roster_ids": list(roster_ids),
                "adds": adds or {"111": 1}, "drops": drops or {"222": 1},
                "settings": {"waiver_bid": bid} if tx_type == "waiver" else None,
                "consenter_ids": list(roster_ids)}

    def _run(self, txs_by_week, path, now_ms=1_756_900_000_000, current_week=1, standings=None):
        import fantasy_sim.sync as syncmod

        def fake_get(url, timeout=None):
            m = MagicMock(); m.status_code = 200
            wk = int(url.rsplit("/", 1)[-1])
            m.json.return_value = txs_by_week.get(wk, [])
            return m

        with patch("requests.get", side_effect=fake_get), \
             patch.object(syncmod, "_now_ms", return_value=now_ms):
            return syncmod.ingest_transactions(self.ROSTER_MAP, current_week, self.BASELINES,
                                               self.PLAYERS_DB, my_team="Legion of Coom", path=path,
                                               standings=standings)

    def test_appends_one_record_per_completed_transaction_with_terms_and_snapshot(self):
        import json as _json, tempfile, os as _os
        fresh = 1_756_900_000_000 - 3_600_000            # one hour before the snapshot
        stale = 1_756_900_000_000 - 5 * 86_400_000       # five days before
        with tempfile.TemporaryDirectory() as d:
            path = _os.path.join(d, "decision_log.jsonl")
            n = self._run({1: [self._tx("t1", fresh), self._tx("t2", stale, roster_ids=(2,),
                                                               adds={"111": 2}, drops={"222": 2}),
                               self._tx("t3", fresh, status="failed")]}, path)
            self.assertEqual(n, 2, "two completed transactions; the failed one is skipped")
            rows = [_json.loads(l) for l in open(path, encoding="utf-8")]
        r1 = next(r for r in rows if r["transaction_id"] == "t1")
        self.assertEqual(r1["type"], "waiver"); self.assertEqual(r1["week"], 1)
        self.assertTrue(r1["is_mine"]); self.assertEqual(r1["faab_bid"], 3)
        self.assertEqual(r1["adds"][0]["name"], "Player A")
        self.assertEqual(r1["adds"][0]["to_team"], "Legion of Coom")
        self.assertAlmostEqual(r1["adds"][0]["projection"]["mean"], 10.0)
        self.assertEqual(r1["drops"][0]["projection"]["injury_status"], "Questionable")
        self.assertFalse(r1["snapshot_is_retroactive"])
        self.assertLess(r1["snapshot_lag_days"], 0.1)
        r2 = next(r for r in rows if r["transaction_id"] == "t2")
        self.assertFalse(r2["is_mine"])
        self.assertTrue(r2["snapshot_is_retroactive"], "a 5-day-old move's snapshot is backfilled")
        self.assertGreater(r2["snapshot_lag_days"], 4.9)

    def test_waiver_records_carry_the_bidders_remaining_faab_when_standings_are_supplied(self):
        import json as _json, tempfile, os as _os
        with tempfile.TemporaryDirectory() as d:
            path = _os.path.join(d, "decision_log.jsonl")
            txs = {1: [self._tx("w1", 1_756_899_000_000, tx_type="waiver", bid=6),
                       self._tx("f1", 1_756_899_000_000, tx_type="free_agent", bid=None)]}
            standings = {"Legion of Coom": {"remaining_faab": 87.0}}
            self._run(txs, path, standings=standings)
            with open(path, encoding="utf-8") as fh:
                rows = {r["transaction_id"]: r for r in map(_json.loads, fh)}
        self.assertEqual(rows["w1"]["bidder_remaining_faab"], 87.0)
        self.assertIsNone(rows["f1"].get("bidder_remaining_faab"))

    def test_dedupes_on_transaction_id_across_repeated_syncs(self):
        import tempfile, os as _os
        with tempfile.TemporaryDirectory() as d:
            path = _os.path.join(d, "decision_log.jsonl")
            txs = {1: [self._tx("t1", 1_756_899_000_000)]}
            self.assertEqual(self._run(txs, path), 1)
            self.assertEqual(self._run(txs, path), 0, "already-ingested transaction must not append again")
            self.assertEqual(sum(1 for _ in open(path, encoding="utf-8")), 1)

    def test_trade_records_both_sides_and_a_player_missing_from_baselines_is_noted(self):
        import json as _json, tempfile, os as _os
        tx = self._tx("tr1", 1_756_899_000_000, tx_type="trade", roster_ids=(1, 2),
                      adds={"111": 2, "333": 1}, drops={"111": 1, "333": 2}, bid=None)
        with tempfile.TemporaryDirectory() as d:
            path = _os.path.join(d, "decision_log.jsonl")
            self._run({1: [tx]}, path)
            r = _json.loads(open(path, encoding="utf-8").read())
        self.assertEqual(r["type"], "trade"); self.assertTrue(r["is_mine"])
        self.assertEqual({(a["name"], a["to_team"]) for a in r["adds"]},
                         {("Player A", "Femboy Cats"), ("Player C", "Legion of Coom")})
        pc = next(a for a in r["adds"] if a["name"] == "Player C")
        self.assertIsNone(pc["projection"], "no baseline for the player: projection is None, not invented")

    def test_a_write_failure_warns_and_never_raises(self):
        import fantasy_sim.sync as syncmod
        with patch("builtins.open", side_effect=OSError("disk full")), \
             self.assertLogs(level="WARNING") as logs:
            n = self._run({1: [self._tx("t1", 1_756_899_000_000)]}, path="unwritable/decision_log.jsonl")
        self.assertEqual(n, 0)
        self.assertTrue(any("DECISION LOG" in m for m in logs.output))


if __name__ == "__main__":
    unittest.main()

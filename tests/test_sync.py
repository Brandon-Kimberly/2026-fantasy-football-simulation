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
             patch('fantasy_sim.sync.fetch_espn_projections', return_value={}), \
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
             patch('fantasy_sim.sync.fetch_espn_projections', return_value={}), \
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
        """Numerically verifies the empirical-Bayes shrinkage for defensive ratings, using the
        same n_0=4.0 pattern as the player-baseline model for statistical consistency. Patches
        PRESEASON_DEFENSIVE_PRIOR to empty to isolate the math against a known flat prior,
        independent of today's real seeded prior data (tested separately)."""
        completed_results = [("SEA", 20.0), ("SEA", 30.0)]  # SEA allowed 20, then 30 -> avg 25.0
        with patch('builtins.open', mock_open()), \
             patch('json.dump') as mock_json_dump, \
             patch('fantasy_sim.sync.PRESEASON_DEFENSIVE_PRIOR', {}):
            generate_defensive_ratings(completed_results)

        # First call writes nfl_defensive_ratings.json, second writes nfl_defensive_tiers.json
        ratings_call = mock_json_dump.call_args_list[0]
        ratings = ratings_call.args[0]

        # estimate = (4.0*21.5 + 2*25.0) / (4.0+2) = 136.0/6.0 = 22.666...
        self.assertAlmostEqual(ratings["SEA"]["points_allowed_estimate"], 22.67, places=2)
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
             patch('fantasy_sim.sync.fetch_espn_projections', return_value={"test player": 40.0}), \
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
             patch('fantasy_sim.sync.fetch_espn_projections', return_value={}), \
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


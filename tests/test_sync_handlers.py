"""F26's tracked follow-up: the sync's warn-never-raise exception handlers, exercised.

These handlers are quiet BY DESIGN -- a failure degrades the data and leaves nothing
louder than a manifest warning -- and F26's coverage map showed their bodies never
executed under the suite: quiet-by-design and never-tested compounded exactly where the
live internet hits the pipeline every week. Each test drives one handler and asserts the
DOCUMENTED degradation (the warning's substance and the fallback state), not merely that
the body ran. Built pre-kickoff (2026-09-03) because weekly live syncs are when these
paths become real.
"""
import datetime as _real_dt
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import fantasy_sim.sync as sync


def _resp(payload=None, status=200, raise_on_json=False):
    m = MagicMock()
    m.status_code = status
    if raise_on_json:
        m.json.side_effect = ValueError("not json")
    else:
        m.json.return_value = payload if payload is not None else {}
    m.raise_for_status.return_value = None
    return m


class _PostGateDatetime(_real_dt.datetime):
    """A clock past the 2026-09-09 odds gate, so fetch_vegas_implied_totals actually
    reaches its API branch instead of returning the verified week-1 table."""
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 10, 12, 0, 0)


class TestNflScheduleHandlers(unittest.TestCase):
    def test_week_fetch_failure_is_recorded_and_warned_not_swallowed(self):
        with patch.object(sync.requests, "get", side_effect=OSError("network down")), \
             patch.object(sync, "save_json") as saved, \
             self.assertLogs(level="WARNING") as logs:
            sync.generate_nfl_schedule(current_nfl_week=3)
        schedule = saved.call_args_list[0].args[1]
        self.assertEqual(len(schedule["_meta"]["failed_weeks"]), 18, "every week failed")
        self.assertTrue(any("could not be fetched" in m for m in logs.output))
        self.assertTrue(any("defensive sample" in m for m in logs.output),
                        "a pre-current-week failure must say the defensive sample is short")

    def test_malformed_event_is_skipped_with_a_warning_and_good_events_survive(self):
        good = {"date": "2026-09-13T17:00Z",
                "competitions": [{"competitors": [
                    {"team": {"abbreviation": "DET"}, "homeAway": "home"},
                    {"team": {"abbreviation": "GB"}, "homeAway": "away"}]}]}
        bad = {"date": "2026-09-13T20:00Z", "competitions": []}   # IndexError inside
        payload = {"events": [bad, good]}
        with patch.object(sync.requests, "get", return_value=_resp(payload)), \
             patch.object(sync, "save_json") as saved, \
             self.assertLogs(level="WARNING") as logs:
            sync.generate_nfl_schedule(current_nfl_week=1)
        self.assertTrue(any("malformed event" in m for m in logs.output))
        schedule = saved.call_args_list[0].args[1]
        self.assertIn("GB", schedule["1"], "the well-formed event still landed")

    def test_completed_game_with_non_numeric_score_is_dropped_from_the_sample(self):
        event = {"date": "2026-09-13T17:00Z",
                 "competitions": [{"status": {"type": {"completed": True}},
                                   "competitors": [
                    {"team": {"abbreviation": "DET"}, "homeAway": "home", "score": "abandoned"},
                    {"team": {"abbreviation": "GB"}, "homeAway": "away", "score": None}]}]}
        with patch.object(sync.requests, "get", return_value=_resp({"events": [event]})), \
             patch.object(sync, "save_json"), \
             self.assertLogs(level="WARNING") as logs:
            completed = sync.generate_nfl_schedule(current_nfl_week=5)
        self.assertTrue(any("no numeric" in m for m in logs.output))
        self.assertFalse(any(g for g in completed), "the unscored game contributed nothing")


class TestVegasHandlers(unittest.TestCase):
    def test_odds_api_failure_writes_the_flat_fallback_with_its_source_tag(self):
        with patch.object(sync, "datetime", _PostGateDatetime), \
             patch.object(sync, "ODDS_API_KEY", "dummy-key"), \
             patch.object(sync.requests, "get", side_effect=OSError("timeout")), \
             patch.object(sync, "save_json") as saved, \
             self.assertLogs(level="WARNING") as logs:
            sync.fetch_vegas_implied_totals(current_nfl_week=2)
        self.assertTrue(any("odds API request failed" in m for m in logs.output))
        payload = saved.call_args_list[0].args[1]
        self.assertEqual(payload["_meta"]["source"], "fallback_api_error")
        self.assertEqual(payload["DET"]["total"], 21.5, "flat fallback totals in force")

    def test_weather_failure_is_silent_and_the_game_totals_survive_without_it(self):
        game = {"home_team": "Detroit Lions", "away_team": "Green Bay Packers",
                "commence_time": "2026-09-13T17:00:00Z",
                "bookmakers": [{"key": "draftkings", "markets": [
                    {"key": "totals", "outcomes": [{"name": "Over", "point": 48.0}]},
                    {"key": "spreads", "outcomes": [
                        {"name": "Detroit Lions", "point": -3.5},
                        {"name": "Green Bay Packers", "point": 3.5}]}]}]}

        def get(url, timeout=None, **kw):
            if "the-odds-api" in url:
                return _resp([game])
            raise OSError("weather API down")   # open-meteo call -> the silent handler

        with patch.object(sync, "datetime", _PostGateDatetime), \
             patch.object(sync, "ODDS_API_KEY", "dummy-key"), \
             patch.object(sync.requests, "get", side_effect=get), \
             patch.object(sync, "save_json") as saved:
            sync.fetch_vegas_implied_totals(current_nfl_week=2)
        payload = saved.call_args_list[0].args[1]
        self.assertAlmostEqual(payload["DET"]["total"], 25.75)   # (48 + 3.5) / 2
        self.assertEqual(payload["DET"]["wind_mph"], 0.0, "no weather, not no line")


class TestBaselineFetchHandlers(unittest.TestCase):
    DB = {"77": {"first_name": "Test", "last_name": "Backer", "position": "LB",
                 "team": "DET", "team_bye": 5}}
    ROSTERS = {"SomeTeam": [{"name": "Test Backer", "pos": "LB", "team": "DET"}]}
    SCORING = {"idp_tkl_solo": 1.5}

    def _gen(self, fake_get, exists=False, load_raises=False):
        loader = MagicMock(side_effect=OSError("corrupt")) if load_raises else MagicMock(return_value={})
        with patch.object(sync.os.path, "exists", return_value=exists), \
             patch.object(sync, "load_json", loader), \
             patch.object(sync.requests, "get", side_effect=fake_get), \
             patch.object(sync, "fetch_espn_projection_data", side_effect=OSError("espn down")), \
             patch.object(sync, "save_json") as saved, \
             patch.object(sync, "append_projection_log", return_value=0):
            out = sync.generate_player_baselines(self.SCORING, self.DB, self.ROSTERS,
                                                 current_year="2026", week=1)
        return out

    def test_unreadable_prior_baselines_degrade_to_fresh_projections(self):
        def get(url, timeout=None):
            if "/projections/nfl/regular/2026/1" in url:
                return _resp({"77": {"stats": {"idp_tkl_solo": 4}}})
            return _resp({}, status=404)
        out = self._gen(get, exists=True, load_raises=True)
        self.assertAlmostEqual(out["Test Backer"]["mean"], 6.0,
                               "fresh projection used; the corrupt prior neither crashed nor blended")

    def test_weekly_fetch_failure_falls_back_to_the_season_endpoint(self):
        def get(url, timeout=None):
            if url.endswith("/2026/1"):
                raise OSError("weekly down")
            if url.endswith("/2026"):
                return _resp({"77": {"stats": {"idp_tkl_solo": 64, "gp": 16.0}}})
            return _resp({}, status=404)
        out = self._gen(get)
        self.assertAlmostEqual(out["Test Backer"]["mean"], 6.0, "96 pts / 16 games via the season fallback")

    def test_both_projection_endpoints_failing_yields_no_baselines_not_a_crash(self):
        out = self._gen(lambda url, timeout=None: (_ for _ in ()).throw(OSError("all down")))
        self.assertEqual(out, {}, "no projections -> no invented baselines")

    def test_espn_failure_degrades_to_sleeper_only(self):
        # fetch_espn_projection_data raises in every test above (the patch); this asserts
        # the positive half: Sleeper baselines still exist and carry no ESPN blend.
        def get(url, timeout=None):
            if "/projections/nfl/regular/2026/1" in url:
                return _resp({"77": {"stats": {"idp_tkl_solo": 4}}})
            return _resp({}, status=404)
        out = self._gen(get)
        self.assertIn("Test Backer", out)
        self.assertAlmostEqual(out["Test Backer"]["mean"], 6.0, "Sleeper-only, unblended")


class TestIngestionHandlers(unittest.TestCase):
    ROSTER_MAP = {1: "Legion of Coom"}

    def test_unreadable_decision_log_skips_ingestion_rather_than_duplicating(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "decision_log.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not valid json\n")
            with self.assertLogs(level="WARNING") as logs:
                n = sync.ingest_transactions(self.ROSTER_MAP, 1, {}, {}, path=path)
        self.assertEqual(n, 0)
        self.assertTrue(any("risking duplicates" in m for m in logs.output))

    def test_transaction_week_fetch_failure_is_deferred_to_a_later_sync(self):
        def get(url, timeout=None):
            if "/transactions/1" in url:
                raise OSError("week 1 down")
            return _resp([])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "decision_log.jsonl")
            with patch.object(sync.requests, "get", side_effect=get), \
                 self.assertLogs(level="WARNING") as logs:
                n = sync.ingest_transactions(self.ROSTER_MAP, 2, {}, {}, path=path)
        self.assertEqual(n, 0)
        self.assertTrue(any("picked up by a later sync" in m for m in logs.output))

    def test_draft_league_fetch_failure_warns_and_stops_the_chain(self):
        with patch.object(sync.requests, "get", side_effect=OSError("league down")), \
             self.assertLogs(level="WARNING") as logs:
            n = sync.ingest_drafts({"1": "Legion of Coom"}, league_id="12345",
                                   path_fn=lambda season: os.path.join(tempfile.gettempdir(), f"d{season}.json"))
        self.assertEqual(n, 0)
        self.assertTrue(any("DRAFT LOG" in m for m in logs.output))

    def test_season_bundle_mid_fetch_failure_writes_nothing(self):
        calls = {"n": 0}

        def get(url, timeout=None):
            calls["n"] += 1
            if url.endswith("/league/999"):
                return _resp({"season": "2025", "name": "x", "status": "complete", "settings": {}})
            raise OSError("users endpoint down")
        with tempfile.TemporaryDirectory() as d:
            path_fn = lambda season: os.path.join(d, f"season_{season}.json")
            with patch.object(sync.requests, "get", side_effect=get), \
                 self.assertLogs(level="WARNING") as logs:
                n = sync.ingest_season("999", path_fn=path_fn)
            self.assertEqual(n, 0)
            self.assertFalse(os.path.exists(path_fn("2025")), "no partial bundle on disk")
        self.assertTrue(any("nothing written" in m for m in logs.output))

    def test_projection_log_append_failure_warns_and_reports_zero(self):
        with patch("builtins.open", side_effect=OSError("disk full")), \
             self.assertLogs(level="WARNING") as logs:
            n = sync.append_projection_log([{"a": 1}],
                                           path=os.path.join(tempfile.gettempdir(), "x", "p.jsonl"))
        self.assertEqual(n, 0)
        self.assertTrue(any("cannot be measured next season" in m for m in logs.output))

    def test_bracket_fetch_failure_writes_an_empty_bracket_for_standings_seeding(self):
        with patch.object(sync.requests, "get", side_effect=OSError("bracket down")), \
             patch.object(sync, "save_json") as saved, \
             self.assertLogs(level="WARNING") as logs:
            sync.generate_playoff_bracket({"settings": {"playoff_week_start": 15,
                                                        "playoff_teams": 4}}, {})
        self.assertTrue(any("empty bracket" in m for m in logs.output))
        payload = saved.call_args_list[0].args[1]
        self.assertEqual(payload, {}, "documented contract: an EMPTY bracket file, so the "
                                      "engine seeds from banked standings only")

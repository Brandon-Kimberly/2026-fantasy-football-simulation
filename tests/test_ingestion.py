"""
tests.test_ingestion

AUDIT_PLAN.md Phase 3 -- data ingestion integrity.

    Invariant: every field that looks live is live; every fallback is loud.

Each fallback in fantasy_sim.sync is exercised under the condition that triggers it, and the
test asserts the property the plan names: the degraded path must announce itself (a WARNING
on the root logger, checked with assertLogs) and must not leave a stale or wrong artefact
behind. Tests that FAIL characterise the defects in AUDIT_PHASE_3_FINDINGS.md; they are red
on purpose until remediation is decided. Tests that pass lock behaviour verified to hold.

Nothing here touches the network. Live measurements (ESPN match rate, cache drift) were taken
once for the findings and are recorded there; a live re-check is available behind
RUN_LIVE_INGESTION_TESTS=1 so it never makes the suite flaky.

WHAT IS NOT COVERED
-------------------
1. The Open-Meteo weather fetch. It is exercised only inside the live Odds-API path, and its
   output (wind_mph, precip_prob) is never read by the engine -- see finding 9. There is
   nothing to assert until a consumer exists.
2. The 2026-09-09 date gate in fetch_vegas_implied_totals is pinned by test, but whether that
   date is the real kickoff is unverifiable from code.
"""
import logging
import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from fantasy_sim import sync
from fantasy_sim.config import (
    DEFAULT_FALLBACK_TOTALS, WEEK_1_VERIFIED_VEGAS, EPISTEMIC_ERROR_RATES, VOLATILITY_CONSTANTS,
    LEAGUE_AVG_PPG, PRESEASON_DEFENSIVE_PRIOR, DEF_RATING_SHRINKAGE_N0, SIM_CONFIG,
)
from fantasy_sim.storage import (
    VEGAS_FILE, NFL_SCHEDULE_FILE, BASELINES_FILE, TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE,
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, LIVE_ROSTERS_FILE, DEFENSIVE_TIERS_FILE,
    LEAGUE_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE, PLAYER_CACHE_FILE,
)


class _InSeason(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 1)


class _no_logs(object):
    """assertNoLogs for Python < 3.10: fails the test if any WARNING+ record is emitted."""

    def __init__(self, case):
        self.case = case
        self.records = []
        self.handler = logging.Handler()
        self.handler.setLevel(logging.WARNING)
        self.handler.emit = self.records.append

    def __enter__(self):
        logging.getLogger().addHandler(self.handler)
        return self

    def __exit__(self, *exc):
        logging.getLogger().removeHandler(self.handler)
        if exc[0] is None and self.records:
            self.case.fail("unexpected log records: %s" % [r.getMessage() for r in self.records])
        return False


def _capture_saves():
    saved = {}

    def fake_save(path, data, indent=2):
        saved[os.path.basename(path)] = data
    return saved, fake_save


def _scoreboard_response(completed=True):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"events": [{"date": "2026-09-10T00:20Z", "competitions": [{
        "competitors": [{"team": {"abbreviation": "DET"}, "score": "27"},
                        {"team": {"abbreviation": "CHI"}, "score": "20"}],
        "status": {"type": {"completed": completed}},
    }]}]}
    return m


# ------------------------------------------------------------------------------- Vegas
class TestVegasFallbacks(unittest.TestCase):
    """fetch_vegas_implied_totals has four paths: preseason table, no API key, API failure,
    API success. The plan asks that the fallbacks fire only when intended and that staleness
    be detectable."""

    def test_preseason_gate_serves_the_verified_week_one_table_without_calling_the_api(self):
        saved, fake_save = _capture_saves()
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync, "ODDS_API_KEY", "key"), \
             patch.object(sync.requests, "get", side_effect=AssertionError("API must not be called")):
            out = sync.fetch_vegas_implied_totals(1)
        for team, line in WEEK_1_VERIFIED_VEGAS.items():
            self.assertEqual(out[team], line)
        written = saved[os.path.basename(VEGAS_FILE)]
        self.assertEqual(written["_meta"]["week"], 1)
        self.assertEqual(written["_meta"]["source"], "week1_verified_table")

    def test_in_season_without_api_key_writes_the_fallback_it_returns(self):
        """Regression guard for Phase 3 finding 1. This path used to return
        DEFAULT_FALLBACK_TOTALS and rewrite the power ratings from it, but never write
        vegas_totals.json. The engine then read whatever was last written -- the week-1
        preseason table -- as the CURRENT week's environment, with week-1 opponents, for the
        rest of the season. Every path now writes, and stamps the file with the week it is
        for and where it came from."""
        saved, fake_save = _capture_saves()
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync, "datetime", _InSeason), \
             patch.object(sync, "ODDS_API_KEY", ""), \
             self.assertLogs(level="WARNING"):
            out = sync.fetch_vegas_implied_totals(5)
        self.assertIn(os.path.basename(TEAM_RATINGS_FILE), saved)
        written = saved[os.path.basename(VEGAS_FILE)]
        self.assertEqual(written["_meta"]["week"], 5)
        self.assertEqual(written["_meta"]["source"], "fallback_no_api_key")
        self.assertEqual(out["DET"], DEFAULT_FALLBACK_TOTALS["DET"])
        # the stamp must not leak into the power ratings as a 33rd "team"
        self.assertNotIn("_meta", saved[os.path.basename(TEAM_RATINGS_FILE)])

    def test_in_season_api_failure_writes_the_fallback_it_returns(self):
        """Regression guard for Phase 3 finding 1, API-error path."""
        saved, fake_save = _capture_saves()
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync, "datetime", _InSeason), \
             patch.object(sync, "ODDS_API_KEY", "key"), \
             patch.object(sync.requests, "get", side_effect=Exception("down")), \
             self.assertLogs(level="WARNING"):
            sync.fetch_vegas_implied_totals(5)
        written = saved[os.path.basename(VEGAS_FILE)]
        self.assertEqual(written["_meta"]["source"], "fallback_api_error")
        self.assertEqual(written["_meta"]["week"], 5)

    def test_in_season_fallbacks_are_loud(self):
        """Regression guard for Phase 3 finding 1. A season run with no market data is a
        materially different forecast (flat 21.5 for every team, opponent 'FA' everywhere, no
        defensive tiers applied). It is announced, and the announcement names the fix."""
        saved, fake_save = _capture_saves()
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync, "datetime", _InSeason), \
             patch.object(sync, "ODDS_API_KEY", ""), \
             self.assertLogs(level="WARNING"):
            sync.fetch_vegas_implied_totals(5)

    def test_empty_api_response_does_not_silently_write_a_flat_environment(self):
        """Regression guard for Phase 3 finding 1. An empty odds payload (wrong window, market
        not yet posted) used to write a file in which every team is 21.5 / opponent 'FA' --
        indistinguishable from real data to every consumer -- with no warning. It now warns
        and stamps the file as a fallback so the engine can tell."""
        saved, fake_save = _capture_saves()
        r = MagicMock()
        r.json.return_value = []
        r.raise_for_status.return_value = None
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync, "datetime", _InSeason), \
             patch.object(sync, "ODDS_API_KEY", "key"), \
             patch.object(sync.requests, "get", return_value=r), \
             self.assertLogs(level="WARNING"):
            sync.fetch_vegas_implied_totals(5)


class TestVegasStalenessIsDetectable(unittest.TestCase):
    """The engine applies the Vegas file to the CURRENT week. If that file was produced for
    a different week, every opponent in it is wrong, and the engine has the information to
    notice: nfl_schedule.json names the real opponent for every team in every week."""

    def _engine(self, current_week, vegas, schedule):
        from fantasy_sim.simulation import FantasySimulationEngine
        roster = {"T": [{"name": "P", "pos": "QB", "team": "DET"}]}
        fs = {
            LEAGUE_STATE_FILE: {"current_week": current_week},
            LEAGUE_STANDINGS_FILE: {"T": {"remaining_faab": 100}},
            VEGAS_FILE: vegas, LIVE_ROSTERS_FILE: roster,
            BASELINES_FILE: {"P": {"mean": 18.0, "std_aleatoric": 7.0, "std_epistemic": 5.0, "pos": "QB", "team": "DET"}},
            TEAM_RATINGS_FILE: {"DET": {"off_rating": 24.0}, "GB": {"off_rating": 22.0}, "CHI": {"off_rating": 20.0}},
            DEFENSIVE_RATINGS_FILE: {}, DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
            LEAGUE_SCHEDULE_FILE: [], NFL_SCHEDULE_FILE: schedule, WEEKLY_ACTUALS_FILE: {},
        }
        with patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]):
            return FantasySimulationEngine()

    def test_current_week_environment_uses_the_scheduled_opponent_not_a_stale_vegas_file(self):
        """Regression guard for Phase 3 finding 1 (engine side). An UNSTAMPED week-1 Vegas
        file (DET vs CHI) still on disk at week 5, where the schedule says DET plays GB. The
        engine used to hand DET a week-1 line against the wrong opponent and nothing flagged
        it. It now condemns the line by the opponent mismatch alone -- no stamp needed -- logs
        at ERROR, and gives DET the ratings-model environment for the real opponent."""
        vegas = {"DET": {"total": 28.25, "spread": -7.0, "opponent": "CHI", "wind_mph": 0.0, "precip_prob": 0.0},
                 "CHI": {"total": 21.25, "spread": 7.0, "opponent": "DET", "wind_mph": 0.0, "precip_prob": 0.0}}
        schedule = {"5": {"DET": "GB", "GB": "DET", "CHI": "MIN", "MIN": "CHI"}}
        with self.assertLogs(level="ERROR") as logs:
            engine = self._engine(5, vegas, schedule)
        self.assertEqual(engine.stale_vegas_teams, {"DET", "CHI"})
        self.assertTrue(any("VEGAS STALE" in m for m in logs.output))
        env = engine._compute_week_environment(5, "DET")
        self.assertEqual(env["opponent"], "GB",
                         "engine used opponent %r from a Vegas file that was not produced for "
                         "week 5; staleness is undetected" % env["opponent"])

    def test_stamped_wrong_week_is_condemned_even_when_opponents_happen_to_match(self):
        """The _meta stamp is the primary signal: a file stamped for week 4 is refused at
        week 5 even if the pairing coincidentally repeats."""
        vegas = {"_meta": {"week": 4, "source": "odds_api", "fetched_at": "x"},
                 "DET": {"total": 24.0, "spread": -3.0, "opponent": "GB", "wind_mph": 0.0, "precip_prob": 0.0}}
        schedule = {"5": {"DET": "GB", "GB": "DET"}}
        with self.assertLogs(level="ERROR"):
            engine = self._engine(5, vegas, schedule)
        self.assertEqual(engine.stale_vegas_teams, {"DET"})

    def test_fresh_stamped_file_is_used_silently(self):
        vegas = {"_meta": {"week": 5, "source": "odds_api", "fetched_at": "x"},
                 "DET": {"total": 24.0, "spread": -3.0, "opponent": "GB", "wind_mph": 0.0, "precip_prob": 0.0}}
        schedule = {"5": {"DET": "GB", "GB": "DET"}}
        logging.getLogger().addHandler(logging.NullHandler())
        with self.assertNoLogs(level="WARNING") if hasattr(self, "assertNoLogs") else _no_logs(self):
            engine = self._engine(5, vegas, schedule)
        self.assertEqual(engine.stale_vegas_teams, set())
        self.assertEqual(engine._compute_week_environment(5, "DET")["total"], 24.0)

    def test_fallback_stamped_file_warns_that_matchup_effects_are_off(self):
        vegas = {"_meta": {"week": 5, "source": "fallback_no_api_key", "fetched_at": "x"},
                 "DET": {"total": 21.5, "spread": 0.0, "opponent": "FA", "wind_mph": 0.0, "precip_prob": 0.0}}
        schedule = {"5": {"DET": "GB", "GB": "DET"}}
        with self.assertLogs(level="WARNING") as logs:
            self._engine(5, vegas, schedule)
        self.assertTrue(any("ODDS_API_KEY" in m for m in logs.output))


# ---------------------------------------------------------------------- NFL schedule
class TestNflScheduleFallbacks(unittest.TestCase):
    def test_week_one_falls_back_to_the_verified_table_when_the_whole_fetch_fails(self):
        saved, fake_save = _capture_saves()
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync.requests, "get", side_effect=Exception("down")):
            sync.generate_nfl_schedule(1)
        sched = saved[os.path.basename(NFL_SCHEDULE_FILE)]
        self.assertEqual(sched["1"]["DET"], "NO")   # from WEEK_1_VERIFIED_VEGAS
        self.assertEqual(sched["5"], {})

    def test_meta_carries_each_weeks_kickoff_datetimes(self):
        # run_windows computes canonical-run deadlines from real kickoff times; the fetch
        # already has them, so _meta persists them (invisible to the engine, like the rest
        # of _meta). Written before the kickoffs key existed.
        saved, fake_save = _capture_saves()
        with patch.object(sync, "save_json", side_effect=fake_save),              patch.object(sync.requests, "get", return_value=_scoreboard_response()):
            sync.generate_nfl_schedule(1)
        sched = saved[os.path.basename(NFL_SCHEDULE_FILE)]
        self.assertEqual(sched["_meta"]["kickoffs"]["1"], ["2026-09-10T00:20Z"])
        self.assertEqual(sched["_meta"]["kickoffs"]["18"], ["2026-09-10T00:20Z"])

    def test_a_single_failed_week_is_loud(self):
        """Regression guard for Phase 3 finding 2. Week 7 fails; every other week is fine.
        The result is an empty week 7 (every team resolves to 'FA' -> flat 21.5, no opponent)
        and, because the completed scores for that week are collected in the same pass, the
        defensive ratings lose a game per team. That used to happen with no warning."""
        saved, fake_save = _capture_saves()

        def flaky(url, timeout=5):
            if "week=7" in url:
                raise Exception("timeout")
            return _scoreboard_response()
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync.requests, "get", side_effect=flaky), \
             self.assertLogs(level="WARNING") as logs:
            sync.generate_nfl_schedule(9)
        self.assertTrue(any("week 7" in m and "defensive sample" in m for m in logs.output), logs.output)

    def test_a_single_failed_week_is_recorded_not_silently_dropped(self):
        """Regression guard for Phase 3 finding 2 (consequence). The lost week's games cannot
        be recovered without a re-fetch, so the honest artefact is a RECORD of the loss:
        nfl_schedule['_meta']['failed_weeks'] names it, the week itself is empty, and the
        defensive sample is short by exactly that week. Before, 14 rows appeared where 16
        belonged and nothing said why."""
        saved, fake_save = _capture_saves()

        def flaky(url, timeout=5):
            if "week=7" in url:
                raise Exception("timeout")
            return _scoreboard_response()
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync.requests, "get", side_effect=flaky), \
             self.assertLogs(level="WARNING"):
            results = sync.generate_nfl_schedule(9)
        sched = saved[os.path.basename(NFL_SCHEDULE_FILE)]
        self.assertEqual(sched["_meta"]["failed_weeks"], [7])
        self.assertEqual(sched["7"], {})
        self.assertEqual(len(results), 14)   # 8 completed weeks minus the recorded one
        # non-2xx is a failure too, not an empty week
        def not_found(url, timeout=5):
            m = MagicMock()
            m.status_code = 404 if "week=3" in url else 200
            m.json.return_value = _scoreboard_response().json.return_value
            return m
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync.requests, "get", side_effect=not_found), \
             self.assertLogs(level="WARNING"):
            sync.generate_nfl_schedule(1)
        self.assertEqual(saved[os.path.basename(NFL_SCHEDULE_FILE)]["_meta"]["failed_weeks"], [3])

    def test_league_schedule_keeps_one_entry_per_week_when_a_week_fails(self):
        """Regression guard for Phase 3 finding 2b. The engine indexes league_schedule[week_idx]
        positionally. A failed week used to be skipped with `continue`, shifting every later
        week's fantasy matchups one index earlier. It now contributes an empty week, loudly."""
        saved, fake_save = _capture_saves()
        roster_map = {1: "A", 2: "B"}

        def flaky(url, timeout=10):
            if "/matchups/5" in url:
                raise Exception("timeout")
            m = MagicMock()
            m.status_code = 200
            wk = int(url.rsplit("/", 1)[1])
            m.json.return_value = [{"roster_id": 1, "matchup_id": wk}, {"roster_id": 2, "matchup_id": wk}]
            return m
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync.requests, "get", side_effect=flaky), \
             self.assertLogs(level="WARNING"):
            failed = sync.generate_league_schedule(roster_map, regular_season_weeks=14)
        sched = saved[os.path.basename(LEAGUE_SCHEDULE_FILE)]
        self.assertEqual(len(sched), 14)
        self.assertEqual(sched[4], [])                 # week 5 -> index 4, empty, not skipped
        self.assertEqual(sched[5], [("A", "B")])       # week 6 still at index 5
        self.assertEqual(failed, [5])


# ------------------------------------------------------------------ player baselines
class _BaselineHarness(unittest.TestCase):
    SCORING = {"pass_yd": 0.04, "rush_yd": 0.1, "idp_tkl_solo": 1.0}

    def _run(self, players_db, projections, existing=None, espn=None):
        saved, fake_save = _capture_saves()
        weekly = MagicMock()
        weekly.status_code = 200
        weekly.json.return_value = projections
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync.os.path, "exists", return_value=existing is not None), \
             patch.object(sync, "load_json", return_value=existing or {}), \
             patch.object(sync.requests, "get", return_value=weekly), \
             patch.object(sync, "fetch_espn_projections", return_value=espn or {}):
            out = sync.generate_player_baselines(self.SCORING, players_db, {}, "2026", 1)
        return out, saved


class TestBaselineIngestion(_BaselineHarness):
    def test_position_constants_are_looked_up_by_normalised_position(self):
        """Regression guard for Phase 3 finding 3. VOLATILITY_CONSTANTS and
        EPISTEMIC_ERROR_RATES are keyed by the engine's slot positions (DL, DB, RB ...).
        Sleeper reports DE, DT, NT, CB, S, FS, SS, FB. sync used to look the constants up by
        the RAW position, so every one of those got the anonymous default (k=1.5, error 0.18):
        a DE got 0.18 instead of DL's 0.15, a fullback 0.18 instead of RB's 0.63. Five rostered
        DEs were affected. normalize_position now lives in config and sync applies it first."""
        db = {"1": {"first_name": "A", "last_name": "End", "position": "DE", "team": "DET"},
              "2": {"first_name": "B", "last_name": "Back", "position": "FB", "team": "DET"}}
        proj = {"1": {"stats": {"idp_tkl_solo": 8.0}}, "2": {"stats": {"rush_yd": 60.0}}}
        out, _ = self._run(db, proj)
        self.assertAlmostEqual(out["A End"]["std_epistemic"], round(EPISTEMIC_ERROR_RATES["DL"] * 8.0, 2),
                               msg="DE epistemic rate %.2f: used the default, not DL's %.2f"
                                   % (out["A End"]["std_epistemic"] / 8.0, EPISTEMIC_ERROR_RATES["DL"]))
        self.assertAlmostEqual(out["B Back"]["std_epistemic"], round(EPISTEMIC_ERROR_RATES["RB"] * 6.0, 2),
                               msg="FB epistemic rate %.2f: used the default, not RB's %.2f"
                                   % (out["B Back"]["std_epistemic"] / 6.0, EPISTEMIC_ERROR_RATES["RB"]))

    def test_explicit_none_team_is_stored_as_fa(self):
        """Regression guard for Phase 3 finding 4. _build_roster_player_entry documents exactly
        this bug for rosters (`.get('team', 'FA')` does not catch an explicit null) and fixed
        it there; generate_player_baselines had the same line unfixed, and two committed
        baselines carried team: null."""
        db = {"1": {"first_name": "Free", "last_name": "Agent", "position": "WR", "team": None}}
        out, _ = self._run(db, {"1": {"stats": {"rush_yd": 50.0}}})
        self.assertEqual(out["Free Agent"]["team"], "FA")

    MURPHYS = {"1": {"first_name": "Byron", "last_name": "Murphy", "position": "CB", "team": "MIN"},
               "2": {"first_name": "Byron", "last_name": "Murphy", "position": "DL", "team": "SEA"}}
    MURPHY_PROJ = {"1": {"stats": {"idp_tkl_solo": 7.0}}, "2": {"stats": {"idp_tkl_solo": 6.5}}}

    def _run_rostered(self, players_db, projections, rostered, existing=None):
        saved, fake_save = _capture_saves()
        weekly = MagicMock()
        weekly.status_code = 200
        weekly.json.return_value = projections
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync.os.path, "exists", return_value=existing is not None), \
             patch.object(sync, "load_json", return_value=existing or {}), \
             patch.object(sync.requests, "get", return_value=weekly), \
             patch.object(sync, "fetch_espn_projections", return_value={}):
            return sync.generate_player_baselines(self.SCORING, players_db, {}, "2026", 1,
                                                  rostered_pids=rostered)

    def test_two_players_sharing_a_name_do_not_overwrite_each_other(self):
        """Regression guard for Phase 3 finding 5 (interim). Baselines are keyed by full name
        and Sleeper has duplicate names (today: Justin Jefferson WR/MIN and LB/CLE; Byron
        Murphy CB/MIN and DL/SEA). Whichever pid iterated last used to win, silently -- Byron
        Murphy's committed baseline was the SEA DL's. Neither rostered: both are now stored as
        'Name (pid)', loudly."""
        with self.assertLogs(level="WARNING") as logs:
            out = self._run_rostered(self.MURPHYS, self.MURPHY_PROJ, rostered=set())
        self.assertEqual(sorted(k for k in out if "Murphy" in k), ["Byron Murphy (1)", "Byron Murphy (2)"])
        self.assertEqual(out["Byron Murphy (1)"]["pos"], "CB")
        self.assertEqual(out["Byron Murphy (2)"]["pos"], "DL")
        self.assertTrue(any("NAME COLLISION" in m for m in logs.output))

    def test_sole_rostered_claimant_keeps_the_plain_name(self):
        """Rosters are minted from the same plain name, so the rostered player must stay
        reachable under it; the other pid is suffixed."""
        with self.assertLogs(level="WARNING"):
            out = self._run_rostered(self.MURPHYS, self.MURPHY_PROJ, rostered={"1"})
        self.assertEqual(out["Byron Murphy"]["pos"], "CB")
        self.assertEqual(out["Byron Murphy (2)"]["pos"], "DL")
        self.assertEqual(out["Byron Murphy"]["player_id"], "1")

    def test_two_rostered_players_sharing_a_name_fail_loudly(self):
        """Genuinely unrepresentable under name keying (AUDIT_PLAN.md F1 is the fix)."""
        with self.assertRaises(ValueError):
            self._run_rostered(self.MURPHYS, self.MURPHY_PROJ, rostered={"1", "2"})

    def test_prior_follows_the_player_across_a_key_flip_in_both_directions(self):
        """The sync-to-sync prior blend (0.6 fresh + 0.4 last mean) is looked up by pid, so a
        player whose key flips 'Name (pid)' <-> 'Name' as his roster status changes carries
        his OWN history and never inherits the other same-name player's."""
        # sync 1: neither rostered -> suffixed keys, pids stored
        with self.assertLogs(level="WARNING"):
            first = self._run_rostered(self.MURPHYS, self.MURPHY_PROJ, rostered=set())
        self.assertEqual(first["Byron Murphy (1)"]["mean"], 7.0)
        # sync 2: the CB is rostered -> plain key; prior must be HIS 7.0, not the DL's 6.5
        with self.assertLogs(level="WARNING"):
            second = self._run_rostered(self.MURPHYS, self.MURPHY_PROJ, rostered={"1"}, existing=first)
        self.assertAlmostEqual(second["Byron Murphy"]["mean"], round(0.6 * 7.0 + 0.4 * 7.0, 2))
        self.assertAlmostEqual(second["Byron Murphy (2)"]["mean"], round(0.6 * 6.5 + 0.4 * 6.5, 2))
        # sync 3: CB dropped -> back to suffixed; history still his
        proj3 = {"1": {"stats": {"idp_tkl_solo": 9.0}}, "2": {"stats": {"idp_tkl_solo": 6.5}}}
        with self.assertLogs(level="WARNING"):
            third = self._run_rostered(self.MURPHYS, proj3, rostered=set(), existing=second)
        self.assertAlmostEqual(third["Byron Murphy (1)"]["mean"], round(0.6 * 9.0 + 0.4 * second["Byron Murphy"]["mean"], 2))

    def test_legacy_pidless_file_never_lends_a_colliding_name_its_history(self):
        """The committed baselines file predates player_id and holds the SEA DL under the
        plain 'Byron Murphy'. A newly rostered CB must not be blended with it: fresh-only for
        this one sync, with a WARNING; a non-colliding name still gets its legacy prior."""
        db = dict(self.MURPHYS, **{"3": {"first_name": "Solo", "last_name": "Player", "position": "WR", "team": "DET"}})
        proj = dict(self.MURPHY_PROJ, **{"3": {"stats": {"rush_yd": 100.0}}})
        legacy = {"Byron Murphy": {"pos": "DL", "mean": 6.64, "team": "SEA"},
                  "Solo Player": {"pos": "WR", "mean": 12.0, "team": "DET"}}
        with self.assertLogs(level="WARNING") as logs:
            out = self._run_rostered(db, proj, rostered={"1"}, existing=legacy)
        self.assertEqual(out["Byron Murphy"]["mean"], 7.0, "CB inherited the DL's stored mean")
        self.assertTrue(any("PRIOR SKIPPED" in m for m in logs.output))
        self.assertAlmostEqual(out["Solo Player"]["mean"], round(0.6 * 10.0 + 0.4 * 12.0, 2))

    def test_weekly_player_scores_use_the_same_collision_keys_as_baselines(self):
        matchups = [{"roster_id": 1, "players_points": {"1": 8.0, "2": 4.0}}]
        with self.assertLogs(level="WARNING"):
            scores = sync._extract_weekly_player_scores(matchups, self.MURPHYS, rostered_pids={"1"})
        self.assertEqual(scores, {"Byron Murphy": 8.0, "Byron Murphy (2)": 4.0})

    def test_rostered_player_with_no_projection_is_loud(self):
        """Regression guard for Phase 3 finding 6 / inventory P5. A rostered player whose
        projection totals 0 (today: Jordyn Tyson, WR/NO, present in the payload with an empty
        stats block) is skipped. The engine then aborts unless the name is hand-typed into
        KNOWN_MISSING_ASSETS. The drop used to be silent; it now warns, naming the player,
        the whitelist, and the team the entry must carry."""
        db = {"1": {"first_name": "Jordyn", "last_name": "Tyson", "position": "WR", "team": "NO"}}
        live = {"T": [{"name": "Jordyn Tyson", "pos": "WR", "team": "NO"}]}
        saved, fake_save = _capture_saves()
        weekly = MagicMock()
        weekly.status_code = 200
        weekly.json.return_value = {"1": {"stats": {"pass_yd": 0.0}}}
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync.os.path, "exists", return_value=False), \
             patch.object(sync.requests, "get", return_value=weekly), \
             patch.object(sync, "fetch_espn_projections", return_value={}), \
             self.assertLogs(level="WARNING"):
            sync.generate_player_baselines(self.SCORING, db, live, "2026", 1)

    def test_known_missing_asset_matches_the_player_database_and_roster(self):
        """Regression guard for Phase 3 finding 6 (data). The whitelist is the engine's only
        baseline for a player Sleeper publishes no projection for, and it is hand-typed. Its
        team and position must agree with Sleeper's record -- committed in data/ as both the
        player cache and the roster file -- because the engine reads them for the real-NFL
        position group and the pass-catcher ranking. Jordyn Tyson's entry said 'FA' while
        both files said NO; corrected. This test makes the next drift a red test, not a
        manual audit."""
        import json
        from fantasy_sim.simulation import normalize_position
        if not os.path.exists(PLAYER_CACHE_FILE) or not os.path.exists(LIVE_ROSTERS_FILE):
            self.skipTest("committed data files not present")
        cache = json.load(open(PLAYER_CACHE_FILE))
        rosters = json.load(open(LIVE_ROSTERS_FILE))
        rostered = {p["name"]: p for team in rosters.values() for p in team}
        checked = 0
        for name, entry in SIM_CONFIG["KNOWN_MISSING_ASSETS"].items():
            first, last = name.split(" ", 1)
            real = [p for p in cache.values() if isinstance(p, dict)
                    and p.get("first_name") == first and p.get("last_name") == last]
            for source, rec in (("cache", real[0] if real else None), ("roster", rostered.get(name))):
                if rec is None:
                    continue
                checked += 1
                self.assertEqual(entry["team"], rec.get("team") or "FA",
                                 "%s: whitelist team %r, %s says %r"
                                 % (name, entry["team"], source, rec.get("team")))
                self.assertEqual(normalize_position(entry["pos"]),
                                 normalize_position(rec.get("position") or rec.get("pos")),
                                 "%s: whitelist pos %r, %s says %r"
                                 % (name, entry["pos"], source, rec.get("position") or rec.get("pos")))
        self.assertGreater(checked, 0, "no whitelisted player could be cross-checked")

    def test_engine_warns_when_a_whitelisted_team_disagrees_with_the_roster(self):
        """Regression guard for Phase 3 finding 6 (runtime). The engine consults the roster
        at imputation time and says so when the hand-typed entry disagrees, so the drift is
        visible on every run, not only when someone re-audits config.py."""
        from fantasy_sim.simulation import FantasySimulationEngine
        fs = {
            LEAGUE_STATE_FILE: {"current_week": 1},
            LEAGUE_STANDINGS_FILE: {"T": {"remaining_faab": 100}},
            # stamped for the current week so the Vegas staleness check stays silent and the
            # only WARNING that can appear is the whitelist one under test
            VEGAS_FILE: {"_meta": {"week": 1, "source": "odds_api", "fetched_at": "x"}},
            TEAM_RATINGS_FILE: {}, DEFENSIVE_RATINGS_FILE: {},
            DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
            LEAGUE_SCHEDULE_FILE: [], NFL_SCHEDULE_FILE: {}, WEEKLY_ACTUALS_FILE: {},
            LIVE_ROSTERS_FILE: {"T": [{"name": "Ghost Player", "pos": "WR", "team": "NO"}]},
            BASELINES_FILE: {},
        }
        entry = {"mean": 6.5, "std_aleatoric": 3.0, "std_epistemic": 1.2, "pos": "WR", "team": "FA", "bye": 0}
        with patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]), \
             patch.dict(SIM_CONFIG["KNOWN_MISSING_ASSETS"], {"Ghost Player": entry}, clear=True), \
             self.assertLogs(level="WARNING") as logs:
            engine = FantasySimulationEngine()
        self.assertTrue(any("KNOWN_MISSING_ASSETS" in m and "'FA'" in m and "'NO'" in m for m in logs.output),
                        logs.output)
        # the entry is used as written (the fix is in config.py, not a silent override) ...
        self.assertEqual(engine.baselines["Ghost Player"]["team"], "FA")
        # ... and an agreeing entry is silent.
        entry_ok = dict(entry, team="NO")
        with patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]), \
             patch.dict(SIM_CONFIG["KNOWN_MISSING_ASSETS"], {"Ghost Player": entry_ok}, clear=True), \
             _no_logs(self):
            FantasySimulationEngine()

    def test_espn_blend_is_applied_when_a_match_exists(self):
        """Passes -- locks the blend path the live measurement relied on (97% of rostered
        eligible players matched on 2026-08-28; see findings)."""
        db = {"1": {"first_name": "Justin", "last_name": "Jefferson", "position": "WR", "team": "MIN"}}
        out, _ = self._run(db, {"1": {"stats": {"rush_yd": 140.0}}}, espn={"justin jefferson": 16.0})
        self.assertAlmostEqual(out["Justin Jefferson"]["mean"], 15.0)
        self.assertAlmostEqual(out["Justin Jefferson"]["std_epistemic"],
                               round(max(EPISTEMIC_ERROR_RATES["WR"] * 15.0, 1.0), 2))


# ---------------------------------------------------------------------- player cache
class TestPlayerCacheFreshness(unittest.TestCase):
    def test_cache_is_refreshed_when_stale(self):
        """Regression guard for Phase 3 finding 7. update_player_cache used to fetch once
        and read the file forever: no age check, no force path. Team, position and injury
        fields drift from the day the file was written (late-August cuts and trades are
        exactly when they move most); the live comparison on 2026-08-28 found a one-day-old
        cache already differing from Sleeper on a rostered player's injury_status. The cache
        is now refreshed past PLAYER_CACHE_MAX_AGE_SECONDS (one day) or on force=True, and a
        failed refresh serves the stale file loudly rather than crashing."""
        from fantasy_sim.clients import sleeper
        fetched = []
        r = MagicMock()
        r.json.return_value = {"1": {"first_name": "X"}}
        old = datetime(2026, 7, 1).timestamp()
        with patch.object(sleeper.os.path, "exists", return_value=True), \
             patch.object(sleeper.os.path, "getmtime", return_value=old, create=True), \
             patch.object(sleeper, "load_json", return_value={"1": {"first_name": "stale"}}), \
             patch.object(sleeper, "save_json", side_effect=lambda p, d: fetched.append(d)), \
             patch.object(sleeper.requests, "get", return_value=r):
            out = sleeper.update_player_cache()
        self.assertTrue(fetched, "a two-month-old cache was served without any refresh")


# ------------------------------------------------------------------ defensive ratings
class TestDefensiveRatingShrinkage(unittest.TestCase):
    """The plan's question: does the n_0 shrinkage behave as claimed as games_sampled grows?
    It does -- the arithmetic is the standard pseudo-count form. Whether n_0 = 4 is the
    right count is the bounded joint piece (findings, § The n_0 decision)."""

    def _ratings(self, samples_by_team):
        results = [(t, p) for t, s in samples_by_team.items() for p in s]
        saved, fake_save = _capture_saves()
        with patch.object(sync, "save_json", side_effect=fake_save):
            ratings, _tiers = sync.generate_defensive_ratings(results)
        return ratings

    def test_no_games_returns_the_prior_and_weight_on_data_is_n_over_n_plus_n0(self):
        prior = PRESEASON_DEFENSIVE_PRIOR["DET"]
        self.assertAlmostEqual(self._ratings({})["DET"]["points_allowed_estimate"], prior, places=2)
        for n in (1, 2, 4, 8, 17):
            est = self._ratings({"DET": [30.0] * n})["DET"]["points_allowed_estimate"]
            w = n / (n + DEF_RATING_SHRINKAGE_N0)
            self.assertAlmostEqual(est, prior + w * (30.0 - prior), places=2)

    def test_estimate_is_monotone_in_games_and_bounded_by_prior_and_data(self):
        prior = PRESEASON_DEFENSIVE_PRIOR["DET"]
        prev = prior
        for n in range(1, 18):
            est = self._ratings({"DET": [30.0] * n})["DET"]["points_allowed_estimate"]
            self.assertGreaterEqual(est, prev - 1e-9)
            self.assertLessEqual(est, 30.0)
            prev = est

    def test_prior_fallback_is_on_the_same_scale_as_the_prior_table(self):
        """Regression guard for Phase 3 finding 8. A team missing from PRESEASON_DEFENSIVE_PRIOR
        used to fall back to LEAGUE_AVG_PPG = 21.5 while the table averages ~22.8 (and real
        2025 points allowed 23.0), ranking the missing team as an above-average defence by
        construction. The fallback is now the table's own mean -- derived, not another
        hardcoded constant -- and is announced. LEAGUE_AVG_PPG remains the fallback only when
        the table is empty (test_sync covers that path)."""
        table = dict(PRESEASON_DEFENSIVE_PRIOR)
        table.pop("ARI")
        table_mean = sum(table.values()) / len(table)
        saved, fake_save = _capture_saves()
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync, "PRESEASON_DEFENSIVE_PRIOR", table), \
             self.assertLogs(level="WARNING") as logs:
            ratings, _ = sync.generate_defensive_ratings([])
        self.assertAlmostEqual(ratings["ARI"]["points_allowed_estimate"], table_mean, places=2)
        self.assertNotAlmostEqual(ratings["ARI"]["points_allowed_estimate"], LEAGUE_AVG_PPG, places=1)
        self.assertTrue(any("ARI" in m for m in logs.output))


# ---------------------------------------------------------------------- bye weeks (step 1)
class TestByeWeekDerivation(unittest.TestCase):
    """Bye modelling, step 1: the data source. Sleeper's payload has no bye field (Phase 1
    finding 7) and ESPN's team endpoint returns None; the scoreboard pairings sync already
    fetches make it derivable. On the committed 2026 schedule every team is absent from
    exactly one week in 5-14 (32/32), and the same holds for 2025."""

    def _schedule(self, absent_by_week):
        from fantasy_sim.config import NFL_TEAMS
        sched = {}
        for w in range(1, 19):
            playing = [t for t in NFL_TEAMS if t not in absent_by_week.get(w, ())]
            sched[str(w)] = {t: playing[(i + 1) % len(playing)] for i, t in enumerate(playing)}
        return sched

    def test_bye_is_the_one_week_a_team_appears_in_no_pairing(self):
        from fantasy_sim.config import derive_bye_weeks
        byes = derive_bye_weeks(self._schedule({6: ("DET", "MIN"), 9: ("KC",)}))
        self.assertEqual(byes["DET"], 6)
        self.assertEqual(byes["MIN"], 6)
        self.assertEqual(byes["KC"], 9)
        self.assertNotIn("BUF", byes)

    def test_a_failed_fetch_week_is_not_a_bye(self):
        """Week 7 failed (empty) AND DET is genuinely off in week 6: without the exclusion
        DET would be absent from two weeks and get no bye; with it, week 6 stands."""
        from fantasy_sim.config import derive_bye_weeks
        sched = self._schedule({6: ("DET",)})
        sched["7"] = {}
        self.assertNotIn("DET", derive_bye_weeks(sched))            # ambiguous without the record
        self.assertEqual(derive_bye_weeks(sched, failed_weeks=[7])["DET"], 6)

    def test_ambiguous_teams_get_no_bye_rather_than_a_guess(self):
        from fantasy_sim.config import derive_bye_weeks
        byes = derive_bye_weeks(self._schedule({6: ("DET",), 11: ("DET",)}))
        self.assertNotIn("DET", byes)

    def test_committed_2026_schedule_yields_one_bye_per_team(self):
        from fantasy_sim.config import NFL_TEAMS, derive_bye_weeks
        if not os.path.exists(NFL_SCHEDULE_FILE):
            self.skipTest("no synced schedule on disk")
        import json
        sched = json.load(open(NFL_SCHEDULE_FILE))
        byes = derive_bye_weeks(sched, sched.get("_meta", {}).get("failed_weeks", []))
        self.assertEqual(sorted(byes), sorted(NFL_TEAMS))
        self.assertTrue(all(5 <= w <= 14 for w in byes.values()), byes)

    def test_generate_nfl_schedule_records_byes_and_warns_on_the_underivable(self):
        saved, fake_save = _capture_saves()

        def scoreboard(url, timeout=5):
            wk = int(url.split("week=")[1].split("&")[0])
            m = MagicMock()
            m.status_code = 200
            # DET plays CHI every week except 6 (both absent); everyone else absent always
            m.json.return_value = {"events": []} if wk == 6 else {"events": [{"competitions": [{
                "competitors": [{"team": {"abbreviation": "DET"}, "score": "0"}, {"team": {"abbreviation": "CHI"}, "score": "0"}],
                "status": {"type": {"completed": False}}}]}]}
            return m
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync.requests, "get", side_effect=scoreboard), \
             self.assertLogs(level="WARNING") as logs:
            sync.generate_nfl_schedule(1)
        meta = saved[os.path.basename(NFL_SCHEDULE_FILE)]["_meta"]
        self.assertEqual(meta["byes"], {"DET": 6, "CHI": 6})
        self.assertTrue(any("no single bye week derivable" in m for m in logs.output))

    def test_baselines_carry_the_schedule_bye(self):
        db = {"1": {"first_name": "Amon-Ra", "last_name": "St. Brown", "position": "WR", "team": "DET"},
              "2": {"first_name": "Free", "last_name": "Agent", "position": "WR", "team": None}}
        proj = {"1": {"stats": {"rush_yd": 100.0}}, "2": {"stats": {"rush_yd": 50.0}}}
        saved, fake_save = _capture_saves()
        weekly = MagicMock()
        weekly.status_code = 200
        weekly.json.return_value = proj
        with patch.object(sync, "save_json", side_effect=fake_save), \
             patch.object(sync.os.path, "exists", return_value=False), \
             patch.object(sync.requests, "get", return_value=weekly), \
             patch.object(sync, "fetch_espn_projections", return_value={}):
            out = sync.generate_player_baselines({"rush_yd": 0.1}, db, {}, "2026", 1, byes={"DET": 6})
        self.assertEqual(out["Amon-Ra St. Brown"]["bye"], 6)
        self.assertEqual(out["Free Agent"]["bye"], 0)


# ------------------------------------------------------------------------------ live
@unittest.skipUnless(os.environ.get("RUN_LIVE_INGESTION_TESTS") == "1",
                     "set RUN_LIVE_INGESTION_TESTS=1 to hit Sleeper/ESPN")
class TestLiveIngestion(unittest.TestCase):
    def test_espn_match_rate_for_rostered_eligible_players(self):
        """Measured 97% (116/119) on 2026-08-28; the three misses were players ESPN had no
        week-1 projection for at all, not normalisation failures."""
        import json
        from fantasy_sim.clients.espn import fetch_espn_projections, normalize_player_name_for_matching as norm
        from fantasy_sim.config import ESPN_BLEND_ELIGIBLE_POSITIONS
        from fantasy_sim.simulation import normalize_position
        rosters = json.load(open(LIVE_ROSTERS_FILE))
        espn = fetch_espn_projections(2026, 1)
        elig = [p["name"] for t in rosters.values() for p in t
                if normalize_position(p["pos"]) in ESPN_BLEND_ELIGIBLE_POSITIONS]
        rate = sum(1 for n in elig if norm(n) in espn) / max(1, len(elig))
        self.assertGreaterEqual(rate, 0.90, "ESPN match rate %.0f%%" % (100 * rate))


if __name__ == "__main__":
    unittest.main()



class TestInjuryStatusIngestion(unittest.TestCase):
    """F4 step 1 guards (were characterisation). Sleeper's player record has `injury_status`
    (IR / PUP / Out / Doubtful / Questionable / ...) and the league roster payload has a
    `reserve` list (the fantasy IR slot); both now reach live_rosters.json and the baselines,
    additively. The engine does not read them yet (step 2)."""

    def test_roster_entry_carries_the_players_injury_status(self):
        db = {"7640": {"first_name": "Micah", "last_name": "Parsons", "position": "LB", "team": "GB",
                       "injury_status": "PUP", "status": "Active"}}
        entry = sync._build_roster_player_entry("7640", db)
        self.assertEqual(entry.get("injury_status"), "PUP", msg="entry is %r" % (entry,))

    def test_roster_entry_marks_the_league_ir_slot_regardless_of_status(self):
        """`on_ir` follows the roster payload's `reserve` list, not the medical status: a
        Questionable player a manager parked on IR is out (accepted cost, AUDIT_PLAN F4)."""
        db = {"8142": {"first_name": "Alec", "last_name": "Pierce", "position": "WR", "team": "IND",
                       "injury_status": "Questionable"},
              "1": {"first_name": "Healthy", "last_name": "Guy", "position": "RB", "team": "SEA",
                    "injury_status": None}}
        on = sync._build_roster_player_entry("8142", db, reserve_pids={"8142"})
        off = sync._build_roster_player_entry("1", db, reserve_pids={"8142"})
        self.assertEqual((on["on_ir"], on["injury_status"]), (True, "Questionable"))
        self.assertEqual((off["on_ir"], off["injury_status"]), (False, None))
        self.assertFalse(sync._build_roster_player_entry("8142", db)["on_ir"], "default: not on IR")

    def test_baselines_carry_injury_status_and_on_ir(self):
        db = {"7640": {"first_name": "Micah", "last_name": "Parsons", "position": "LB", "team": "GB", "injury_status": "PUP"},
              "1": {"first_name": "Healthy", "last_name": "Guy", "position": "RB", "team": "SEA", "injury_status": None}}
        proj = {"7640": {"stats": {"rush_yd": 100.0}}, "1": {"stats": {"rush_yd": 50.0}}}
        saved, fake_save = _capture_saves()
        weekly = MagicMock()
        weekly.status_code = 200
        weekly.json.return_value = proj
        with patch.object(sync, "save_json", side_effect=fake_save):
            with patch.object(sync.os.path, "exists", return_value=False):
                with patch.object(sync.requests, "get", return_value=weekly):
                    with patch.object(sync, "fetch_espn_projections", return_value={}):
                        out = sync.generate_player_baselines({"rush_yd": 0.1}, db, {}, "2026", 1,
                                                             byes={"GB": 5}, reserve_pids={"7640"})
        mp, hg = out["Micah Parsons"], out["Healthy Guy"]
        self.assertEqual((mp["injury_status"], mp["on_ir"], mp["bye"]), ("PUP", True, 5))
        self.assertEqual((hg["injury_status"], hg["on_ir"]), (None, False))

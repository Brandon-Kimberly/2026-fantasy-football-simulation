"""
tests.test_weekly_report -- the weekly orchestrator (scripts/weekly_report.py, logic in
fantasy_sim.weekly_report). Written before the module existed.

Hard requirement under test: fail LOUD on any step failure and never run a downstream step on
stale or partial data -- the sync gate refuses unless a manifest from THIS run exists (written
last by sync_all, so its presence means completion), the simulation gate refuses unless the
week's export is newer than the step's start, and the runner stops at the first exception,
writing a digest with a FAILED banner and no downstream sections.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from fantasy_sim.weekly_report import (
    run_steps, gate_sync_fresh, gate_export_fresh, render_digest, StepFailed,
)


class TestRunner(unittest.TestCase):
    def test_runs_in_order_collects_results_and_reports_ok(self):
        calls = []
        steps = [("a", lambda: calls.append("a") or {"x": 1}), ("b", lambda: calls.append("b") or {"y": 2})]
        report = run_steps(steps)
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(report["status"], "OK")
        self.assertEqual(report["results"], {"a": {"x": 1}, "b": {"y": 2}})
        self.assertIsNone(report["failed_step"])

    def test_stops_at_the_first_failure_and_never_runs_downstream_steps(self):
        calls = []

        def boom():
            raise RuntimeError("Sleeper 503")
        steps = [("sync", lambda: calls.append("sync") or {"ok": True}), ("simulation", boom),
                 ("lineup", lambda: calls.append("lineup") or {})]
        report = run_steps(steps)
        self.assertEqual(calls, ["sync"], "nothing after the failing step may run")
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["failed_step"], "simulation")
        self.assertIn("Sleeper 503", report["error"])
        self.assertIn("sync", report["results"]); self.assertNotIn("lineup", report["results"])

    def test_a_gate_failure_is_a_step_failure(self):
        def gate():
            raise StepFailed("sync did not complete: no fresh manifest")
        report = run_steps([("sync", lambda: {}), ("sync gate", gate), ("simulation", lambda: {})])
        self.assertEqual(report["status"], "FAILED"); self.assertEqual(report["failed_step"], "sync gate")
        self.assertNotIn("simulation", report["results"])


class TestGates(unittest.TestCase):
    def test_sync_gate_requires_a_manifest_from_this_run(self):
        now = 2_000_000.0
        with patch("fantasy_sim.weekly_report.read_manifest", return_value=(None, None)):
            with self.assertRaises(StepFailed):
                gate_sync_fresh(run_started=now)
        stale = ({"ok": True, "current_week": 1, "started_at": "x"}, now - 3600)
        with patch("fantasy_sim.weekly_report.read_manifest", return_value=stale):
            with self.assertRaises(StepFailed):
                gate_sync_fresh(run_started=now)
        fresh = ({"ok": True, "current_week": 3, "started_at": "x", "degraded": []}, now + 5)
        with patch("fantasy_sim.weekly_report.read_manifest", return_value=fresh):
            m = gate_sync_fresh(run_started=now)
        self.assertEqual(m["current_week"], 3)

    def test_export_gate_requires_the_weeks_export_newer_than_the_step_start(self):
        with patch("fantasy_sim.weekly_report.read_export_mtime", return_value=None):
            with self.assertRaises(StepFailed):
                gate_export_fresh(week=3, step_started=100.0)
        with patch("fantasy_sim.weekly_report.read_export_mtime", return_value=50.0):
            with self.assertRaises(StepFailed):
                gate_export_fresh(week=3, step_started=100.0)
        with patch("fantasy_sim.weekly_report.read_export_mtime", return_value=150.0):
            gate_export_fresh(week=3, step_started=100.0)


def _fixture_results():
    return {
        "sync": {"manifest": {"current_week": 3, "finished_at": "2026-09-22T12:00:00Z",
                              "degraded": ["WARNING | ODDS: no key"], "notices_count": 4}},
        "simulation": {"season_outcomes": [
            {"Team": "Legion of Coom", "Playoff_Pct": 61.0, "Champ_Pct": 15.5, "Expected_Wins": 15.1, "Expected_Points": 2300.0},
            {"Team": "Femboy Cats", "Playoff_Pct": 50.0, "Champ_Pct": 12.0, "Expected_Wins": 14.0, "Expected_Points": 2250.0}]},
        "league": {"week": 3, "n": 5000, "cross": True,
                   "matchups": [{"a": "Legion of Coom", "b": "Canton Killers", "p_a": 0.588, "p_b": 0.41, "p_tie": 0.002, "se": 0.007,
                                 "a_expected": 166.4, "b_expected": 153.4, "margin_mean": 13.0, "margin_sd": 50.3}],
                   "teams": {"Legion of Coom": {"opponent": "Canton Killers", "p_beat_median": 0.575, "expected_total": 166.4, "expected_pre_total": 177.3, "sd_total": 37.9,
                                                "lineup": [{"slot": "QB", "name": "Jayden Daniels", "expected": 16.1, "sd": 8.7, "nfl_team": "WAS"}]},
                             "Canton Killers": {"opponent": "Legion of Coom", "p_beat_median": 0.41, "expected_total": 153.4, "expected_pre_total": 164.0, "sd_total": 36.0,
                                                "lineup": [{"slot": "QB", "name": "Jalen Hurts", "expected": 25.0, "sd": 9.0, "nfl_team": "PHI"}]}}},
        "roster_grades": {"league": {"teams": [{"rank": 1, "team": "Legion of Coom", "lineup_vorp": 25.9, "depth_vorp": 0.0, "optimal_score": 169.7, "holes": 0, "tier1_starters": 11, "starters_below_replacement": 1},
                                               {"rank": 2, "team": "Femboy Cats", "lineup_vorp": 20.0, "depth_vorp": 1.0, "optimal_score": 165.0, "holes": 0, "tier1_starters": 9, "starters_below_replacement": 2}]},
                          "team_detail": {"by_position": {"RB": {"n_starters": 3, "n_bench": 4, "starters_vorp": 14.2, "depth_vorp": 0.0, "tiers": [1, 1, 1], "best_free_agent": None}}}},
        "lineup": {"expected_total": 177.3, "unfilled": [],
                   "lineup": [{"slot": "QB", "name": "Jayden Daniels", "pos": "QB", "expected": 16.1, "p10": 5.7, "p50": 14.1, "p90": 26.6, "p_zero": 0.05, "margin": 3.0, "alternative": "QB_backup"}],
                   "bench": [{"name": "QB_backup", "pos": "QB", "expected": 13.1, "reason": ""}]},
        "matchup": {"opponent": "Canton Killers", "favoured_by_max_mean": True, "cross": True, "n": 5000,
                    "ranking_by_p_beat_opponent": ["max_mean", "safe", "stack", "p_max"],
                    "constructions": {k: {"mean": 166.4, "sd": 37.9, "p_beat_opponent": 0.602, "se": 0.007, "p_beat_median": 0.575,
                                          "margin_mean": 13.0, "margin_sd": 50.3,
                                          "lineup": [{"slot": "QB", "name": "Jayden Daniels", "expected": 16.1, "sd": 8.7, "nfl_team": "WAS"}]}
                                      for k in ("max_mean", "safe", "stack", "p_max")},
                    "opponent_lineup": [{"slot": "QB", "name": "Jalen Hurts", "expected": 25.0}], "opponent_lineup_assumed": True,
                    "note": "joint sample"},
        "waivers": {"holes": [], "holes_next_week": [], "remaining_faab": 100.0, "league_avg_faab": 99.0,
                    "targets": [{"name": "Tuli Tuipulotu", "pos": "DL", "tier": 1, "mean": 10.5, "vorp": 2.1, "fills": "upgrade",
                                 "week": {"mean": 14.4, "p10": 6.0, "p50": 13.3, "p90": 23.4, "p_zero": 0.03},
                                 "bid": {"suggested": 8, "typical_manager_model": 1.0}, "incumbent": "Danielle Hunter",
                                 "p_beats_incumbent": {"p": 0.78}}], "caveat": "independent draws"},
    }


class TestDigest(unittest.TestCase):
    def test_ok_digest_has_every_section_and_the_degraded_block(self):
        report = {"status": "OK", "failed_step": None, "error": None, "results": _fixture_results(),
                  "started_at": "2026-09-22T12:00:00Z", "finished_at": "2026-09-22T12:12:00Z"}
        md = render_digest(report, team="Legion of Coom", week=3)
        for needle in ("# Weekly report", "week 3", "DEGRADED", "ODDS: no key", "## Season outlook", "61.0",
                       "## League this week", "Legion of Coom v Canton Killers", "58.8", "Jalen Hurts",
                       "## Roster grade", "25.9", "## Lineup", "Jayden Daniels", "## Matchup", "Canton Killers", "60.2",
                       "## Waiver targets", "Tuli Tuipulotu", "no variance lever"):
            self.assertIn(needle, md, needle)
        self.assertNotIn("## Trade targets", md)
        self.assertNotIn("FAILED", md)

    def test_failed_digest_has_the_banner_and_no_downstream_sections(self):
        results = {"sync": _fixture_results()["sync"]}
        report = {"status": "FAILED", "failed_step": "simulation", "error": "ValueError: CRITICAL ABORT: 2 rostered players lack projections",
                  "results": results, "started_at": "x", "finished_at": "y"}
        md = render_digest(report, team="Legion of Coom", week=3)
        self.assertIn("FAILED AT STEP `simulation`", md)
        self.assertIn("CRITICAL ABORT", md)
        for absent in ("## Lineup", "## Matchup", "## Waiver targets", "## Roster grade", "## Season outlook", "## League this week"):
            self.assertNotIn(absent, md)
        self.assertIn("did not run", md)

    def test_trade_section_present_when_full(self):
        results = _fixture_results()
        results["trades"] = {"buy": [{"with": "Femboy Cats", "target": "X", "target_mean": 10.0, "buried_behind": "Y", "fills_my_slot": "LB",
                                      "i_give": ["A"], "i_get": ["X"], "my_gain": 0.5, "their_gain": 0.2, "acceptable": True,
                                      "their_playoff_pct": 50.0, "seller": False, "willingness": 0.85}],
                             "sell": [], "contention_note": "seller = ..."}
        report = {"status": "OK", "failed_step": None, "error": None, "results": results, "started_at": "x", "finished_at": "y"}
        md = render_digest(report, team="Legion of Coom", week=3)
        self.assertIn("## Trade targets", md); self.assertIn("Femboy Cats", md)

    def test_digest_is_written_to_the_given_path(self):
        report = {"status": "OK", "failed_step": None, "error": None, "results": _fixture_results(), "started_at": "x", "finished_at": "y"}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "weekly_report.md")
            from fantasy_sim.weekly_report import write_digest
            write_digest(render_digest(report, "Legion of Coom", 3), path)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                self.assertIn("# Weekly report", f.read())


if __name__ == "__main__":
    unittest.main()

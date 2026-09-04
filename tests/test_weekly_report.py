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
    html_table, render_html, build_steps,
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


class TestHousekeepingReminder(unittest.TestCase):
    def test_unevaluated_trades_are_listed_in_both_formats(self):
        report = {"status": "OK", "failed_step": None, "error": None, "results": _fixture_results(),
                  "started_at": "x", "finished_at": "y",
                  "housekeeping": {"unevaluated_trades": [
                      {"transaction_id": "123", "week": 2, "teams": ["Legion of Coom", "Femboy Cats"]}]}}
        md = render_digest(report, team="Legion of Coom", week=3)
        self.assertIn("Housekeeping", md)
        self.assertIn("scripts.evaluate_trade --log-tx 123", md)
        with patch("fantasy_sim.weekly_report.os.path.exists", return_value=True):
            html = render_html(report, team="Legion of Coom", week=3)
        self.assertIn("--log-tx 123", html)

    def test_no_reminder_when_nothing_is_pending(self):
        report = {"status": "OK", "failed_step": None, "error": None, "results": _fixture_results(),
                  "started_at": "x", "finished_at": "y", "housekeeping": {"unevaluated_trades": []}}
        self.assertNotIn("Housekeeping", render_digest(report, team="Legion of Coom", week=3))


class TestHtmlTable(unittest.TestCase):
    """Reuses positional_tiers' sortable-table pattern: every header carries data-key/data-type,
    every cell data-sort (numbers by value, text lower-cased), and the page carries the sorter."""

    def test_headers_cells_and_types(self):
        h = html_table(["player", "exp", "P(win)"], [["Jayden Daniels", 16.1, "60.2%"], ["Zed", 9.0, "5.0%"]],
                       types=["text", "number", "number"], sort_keys=[["jayden daniels", 16.1, 60.2], ["zed", 9.0, 5.0]])
        self.assertIn('<th data-key="exp" data-type="number">exp</th>', h)
        # numeric cells carry class="num" (the tier table's convention), text cells do not
        self.assertIn('<td class="num" data-sort="16.1"', h); self.assertIn('<td class="num" data-sort="60.2"', h)
        self.assertIn('<td data-sort="jayden daniels"', h)
        self.assertIn("Jayden Daniels", h); self.assertIn("60.2%", h)

    def test_types_are_inferred_when_not_given(self):
        h = html_table(["a", "b"], [["x", 1.5], ["y", 2.5]])
        self.assertIn('data-type="text">a', h); self.assertIn('data-type="number">b', h)

    def test_placeholder_dash_does_not_detype_a_numeric_column(self):
        """The real report's numeric columns carry '-' placeholders (no bench alternative, no
        opponent) -- under all-or-nothing typing one placeholder detyped the whole column to
        text: left-aligned, non-tabular, lexically sorted. Written failing against that
        behaviour: neutral cells ('-' or empty) must not decide the type, must still align
        with the numbers (class num), and must sort to the numeric bottom."""
        h = html_table(["player", "margin"], [["A", "+1.2"], ["B", "-"], ["C", "-0.8"]])
        self.assertIn('data-type="number">margin', h)
        self.assertIn('class="num" data-sort="-1e999">-<', h)   # neutral: aligned, sinks in sort

    def test_signed_cols_color_only_opted_in_columns(self):
        """Sign-carrying semantic color is OPT-IN by column header, never blanket sign-sniffing:
        '+' cells get pos, '-' cells get neg, unsigned and zero cells stay plain, and a column
        not named in signed_cols never gains either class even when its cells carry signs."""
        h = html_table(["player", "margin", "sd"], [["A", "+1.2", "+3.0"], ["B", "-0.8", "2.0"], ["C", "+0.0", "1.0"]],
                       signed_cols=("margin",))
        self.assertIn('class="num pos" data-sort="1.2"', h)
        self.assertIn('class="num neg" data-sort="-0.8"', h)
        self.assertIn('class="num" data-sort="0.0"', h)         # +0.0: signed but zero, no color
        self.assertIn('class="num" data-sort="3.0"', h)         # sd not opted in: plain num


class TestHtmlReport(unittest.TestCase):
    def _ok_report(self):
        return {"status": "OK", "failed_step": None, "error": None, "results": _fixture_results(),
                "started_at": "2026-09-22T12:00:00Z", "finished_at": "2026-09-22T12:12:00Z"}

    def test_ok_html_has_every_section_details_per_team_and_relative_images(self):
        with patch("fantasy_sim.weekly_report.os.path.exists", return_value=True):
            html = render_html(self._ok_report(), team="Legion of Coom", week=3)
        for needle in ("<title>", "Weekly report", "DEGRADED", "ODDS: no key", "League this week", "Season outlook",
                       "Roster grade", "Lineup", "Matchup", "Waiver targets", "no variance lever",
                       "<details>", "<summary>Legion of Coom", "<summary>Canton Killers", "Jalen Hurts",
                       'data-type="number"', "querySelectorAll('th[data-key]')"):
            self.assertIn(needle, html, needle)
        # charts referenced relative to data/decisions/, in the sections proposed
        self.assertIn('src="../weeks/week_03/Season_Outcomes.png"', html)
        self.assertIn('src="../weeks/week_03/All_Teams_Trajectories.png"', html)
        self.assertIn('src="../weeks/week_03/Win_Trajectory.png"', html)
        self.assertIn("boom_bust", html); self.assertIn("floor_ceiling", html)
        self.assertIn('src="../weeks/week_03/tiers/DL', html, "tier chart for a position in the waiver list")
        self.assertIn("Strength_of_Schedule_By_Roster.png", html)
        self.assertNotIn("Trade targets", html)

    def test_missing_chart_is_stated_not_broken(self):
        with patch("fantasy_sim.weekly_report.os.path.exists", return_value=False):
            html = render_html(self._ok_report(), team="Legion of Coom", week=3)
        self.assertNotIn("<img", html)
        self.assertIn("chart not generated", html)

    def test_embed_inlines_images_as_data_uris(self):
        with (patch("fantasy_sim.weekly_report.os.path.exists", return_value=True),
              patch("fantasy_sim.weekly_report._read_bytes", return_value=bytes([137, 80, 78, 71]))):
            html = render_html(self._ok_report(), team="Legion of Coom", week=3, embed=True)
        self.assertIn('src="data:image/png;base64,', html)
        self.assertNotIn('src="../weeks/', html)

    def test_failed_html_has_the_banner_and_no_downstream_sections(self):
        report = {"status": "FAILED", "failed_step": "simulation", "error": "ValueError: CRITICAL ABORT",
                  "results": {"sync": _fixture_results()["sync"]}, "started_at": "x", "finished_at": "y",
                  "planned": ["sync", "simulation", "lineup"]}
        with patch("fantasy_sim.weekly_report.os.path.exists", return_value=True):
            html = render_html(report, team="Legion of Coom", week=3)
        self.assertIn("FAILED AT STEP", html); self.assertIn("CRITICAL ABORT", html); self.assertIn("DEGRADED", html)
        for absent in ("League this week", "Season outlook", "Roster grade", 'id="lineup"', "Matchup", "Waiver targets"):
            self.assertNotIn(absent, html)


class TestChain(unittest.TestCase):
    def test_default_chain_order_includes_the_three_chart_steps_after_the_simulation(self):
        steps, _ = build_steps("Legion of Coom")
        self.assertEqual([n for n, _ in steps],
                         ["sync", "simulation", "positional_tiers", "strength_of_schedule", "win_trajectory",
                          "league", "predictions_log", "roster_grades", "lineup", "matchup", "waivers"])
        steps, _ = build_steps("Legion of Coom", full=True, skip_sync=True)
        self.assertEqual(steps[0][0], "freshness"); self.assertEqual(steps[-1][0], "trades")


class TestPredictionsLog(unittest.TestCase):
    """The tracked prediction record F18 and F19 need (data/logs/predictions_{season}.jsonl):
    one line per week with the season-outcome table, the week's matchup win probabilities and
    P(>= median), the commit hash and the sync-manifest timestamps. Unlike data/weeks/ it is
    git-tracked, so it survives a machine loss. Written before append_predictions_log existed."""

    OUTCOMES = [{"Team": "Legion of Coom", "Expected_Wins": 15.93, "Expected_Points": 2457.9,
                 "Playoff_Pct": 61.5, "Playoff_SE": 0.49, "Champ_Pct": 20.2, "Toilet_Pct": 7.5},
                {"Team": "Clankers", "Expected_Wins": 14.1, "Expected_Points": 2400.0,
                 "Playoff_Pct": 55.0, "Playoff_SE": 0.5, "Champ_Pct": 15.0, "Toilet_Pct": 9.0}]
    OUTLOOK = {"week": 1, "n": 5000, "cross": True,
               "matchups": [{"a": "Legion of Coom", "b": "Clankers", "p_a": 0.654, "p_b": 0.346,
                             "p_tie": 0.0, "se": 0.007, "a_expected": 174.0, "b_expected": 153.6,
                             "margin_sd": 50.8}],
               "teams": {"Legion of Coom": {"opponent": "Clankers", "p_beat_median": 0.61,
                                            "expected_total": 174.0, "sd_total": 36.0},
                         "Clankers": {"opponent": "Legion of Coom", "p_beat_median": 0.44,
                                      "expected_total": 153.6, "sd_total": 34.0}}}
    MANIFEST = {"season": "2026", "started_at": "2026-09-01T19:44:32Z",
                "finished_at": "2026-09-01T19:44:39Z"}

    def test_one_line_per_append_with_the_fields_f18_and_f19_need(self):
        import json, os, tempfile
        from fantasy_sim.weekly_report import append_predictions_log
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "predictions_2026.jsonl")
            n = append_predictions_log(1, self.OUTCOMES, self.OUTLOOK, path=path,
                                       manifest=self.MANIFEST, commit="abc123")
            self.assertEqual(n, 1)
            with open(path, encoding="utf-8") as f:
                rows = [json.loads(l) for l in f]
        r = rows[0]
        self.assertEqual(r["record_type"], "week_predictions")
        self.assertEqual((r["season"], r["week"]), ("2026", 1))
        self.assertEqual(r["commit"], "abc123")
        self.assertEqual(r["sync_started_at"], "2026-09-01T19:44:32Z")
        self.assertFalse(r["backfilled"])
        teams = {o["Team"]: o for o in r["season_outcomes"]}
        self.assertAlmostEqual(teams["Legion of Coom"]["Champ_Pct"], 20.2)
        self.assertAlmostEqual(teams["Legion of Coom"]["Playoff_Pct"], 61.5)
        self.assertAlmostEqual(teams["Legion of Coom"]["Expected_Wins"], 15.93)
        m = r["matchups"][0]
        self.assertEqual((m["a"], m["b"]), ("Legion of Coom", "Clankers"))
        self.assertAlmostEqual(m["p_a"], 0.654)
        self.assertAlmostEqual(r["median"]["Clankers"]["p_beat_median"], 0.44)
        self.assertEqual(r["outlook_sims"], 5000)

    def test_append_only_a_rerun_appends_again(self):
        import json, os, tempfile
        from fantasy_sim.weekly_report import append_predictions_log
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "predictions_2026.jsonl")
            append_predictions_log(1, self.OUTCOMES, self.OUTLOOK, path=path,
                                   manifest=self.MANIFEST, commit="abc123")
            append_predictions_log(1, self.OUTCOMES, self.OUTLOOK, path=path,
                                   manifest=self.MANIFEST, commit="def456")
            with open(path, encoding="utf-8") as f:
                rows = [json.loads(l) for l in f]
        self.assertEqual(len(rows), 2, "append-only; consumers keep the last row per (season, week)")
        self.assertEqual([r["commit"] for r in rows], ["abc123", "def456"])

    def test_a_write_failure_raises_instead_of_warning(self):
        # Divergence from append_projection_log's warn-never-raise, on purpose: this runs as
        # an orchestrator STEP, and the orchestrator's contract is fail-loud. A silent miss
        # here would be a hole in the F18/F19 record nobody notices until January.
        import os, tempfile
        from fantasy_sim.weekly_report import append_predictions_log
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "not_a_dir_file")
            open(bad, "w").close()
            with self.assertRaises(OSError):
                append_predictions_log(1, self.OUTCOMES, self.OUTLOOK,
                                       path=os.path.join(bad, "x.jsonl"),
                                       manifest=self.MANIFEST, commit=None)

    def test_the_orchestrator_plans_the_step_after_league(self):
        from fantasy_sim.weekly_report import build_steps
        steps, _state = build_steps("Legion of Coom", skip_sync=True)
        names = [n for n, _ in steps]
        self.assertIn("predictions_log", names)
        self.assertGreater(names.index("predictions_log"), names.index("league"),
                           "the step needs the league outlook, so it runs after it")
        self.assertGreater(names.index("predictions_log"), names.index("simulation"))


class TestDecisionsLayout(unittest.TestCase):
    """data/decisions/ layout (F9's shape applied to decisions): weekly artifacts under
    week_NN/ when the run is marked canonical (--canonical: the scheduled Tuesday/Sunday
    runs, or a deliberate re-run after a real roster move) and week_NN/archive/ by default
    (exploratory is the cheap default; marking canonical is a deliberate act). Season
    one-offs under season/, ad-hoc tool output under adhoc/. Intent is a caller flag --
    it is not inferrable from the artifact. Written before the helpers existed."""

    def test_week_helper_routes_on_the_canonical_flag_in_both_directions(self):
        import os
        from fantasy_sim.storage import decisions_week_path
        canon = decisions_week_path(3, "lineup_x.json", canonical=True)
        arch = decisions_week_path(3, "lineup_x.json")
        self.assertTrue(canon.replace(os.sep, "/").endswith("data/decisions/week_03/lineup_x.json"))
        self.assertTrue(arch.replace(os.sep, "/").endswith("data/decisions/week_03/archive/lineup_x.json"))

    def test_season_and_adhoc_helpers(self):
        import os
        from fantasy_sim.storage import decisions_adhoc_path, decisions_season_path
        self.assertTrue(decisions_season_path("draft_review_2026.json").replace(os.sep, "/")
                        .endswith("data/decisions/season/draft_review_2026.json"))
        self.assertTrue(decisions_adhoc_path("move_x.json").replace(os.sep, "/")
                        .endswith("data/decisions/adhoc/move_x.json"))

    def test_rel_is_anchored_to_the_html_files_own_directory(self):
        import os
        from fantasy_sim.storage import decisions_week_path, tier_chart_path
        from fantasy_sim.weekly_report import _rel
        chart = tier_chart_path("DL", 3)
        canon_dir = os.path.dirname(decisions_week_path(3, "x.html", canonical=True))
        arch_dir = os.path.dirname(decisions_week_path(3, "x.html"))
        self.assertEqual(_rel(chart, canon_dir).replace(os.sep, "/"),
                         "../../weeks/week_03/tiers/DL_tiers.png".replace(
                             "DL_tiers.png", os.path.basename(chart)))
        self.assertEqual(_rel(chart, arch_dir).count("../"), 3,
                         "one level deeper from archive/")

    def test_digest_names_mark_embed_and_failed(self):
        from fantasy_sim.weekly_report import _digest_name
        self.assertEqual(_digest_name(3, "20260907T120000Z", "html", failed=False, embed=False),
                         "weekly_report_week3_20260907T120000Z.html")
        self.assertEqual(_digest_name(3, "20260907T120000Z", "html", failed=False, embed=True),
                         "weekly_report_week3_20260907T120000Z_embed.html")
        self.assertEqual(_digest_name(3, "20260907T120000Z", "md", failed=True, embed=False),
                         "weekly_report_week3_20260907T120000Z_FAILED.md")

    def test_orchestrator_threads_the_canonical_flag_to_the_tools(self):
        from fantasy_sim.weekly_report import build_steps
        _steps, state = build_steps("Legion of Coom", skip_sync=True, canonical=True)
        self.assertEqual(state["tool_extra_argv"], ["--canonical"])
        _steps, state = build_steps("Legion of Coom", skip_sync=True)
        self.assertEqual(state["tool_extra_argv"], [])


class TestTradeAndMatchupRendering(unittest.TestCase):
    """Rendering fixes from real-report inspection (2026-09-02): an empty buy side must say
    so instead of emitting a header row over an empty tbody; the sell side is a sortable
    table like every other section; and the Matchup section's P(beat opp) is an independent
    sample from the League table's matchup row -- the report says so explicitly instead of
    letting a 0.3-point disagreement look like a bug. Written before the fixes existed."""

    def _report(self, buy, sell):
        results = _fixture_results()
        results["trades"] = {"buy": buy, "sell": sell, "contention_note": "n"}
        return {"status": "OK", "failed_step": None, "error": None, "results": results,
                "started_at": "x", "finished_at": "y"}

    def test_an_empty_buy_side_prints_a_message_not_an_empty_table(self):
        report = self._report([], [])
        md = render_digest(report, team="Legion of Coom", week=3)
        html = render_html(report, team="Legion of Coom", week=3)
        for out in (md, html):
            self.assertIn("no buy-side candidates met both sides' acceptance rule", out)
        self.assertNotIn('data-key="from"', html, "no header row over an empty tbody")

    def test_the_sell_side_is_a_table_with_the_buy_tables_column_family(self):
        sell = [{"buyer": "Drunk Cats", "they_want": ["Fred Warner"],
                 "they_give": ["Bijan Robinson"], "my_gain": 3.2, "their_gain": 1.1}]
        report = self._report([], sell)
        md = render_digest(report, team="Legion of Coom", week=3)
        html = render_html(report, team="Legion of Coom", week=3)
        self.assertNotIn("Sell side: Drunk Cats wants", md, "prose paragraph replaced")
        self.assertIn("| Drunk Cats | Fred Warner | Fred Warner | Bijan Robinson | +3.2 | +1.1 |", md)
        for col in ("From", "Target", "I give", "I get", "My gain", "Their gain"):
            self.assertIn(f'data-key="{col}"', html)
        self.assertIn("Drunk Cats", html)

    def test_the_matchup_section_notes_its_sample_is_independent_of_the_league_table(self):
        report = self._report([], [])
        md = render_digest(report, team="Legion of Coom", week=3)
        html = render_html(report, team="Legion of Coom", week=3)
        for out in (md, html):
            self.assertIn("independent", out)
            self.assertIn("sampling noise", out)


class TestDecisionLogSection(unittest.TestCase):
    """The decision log finally renders: the week's transactions as a sortable table with
    frozen snapshot means, retro flags, inline evaluation deltas where a record exists and
    the exact --log-tx command where it does not -- plus the computed contemporaneity split
    (which moves' snapshots were actually recorded at decision time; the backfilled majority
    were not, which F18 must not have to infer from per-row flags). Written before
    _decision_log_summary existed."""

    def _log(self, d):
        import json, os
        path = os.path.join(d, "decision_log.jsonl")
        rows = [
            {"transaction_id": "t1", "type": "waiver", "week": 1, "created": "2026-09-01T02:00:00Z",
             "snapshot_lag_days": 0.03, "snapshot_is_retroactive": False, "teams": ["Legion of Coom"],
             "is_mine": True, "faab_bid": 9,
             "adds": [{"name": "Nick Bolton", "projection": {"mean": 9.6}}],
             "drops": [{"name": "Courtland Sutton", "projection": {"mean": 11.2}}]},
            {"transaction_id": "t2", "type": "free_agent", "week": 1, "created": "2026-08-23T00:00:00Z",
             "snapshot_lag_days": 8.84, "snapshot_is_retroactive": True, "teams": ["Drunk Cats"],
             "is_mine": False, "faab_bid": None,
             "adds": [{"name": "Some Guy", "projection": {"mean": 5.0}}], "drops": []},
            {"transaction_id": "t3", "type": "free_agent", "week": 1, "created": "2026-09-01T03:00:00Z",
             "snapshot_lag_days": 0.04, "snapshot_is_retroactive": False, "teams": ["Canton Killers"],
             "is_mine": False, "faab_bid": None,
             "adds": [{"name": "Seth McGowan", "projection": {"mean": 4.1}}], "drops": []},
            {"transaction_id": "old", "type": "free_agent", "week": 0, "created": "2026-08-20T00:00:00Z",
             "snapshot_lag_days": 10.0, "snapshot_is_retroactive": True, "teams": ["Clankers"],
             "is_mine": False, "faab_bid": None, "adds": [], "drops": []},
            {"record_type": "evaluation", "transaction_id": "t1", "post_execution_reversed": True,
             "teams": {"Legion of Coom": {"champ_pct": {"delta": -0.33, "se": 1.27},
                                          "playoff_pct": {"delta": -1.27, "se": 1.48}}}},
        ]
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + chr(10))
        return path

    def test_summary_joins_evaluations_and_computes_the_contemporaneity_split(self):
        import tempfile
        from fantasy_sim.weekly_report import _decision_log_summary
        with tempfile.TemporaryDirectory() as d:
            s_ = _decision_log_summary(1, log_path=self._log(d))
        self.assertEqual(len(s_["rows"]), 3, "week-1 rows only")
        by_id = {r["transaction_id"]: r for r in s_["rows"]}
        self.assertAlmostEqual(by_id["t1"]["eval"]["champ_delta"], -0.33)
        self.assertAlmostEqual(by_id["t1"]["eval"]["playoff_se"], 1.48)
        self.assertIsNone(by_id["t2"]["eval"])
        self.assertTrue(by_id["t2"]["retro"])
        self.assertEqual(s_["contemporaneous_mine"], 1)
        self.assertEqual(s_["contemporaneous_other"], 1)
        self.assertEqual(s_["retro_count"], 1, "within the week")
        self.assertEqual(s_["older_unevaluated"], 1, "the week-0 leftover is counted, not hidden")


    def test_a_duplicated_transaction_renders_once(self):
        """Union-merge tolerance (tier 1.5, 2026-09-04): first row per transaction_id."""
        import tempfile
        from fantasy_sim.weekly_report import _decision_log_summary
        with tempfile.TemporaryDirectory() as d:
            path = self._log(d)
            with open(path, encoding="utf-8") as f:
                first = f.readline()
            with open(path, "a", encoding="utf-8") as f:
                f.write(first)   # t1 appears twice, as after a union merge
            s_ = _decision_log_summary(1, log_path=path)
        self.assertEqual(len(s_["rows"]), 3, "the duplicated t1 must not render twice")

    def test_rendering_shows_deltas_commands_retro_flags_and_the_caveat(self):
        import tempfile
        from fantasy_sim.weekly_report import _decision_log_summary
        with tempfile.TemporaryDirectory() as d:
            summary = _decision_log_summary(1, log_path=self._log(d))
        report = {"status": "OK", "failed_step": None, "error": None, "results": _fixture_results(),
                  "started_at": "x", "finished_at": "y", "decision_log": summary}
        md = render_digest(report, team="Legion of Coom", week=1)
        html = render_html(report, team="Legion of Coom", week=1)
        for out in (md, html):
            self.assertIn("Nick Bolton (9.6)", out)
            self.assertIn("Courtland Sutton (11.2)", out)
            self.assertIn("-0.3", out)                       # champ delta inline
            self.assertIn("retro +8.8d", out)
            self.assertIn("scripts.evaluate_move --log-tx t2", out)
            self.assertIn("never a record of what the model thought at decision time", out)
            self.assertIn("1 of my", out)                    # computed split, not hardcoded
        self.assertIn('data-key="Added"', html, "sortable")


class TestPredictionsCanonicality(unittest.TestCase):
    """The predictions log records whether its run was canonical, and the consumer entry
    point (read_predictions_log -- what F18/F19 read) prefers the LAST CANONICAL row per
    week over append order; only a week with no canonical row falls back to its last row.
    Rows predating the field count as non-canonical. Written before either existed."""

    OUTCOMES = [{"Team": "Legion of Coom", "Playoff_Pct": 61.5, "Champ_Pct": 20.2, "Expected_Wins": 15.9}]
    OUTLOOK = {"n": 100, "cross": True, "matchups": [], "teams": {}}
    MANIFEST = {"season": "2026", "started_at": "s", "finished_at": "f"}

    def _append(self, path, commit, canonical=False):
        from fantasy_sim.weekly_report import append_predictions_log
        append_predictions_log(1, self.OUTCOMES, self.OUTLOOK, path=path,
                               manifest=self.MANIFEST, commit=commit, canonical=canonical)

    def test_the_record_carries_the_canonical_flag(self):
        import json, os, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "predictions_2026.jsonl")
            self._append(path, "a")                       # default: exploratory
            self._append(path, "b", canonical=True)
            with open(path, encoding="utf-8") as f:
                rows = [json.loads(l) for l in f]
        self.assertFalse(rows[0]["canonical"])
        self.assertTrue(rows[1]["canonical"])

    def test_the_reader_prefers_the_last_canonical_row_per_week(self):
        import json, os, tempfile
        from fantasy_sim.weekly_report import read_predictions_log
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "predictions_2026.jsonl")
            self._append(path, "early-noncanon")
            self._append(path, "the-canonical", canonical=True)
            self._append(path, "later-noncanon")           # append order would pick this
            # a legacy row with NO canonical field, for week 2
            legacy = {"record_type": "week_predictions", "season": "2026", "week": 2,
                      "commit": "legacy", "season_outcomes": [], "matchups": [], "median": {}}
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(legacy) + chr(10))
            sel = read_predictions_log("2026", path=path)
        self.assertEqual(sel[1]["commit"], "the-canonical",
                         "last CANONICAL wins over the later non-canonical append")
        self.assertEqual(sel[2]["commit"], "legacy", "no canonical row: last row stands in")

    def test_digest_names_carry_the_window_id_when_given(self):
        from fantasy_sim.weekly_report import _digest_name
        self.assertEqual(_digest_name(3, "20260907T120000Z", "html", window="run1_pre_kickoff"),
                         "weekly_report_week3_run1_pre_kickoff_20260907T120000Z.html")
        self.assertEqual(_digest_name(3, "20260907T120000Z", "md"),
                         "weekly_report_week3_20260907T120000Z.md", "no window: unchanged shape")

    WINDOWS = None  # built in _windows()

    @staticmethod
    def _windows():
        from datetime import datetime, timezone
        u = lambda s_: datetime.fromisoformat(s_).replace(tzinfo=timezone.utc)
        return [
            {"name": "run1_pre_kickoff", "start": u("2026-09-02T07:00:00"), "deadline": u("2026-09-10T00:20:00")},
            {"name": "run2_sunday", "start": u("2026-09-13T07:00:00"), "deadline": u("2026-09-13T17:00:00")},
            {"name": "run3_tuesday", "start": u("2026-09-15T07:00:00"), "deadline": u("2026-09-16T07:00:00")},
        ]

    def _run_supersede(self, files, current_window, keep_stamp):
        import os, tempfile
        from fantasy_sim.weekly_report import _archive_superseded
        with tempfile.TemporaryDirectory() as d:
            for n in files:
                open(os.path.join(d, n), "w").close()
            moved = _archive_superseded(d, self._windows(), current_window, keep_stamp)
            remaining = sorted(n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n)))
            archived = sorted(os.listdir(os.path.join(d, "archive"))) if os.path.isdir(
                os.path.join(d, "archive")) else []
        return sorted(moved), remaining, archived

    def test_a_run_set_spanning_a_second_boundary_is_never_split(self):
        # The real 2026-09-02 bug: tool JSONs stamped 215239Z, digest 215240Z -- exact-stamp
        # keep archived three of the new run's own files. Tolerance protects the whole set.
        new_set = ["lineup_20260902T215239Z_week1.json", "matchup_20260902T215239Z_week1.json",
                   "roster_grades_20260902T215239Z_week1.json",
                   "trade_targets_20260902T215240Z_week1.json",
                   "weekly_report_week1_run1_pre_kickoff_20260902T215240Z.md"]
        old_in_window = ["weekly_report_week1_run1_pre_kickoff_20260902T120000Z.md",
                         "lineup_20260902T115959Z_week1.json"]
        moved, remaining, _arch = self._run_supersede(new_set + old_in_window,
                                                      "run1_pre_kickoff", "20260902T215240Z")
        self.assertEqual(moved, sorted(old_in_window), "only the OLD in-window set moves")
        self.assertEqual(remaining, sorted(new_set), "the new run's whole set stays, both stamps")

    def test_a_pre_window_stray_set_is_superseded_even_with_the_old_name_shape(self):
        # The 02:47Z case: an old-shape digest (no window infix) whose stamps predate every
        # window. It covers nothing, so a new canonical run archives it.
        stray = ["weekly_report_week1_20260902T024723Z.md",
                 "weekly_report_week1_20260902T024723Z_embed.html",
                 "lineup_20260902T024722Z_week1.json"]
        new_set = ["weekly_report_week1_run1_pre_kickoff_20260902T215240Z.md"]
        moved, remaining, _arch = self._run_supersede(stray + new_set,
                                                      "run1_pre_kickoff", "20260902T215240Z")
        self.assertEqual(moved, sorted(stray))
        self.assertEqual(remaining, new_set)

    def test_another_windows_canonical_cover_is_never_touched(self):
        run1_cover = ["weekly_report_week1_run1_pre_kickoff_20260909T230000Z.md",
                      "lineup_20260909T225959Z_week1.json"]
        new_run2 = ["weekly_report_week1_run2_sunday_20260913T163000Z.md"]
        moved, remaining, _arch = self._run_supersede(run1_cover + new_run2,
                                                      "run2_sunday", "20260913T163000Z")
        self.assertEqual(moved, [], "run 1's canonical record survives run 2's supersede")
        self.assertEqual(remaining, sorted(run1_cover + new_run2))

    def test_build_steps_stashes_the_canonical_flag_for_the_predictions_step(self):
        _steps, state = build_steps("Legion of Coom", skip_sync=True, canonical=True)
        self.assertTrue(state["canonical"])
        _steps, state = build_steps("Legion of Coom", skip_sync=True)
        self.assertFalse(state["canonical"])


class TestDepthWaiverRendering(unittest.TestCase):
    """Depth waiver targets render as their OWN subsection in both digest formats, with the
    qualification rule stated -- clearly separated, never merged into the main ranking.
    Written before the rendering existed."""

    def test_depth_targets_get_a_separate_table_and_absence_renders_nothing(self):
        results = _fixture_results()
        depth_row = {"name": "Mark Andrews", "pos": "TE", "tier": 2, "mean": 9.8, "vorp": 1.0,
                     "fills": "depth", "incumbent": None,
                     "week": {"mean": 10.1, "p10": 3.0, "p50": 9.2, "p90": 18.0, "p_zero": 0.02},
                     "bid": {"suggested": 2, "typical_manager_model": 1.0},
                     "p_beats_incumbent": None}
        results["waivers"]["targets"] = results["waivers"]["targets"] + [depth_row]
        report = {"status": "OK", "failed_step": None, "error": None, "results": results,
                  "started_at": "x", "finished_at": "y"}
        md = render_digest(report, team="Legion of Coom", week=3)
        html = render_html(report, team="Legion of Coom", week=3)
        for out in (md, html):
            self.assertIn("Depth upgrades", out)
            self.assertIn("Mark Andrews", out)
            self.assertIn("worst bench", out, "the qualification rule is stated on the page")
        # main table does not contain the depth row's name ahead of the depth section
        self.assertLess(md.index("Tuli Tuipulotu"), md.index("Depth upgrades"),
                        "depth is separated below the main ranking")

        results["waivers"]["targets"] = [t for t in results["waivers"]["targets"]
                                         if t["fills"] != "depth"]
        md2 = render_digest(report, team="Legion of Coom", week=3)
        self.assertNotIn("Depth upgrades", md2, "no depth rows: no empty section")


class TestCanonicalLogsPush(unittest.TestCase):
    """--canonical runs end by committing and pushing the tracked data/logs files
    (owner, 2026-09-04): the logs are the only unrecoverable season data, and the
    canonical run is the natural moment durability becomes mechanical. The step is
    warn-never-fail BY DESIGN, a documented divergence from the orchestrator's
    fail-loud contract: a push failure (network down) does not invalidate the report,
    and it still surfaces in the digest housekeeping and in check_freshness."""

    def test_canonical_appends_the_logs_push_step_last_and_plain_runs_lack_it(self):
        from fantasy_sim.weekly_report import build_steps
        names = [n for n, _ in build_steps("Legion of Coom", canonical=True)[0]]
        self.assertEqual(names[-1], "logs_push")
        names = [n for n, _ in build_steps("Legion of Coom")[0]]
        self.assertNotIn("logs_push", names)

    def test_nothing_staged_means_no_commit_and_no_push_attempt_when_not_ahead(self):
        from fantasy_sim.weekly_report import commit_and_push_logs
        calls = []
        def git(args):
            calls.append(args[0])
            if args[0] == "diff":
                return 0, ""          # --cached --quiet: exit 0 = nothing staged
            if args[0] == "rev-list":
                return 0, "0"         # nothing unpushed either
            return 0, ""
        out = commit_and_push_logs(3, git=git)
        self.assertEqual(out["committed"], 0)
        self.assertNotIn("commit", calls)
        self.assertNotIn("push", calls)

    def test_staged_changes_commit_scoped_to_the_logs_and_push(self):
        from fantasy_sim.weekly_report import commit_and_push_logs
        calls = []
        def git(args):
            calls.append(args)
            if args[0] == "diff":
                return 1, ""          # staged changes under data/logs
            return 0, ""
        out = commit_and_push_logs(3, git=git)
        self.assertEqual(out["committed"], 1); self.assertTrue(out["pushed"])
        commit = next(a for a in calls if a[0] == "commit")
        self.assertEqual(commit[-2:], ["--", "data/logs"],
                         "the commit must be scoped to data/logs so a user's unrelated "
                         "staged work is never swept into an automated commit")
        self.assertIn("week 03", " ".join(commit))
        self.assertIn(["push"], calls)

    def test_push_failure_warns_and_never_raises(self):
        from fantasy_sim.weekly_report import commit_and_push_logs
        def git(args):
            if args[0] == "diff":
                return 1, ""
            if args[0] == "push":
                return 1, "fatal: unable to access remote"
            return 0, ""
        out = commit_and_push_logs(3, git=git)
        self.assertEqual(out["committed"], 1); self.assertFalse(out["pushed"])
        self.assertIn("warning", out)



class TestCanonicalRowProvenance(unittest.TestCase):
    def test_run_provenance_carries_vegas_source_degraded_count_and_runner_flag(self):
        """F36's mitigation, made durable: the canonical row must record the sync state
        it was quoted under (vegas source, tolerated-failure count, whether a runner
        produced it) -- the manifest is untracked and overwritten, so the row's
        provenance is the only record that survives."""
        import os
        from unittest.mock import patch as _patch
        from fantasy_sim.weekly_report import run_provenance
        manifest = {"degraded": ["a", "b"], "current_week": 3}
        vegas_meta = {"source": "odds_api", "week": 3}
        with _patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            p = run_provenance(manifest, vegas_meta)
        self.assertEqual(p, {"vegas_source": "odds_api", "degraded": 2, "runner": True})
        with _patch.dict(os.environ, {}, clear=True):
            p = run_provenance(manifest, vegas_meta)
        self.assertFalse(p["runner"])



if __name__ == "__main__":
    unittest.main()

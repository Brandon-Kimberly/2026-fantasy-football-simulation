"""
tests.test_projection_log

F7 (AUDIT_PLAN.md): every sync appends the projections it used for rostered players to
data/projection_log.jsonl, so that next season's projection error -- the quantity
EPISTEMIC_ERROR_RATES actually denotes, never measurable for 2025 because Sleeper serves only
the current week's projections -- can be derived directly.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fantasy_sim import sync
from fantasy_sim.backtest_player import analyze_projection_error, load_projection_log


class TestProjectionLogWriter(unittest.TestCase):
    def test_append_writes_one_json_line_per_row_and_appends_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "projection_log.jsonl")
            rows = [{"season": "2026", "week": 3, "player_id": "1", "sleeper_mean": 12.5, "espn_mean": None}]
            self.assertEqual(sync.append_projection_log(rows, path=path), 1)
            self.assertEqual(sync.append_projection_log(rows, path=path), 1)
            with open(path) as handle:
                lines = [json.loads(l) for l in handle if l.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["player_id"], "1")
        self.assertIsNone(lines[0]["espn_mean"])

    def test_a_write_failure_never_raises(self):
        """A sync must not break because the log could not be written; it warns and returns 0."""
        with self.assertLogs(level="WARNING"):
            n = sync.append_projection_log([{"season": "2026", "week": 1, "player_id": "1"}],
                                           path=os.path.join(os.devnull, "impossible", "x.jsonl"))
        self.assertEqual(n, 0)

    def test_empty_rows_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "projection_log.jsonl")
            self.assertEqual(sync.append_projection_log([], path=path), 0)
            self.assertFalse(os.path.exists(path))


class TestProjectionLogFromSync(unittest.TestCase):
    def test_generate_player_baselines_logs_rostered_players_with_both_sources(self):
        """Rostered players are logged with the Sleeper weekly mean and the matched ESPN mean;
        an unrostered player is not logged; the fallback flag is carried."""
        db = {"1": {"first_name": "Amon-Ra", "last_name": "St. Brown", "position": "WR", "team": "DET"},
              "2": {"first_name": "Bench", "last_name": "Guy", "position": "RB", "team": "SEA"},
              "3": {"first_name": "Not", "last_name": "Rostered", "position": "WR", "team": "KC"}}
        proj = {"1": {"stats": {"rush_yd": 100.0}}, "2": {"stats": {"rush_yd": 60.0}}, "3": {"stats": {"rush_yd": 80.0}}}
        live_rosters = {"T": [{"name": "Amon-Ra St. Brown", "pos": "WR", "team": "DET"},
                              {"name": "Bench Guy", "pos": "RB", "team": "SEA"}]}
        weekly = MagicMock(); weekly.status_code = 200; weekly.json.return_value = proj
        logged = {}

        def fake_append(rows, path=None):
            logged["rows"] = rows
            return len(rows)
        with patch.object(sync, "save_json"), patch.object(sync.os.path, "exists", return_value=False), \
             patch.object(sync.requests, "get", return_value=weekly), \
             patch.object(sync, "fetch_espn_projection_data", return_value=({sync._normalize_player_name_for_matching("Amon-Ra St. Brown"): 12.0}, {})), \
             patch.object(sync, "append_projection_log", side_effect=fake_append):
            sync.generate_player_baselines({"rush_yd": 0.1}, db, live_rosters, "2026", 3)
        rows = {r["player_id"]: r for r in logged["rows"]}
        self.assertEqual(sorted(rows), ["1", "2"], "only rostered players are logged")
        self.assertEqual((rows["1"]["season"], rows["1"]["week"], rows["1"]["pos"], rows["1"]["team"]), ("2026", 3, "WR", "DET"))
        self.assertEqual(rows["1"]["sleeper_mean"], 10.0)
        self.assertEqual(rows["1"]["espn_mean"], 12.0)
        self.assertIsNone(rows["2"]["espn_mean"])
        self.assertFalse(rows["1"]["fallback_season"])
        self.assertTrue(rows["1"]["synced_at"].endswith("Z"))


class TestProjectionErrorAnalysis(unittest.TestCase):
    def _rows(self):
        rows = []
        # two RBs, 5 weeks each: A projected 10 and scores 12 every week (pure bias, no noise);
        # B projected 10 and scores 10 +/- 2 alternating (no bias, pure noise); one zero week for B
        for wk in range(1, 6):
            rows.append({"season": "2026", "week": wk, "player_id": "A", "pos": "RB", "sleeper_mean": 10.0})
            rows.append({"season": "2026", "week": wk, "player_id": "B", "pos": "RB", "sleeper_mean": 10.0})
        actual = {}
        for wk in range(1, 6):
            actual[("2026", wk, "A")] = 12.0
            actual[("2026", wk, "B")] = 12.0 if wk % 2 else 8.0
        actual[("2026", 5, "B")] = 0.0                                   # absence, must be excluded
        return rows, actual

    def test_bias_and_noise_are_separated(self):
        rows, actual = self._rows()
        out = analyze_projection_error(rows, actual, min_weeks=4)
        self.assertEqual(out["RB"]["n_players"], 2)
        # A: error +2, sampling 0.  B (4 played weeks: 12, 8, 12, 8): mean error 0, var(a-p)=5.33/4
        self.assertAlmostEqual(out["RB"]["mean_signed_error"], 1.0, places=2)
        self.assertAlmostEqual(out["RB"]["rms_error"], 1.41, places=2)
        # epistemic var = mean(err^2) - mean(sampling) = 2.0 - (0 + 1.333)/2 = 1.333 -> sd 1.15
        self.assertAlmostEqual(out["RB"]["epistemic_sd"], 1.15, places=2)
        self.assertAlmostEqual(out["RB"]["epistemic_rate"], 0.115, places=3)

    def test_last_row_per_week_wins_and_min_weeks_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "log.jsonl")
            with open(path, "w") as handle:
                handle.write(json.dumps({"season": "2026", "week": 1, "player_id": "A", "pos": "RB", "sleeper_mean": 9.0}) + "\n")
                handle.write(json.dumps({"season": "2026", "week": 1, "player_id": "A", "pos": "RB", "sleeper_mean": 11.0}) + "\n")
            rows = load_projection_log(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sleeper_mean"], 11.0)
        self.assertEqual(analyze_projection_error(rows, {("2026", 1, "A"): 10.0}, min_weeks=4), {})

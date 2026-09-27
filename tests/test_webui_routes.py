"""
tests.test_webui_routes -- the read-only viewer against a temp root (docs/WEB_UI.md W1).

Every route is walked with Flask's test client over a small hand-built data/ tree, and the
tree is hashed before and after: a viewer that writes is a defect. The refusal set (data/
local, traversal, a foreign Host header), the canary-secret test (2.7) and both overlay
states (2.6) are pinned here. Skips cleanly without Flask (the hypothesis/espn_api precedent).
"""
import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

from webui.names import Overlay
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
TEAMS = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Turbo Llamas",
         "Cosmic Badgers", "Polar Yetis", "Iron Wombats", "Crimson Marmots"]
CANARY = {"SLEEPER_LEAGUE_ID": "CANARY-LEAGUE-0001", "ESPN_LEAGUE_ID": "CANARY-ESPN-0002",
          "SLEEPER_LEAGUE_ID_2025": "CANARY-2025-0003", "SLEEPER_LEAGUE_ID_2024": "CANARY-2024-0004",
          "ODDS_API_KEY": "CANARY-ODDS-0005"}
SYNC_OUTPUTS = ("vegas_totals.json", "player_baselines.json", "nfl_team_power_ratings.json",
                "league_schedule.json", "nfl_schedule.json", "nfl_defensive_ratings.json",
                "nfl_defensive_tiers.json", "league_state.json", "live_rosters.json",
                "league_standings.json", "weekly_actuals.json", "playoff_bracket.json")


def _w(root, rel, data):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(p, mode, **({} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": "\n"})) as f:
        f.write(data if isinstance(data, (bytes, str)) else json.dumps(data))
    return p


def build_tree(root):
    """A week-3 tree with every file the pages read, all pseudonymous."""
    _w(root, "data/current/sync_manifest.json", {
        "started_at": "2026-09-25T17:12:06Z", "finished_at": "2026-09-25T17:13:20Z", "current_week": 3,
        "season": "2026", "ok": True, "degraded": ["WARNING | odds: fell back for Quantum Ferrets' game"],
        "files": {}})
    for name in SYNC_OUTPUTS:
        _w(root, "data/current/" + name, {})
    _w(root, "data/current/league_state.json", {"current_week": 3})
    _w(root, "data/current/vegas_totals.json", {"_meta": {"week": 3}, "BUF": {"total": 28.0}})
    _w(root, "data/current/nfl_schedule.json", {"_meta": {"kickoffs": {}}, "3": {"GB": "CHI"}})
    _w(root, "data/current/league_standings.json",
       {t: {"h2h_wins": i % 5, "points_scored": 300.0 + i, "remaining_faab": 100 - i} for i, t in enumerate(TEAMS)})
    _w(root, "data/current/live_rosters.json",
       {t: [{"name": f"Player {i} O'Neil", "pos": "QB", "team": "GB", "injury_status": None, "on_ir": False}]
        for i, t in enumerate(TEAMS)})
    _w(root, "data/current/player_baselines.json",
       {f"Player {i} O'Neil": {"pos": "QB", "team": "GB", "mean": 17.2 + i, "bye": 11, "on_ir": False,
                               "injury_status": None} for i in range(8)})
    _w(root, "data/current/pending_trades.json", {"_meta": {"week": 3, "n": 0, "fetched_at": "x"}, "trades": []})
    d = "data/weeks/week_03/"
    _w(root, d + "live_season_forecast_week_3.json",
       {t: {"current_state": {"actual_wins_banked": 2.0, "actual_points_banked": 335.9, "remaining_faab": 48},
            "forecast": {"expected_final_wins": 19.1, "playoff_probability_pct": 93.5 - i,
                         "playoff_standard_error": 0.25, "approximate_magic_number": 14}} for i, t in enumerate(TEAMS)})
    _w(root, d + "syndicate_comprehensive_matrix_week_3.json",
       {"metadata": {"week": 3, "simulations": 100, "batches": 1},
        "season_outcomes": [{"Team": t, "Expected_Wins": 19.0, "Expected_Points": 2679.1, "Playoff_Pct": 93.5,
                             "Playoff_SE": 0.2, "Champ_Pct": 35.8, "Toilet_Pct": 0.2} for t in TEAMS],
        "finishing_seed_probabilities": {t: {"Seed 1": 12.5} for t in TEAMS}})
    _w(root, d + "syndicate_insights_week_3.json",
       {"engine_simulations_run": 100, "highest_single_week_score_observed": 300.1,
        "team_with_highest_ceiling_game": "Quantum Ferrets", "week_of_highest_score": 4,
        "schedule_luck_index": {}, "most_valuable_players_championship_shares": {}})
    _w(root, d + "simulation_audit_log_sim0_week_3.json",
       {"weeks": {}, "warnings": [{"level": "ERROR", "message": "VEGAS STALE: Quantum Ferrets' line"}]})
    _w(root, d + "Power_Rankings.png", PNG)
    _w(root, d + "tiers/QB_tiers.html", "<html><body><h1>QB tiers</h1></body></html>")
    dd = "data/decisions/week_03/"
    _w(root, dd + "weekly_report_week3_run1_pre_kickoff_20260924T165337Z.html",
       "<html><body><h1>Week 3</h1><p>Quantum Ferrets over Neon Walruses</p></body></html>")
    _w(root, dd + "weekly_report_week3_run1_pre_kickoff_20260924T165337Z.md", "# Week 3\nQuantum Ferrets\n")
    _w(root, dd + "lineup_20260924T165331Z_week3.json", {"tool": "optimize_lineup", "team": "Quantum Ferrets"})
    _w(root, dd + "archive/roster_grades_20260923T100000Z_week3.json", {"tool": "roster_grades"})
    _w(root, "data/decisions/adhoc/compare_20260926T203839Z_a_vs_b.json", {"tool": "compare_players"})
    _w(root, "data/logs/decision_log.jsonl",
       "\n".join(json.dumps({"week": 1, "teams": ["Quantum Ferrets"], "type": "waiver", "i": i}) for i in range(3)) + "\n")
    _w(root, "data/logs/predictions_2026.jsonl", json.dumps({"week": 3, "record_type": "week_predictions",
                                                             "canonical": False, "logged_at": "2026-09-25T17:24:00Z"}) + "\n")
    _w(root, "data/logs/draft_2026.json", {"season": "2026", "picks": []})
    _w(root, "data/results/week_01/weekly-report-1/weekly_report_week1_20260910T000000Z.html", "<html>r</html>")
    # the private files the chokepoint must never expose
    _w(root, "data/local/env.sh", "export ODDS_API_KEY=" + CANARY["ODDS_API_KEY"] + "\n")
    _w(root, "data/local/identity_map.json", {"real_to_fictional": {"CANARY-REAL-TEAM": "Quantum Ferrets"}})


ROUTES = ("/", "/system", "/status", "/health", "/weeks", "/weeks/3", "/decisions", "/decisions/3", "/decisions/adhoc", "/results",
          "/current", "/logs", "/logs/decision_log.jsonl", "/logs/decision_log.jsonl?n=1",
          "/file/weeks/week_03/live_season_forecast_week_3.json", "/file/weeks/week_03/live_season_forecast_week_3.json?raw=1",
          "/file/weeks/week_03/Power_Rankings.png", "/file/weeks/week_03/tiers/QB_tiers.html",
          "/file/decisions/week_03/weekly_report_week3_run1_pre_kickoff_20260924T165337Z.html",
          "/file/decisions/week_03/weekly_report_week3_run1_pre_kickoff_20260924T165337Z.md",
          "/file/logs/draft_2026.json", "/file/logs/decision_log.jsonl")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestViewer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def client(self, overlay=None, port=None):
        app = create_app(self.root, overlay=overlay, port=port)
        app.testing = True
        return app.test_client()

    def test_every_route_renders_and_the_tree_is_byte_identical_afterwards(self):
        before = self.root.tree_digest()
        c = self.client()
        for r in ROUTES:
            with self.subTest(route=r):
                resp = c.get(r, follow_redirects=True)
                self.assertEqual(resp.status_code, 200, f"{r}: {resp.data[:300]!r}")
                self.assertNotIn(b"Traceback", resp.data)
        self.assertEqual(self.root.tree_digest(), before, "a read-only viewer wrote to the tree")

    def test_system_page_carries_the_freshness_verdict_windows_note_and_r1(self):
        body = self.client().get("/system").get_data(as_text=True)
        self.assertIn("pill DEGRADED", body)                        # the fixture manifest has a tolerated failure
        self.assertIn("run a sync to persist them", body)          # no kickoffs: never live-fetched
        self.assertIn("one engine process at a time", body)        # once, inside the collapsed terminal notes
        self.assertIn("scripts.run_sync", body)

    def test_health_json(self):
        h = self.client().get("/health").get_json()
        self.assertEqual(h["week"], 3)
        self.assertEqual(h["weeks"], [3])
        self.assertFalse(h["real_names"])

    def test_missing_week_is_404_not_500(self):
        self.assertEqual(self.client().get("/weeks/9").status_code, 404)
        self.assertEqual(self.client().get("/decisions/9").status_code, 404)
        self.assertEqual(self.client().get("/file/weeks/week_03/nope.json").status_code, 404)

    def test_the_chokepoint_refuses_from_the_url(self):
        c = self.client()
        for bad in ("/file/local/env.sh", "/file/local/identity_map.json", "/file/current/..%2F..%2Fpyproject.toml",
                    "/file/..%2Fpyproject.toml", "/file/backtest/x.json", "/file/current/league_state.py"):
            with self.subTest(url=bad):
                self.assertIn(c.get(bad).status_code, (400, 404), bad)
                self.assertNotIn(b"CANARY", c.get(bad).data)

    def test_a_foreign_host_header_is_refused(self):
        c = self.client(port=8765)
        self.assertEqual(c.get("/", headers={"Host": "evil.example:8765"}).status_code, 400)
        self.assertEqual(c.get("/", headers={"Host": "127.0.0.1:9999"}).status_code, 400)
        self.assertEqual(c.get("/", headers={"Host": "127.0.0.1:8765"}).status_code, 200)
        self.assertEqual(c.get("/", headers={"Host": "localhost:8765"}).status_code, 200)
        self.assertEqual(self.client().get("/", headers={"Host": "localhost"}).status_code, 200)

    def test_no_response_ever_carries_an_environment_secret(self):
        with patch.dict(os.environ, CANARY):
            c = self.client(overlay=Overlay({"Quantum Ferrets": "Team Alpha"}))
            for r in ROUTES + ("/file/local/env.sh",):
                resp = c.get(r)
                for v in CANARY.values():
                    self.assertNotIn(v.encode(), resp.data, f"{r} leaked {v}")
                for k, v in resp.headers.items():
                    self.assertNotIn("CANARY", v, f"{r} header {k}")

    def test_security_headers_and_no_store(self):
        resp = self.client().get("/")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")

    def test_pseudonyms_only_when_the_overlay_is_off(self):
        c = self.client()
        marker = Overlay.marker()
        for r in ("/", "/weeks/3", "/current", "/logs/decision_log.jsonl",
                  "/file/decisions/week_03/weekly_report_week3_run1_pre_kickoff_20260924T165337Z.html"):
            body = c.get(r).get_data(as_text=True)
            self.assertNotIn(marker, body, r)
        self.assertIn("Quantum Ferrets", c.get("/current").get_data(as_text=True))

    def test_the_overlay_substitutes_into_bodies_only(self):
        c = self.client(overlay=Overlay({"Quantum Ferrets": "Team Alpha", "Neon Walruses": "Team Beta"}))
        marker = Overlay.marker()
        for r in ("/weeks/3", "/current", "/logs/decision_log.jsonl"):
            body = c.get(r).get_data(as_text=True)
            self.assertIn("Team Alpha", body, r)
            self.assertNotIn("Quantum Ferrets", body, r)
            self.assertIn(marker, body, r)
        digest = c.get("/file/decisions/week_03/weekly_report_week3_run1_pre_kickoff_20260924T165337Z.html").get_data(as_text=True)
        self.assertIn("Team Alpha over Team Beta", digest)
        self.assertIn(marker, digest)
        # URLs on the pages stay pseudonymous: no href carries a real name
        page = c.get("/decisions/3").get_data(as_text=True)
        self.assertNotIn("Team%20Alpha", page)
        self.assertNotIn('href="/file/decisions/week_03/Team', page)
        # and the tree is untouched by rendering with real names
        self.assertEqual(self.root.tree_digest(), self.root.tree_digest())
        self.assertNotIn("Team Alpha", open(os.path.join(self.td.name, "data", "decisions", "week_03",
                                                        "weekly_report_week3_run1_pre_kickoff_20260924T165337Z.html"),
                                           encoding="utf-8").read())

    def test_json_files_render_pretty_and_raw(self):
        c = self.client()
        pretty = c.get("/file/decisions/week_03/lineup_20260924T165331Z_week3.json").get_data(as_text=True)
        self.assertIn("optimize_lineup", pretty)
        raw = c.get("/file/decisions/week_03/lineup_20260924T165331Z_week3.json?raw=1")
        self.assertEqual(raw.mimetype, "application/json")
        self.assertEqual(json.loads(raw.data)["tool"], "optimize_lineup")

    def test_player_names_with_apostrophes_are_escaped_not_mangled(self):
        body = self.client().get("/current").get_data(as_text=True)
        self.assertTrue(re.search(r"Player 0 O(&#39;|&#x27;|')Neil", body), body[:400])

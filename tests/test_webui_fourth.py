"""
tests.test_webui_fourth -- the fourth pass on the web UI.

Pinned: readable addresses (records, jobs, logs, forecasts, league) resolve to the same
pages the file paths do, and the old paths still work; the Decisions tab joins every
transaction to its paired evaluation and sums each team's own moves; the League page
shows roster VORP by player from a roster_grades record that carries `rosters`; a log
opens as a table and 'the whole file' really is the whole file; --json tools are forced
to --json and their stdout renders as tables; the console parser reads '|' columns;
the chart helper leaves headroom so the last value label is inside the drawing; player
statuses abbreviate the way the app does; the owner's team colours apply; and no
stylesheet rule invites a horizontal scroll above phone width.
"""
import json
import os
import re
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui import glance, render
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.jobs import OK
    from webui.live import LiveBoard
    from tests.test_webui_home import enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import TEAMS, build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

MARKET_SWEEP = """
  pos  slot-losing starter        mean | best available             mean   gain | drop (worst owned)       mean
  QB   Patrick Mahomes (1/2)     23.27 | C.J. Stroud               16.41  -6.86 | Jordan Love             17.53
  RB   Javonte Williams (3/4)    13.99 | Tony Pollard               7.82  -6.17 | Emanuel Wilson           6.38
  DB   Cole Bishop (1/1)          8.38 | Amani Hooker              10.05  +1.67 | Cole Bishop              8.38  <-- UPGRADE
"""


def _w(root, rel, obj):
    p = os.path.join(root, "data", *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        if isinstance(obj, str):
            fh.write(obj)
        else:
            json.dump(obj, fh)


def plant(root):
    """A decision log with two moves and one trade, each evaluated; a roster_grades record with players."""
    tx = [{"transaction_id": "t1", "type": "waiver", "week": 2, "created": "2026-09-16T12:00:00Z", "is_mine": True, "teams": [MY_TEAM], "faab_bid": 7,
           "adds": [{"name": "Nick Bolton", "player_id": "7648", "to_team": MY_TEAM, "projection": {"pos": "LB", "mean": 11.7}}],
           "drops": [{"name": "Courtland Sutton", "player_id": "5045", "to_team": MY_TEAM, "projection": {"pos": "WR", "mean": 9.2}}]},
          {"transaction_id": "t2", "type": "free_agent", "week": 2, "created": "2026-09-17T12:00:00Z", "is_mine": False, "teams": [TEAMS[1]], "faab_bid": None,
           "adds": [{"name": "Tuli Tuipulotu", "player_id": "10898", "to_team": TEAMS[1], "projection": {"pos": "DL", "mean": 10.5}}], "drops": []},
          {"transaction_id": "t3", "type": "trade", "week": 3, "created": "2026-09-20T12:00:00Z", "is_mine": False, "teams": [TEAMS[1], TEAMS[2]],
           "adds": [{"name": "Josh Jacobs", "player_id": "5850", "to_team": TEAMS[2], "projection": {"pos": "RB", "mean": 13.0}},
                    {"name": "Tyrone Tracy", "player_id": "11655", "to_team": TEAMS[1], "projection": {"pos": "RB", "mean": 2.7}}],
           "drops": []},
          {"transaction_id": "t4", "type": "free_agent", "week": 3, "created": "2026-09-21T12:00:00Z", "is_mine": True, "teams": [MY_TEAM],
           "adds": [{"name": "Harrison Mevis", "player_id": "12015", "to_team": MY_TEAM, "projection": {"pos": "K", "mean": 11.4}}], "drops": []}]
    ev = [{"record_type": "evaluation", "transaction_id": "t1", "evaluated_at": "2026-09-18T00:00:00Z", "n_sims": 3000, "batches": 10, "post_execution_reversed": False,
           "teams": {MY_TEAM: {"playoff_pct": {"delta": 3.0, "se": 1.0, "with": 60.0, "without": 57.0}, "champ_pct": {"delta": 1.0, "se": 0.5, "with": 11, "without": 10}},
                     TEAMS[1]: {"playoff_pct": {"delta": -0.5, "se": 1.0, "with": 30, "without": 30.5}, "champ_pct": {"delta": 0.0, "se": 0.4, "with": 5, "without": 5}}}},
          {"record_type": "evaluation", "transaction_id": "t2", "evaluated_at": "2026-09-18T00:00:00Z", "n_sims": 3000, "batches": 10, "post_execution_reversed": True,
           "teams": {TEAMS[1]: {"playoff_pct": {"delta": 2.0, "se": 0.8, "with": 32, "without": 30}, "champ_pct": {"delta": 0.4, "se": 0.3, "with": 5.4, "without": 5}}}},
          {"record_type": "evaluation", "transaction_id": "t3", "evaluated_at": "2026-09-22T00:00:00Z", "n_sims": 3000, "batches": 10, "post_execution_reversed": False,
           "teams": {TEAMS[1]: {"playoff_pct": {"delta": -4.0, "se": 1.2, "with": 28, "without": 32}, "champ_pct": {"delta": -1.0, "se": 0.5, "with": 4, "without": 5}},
                     TEAMS[2]: {"playoff_pct": {"delta": 5.0, "se": 1.1, "with": 45, "without": 40}, "champ_pct": {"delta": 1.5, "se": 0.5, "with": 8, "without": 6.5}}}},
          {"record_type": "evaluation", "transaction_id": "t4", "evaluated_at": "2026-09-22T00:00:00Z", "skipped": "roster drift since the logged move"}]
    _w(root, "logs/decision_log.jsonl", "\n".join(json.dumps(r) for r in tx + ev) + "\n")
    _w(root, "decisions/week_03/archive/roster_grades_20260926T120000Z_week3.json", {
        "timestamp_utc": "20260926T120000Z", "tool": "roster_grades",
        "league": {"week": 3, "teams": [{"team": t, "lineup_vorp": 30.0 - i, "depth_vorp": 4.0, "optimal_score": 180.0, "holes": 0,
                                         "tier1_starters": 2, "starters_below_replacement": 0, "rank": i + 1} for i, t in enumerate(TEAMS)]},
        "rosters": {t: {"replacement_levels": {"QB": 18.0}, "players": [{"name": f"Player {i} O'Neil", "pos": "QB", "role": "starter", "slot": "QB",
                                                                          "tier": 1, "mean": 17.2 + i, "vorp": (17.2 + i) - 18.0, "injury_status": None, "on_ir": False, "bye": 11}]}
                    for i, t in enumerate(TEAMS)}})


class TestHelpers(unittest.TestCase):
    def test_pretty_url_shapes(self):
        e = {"rel": "decisions/week_03/lineup_20260924T165331Z_week3.json", "name": "lineup_20260924T165331Z_week3.json", "tool": "lineup", "stamp": "20260924T165331Z", "link": "/file/x"}
        self.assertEqual(render.pretty_url(e), "/records/week-3/optimal-lineup/2026-09-24-165331")
        e2 = dict(e, rel="decisions/week_03/archive/" + e["name"])
        self.assertEqual(render.pretty_url(e2), "/records/week-3/archive/optimal-lineup/2026-09-24-165331")
        c = {"rel": "decisions/adhoc/compare_20260926T203839Z_C.J._Stroud_vs_Jordan_Love.json", "name": "compare_20260926T203839Z_C.J._Stroud_vs_Jordan_Love.json",
             "tool": "compare", "stamp": "20260926T203839Z", "link": "/file/y"}
        self.assertEqual(render.pretty_url(c), "/records/ad-hoc/compare-players/2026-09-26-203839/c-j-stroud-vs-jordan-love")
        d = {"rel": "decisions/week_03/weekly_report_week3_run1_pre_kickoff_20260924T165337Z.md", "name": "weekly_report_week3_run1_pre_kickoff_20260924T165337Z.md",
             "tool": "weekly_report", "stamp": "20260924T165337Z", "link": "/file/z"}
        self.assertEqual(render.pretty_url(d), "/records/week-3/weekly-digest/2026-09-24-165337/run1-pre-kickoff/markdown")
        self.assertEqual(render.pretty_url({"rel": "logs/decision_log.jsonl", "name": "decision_log.jsonl", "link": "/file/l"}), "/logs/decision-log")
        self.assertEqual(render.pretty_url({"rel": "weeks/week_03/x.json", "name": "x.json", "link": "/file/w"}), "/file/w")
        self.assertEqual(render.job_url({"id": "20260926T000000Z_000001_optimize_lineup"}), "/jobs/optimal-lineup/2026-09-26-000000-000001")

    def test_status_abbreviations_and_team_colours(self):
        self.assertEqual([render.status_abbr(s) for s in ("Questionable", "Doubtful", "Out", "IR", "PUP", "Sus", "")], ["Q", "D", "O", "IR", "PUP", "SUS", ""])
        self.assertEqual(glance.team_hue("Quantum Ferrets"), 272)         # purple, the owner's choice
        self.assertEqual(glance.team_hue("Cosmic Badgers"), 140)          # green
        self.assertTrue(0 <= glance.team_hue("Nobody FC") < 360)

    def test_console_reads_pipe_separated_columns_as_a_table(self):
        blocks = render.console_blocks(MARKET_SWEEP)
        tables = [b for b in blocks if b["kind"] == "table"]
        self.assertEqual(len(tables), 1)
        self.assertEqual(len(tables[0]["rows"]), 4)
        self.assertIn("C.J. Stroud", tables[0]["rows"][1])
        self.assertIn("+1.67", tables[0]["rows"][3])

    def test_stdout_json_finds_the_document_after_the_chatter(self):
        text = "[PRE-FLIGHT SUCCESS] 1249 Projections Validated.\n{\n \"as_of\": \"x\",\n \"banked\": 19.1\n}\n"
        self.assertEqual(render.stdout_json(text)["banked"], 19.1)
        self.assertIsNone(render.stdout_json("no json here"))
        self.assertEqual(render.stdout_json("[\n {\"team\": \"a\"}\n]")[0]["team"], "a")

    def test_json_tool_documents_render_as_tables(self):
        v = render.record_view({"as_of": "2026-09-27T08:23:13Z", "banked": 19.1, "projected": 221.3, "starters_left": 12, "team": MY_TEAM, "opponent": TEAMS[1], "week": 3,
                                "p_head_to_head": 0.738, "p_head_to_head_inflated": 0.688, "p_beat_median": 0.884,
                                "joint_legs": {"expected_wins": 1.63, "n": 40000, "p_2_0": 0.73, "p_1_1": 0.169, "p_0_2": 0.101, "p_2_0_independent": 0.659, "p_0_2_independent": 0.03},
                                "league": {MY_TEAM: {"banked": 19.1, "left": 12, "p_beat_median": 0.88, "projected": 221.3}}}, tool="live_matchup")
        self.assertEqual(v["title"], "Live matchup")
        self.assertEqual(v["tiles"][2]["v"], "73.8%")
        self.assertEqual(v["sections"][0]["rows"][0]["cells"][1]["text"], "73.0%")
        luck = render.record_view([{"team": MY_TEAM, "season": "2026", "weeks": [1, 2, 3], "schedule_luck": {"actual_wins": 2, "expected_wins": 2.29, "delta": -0.29, "se": 0.74, "z": None}}], tool="luck_ledger")
        self.assertEqual(luck["title"], "Luck ledger")
        self.assertEqual(luck["sections"][0]["rows"][0]["cells"][1]["text"], "-0.29")
        health = render.record_view({"checks": [{"name": "a", "verdict": "PASS", "detail": "ok", "players": 1}], "season": "2026", "week": 3, "verdict": "PASS"}, tool="data_health")
        self.assertEqual(health["tiles"][1]["s"], "all passing")

    def test_line_chart_keeps_the_last_label_inside_the_drawing(self):
        svg = str(render.line_chart([{"name": "p", "values": [80.0, 90.0, 93.5], "cls": "me"}], ["wk 1", "wk 2", "wk 3"], unit="%", nd=1, y_min=0, y_max=100, height=200))
        ys = [float(m) for m in re.findall(r'<text class="vl" x="[\d.]+" y="([\d.-]+)" text-anchor="end"', svg)]
        self.assertTrue(ys and all(y >= 0 for y in ys), ys)
        self.assertIn("93.5%", svg)
        self.assertIn("<title>p · wk 3: 93.5%</title>", svg)
        self.assertEqual(svg.count("<polyline"), 1)
        # headroom: without an explicit ceiling the top value still sits below the frame's top
        svg2 = str(render.line_chart([{"name": "p", "values": [1.0, 3.0, 5.0], "cls": "pos"}], ["a", "b", "c"], nd=1, y_min=0))
        top_y = min(float(m) for m in re.findall(r'<circle[^>]*cy="([\d.]+)"', svg2))
        self.assertGreater(top_y, 14)


class TestDecisionsReport(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_joins_each_move_to_its_evaluation_and_sums_each_teams_own_moves(self):
        r = glance.decisions_report(self.root, MY_TEAM)
        self.assertEqual(r["n"], 4)
        self.assertEqual((r["n_evaluated"], r["n_skipped"], r["n_pending"]), (3, 1, 0))
        by_id = {d["id"]: d for d in r["decisions"]}
        self.assertEqual(by_id["t1"]["label"], "waiver claim")
        self.assertEqual(by_id["t1"]["actor_effect"]["playoff"], 3.0)
        self.assertEqual(by_id["t3"]["teams"], [TEAMS[1], TEAMS[2]])
        self.assertEqual(by_id["t3"]["effect"][TEAMS[2]]["playoff"], 5.0)
        self.assertIn("roster drift", by_id["t4"]["skipped"])
        ledger = {L["team"]: L for L in r["ledger"]}
        self.assertEqual(ledger[MY_TEAM]["playoff"], 3.0)
        self.assertEqual(ledger[MY_TEAM]["moves"], 2)                 # t1 and t4 (t4 skipped, still a move)
        self.assertEqual(ledger[MY_TEAM]["evaluated"], 1)
        self.assertAlmostEqual(ledger[TEAMS[1]]["playoff"], 2.0 - 4.0)   # its add plus its side of the trade
        self.assertEqual(ledger[TEAMS[2]]["playoff"], 5.0)
        self.assertEqual(r["my_row"]["team"], MY_TEAM)
        self.assertEqual([x["value"] for x in r["series"]], [3.0])

    def test_roster_vorp_reads_the_newest_record_that_carries_players(self):
        v = glance.roster_vorp(self.root)
        self.assertEqual(v["week"], 3)
        self.assertEqual(v["teams"][MY_TEAM]["rank"], 1)
        self.assertAlmostEqual(v["players"][MY_TEAM]["Player 0 O'Neil"]["vorp"], -0.8)
        self.assertEqual(v["replacement"][MY_TEAM]["QB"], 18.0)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        plant(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def client(self, runner=None):
        app = create_app(self.root, runner=runner or FakeRunner(), csrf_token="tok",
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def get(self, path, runner=None, code=200):
        r = self.client(runner).get(path)
        self.assertEqual(r.status_code, code, path)
        return r.get_data(as_text=True)

    def test_readable_addresses_serve_the_same_pages_as_the_paths(self):
        before = self.root.tree_digest()
        rec = self.get("/records/week-3/optimal-lineup/2026-09-24-165331")
        self.assertIn("Optimal lineup", rec)
        self.assertEqual(rec.count("<h1>"), self.get("/file/decisions/week_03/lineup_20260924T165331Z_week3.json").count("<h1>"))
        self.assertIn("Week 3", self.get("/records/week-3/weekly-digest/2026-09-24-165337/run1-pre-kickoff"))
        self.assertIn("# Week 3", self.get("/records/week-3/weekly-digest/2026-09-24-165337/run1-pre-kickoff/markdown"))
        self.assertIn("Roster grades", self.get("/records/week-3/archive/roster-grades/2026-09-26-120000"))
        self.get("/records/week-3/optimal-lineup/1999-01-01-000000", code=404)
        for path in ("/forecasts", "/forecasts/week-3", "/league", "/records", "/records/week-3", "/records/ad-hoc", "/logs/decision-log", "/decisions"):
            r = self.client().get(path, follow_redirects=True)
            self.assertEqual(r.status_code, 200, path)
        for old in ("/weeks", "/weeks/3", "/current", "/decisions/3", "/decisions/adhoc", "/logs/decision_log.jsonl"):
            r = self.client().get(old, follow_redirects=True)
            self.assertEqual(r.status_code, 200, old)
        listing = self.get("/records/week-3")
        self.assertIn('href="/records/week-3/optimal-lineup/2026-09-24-165331"', listing)
        self.assertNotIn('class="main" href="/file/', listing)
        self.assertIn('href="/team/', self.get("/"))                    # UI-A1: a team link opens the team page
        self.assertIn('href="/forecasts/week-3"', self.get("/forecasts"))
        self.assertEqual(self.root.tree_digest(), before)

    def test_decisions_tab_lists_every_move_with_its_effect(self):
        body = self.get("/decisions")
        self.assertIn("Decisions", body)
        self.assertIn("Nick Bolton", body)
        self.assertIn("+3.0", body)
        self.assertIn("waiver claim", body)
        self.assertIn("not measurable", body)
        self.assertIn("Who has helped themselves", body)
        self.assertIn('data-f="moved"', body)
        self.assertIn("Josh Jacobs", body)

    def test_league_page_shows_vorp_overall_and_by_player_without_a_sideways_scroll(self):
        body = self.get("/league")
        self.assertIn("Lineup VORP", body)
        self.assertIn("my lineup VORP", body)
        self.assertIn("+30.0", body)
        self.assertIn(">VORP<", body)
        self.assertIn("-0.8", body)
        self.assertIn('class="roster"', body)
        self.assertNotIn('<div class="scroller"><table class="roster"', body)
        self.assertIn("/records/week-3/archive/roster-grades/2026-09-26-120000", body)

    def test_statuses_are_abbreviated_on_roster_cards(self):
        rosters = json.load(open(os.path.join(self.td.name, "data", "current", "live_rosters.json"), encoding="utf-8"))
        rosters[TEAMS[1]][0]["injury_status"] = "Questionable"
        with open(os.path.join(self.td.name, "data", "current", "live_rosters.json"), "w", encoding="utf-8") as fh:
            json.dump(rosters, fh)
        body = self.get("/league")
        self.assertIn('<span class="st q" title="Questionable">Q</span>', body)

    def test_log_page_is_a_table_and_the_whole_file_link_is_the_whole_file(self):
        body = self.get("/logs/decision-log")
        self.assertIn("<table", body)
        self.assertIn("transaction id", body)
        self.assertIn("?raw=1", body)
        raw = self.client().get("/file/logs/decision_log.jsonl?raw=1")
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.mimetype, "text/plain")
        self.assertEqual(raw.get_data(as_text=True).count("\n"), 8)
        dl = self.client().get("/file/logs/decision_log.jsonl?raw=1&dl=1")
        self.assertIn("attachment", dl.headers.get("Content-Disposition", ""))

    def test_json_tools_are_forced_to_json_and_render_as_tables_on_the_job_page(self):
        from webui.tools import TOOLS
        for name in ("live_matchup", "luck_ledger", "odds_history", "data_health", "bid_review", "run_windows", "decision_scorecard"):
            self.assertIn("--json", TOOLS[name].forced, name)
            self.assertNotIn("json", [f.name for f in TOOLS[name].fields], name)
        runner = FakeRunner()
        jid = runner.launch(["py", "-m", "scripts.data_health", "--json"], "data_health")
        runner.metas[jid].update(state=OK, finished_at="2026-09-26T00:00:03Z", rc=0)
        runner.log_text = lambda j: "[INFO] chatter\n{\n \"checks\": [{\"name\": \"Sleeper projections\", \"verdict\": \"PASS\", \"detail\": \"1248 of 1248\", \"players\": 1248}],\n \"season\": \"2026\", \"week\": 3, \"verdict\": \"PASS\"\n}\n"
        body = self.get(render.job_url({"id": jid}), runner=runner)
        self.assertIn("Data health", body)
        self.assertIn("Every source", body)
        self.assertIn("all passing", body)

    def test_no_stylesheet_rule_invites_a_sideways_scroll_above_phone_width(self):
        css = self.get("/")
        head = css.split("</style>")[0]
        rules = [ln for ln in head.splitlines() if "overflow-x: auto" in ln]
        # one exception, by the owner's report of 2026-09-29: `.scroller.xscroll`, which the
        # page's script sets only on a table measured wider than its column -- the page clips
        # what spills, so without it the standings' last columns were unreadable, not scrolled
        self.assertTrue(all("@media (max-width" in ln or ln.startswith(".scroller.xscroll {") for ln in rules), rules)
        self.assertNotIn("white-space: nowrap; }", [ln for ln in head.splitlines() if ln.startswith("td.nm")])


if __name__ == "__main__":
    unittest.main()

"""
tests.test_webui_fifth -- the audit's Phase 1 (bug sweep) and Phase 2 (phone and tables),
docs/WEB_UI_AUDIT.md. Each test names the finding it pins.

B2 the ledger bars are block elements; B3 no 'None' for a drop without a projection; B4 the
watch list shows a signed change, never a doubled sign; B5 an h3 hint is styled; B6 the
gradient title rule targets h1 itself; B7 sticky table headers and anchors sit below the
header; B8 STALE shows its cause, never the degraded list; B9 labels keep their case and
help never repeats the default; B10 every link on every simple page resolves; B11 the sync
source is printed once; B12 every finished job has an answer link and no repeated name;
B15 seed tint scales to the table's max; B16 status pills carry line-height 1; B17 a large
file is offered as a download; B18 windows have names; B22 the waiver spec is two tables;
B24 the log page carries no per-row JSON and defaults to 100; B1 the no-break rules and the
scroller at every width; and the audit hook renders only with ?audit=1.
"""
import json
import os
import re
import tempfile
import unittest

from webui import glance, render
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.jobs import OK
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_fourth import plant as plant_fourth
    from tests.test_webui_home import enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import SIMPLE_PAGES
    from tests.test_webui_routes import TEAMS, build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


class TestHelpers(unittest.TestCase):
    def test_sentence_keeps_the_rest_of_the_label(self):
        self.assertEqual(render.sentence("player A"), "Player A")
        self.assertEqual(render.sentence("A gives"), "A gives")
        self.assertEqual(render.sentence(""), "")

    def test_window_titles_are_words(self):
        self.assertEqual(render.window_title("run3_tuesday"), "Tuesday run")
        self.assertEqual(render.window_title("run1_pre_kickoff"), "Pre-kickoff run")
        self.assertEqual(render.window_title("run9_thursday_night"), "Thursday night")
        e = {"tool": "weekly_report", "name": "weekly_report_week3_run1_pre_kickoff_20260924T165337Z.html"}
        self.assertEqual(render.entry_title(e), "Weekly digest · pre-kickoff run")

    def test_job_subtitle_drops_the_tool_name(self):
        self.assertEqual(render.job_subtitle({"tool": "luck_ledger", "label": "Luck ledger · 2026"}), "2026")
        self.assertEqual(render.job_subtitle({"tool": "compare_players", "label": "Compare players · A vs B"}), "A vs B")
        self.assertEqual(render.job_subtitle({"tool": "roster_grades", "label": "roster_grades"}), "")
        self.assertEqual(render.job_subtitle({"tool": "compare_players", "label": "compare_players 2000 X Y"}), "2000 X Y")

    def test_waiver_spec_is_a_primary_table_and_a_folded_one(self):
        v = render.record_view({"tool": "waiver_targets", "team": MY_TEAM if HAS_FLASK else "Quantum Ferrets", "week": 3,
                                "targets": [{"name": "A", "pos": "RB", "team": "KC", "tier": 2, "mean": 9.1, "vorp": 1.2, "fills": "RB", "incumbent": "B",
                                             "week": {"mean": 8.0, "p90": 14, "p_zero": 0.1}, "bid": {"suggested": 3, "v2": {"point": 4, "low": 2, "high": 6}}, "bye": 9}],
                                "holes": [], "remaining_faab": 40, "league_avg_faab": 50})
        tables = [s for s in v["sections"] if s["kind"] == "table"]
        self.assertEqual(len(tables[0]["columns"]), 9)
        self.assertFalse(tables[0]["collapsed"])
        self.assertTrue(tables[1]["collapsed"])

    def test_freshness_splits_the_stale_cause_from_the_degraded_list(self):
        with tempfile.TemporaryDirectory() as td:
            build_tree(td)
            fr = glance.freshness_report(Root(td))
            self.assertIn("stale_reasons", fr)
            self.assertTrue(all(not r.startswith("degraded:") for r in fr["stale_reasons"]))
            self.assertEqual(len(fr["degraded_reasons"]), sum(1 for r in fr["reasons"] if str(r).startswith("degraded:")))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        plant_fourth(cls.td.name)
        from tests.test_webui_render import LINEUP
        with open(os.path.join(cls.td.name, "data", "decisions", "week_03", "lineup_20260924T165331Z_week3.json"), "w", encoding="utf-8") as fh:
            json.dump(LINEUP, fh)                       # a questionable starter for the watch list
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def client(self, mode="dev", runner=None):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=runner or FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def get(self, path, mode="dev", runner=None, code=200):
        r = self.client(mode, runner).get(path)
        self.assertEqual(r.status_code, code, path)
        return r.get_data(as_text=True)

    def test_base_css_carries_the_phone_and_sticky_fixes(self):
        page = self.get("/")
        css = page.split("</style>")[0] + page.split("<style>")[-1].split("</style>")[0]   # base + the page's own block
        self.assertIn("td.num, th.num, .tnum, td.nm { white-space: nowrap; }", css)                # B1
        self.assertNotIn("td { padding: 6px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; overflow-wrap: anywhere; }", css)
        self.assertIn("@media (max-width: 720px) { .scroller { overflow-x: auto;", css)             # B1: inside its box below tablet width
        self.assertIn("th { position: static; } }", css)                                            # ...where a sticky header would sit mid-table
        self.assertIn(".game > span { min-width: 0; overflow: hidden; text-overflow: ellipsis;", css)  # the games list shrinks, never spills
        self.assertIn(".wrap { overflow-x: clip; }", css)
        self.assertIn("position: sticky; top: var(--head-h, 0px); }", css)                          # B7
        self.assertIn("[id] { scroll-margin-top: calc(var(--head-h, 0px) + 12px); }", css)
        self.assertIn("h3 .hint {", css)                                                            # B5
        self.assertIn("h1 { background: var(--g-brand);", css)                                      # B6
        self.assertNotIn("h1 > :first-child", css)
        self.assertIn(".hbar .b { display: block;", css)                                            # B2
        self.assertIn("line-height: 1; padding: 3px 5px;", css)                                     # B16

    def test_home_watch_list_shows_a_signed_change_and_a_named_window(self):
        body = self.get("/")
        self.assertNotIn("−-", body)                                                                # B4
        self.assertIn(">Change<", body)
        self.assertIn("-2.3", body)                                                                 # Coker's fixture give_up 2.3, as a signed change
        self.assertNotIn("run3_tuesday", body)                                                      # B18
        records = self.get("/records/week-3")
        self.assertIn("Weekly digest · pre-kickoff run", records)                                    # the file name stays a hover title only
        self.assertNotIn("run1 pre kickoff", records)

    def test_decisions_has_block_bars_and_no_none(self):
        body = self.get("/decisions")
        self.assertIn('<span class="b ', body)                                                      # B2
        self.assertNotIn('<span class="neg"><span class="b">', body)
        self.assertNotIn(" None ", body)                                                            # B3
        rows = [json.loads(l) for l in open(os.path.join(self.td.name, "data", "logs", "decision_log.jsonl"), encoding="utf-8") if l.strip()]
        rows.append({"transaction_id": "t9", "type": "free_agent", "week": 3, "created": "2026-09-22T12:00:00Z", "is_mine": False, "teams": [TEAMS[2]],
                     "adds": [{"name": "Nate Landman", "player_id": "1", "to_team": TEAMS[2], "projection": {"pos": "LB", "mean": 9.9}}],
                     "drops": [{"name": "Nick Bosa", "player_id": "2", "to_team": TEAMS[2], "projection": {}}]})
        with open(os.path.join(self.td.name, "data", "logs", "decision_log.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
        body = self.get("/decisions")
        self.assertIn("Nick Bosa", body)
        self.assertNotIn("Nick Bosa None", body)
        self.assertNotIn("None ·", body)

    def test_stale_pages_show_the_cause_not_the_degraded_wall(self):
        # make the fixture STALE the way the sandbox was: the export predates the sync
        p = os.path.join(self.td.name, "data", "current", "sync_manifest.json")
        m = json.load(open(p, encoding="utf-8"))
        m["degraded"] = ["WARNING | x %d fell back" % i for i in range(30)]
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        export = os.path.join(self.td.name, "data", "weeks", "week_03", "syndicate_comprehensive_matrix_week_3.json")
        os.utime(export, (1_600_000_000, 1_600_000_000))     # the export predates the sync: STALE
        try:
            fr = glance.freshness_report(self.root)
            self.assertEqual(fr["status"], "STALE")
            body = self.get("/system")
            lede = re.search(r'<p class="lede">(.*?)</p>', body, re.S).group(1)
            self.assertLess(len(lede), 600, "the STALE lede is a sentence, not the degraded list")
            self.assertNotIn("x 29 fell back", lede)                                                # B8
            self.assertIn("What fell back", body)
            form = self.get("/tools/weekly_report")
            self.assertNotIn("<li>WARNING | x 29", form)
            r = self.client().post("/tools/weekly_report", data={"_csrf": "tok", "team": MY_TEAM})
            self.assertEqual(r.status_code, 409)
            self.assertNotIn("x 29 fell back", r.get_data(as_text=True))
        finally:
            m["degraded"] = ["WARNING | odds: fell back for Quantum Ferrets' game"]
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(m, fh)
            os.utime(export, None)

    def test_tool_form_labels_and_help(self):
        body = self.get("/tools/compare_players")
        self.assertIn(">Player A<", body)                                                           # B9
        self.assertNotIn(">Player a<", body)
        self.assertNotIn("default 2000 · default 2000", body)
        self.assertEqual(body.count("default 2000"), 1)

    def test_every_link_on_every_simple_page_resolves(self):
        c = self.client("simple")
        seen = set()
        for path in SIMPLE_PAGES:
            html = c.get(path).get_data(as_text=True)
            for href in re.findall(r'href="(/[^"#]*)"', html):
                if href in seen or href.startswith("/file/") or "/jobs/" in href:
                    continue
                seen.add(href)
                with self.subTest(page=path, href=href):
                    self.assertEqual(c.get(href).status_code, 200, f"{href} linked from {path}")     # B10
        self.assertNotIn("/tools/roster_grades", c.get("/league").get_data(as_text=True))

    def test_sync_source_is_printed_once(self):
        body = self.get("/sync")
        self.assertNotIn("Windows User scope (the Windows User scope)", body)                         # B11

    def test_jobs_answer_links_and_subtitles(self):
        runner = FakeRunner()
        a = runner.launch(["py", "-m", "scripts.luck_ledger", "--json"], "luck_ledger", label="Luck ledger · 2026")
        runner.metas[a].update(state=OK, finished_at="2026-09-26T00:00:05Z", rc=0)
        body = self.get("/jobs", runner=runner)
        self.assertIn("open answer", body)                                                          # B12
        self.assertNotIn("Luck ledger · 2026", body)
        self.assertIn(">2026<", body)

    def test_large_files_are_offered_as_a_download_not_a_page(self):
        p = os.path.join(self.td.name, "data", "current", "sleeper_players_cache.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({str(i): {"first_name": "A", "last_name": "B", "team": "KC"} for i in range(40000)}))
        try:
            r = self.client().get("/file/current/sleeper_players_cache.json")
            self.assertEqual(r.status_code, 200)
            self.assertLess(len(r.data), 60_000, "not pretty-printed")                              # B17
            self.assertIn("Download it", r.get_data(as_text=True))
            raw = self.client().get("/file/current/sleeper_players_cache.json?raw=1")
            self.assertGreater(len(raw.data), 1_000_000)
        finally:
            os.remove(p)

    def test_log_page_defaults_to_100_rows_without_per_row_json(self):
        body = self.get("/logs/decision-log")
        self.assertNotIn("<details><summary>json</summary>", body)                                    # B24
        self.assertIn("?n=100", body)

    def test_seed_heat_map_scales_to_its_max(self):
        body = self.get("/forecasts/week-3")
        self.assertIn("rgba(107,91,210,0.85)", body, "the likeliest seed is fully tinted")          # B15

    def test_the_audit_hook_renders_only_when_asked(self):
        self.assertNotIn("data-audit-scroll", self.get("/"))
        self.assertIn("data-audit-scroll", self.get("/?audit=1"))


if __name__ == "__main__":
    unittest.main()

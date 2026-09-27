"""
tests.test_webui_polish -- the second visual pass: every label a person can read, nothing
that should be a link left as text, walls of text folded, nested runner artifacts found.

Pinned here: the humanising filters (ago / duration / when / slug / log_title), digest
pairing (html + md as one row), record titles from filenames, nested values never shown
as a Python repr, upper-case paired sides sorted as principals, a tool page pre-filled
from the query string, DEGRADED fallbacks folded on System and the report form, team
anchors on the League page, my odds on the Forecasts list, one merged table on a week
page, the Jobs page saying how long a run took, and the team mark's hue coming from the
same function as everywhere else.
"""
import copy
import re
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui import glance, render
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from tests.test_webui_home import enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_render import TRADE
    from tests.test_webui_routes import TEAMS, build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


class TestHumanisers(unittest.TestCase):
    def test_ago_reads_in_the_unit_a_person_would_pick(self):
        self.assertEqual(render.ago(0.4), "24 min ago")
        self.assertEqual(render.ago(37.7), "38 h ago")
        self.assertEqual(render.ago(50), "2 d ago")
        self.assertEqual(render.ago(None), "—")

    def test_duration_drops_the_units_that_are_zero(self):
        self.assertEqual(render.duration(1), "1 s")
        self.assertEqual(render.duration(95), "1 m 35 s")
        self.assertEqual(render.duration(120), "2 m")
        self.assertEqual(render.duration(3720), "1 h 2 m")
        self.assertEqual(render.duration("x"), "—")

    def test_when_accepts_both_stamp_shapes(self):
        self.assertEqual(render.when("2026-09-27T05:59:45Z"), "Sep 27 05:59Z")
        self.assertEqual(render.when("20260924T165331Z"), "Sep 24 16:53Z")
        self.assertEqual(render.when(None), "—")

    def test_slug_is_url_safe_and_stable(self):
        self.assertEqual(render.slug("Quantum Ferrets"), "quantum-ferrets")
        self.assertEqual(render.slug("O'Neil & Sons!"), "o-neil-sons")

    def test_log_title_names_the_season_logs_and_falls_back_to_the_stem(self):
        self.assertEqual(render.log_title("predictions_2026.jsonl")[0], "Predictions 2026")
        self.assertTrue(render.log_title("decision_log.jsonl")[1])
        self.assertEqual(render.log_title("weekly_actuals_new_idp_scale_2026_09_24.json")[0],
                         "Weekly actuals new idp scale 2026 09 24")
        self.assertEqual(render.log_title("mystery_thing.jsonl"), ("mystery thing", ""))

    def test_tool_title_and_entry_title_read_as_words(self):
        self.assertEqual(render.tool_title("optimize_lineup"), "Optimal lineup")
        self.assertEqual(render.tool_title("brand_new_tool"), "Brand new tool")
        e = {"tool": "compare", "name": "compare_20260926T203839Z_C.J._Stroud_vs_Jordan_Love.json"}
        self.assertEqual(render.entry_title(e), "Compare players · C.J. Stroud vs Jordan Love")
        d = {"tool": "weekly_report", "name": "weekly_report_week3_run1_pre_kickoff_20260924T165337Z.html"}
        self.assertEqual(render.entry_title(d), "Weekly digest · run1 pre kickoff")

    def test_pair_digests_shows_the_html_once_with_the_markdown_attached(self):
        entries = [{"name": "weekly_report_x.html", "ext": "html", "link": "/file/h"},
                   {"name": "weekly_report_x.md", "ext": "md", "link": "/file/m"},
                   {"name": "lineup_y.json", "ext": "json", "link": "/file/j"}]
        rows = render.pair_digests(entries)
        self.assertEqual([r["name"] for r in rows], ["weekly_report_x.html", "lineup_y.json"])
        self.assertEqual(rows[0]["md_link"], "/file/m")
        self.assertNotIn("md_link", rows[1])

    def test_join_never_shows_a_python_repr(self):
        self.assertEqual(render._join(["a", "b"]), "a, b")
        self.assertEqual(render._join({"k": [1, 2], "z": {"n": True}}), "k: 1, 2; z: n: yes")
        self.assertEqual(render._join(1.5), "1.50")
        self.assertEqual(render._join([]), "—")
        self.assertNotIn("[", render._join({"drops": {"a": ["x", "y"]}}))

    def test_paired_sides_in_upper_case_still_sort_as_principals(self):
        d = copy.deepcopy(TRADE)
        for t in d["teams"].values():
            t["side"] = t["side"].upper()
        v = render.record_view(d)
        rows = v["sections"][0]["rows"]
        self.assertEqual([r["cells"][0]["text"] for r in rows][:2], ["Quantum Ferrets", "Rocket Pandas"])
        self.assertEqual(rows[0]["cells"][1]["text"], "A")
        self.assertEqual(v["tiles"][0]["k"], "Quantum Ferrets · playoff")
        self.assertEqual(rows[0]["cells"][0]["link"], "team")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def client(self, runner=None):
        app = create_app(self.root, runner=runner or FakeRunner(), csrf_token="tok")
        app.testing = True
        return app.test_client()

    def get(self, path, runner=None):
        r = self.client(runner).get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)

    def test_results_page_finds_the_nested_runner_artifact(self):
        listing = self.root.results()
        self.assertEqual(listing[0]["week"], 1)
        self.assertEqual(listing[0]["runs"][0]["files"][0]["name"], "weekly_report_week1_20260910T000000Z.html")
        body = self.get("/results")
        self.assertIn("weekly_report_week1_20260910T000000Z.html", body)
        self.assertIn("Weekly digest", body)

    def test_records_page_uses_titles_and_pairs_the_digest(self):
        body = self.get("/decisions/3")
        self.assertIn("Weekly digest · run1 pre kickoff", body)
        self.assertIn(">markdown</a>", body)
        self.assertEqual(body.count("weekly_report_week3_run1_pre_kickoff_20260924T165337Z.md"), 1)
        self.assertIn("Optimal lineup", body)
        self.assertIn("Sep 24 16:53Z", body)

    def test_league_page_anchors_every_team_and_links_the_standings_to_them(self):
        body = self.get("/current")
        for t in TEAMS:
            self.assertIn(f'id="t-{render.slug(t)}"', body)
            self.assertIn(f'href="#t-{render.slug(t)}"', body)
        self.assertIn("Playoff odds", body)
        self.assertIn("93.5%", body)                                   # from the week-3 export
        self.assertIn("/tools/evaluate_trade?team_b=", body)

    def test_team_mark_hue_is_the_shared_function(self):
        body = self.get("/current")
        self.assertIn(f"hsl({glance.team_hue(MY_TEAM)} 55% 42%)", body)
        self.assertNotIn("data-hue-of", body)

    def test_forecasts_list_carries_my_odds_not_a_team_count(self):
        body = self.get("/weeks")
        self.assertIn("Playoff odds", body)
        self.assertIn("93.5%", body)
        self.assertNotIn("Teams forecast", body)

    def test_week_page_has_one_merged_table_and_human_file_names(self):
        body = self.get("/weeks/3")
        self.assertEqual(body.count("Playoff</th>"), 1)
        self.assertNotIn("Season outcomes", body)
        self.assertIn("Season forecast", body)
        self.assertIn("Full results matrix", body)
        self.assertIn("Files from this run", body)

    def test_system_page_folds_the_degraded_list_and_humanises_the_sync_age(self):
        body = self.get("/system")
        self.assertIn("What fell back", body)
        self.assertIn('class="n">1</span>', body)
        self.assertIsNone(re.search(r"\d+\.\d h ago", body), "the raw '37.7 h ago' form is gone")
        self.assertIn(" ago", body)
        self.assertIn("Sep 25 17:13Z", body)

    def test_report_form_folds_the_degraded_list_instead_of_listing_it_in_the_banner(self):
        body = self.get("/tools/weekly_report")
        self.assertIn("pill DEGRADED", body)
        self.assertIn("What fell back", body)
        self.assertNotIn("<li>WARNING", body)
        self.assertIn("include the trade finder", body)                  # labels come from LABELS, never raw flags
        self.assertIn("canonical run", body)
        self.assertNotIn(">sims<", body)

    def test_tool_page_prefills_from_the_query_string(self):
        body = self.get("/tools/compare_players?a=Patrick+Mahomes&b=Jordan+Love")
        self.assertIn('value="Patrick Mahomes"', body)
        self.assertIn('value="Jordan Love"', body)
        self.assertIn("Compare players", body)

    def test_home_watch_list_links_each_questionable_starter_to_a_comparison(self):
        body = self.get("/")
        self.assertIn(f"hsl({glance.team_hue(MY_TEAM)} 55% 42%)", body)
        self.assertIn("synced", body)
        self.assertNotIn("pricedpriced", body)
        self.assertIn("This week's games", body)

    def test_jobs_page_says_how_long_a_run_took(self):
        runner = FakeRunner()
        jid = runner.launch(["py", "-m", "x"], "optimize_lineup", label="optimize_lineup Quantum Ferrets week 3")
        runner.metas[jid].update(state="OK", finished_at="2026-09-26T00:01:35Z", rc=0)
        body = self.get("/jobs", runner=runner)
        self.assertIn("Optimal lineup", body)
        self.assertIn("1 m 35 s", body)
        self.assertNotIn(">Exit<", body)
        page = self.get(f"/jobs/{jid}", runner=runner)
        self.assertIn("took <span class=\"elapsed\">1 m 35 s</span>", page)

    def test_tools_page_groups_the_tools_and_names_them_as_words(self):
        body = self.get("/tools")
        self.assertIn("This week", body)
        self.assertIn("Players &amp; trades", body)
        self.assertIn("Optimal lineup", body)
        self.assertNotIn(">optimize lineup<", body)

    def test_logs_page_describes_each_log(self):
        body = self.get("/logs")
        self.assertIn("Decision log", body)
        self.assertIn("Predictions 2026", body)
        self.assertIn("decision_log.jsonl", body)


if __name__ == "__main__":
    unittest.main()

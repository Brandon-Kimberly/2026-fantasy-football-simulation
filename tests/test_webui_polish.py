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

    def test_when_reads_as_local_wall_clock_time_for_every_stamp_shape(self):
        import datetime as dt
        iso = render.when("2026-09-27T05:59:45Z")
        compact = render.when("20260924T165331Z")
        self.assertRegex(iso, r"^[A-Z][a-z]{2} \d{1,2}(, \d{4})?, \d{1,2}:\d{2} [ap]m$")
        self.assertEqual(iso, render.when(dt.datetime(2026, 9, 27, 5, 59, 45, tzinfo=dt.timezone.utc)))
        self.assertEqual(compact, render.when(1790268811))           # the same instant as epoch seconds
        self.assertNotIn("Z", iso)
        self.assertEqual(render.when(None), "—")
        self.assertIn("2025", render.human_time("2025-01-05T12:00:00Z"))   # another year is named

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
        self.assertEqual(render.entry_title(d), "Weekly digest · pre-kickoff run")

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
        self.assertIn("Weekly digest · pre-kickoff run", body)
        self.assertIn(">markdown</a>", body)
        self.assertEqual(body.count("weekly_report_week3_run1_pre_kickoff_20260924T165337Z.md"), 1)
        self.assertIn("Optimal lineup", body)
        self.assertIn(render.when("20260924T165331Z"), body)

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
        self.assertIn("Sync warnings", body)            # UI-F2: warnings, not sources
        self.assertIn('class="n">1</span>', body)
        self.assertIsNone(re.search(r"\d+\.\d h ago", body), "the raw '37.7 h ago' form is gone")
        self.assertIn(" ago", body)
        self.assertIn(render.when("2026-09-25T17:13:20Z"), body)

    def test_report_form_folds_the_degraded_list_instead_of_listing_it_in_the_banner(self):
        body = self.get("/tools/weekly_report")
        self.assertIn("pill DEGRADED", body)
        self.assertIn("Sync warnings", body)            # UI-F2: warnings, not sources
        self.assertNotIn("<li>WARNING", body)
        self.assertIn("include the trade finder", body.lower())          # labels come from LABELS, never raw flags
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


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestThirdPass(unittest.TestCase):
    """Avatars, average run times, range bars, hidden file names, the form redesign."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def client(self, runner=None, overlay=None):
        from webui.live import LiveBoard
        app = create_app(self.root, runner=runner or FakeRunner(), csrf_token="tok", overlay=overlay,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def test_avatars_render_as_images_only_with_real_names_and_fall_back_to_initials(self):
        from webui.names import Overlay
        ov = Overlay({MY_TEAM: "CANARY-REAL-ME"}, avatars={MY_TEAM: "https://example.invalid/CANARY-AVATAR.png"})
        self.assertEqual(ov.avatar(MY_TEAM), "https://example.invalid/CANARY-AVATAR.png")
        self.assertIsNone(ov.avatar(TEAMS[1]))
        body = self.client(overlay=ov).get("/current").get_data(as_text=True)
        self.assertIn('<img class="mark " src="https://example.invalid/CANARY-AVATAR.png"', body)
        self.assertIn(f"hsl({glance.team_hue(TEAMS[1])} 55% 42%)", body, "a team without an avatar keeps its initials mark")
        off = Overlay({}, avatars={MY_TEAM: "https://example.invalid/CANARY-AVATAR.png"})
        self.assertIsNone(off.avatar(MY_TEAM), "no real names, no avatars: the mark identifies the account the way a name does")
        self.assertNotIn("CANARY-AVATAR", self.client(overlay=off).get("/current").get_data(as_text=True))

    def test_tools_page_shows_the_average_of_recent_runs_not_a_vague_word(self):
        from webui.jobs import JobRunner
        runner = FakeRunner()
        for secs in (120, 150, 210):
            jid = runner.launch(["py", "-m", "x"], "optimize_lineup")
            runner.metas[jid].update(state="OK", finished_at=f"2026-09-26T00:{secs // 60:02d}:{secs % 60:02d}Z", rc=0)
        runner.average_seconds = lambda tool, n=5: JobRunner.average_seconds(runner, tool, n)
        self.assertEqual(runner.average_seconds("optimize_lineup"), (160.0, 3))
        self.assertEqual(runner.average_seconds("waiver_targets"), (None, 0))
        body = self.client(runner=runner).get("/tools").get_data(as_text=True)
        self.assertIn("avg <b>2.7 m</b>", body)
        self.assertIn("average of the last 3 successful runs", body)
        self.assertIn("~seconds", body)
        page = self.client(runner=runner).get("/tools/optimize_lineup").get_data(as_text=True)
        self.assertIn("<b>2.7 m</b>average of the last 3 runs", page)

    def test_duration_short(self):
        self.assertEqual(render.duration_short(45), "45 s")
        self.assertEqual(render.duration_short(138), "2.3 m")
        self.assertEqual(render.duration_short(4000), "1.1 h")

    def test_range_cells_draw_one_band_per_row_on_a_shared_scale(self):
        cols = [render.col("name"), render.col("band", "band", "range", nd=0, lo="p10", mid="p50", hi="p90")]
        t = render.table("x", cols, [{"name": "a", "p10": 5, "p50": 10, "p90": 20}, {"name": "b", "p10": 10, "p50": 30, "p90": 40}])
        self.assertEqual(t["rows"][0]["cells"][1]["range"]["max"], 40)
        self.assertEqual(t["rows"][1]["cells"][1]["range"]["max"], 40)
        self.assertEqual(render.table("x", cols, [{"name": "c"}])["rows"][0]["cells"][1]["text"], "—")
        rec = {"tool": "optimize_lineup", "team": MY_TEAM, "week": 3, "expected_total": 1.0,
               "lineup": [{"slot": "QB", "name": "Q", "pos": "QB", "expected": 20, "p10": 10, "p50": 20, "p90": 30, "p_zero": 0.01}]}
        with open(os.path.join(self.td.name, "data", "decisions", "week_03", "lineup_20260925T000000Z_week3.json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        body = self.client().get("/file/decisions/week_03/lineup_20260925T000000Z_week3.json").get_data(as_text=True)
        self.assertIn('<span class="rng">', body)
        self.assertIn("floor–ceiling", body)

    def test_file_names_are_hover_titles_not_page_text(self):
        body = self.client().get("/decisions/3").get_data(as_text=True)
        name = "lineup_20260924T165331Z_week3.json"
        self.assertIn(f'title="{name}"', body)
        self.assertNotIn(f">{name}<", body)
        self.assertNotIn('class="sub">' + name, body)
        logs = self.client().get("/logs").get_data(as_text=True)
        self.assertIn('title="decision_log.jsonl"', logs)
        self.assertNotIn(">decision_log.jsonl<", logs)

    def test_tool_form_is_grouped_with_toggles_and_a_command_preview(self):
        body = self.client().get("/tools/compare_players?a=Patrick+Mahomes").get_data(as_text=True)
        self.assertIn('class="grp g-who"', body)
        self.assertIn('class="grp g-how"', body)
        self.assertIn('class="grp g-opt"', body)
        self.assertIn('class="tog"', body)
        self.assertIn('id="cmd"', body)
        self.assertIn('type="number"', body)
        self.assertIn('data-arg=""', body, "positional players carry no flag in the preview")
        self.assertIn('data-arg="--sims"', body)

    def test_times_everywhere_are_local_and_never_raw_iso(self):
        for path in ("/", "/system", "/decisions/3", "/jobs", "/weeks"):
            body = self.client().get(path).get_data(as_text=True)
            self.assertNotRegex(body, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z(?![^<]*</code>)", path)
            self.assertNotRegex(body, r">[A-Z][a-z]{2} \d{2} \d{2}:\d{2}Z<", path)


if __name__ == "__main__":
    unittest.main()

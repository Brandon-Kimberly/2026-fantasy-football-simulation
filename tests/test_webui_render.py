"""
tests.test_webui_render -- the tools' output made readable (the visual pass).

Pure half: raw stdout -> blocks (chatter folded, headings, aligned tables, the record
line), and a tool's JSON record -> tiles and tables, with the generic walk covering any
record the per-tool specs do not know. Route half (skips without Flask): a job page renders
the record as tables when one exists, and a record JSON opens as tables with the raw JSON
one click away -- with the overlay applied and the tree untouched.
"""
import json
import os
import tempfile
import unittest

from webui import render
from webui.names import Overlay
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from tests.test_webui_launch import FakeRunner
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

CONSOLE = """[INFO] Imputed whitelisted missing asset: Somebody (Turbo Llamas)
[PRE-FLIGHT SUCCESS] 1249 Projections Validated.
ERROR | VEGAS STALE: 2 of 32 lines in vegas_totals.json are not for week 3

ROSTER CALENDAR -- Quantum Ferrets, weeks 3-14
  week 3
  week 5    HOLES: K
      on bye:  Harrison Butker, Jalen Coker
  HOLE: week 5 has no eligible player for K -- plan a claim before it, not during it.

  sources:
    ok         espn_projections          367 row(s)
    ok         nfl_schedule               32 row(s)
    FELL BACK  vegas_odds                 31 row(s)  <- flat 21.5 / no opponent
    ok         weather                    11 row(s)

  logged -> data\\decisions\\week_03\\archive\\roster_calendar_x_week3.json
"""

LINEUP = {"timestamp_utc": "20260927T030606Z", "tool": "optimize_lineup", "team": "Quantum Ferrets", "week": 3,
          "expected_total": 217.59, "pinned": 1, "locked_excluded": 1, "locks_active": True, "note": "the method",
          "lineup": [{"slot": "QB", "name": "Patrick Mahomes", "pos": "QB", "expected": 29.8, "p10": 12.0, "p50": 28.0, "p90": 48.0,
                      "p_zero": 0.015, "alternative": "Jordan Love", "flag": "", "margin": 11.6}],
          "unfilled": [], "bench": [{"name": "Jordan Love", "pos": "QB", "expected": 18.2, "available": True, "reason": "", "flag": ""}],
          "questionable_starters": [{"name": "Jalen Coker", "slot": "FLEX", "pos": "WR", "expected": 13.3, "fallback": "Xavier Worthy",
                                     "fallback_expected": 11.0, "give_up": 2.3}]}
COMPARE = {"timestamp_utc": "20260926T203839Z", "tool": "compare_players", "p_a": 0.5706, "p_b": 0.4224, "p_tie": 0.007, "n": 2000,
           "a_name": "C.J. Stroud", "b_name": "Jordan Love", "week": 5, "path": "mixed", "note": "n", "mean_diff": 2.8036, "se_p": 0.011,
           "a": {"n": 2000, "mean": 18.0, "p10": 6.3, "p25": 11.0, "p50": 16.2, "p75": 23.0, "p90": 31.7, "p_zero": 0.055},
           "b": {"n": 2000, "mean": 15.2, "p10": 3.2, "p25": 8.0, "p50": 13.6, "p75": 20.0, "p90": 28.5, "p_zero": 0.096}}
TRADE = {"timestamp_utc": "x", "tool": "evaluate_trade", "n_sims": 3000, "batches": 10, "note": "paired",
         "trade": {"team_a": "Quantum Ferrets", "a_gives": ["Javonte Williams"], "team_b": "Rocket Pandas", "b_gives": ["Ashton Jeanty"], "drops": {}, "faab_a_to_b": None},
         "teams": {"Quantum Ferrets": {"side": "a", "champ_pct": {"with": 36.0, "without": 35.0, "delta": 1.0, "se": 0.5},
                                       "playoff_pct": {"with": 94.0, "without": 93.5, "delta": 0.5, "se": 0.2}, "expected_wins": {"delta": 0.1}},
                   "Rocket Pandas": {"side": "b", "champ_pct": {"with": 10.0, "without": 10.5, "delta": -0.5, "se": 0.5},
                                     "playoff_pct": {"with": 63.0, "without": 63.2, "delta": -0.2, "se": 0.3}, "expected_wins": {"delta": -0.05}},
                   "Polar Yetis": {"side": "bystander", "champ_pct": {"with": 5.0, "without": 5.1, "delta": -0.1, "se": 0.3},
                                   "playoff_pct": {"with": 64.0, "without": 64.3, "delta": -0.3, "se": 0.4}, "expected_wins": {"delta": 0.0}}}}


class TestConsoleBlocks(unittest.TestCase):
    def setUp(self):
        self.blocks = render.console_blocks(CONSOLE)

    def test_engine_chatter_is_folded_into_one_collapsible_block(self):
        chatter = [b for b in self.blocks if b["kind"] == "chatter"]
        self.assertEqual(len(chatter), 1)
        self.assertEqual(len(chatter[0]["lines"]), 3)
        self.assertTrue(chatter[0]["lines"][2].startswith("ERROR | VEGAS"))

    def test_all_caps_lines_become_headings(self):
        heads = [b["text"] for b in self.blocks if b["kind"] == "heading"]
        self.assertIn("ROSTER CALENDAR -- Quantum Ferrets, weeks 3-14", heads)
        self.assertNotIn("week 3", heads)

    def test_column_aligned_runs_become_tables_with_padded_rows(self):
        tables = [b for b in self.blocks if b["kind"] == "table"]
        self.assertEqual(len(tables), 1)
        rows = tables[0]["rows"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][:2], ["ok", "espn_projections"])
        self.assertEqual(rows[2][0], "FELL BACK")
        self.assertTrue(all(len(r) == len(rows[0]) for r in rows), "rows padded to one width")

    def test_the_record_line_is_its_own_block_with_the_path(self):
        rec = [b for b in self.blocks if b["kind"] == "record"]
        self.assertEqual(len(rec), 1)
        self.assertTrue(rec[0]["path"].endswith("roster_calendar_x_week3.json"))

    def test_everything_else_keeps_its_indentation(self):
        paras = [b["text"] for b in self.blocks if b["kind"] == "para"]
        self.assertTrue(any("      on bye:  Harrison Butker" in p for p in paras))

    def test_empty_and_plain_text_do_not_crash(self):
        self.assertEqual(render.console_blocks(""), [])
        self.assertEqual(render.console_blocks(None), [])
        self.assertEqual([b["kind"] for b in render.console_blocks("just a line")], ["para"])


class TestRecordViews(unittest.TestCase):
    def test_lineup_record_has_the_headline_tiles_and_the_three_tables(self):
        v = render.record_view(LINEUP)
        self.assertEqual(v["tool"], "optimize_lineup")
        self.assertEqual(v["tiles"][0]["v"], "217.6")
        self.assertEqual(v["tiles"][1]["v"], "1")
        titles = [s["title"] for s in v["sections"]]
        self.assertEqual(titles[:3], ["Starters", "Questionable starters", "Bench"])
        starters = v["sections"][0]
        self.assertEqual([c["label"] for c in starters["columns"]][:3], ["slot", "name", "pos"])
        cells = starters["rows"][0]["cells"]
        self.assertEqual(cells[1]["text"], "Patrick Mahomes")
        self.assertEqual(cells[7]["text"], "1.5%")          # p_zero as a percentage
        self.assertEqual(cells[8]["text"], "+11.6")         # margin signed
        self.assertEqual(cells[8]["tone"], "pos")

    def test_compare_record_leads_with_the_probability(self):
        v = render.record_view(COMPARE)
        self.assertIn("C.J. Stroud wins", v["tiles"][0]["k"])
        self.assertEqual(v["tiles"][0]["v"], "57.1%")
        self.assertEqual(v["tiles"][0]["tone"], "pos")
        self.assertEqual(v["tiles"][2]["v"], "+2.80")
        dist = v["sections"][0]
        self.assertEqual([r["cells"][0]["text"] for r in dist["rows"]], ["C.J. Stroud", "Jordan Love"])

    def test_paired_evaluation_puts_the_principals_first_and_signs_the_deltas(self):
        v = render.record_view(TRADE)
        self.assertEqual(v["tiles"][0]["k"], "Quantum Ferrets · playoff")
        self.assertEqual(v["tiles"][0]["v"], "+0.50 pts")
        self.assertEqual(v["tiles"][1]["tone"], "neg")
        rows = v["sections"][0]["rows"]
        self.assertEqual([r["cells"][0]["text"] for r in rows], ["Quantum Ferrets", "Rocket Pandas", "Polar Yetis"])
        self.assertEqual(rows[1]["cells"][2]["text"], "-0.20")
        self.assertEqual(v["sections"][0]["me_key"], "team")

    def test_unknown_tool_falls_back_to_the_generic_walk(self):
        v = render.record_view({"tool": "brand_new_tool", "timestamp_utc": "x", "score": 1.5, "flag": True,
                                "rows": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], "by_team": {"T1": {"w": 1}, "T2": {"w": 2}},
                                "meta": {"k": "v"}, "names": ["p", "q"]})
        self.assertEqual(v["title"], "Brand new tool")
        kinds = [(s["kind"], s["title"]) for s in v["sections"]]
        self.assertEqual(kinds[0], ("kv", "Summary"))
        self.assertIn(("table", "rows"), kinds)
        self.assertIn(("table", "by team"), kinds)
        self.assertIn(("kv", "meta"), kinds)
        self.assertIn(("text", "names"), kinds)
        by_team = [s for s in v["sections"] if s["title"] == "by team"][0]
        self.assertEqual([r["cells"][0]["text"] for r in by_team["rows"]], ["T1", "T2"])

    def test_a_drifted_record_shape_falls_back_rather_than_raising(self):
        v = render.record_view({"tool": "optimize_lineup", "lineup": "not a list", "bench": 3})
        self.assertIsNotNone(v)
        self.assertEqual(v["tool"], "optimize_lineup")

    def test_non_dict_input_is_none(self):
        self.assertIsNone(render.record_view([1, 2]))
        self.assertIsNone(render.record_view(None))

    def test_number_formatting_helpers(self):
        self.assertEqual(render.fpct(0.7624), "76.2%")
        self.assertEqual(render.fpct(93.54), "93.5%")
        self.assertEqual(render.fsigned(-0.333), "-0.33")
        self.assertEqual(render.fnum(None), "—")
        self.assertEqual(render.fnum(float("nan")), "—")
        self.assertEqual(render.tone(-1), "neg")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestRenderedPages(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = os.path.join(self.td.name, "data", "decisions", "week_03", "archive")
        os.makedirs(d)
        os.makedirs(os.path.join(self.td.name, "data", "current"))
        with open(os.path.join(d, "lineup_20260927T030606Z_week3.json"), "w", encoding="utf-8") as fh:
            json.dump(LINEUP, fh)
        with open(os.path.join(self.td.name, "data", "current", "league_state.json"), "w", encoding="utf-8") as fh:
            fh.write('{"current_week": 3}')
        self.root = Root(self.td.name)
        self.runner = FakeRunner()
        jid = self.runner.launch(["PY", "-m", "scripts.optimize_lineup"], "optimize_lineup", "optimize_lineup Quantum Ferrets")
        self.runner.metas[jid].update(state="OK", rc=0, record="/file/decisions/week_03/archive/lineup_20260927T030606Z_week3.json")
        self.runner.log_text = lambda j: CONSOLE
        self.jid = jid

    def tearDown(self):
        self.td.cleanup()

    def client(self, overlay=None):
        app = create_app(self.root, runner=self.runner, csrf_token="tok", overlay=overlay)
        app.testing = True
        return app.test_client()

    def test_job_page_renders_the_record_as_tables_and_folds_the_chatter(self):
        before = self.root.tree_digest()
        body = self.client().get(f"/jobs/{self.jid}").get_data(as_text=True)
        self.assertIn("Optimal lineup", body)
        self.assertIn("Patrick Mahomes", body)
        self.assertIn("<th", body)
        self.assertIn("engine chatter", body)
        self.assertIn('href="/file/decisions/week_03/archive/lineup_20260927T030606Z_week3.json"', body)
        self.assertNotIn("[PRE-FLIGHT SUCCESS]</pre>", body)
        self.assertEqual(self.root.tree_digest(), before)

    def test_record_json_opens_as_tables_with_the_raw_json_one_click_away(self):
        c = self.client()
        page = c.get("/file/decisions/week_03/archive/lineup_20260927T030606Z_week3.json").get_data(as_text=True)
        self.assertIn("Optimal lineup", page)
        self.assertIn("The record as written", page)
        self.assertIn("Questionable starters", page)
        raw = c.get("/file/decisions/week_03/archive/lineup_20260927T030606Z_week3.json?raw=1")
        self.assertEqual(raw.mimetype, "application/json")

    def test_a_record_with_a_key_value_section_renders(self):
        """Found live: `s.items` in the macro resolved to dict.items(), so every record with a
        kv section -- the paired evaluations, every generic record -- was a 500."""
        p = os.path.join(self.td.name, "data", "decisions", "week_03", "archive", "trade_x.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(TRADE, fh)
        page = self.client().get("/file/decisions/week_03/archive/trade_x.json")
        self.assertEqual(page.status_code, 200, page.data[:300])
        body = page.get_data(as_text=True)
        self.assertIn("The trade", body)
        self.assertIn("Javonte Williams", body)
        gen = os.path.join(self.td.name, "data", "decisions", "week_03", "archive", "other_x.json")
        with open(gen, "w", encoding="utf-8") as fh:
            json.dump({"tool": "brand_new", "timestamp_utc": "x", "score": 1.5, "meta": {"k": "v"}}, fh)
        self.assertEqual(self.client().get("/file/decisions/week_03/archive/other_x.json").status_code, 200)

    def test_overlay_applies_inside_rendered_records(self):
        body = self.client(overlay=Overlay({"Quantum Ferrets": "Team Alpha"})).get(f"/jobs/{self.jid}").get_data(as_text=True)
        self.assertIn("Team Alpha", body)
        self.assertNotIn("Quantum Ferrets", body)

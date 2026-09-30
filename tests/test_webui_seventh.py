"""
tests.test_webui_seventh -- the audit's Phase 4 (docs/WEB_UI_AUDIT.md): charts v2.

B14: y ticks land on round numbers (1, 2 or 5 times a power of ten); x labels never
collide and a run of identical labels is written once; chart text is at least 11.5 px;
the Decisions chart is labelled by date, not "wk 1" four times. U1: the Forecasts page
draws the playoff-odds race -- every team, one line each in its own colour, mine loud --
and the same for the title odds, in both views. U9: the home standings carry a rank arrow
against the previous forecast and a sparkline of each team's playoff odds. The one
component: `render.line_chart` carries the data the hover layer reads, `render.sparkline`
exists, and the per-template chart maths (the old `linechart` macro) is gone.
"""
import io
from tests.webui_served import base_source, inline_assets  # UI-E2: the page as served
import json
import os
import re
import tempfile
import unittest

from webui import render

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.glance import home_report, odds_race
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_fourth import plant as plant_fourth
    from tests.test_webui_home import enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import TEAMS, build_tree
    from webui.paths import Root
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

BASE = "webui/templates/base.html"
WEEKS = ["wk %d" % i for i in range(1, 15)]


def _x_labels(svg):
    """(x, text) of every bottom-axis label, in drawing order."""
    return [(float(x), t) for x, t in re.findall(r'<text class="xl" x="([\d.]+)"[^>]*>([^<]*)</text>', svg)]


def _y_ticks(svg):
    return re.findall(r'<text class="yl"[^>]*>([^<]*)</text>', svg)


def plant_race(root):
    """A second week of forecasts, so the race and the rank arrows have two points."""
    d = os.path.join(root, "data", "weeks", "week_02")
    os.makedirs(d, exist_ok=True)
    fc = {t: {"current_state": {"actual_wins_banked": 1.0}, "forecast": {"expected_final_wins": 15.0 + i,
              "playoff_probability_pct": 40.0 + 6 * i, "playoff_standard_error": 0.3}} for i, t in enumerate(TEAMS)}
    with open(os.path.join(d, "live_season_forecast_week_2.json"), "w", encoding="utf-8") as fh:
        json.dump(fc, fh)
    with open(os.path.join(d, "syndicate_comprehensive_matrix_week_2.json"), "w", encoding="utf-8") as fh:
        json.dump({"metadata": {"week": 2, "simulations": 100},
                   "season_outcomes": [{"Team": t, "Champ_Pct": 5.0 + i} for i, t in enumerate(TEAMS)]}, fh)
    # a second evaluated move of mine, so the Decisions chart has two points to label
    rows = [{"transaction_id": "t5", "type": "waiver", "week": 3, "created": "2026-09-24T12:00:00Z", "is_mine": True, "teams": [MY_TEAM],
             "faab_bid": 3, "adds": [{"name": "Some Kicker", "player_id": "1", "to_team": MY_TEAM, "projection": {"pos": "K", "mean": 9.0}}], "drops": []},
            {"record_type": "evaluation", "transaction_id": "t5", "evaluated_at": "2026-09-25T00:00:00Z", "n_sims": 3000, "batches": 10, "post_execution_reversed": False,
             "teams": {MY_TEAM: {"playoff_pct": {"delta": -1.0, "se": 0.9, "with": 62.0, "without": 63.0}, "champ_pct": {"delta": 0.2, "se": 0.4, "with": 11.2, "without": 11.0}}}}]
    with open(os.path.join(root, "data", "logs", "decision_log.jsonl"), "a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in rows))


class TestTicksAndLabels(unittest.TestCase):
    def test_y_ticks_are_round_numbers(self):
        for vals, unit in (([0.0, 5.0, 11.0, 16.0, 21.0], ""), ([-1.0, 12.0, 25.0, 38.0, 51.0], " pts"), ([80.0, 90.0, 93.5], "%")):
            with self.subTest(vals=vals):
                svg = str(render.line_chart([{"name": "p", "values": vals, "cls": "me"}], ["a", "b", "c", "d", "e"][:len(vals)],
                                            unit=unit, nd=1, y_min=0 if vals[0] >= 0 else None))
                ticks = _y_ticks(svg)
                self.assertGreaterEqual(len(ticks), 3, svg)
                nums = [float(t.replace(unit, "").replace(",", "")) for t in ticks]
                step = nums[1] - nums[0]
                mant = step / (10 ** __import__("math").floor(__import__("math").log10(step)))
                self.assertIn(round(mant, 6), (1.0, 2.0, 2.5, 5.0), f"step {step} in {ticks}")
                for a, b in zip(nums, nums[1:]):
                    self.assertAlmostEqual(b - a, step, places=6, msg=ticks)

    def test_percent_axis_keeps_its_ceiling(self):
        svg = str(render.line_chart([{"name": "p", "values": [80.0, 90.0, 93.5], "cls": "me"}], WEEKS[:3], unit="%", y_min=0, y_max=100))
        self.assertEqual(_y_ticks(svg), ["0%", "25%", "50%", "75%", "100%"])

    def test_x_labels_never_collide_and_the_last_one_survives(self):
        svg = str(render.line_chart([{"name": "p", "values": [float(i) for i in range(14)], "cls": "me"}], WEEKS, width=320))
        labs = _x_labels(svg)
        self.assertEqual(labs[-1][1], "wk 14")
        self.assertEqual(labs[0][1], "wk 1")
        for (xa, ta), (xb, tb) in zip(labs, labs[1:]):
            self.assertGreaterEqual(xb - xa, render.label_width(ta), f"{ta!r} and {tb!r} overlap at {xa}, {xb}")

    def test_the_first_label_survives_a_crowded_axis_too(self):
        svg = str(render.line_chart([{"name": "p", "values": [float(i) for i in range(14)], "cls": "me"}], WEEKS, width=440))
        labs = [t for _x, t in _x_labels(svg)]
        self.assertEqual(labs[0], "wk 1")
        self.assertEqual(labs[-1], "wk 14")
        self.assertGreaterEqual(len(labs), 5, labs)

    def test_race_end_labels_dodge_each_other(self):
        series = [{"name": "A", "values": [60.0, 85.7], "hue": 272, "cls": "me"}, {"name": "B", "values": [66.0, 85.4], "hue": 48},
                  {"name": "C", "values": [38.0, 85.0], "hue": 140}, {"name": "D", "values": [30.0, 13.6], "hue": 222}]
        svg = str(render.line_chart(series, ["wk 1", "wk 2"], unit="%", y_min=0, y_max=100, value_labels="last"))
        ys = sorted(float(y) for y in re.findall(r'<text class="vl"[^>]*y="([\d.]+)"', svg))
        self.assertEqual(len(ys), 4)
        for a, b in zip(ys, ys[1:]):
            self.assertGreaterEqual(b - a, render.CHART_FONT_PX + 4, ys)   # +2 met the rule and still read as touching (README shots, 2026-09-30)
        self.assertIn('style="fill:hsl(48 62% var(--line-l))"', svg)             # a race label wears its team's colour

    def test_a_run_of_identical_labels_is_written_once(self):
        svg = str(render.line_chart([{"name": "p", "values": [1.0, 2.0, 3.0, 4.0, 5.0], "cls": "me"}], ["wk 1"] * 4 + ["wk 2"]))
        self.assertEqual([t for _x, t in _x_labels(svg)], ["wk 1", "wk 2"])

    def test_chart_text_is_readable(self):
        css = io.StringIO(base_source()).read().split("</style>")[0]
        m = re.search(r"\.viz text \{ font: ([\d.]+)px", css)
        self.assertTrue(m, ".viz text rule")
        self.assertGreaterEqual(float(m.group(1)), 11.5)


class TestComponent(unittest.TestCase):
    def test_the_chart_carries_what_the_hover_layer_reads(self):
        svg = str(render.line_chart([{"name": "A", "values": [1.0, None, 3.0], "cls": "me"},
                                     {"name": "B", "values": [2.0, 2.5, 2.0], "hue": 200}], ["a", "b", "c"], unit="%"))
        self.assertIn('data-labels=\'["a", "b", "c"]\'', svg)
        self.assertRegex(svg, r'data-xs="[\d.]+,[\d.]+,[\d.]+"')
        self.assertIn('data-name="A" data-vals="[1.0, null, 3.0]"', svg)
        self.assertIn('data-name="B" data-vals="[2.0, 2.5, 2.0]"', svg)
        self.assertIn('style="stroke:hsl(200 62% var(--line-l))"', svg)         # a team line in its own hue
        self.assertIn('data-unit="%"', svg)
        base = io.StringIO(base_source()).read()
        self.assertIn("--line-l:", base.split("@media (prefers-color-scheme: dark)")[0])
        self.assertIn("--line-l:", base.split("@media (prefers-color-scheme: dark)")[1])
        script = base.split("</style>")[1]
        self.assertIn("'viztip'", script)                                         # the tooltip the script builds
        self.assertIn("data-xs", script)                                          # and the script reads the chart
        self.assertIn(".viztip {", base.split("</style>")[0])

    def test_legend_option_names_every_series_with_its_colour(self):
        html = str(render.line_chart([{"name": "Me", "values": [1.0, 2.0], "cls": "me"}, {"name": "Them", "values": [2.0, 1.0], "hue": 4}],
                                     ["a", "b"], legend=True))
        legend = html.split('<div class="legend">', 1)[1]
        self.assertIn("Me", legend)
        self.assertIn("Them", legend)
        self.assertIn("hsl(4 62% var(--line-l))", legend)

    def test_sparkline(self):
        svg = str(render.sparkline([10.0, 20.0, None, 35.5]))
        self.assertIn('class="spark"', svg)
        self.assertEqual(svg.count("<polyline"), 1)
        self.assertEqual(svg.count("<circle"), 1)                                 # the last point only
        self.assertEqual(str(render.sparkline([])), "")
        self.assertEqual(str(render.sparkline([1.0])), "")

    def test_the_per_template_chart_maths_is_gone(self):
        self.assertNotIn("macro linechart", open("webui/templates/_macros.html", encoding="utf-8").read())


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        plant_fourth(cls.td.name)
        plant_race(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def get(self, path, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return inline_assets(r.get_data(as_text=True))

    def test_odds_race_report(self):
        race = odds_race(self.root, MY_TEAM)
        self.assertEqual(race["labels"], ["wk 2", "wk 3"])
        self.assertEqual(len(race["playoff"]), 8)
        self.assertEqual(len(race["champ"]), 8)
        mine = next(s for s in race["playoff"] if s["name"] == MY_TEAM)
        self.assertEqual(mine["cls"], "me")
        self.assertEqual(mine["values"], [40.0, 93.5])
        self.assertTrue(all(isinstance(s["hue"], int) for s in race["playoff"]))
        # ordered by the latest odds, so the legend reads like a table
        vals = [s["values"][-1] for s in race["playoff"]]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_forecasts_draws_the_race_in_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                page = self.get("/forecasts", mode)
                self.assertIn("Playoff-odds race", page)
                self.assertIn("Title-odds race", page)
                for t in TEAMS:
                    self.assertIn(f'data-name="{t}"', page)
                self.assertGreaterEqual(page.count('class="ln me"'), 2)        # my line in both races
                self.assertGreaterEqual(page.count('class="legend"'), 2)
                self.assertNotIn("wk 1", page)                                   # only weeks that ran
                self.assertEqual(page.count('viewBox="0 0 340 200"'), 3)        # the small cards draw at their rendered width
                self.assertEqual(page.count('viewBox="0 0 640 280"'), 1)        # the playoff race, full width

    def test_home_standings_carry_rank_arrows_and_sparklines(self):
        rep = home_report(self.root, MY_TEAM)
        rows = {r["team"]: r for r in rep["standings"]}
        for r in rows.values():
            self.assertIn("rank_delta", r)
            self.assertEqual(len(r["spark"]), 2, r)
        # week 2 put the last fixture team on top of the odds (40 + 6*7); week 3 puts the first
        self.assertGreater(rows[TEAMS[0]]["rank_delta"], 0)
        self.assertLess(rows[TEAMS[-1]]["rank_delta"], 0)
        for mode in ("dev", "simple"):
            page = self.get("/", mode)
            self.assertIn('class="spark"', page)
            # the marker is the change in odds (owner report 2026-09-30): every fixture team's
            # odds rose, so none is marked down -- by places, four "fell" (tests.test_owner_report_0930)
            self.assertIn('class="rankd up"', page)
            self.assertNotIn('class="rankd dn"', page)
            self.assertIn("since the week 2 forecast", page)

    def test_decisions_chart_is_labelled_by_date(self):
        page = self.get("/decisions")
        labs = [t for _x, t in _x_labels(page)]
        self.assertTrue(labs, "the decisions chart has labels")
        self.assertFalse(any(t.startswith("wk ") for t in labs), labs)
        self.assertEqual(len(labs), len(set(labs)), labs)


if __name__ == "__main__":
    unittest.main()

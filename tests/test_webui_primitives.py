"""
tests.test_webui_primitives -- chart primitives (docs/WEB_UI_ROADMAP.md UI-V3).

One library in webui/render.py rather than per-page chart code. Beside the distribution strip
(UI-P2) and the line chart: a slope chart, a heat grid with the value written in every cell, a
dot histogram, a fan chart and a percentage bar for table cells. Each draws on one scale, has
a hover title per mark, carries a table view (the fan, slope and dots; the heat grid IS a
table, and a percentage bar writes its number), and takes its colours from CSS tokens, never
a literal, so both themes hold.

The palette (base.html's --c-* tokens) was run through the dataviz validator in both themes
on 2026-09-29, and this test pins the validated values -- any change to them must be run
through it again (the validator is a Node/browser tool the suite does not run: a coverage gap,
stated). What it found shaped the choices: the site's turf and loss-red cannot share a chart as
categories (colour-blind separation 3.5 in dark mode), so gains and losses are blue and red,
and the owner's team is turf against a recessive grey; the dark turf is stepped down to sit
inside the lightness band; and the sequential ramp is four steps, because five cannot clear
both the light-end contrast floor and the step spacing.
"""
import os
import re
import unittest

from webui import render

HERE = os.path.dirname(os.path.abspath(__file__))
VALIDATED = {
    "light": {"--c-me": "#2e7d4f", "--c-other": "#9a9891", "--c-seq-1": "#95bca5", "--c-seq-2": "#73a788",
              "--c-seq-3": "#50926c", "--c-seq-4": "#2e7d4f"},
    "dark": {"--c-me": "#45a071", "--c-other": "#5a5954", "--c-seq-1": "#305d45", "--c-seq-2": "#377354",
             "--c-seq-3": "#3e8a62", "--c-seq-4": "#45a071"},
}


def attrs(tag):
    return dict(re.findall(r'([a-z0-9-]+)="([^"]*)"', tag))


def no_literal_colour(testcase, markup):
    testcase.assertIsNone(re.search(r'(fill|stroke|background)(-color)?[=:]\s*"?#[0-9a-fA-F]{3,6}', markup), "colours come from tokens")


class TestPalette(unittest.TestCase):
    def test_the_tokens_are_the_validated_values_in_both_themes(self):
        with open(os.path.join(HERE, "..", "webui", "templates", "base.html"), encoding="utf-8") as fh:
            css = fh.read()
        light = css[css.index(":root {"):css.index("@media (prefers-color-scheme: dark)")]
        dark_media = css[css.index("@media (prefers-color-scheme: dark)"):css.index(':root[data-theme="dark"] {')]
        dark_stamp = css[css.index(':root[data-theme="dark"] {'):]
        for block, want in ((light, VALIDATED["light"]), (dark_media, VALIDATED["dark"]), (dark_stamp, VALIDATED["dark"])):
            for token, value in want.items():
                self.assertIn(f"{token}:{value}", block.replace(" ", ""), token)


class TestSlope(unittest.TestCase):
    def setUp(self):
        self.svg = str(render.slope([{"name": "A", "a": 10.0, "b": 20.0}, {"name": "B", "a": 20.0, "b": 10.0}],
                                    "week 2", "week 3", me="A", unit="%", width=400, height=200))

    def test_one_scale_both_sides(self):
        lines = [attrs(t) for t in re.findall(r'<line class="sl[^"]*"[^>]*>', self.svg)]
        a = next(l for l in lines if "me" in l["class"])
        b = next(l for l in lines if "me" not in l["class"])
        self.assertEqual((a["y1"], a["y2"]), (b["y2"], b["y1"]), "10 and 20 sit at the same heights on both sides")

    def test_hover_table_and_tokens(self):
        self.assertEqual(self.svg.count("<title>"), 2)
        self.assertIn('class="tview"', self.svg)
        no_literal_colour(self, self.svg)


class TestHeatGrid(unittest.TestCase):
    def test_every_cell_written_and_binned_on_the_grid_range(self):
        html = str(render.heat(["r1", "r2"], ["c1", "c2"], {("r1", "c1"): 0.1, ("r1", "c2"): 0.4, ("r2", "c1"): 0.6, ("r2", "c2"): 0.9},
                               fmt=lambda v: f"{v * 100:.0f}%"))
        cells = re.findall(r'<td class="hc (h\d)"[^>]*title="[^"]+">([^<]+)</td>', html)
        self.assertEqual(cells, [("h1", "10%"), ("h2", "40%"), ("h3", "60%"), ("h4", "90%")])
        no_literal_colour(self, html)

    def test_a_missing_cell_is_a_dash(self):
        html = str(render.heat(["r1"], ["c1", "c2"], {("r1", "c1"): 0.5}, fmt=str))
        self.assertIn(">—</td>", html)


class TestDots(unittest.TestCase):
    def test_one_dot_per_step_of_seasons(self):
        svg = str(render.dots([("10", 30), ("11", 10), ("12", 0)], per_dot=10, width=300))
        cols = re.findall(r'<g class="dcol"[^>]*>(.*?)</g>', svg, re.S)
        self.assertEqual([c.count("<circle") for c in cols], [3, 1, 0])
        self.assertIn("<title>10: 30 seasons (75%)</title>", svg)
        self.assertIn('class="tview"', svg)
        no_literal_colour(self, svg)


class TestFan(unittest.TestCase):
    def test_bands_and_median_on_one_scale(self):
        bands = [{"p10": 0, "p25": 10, "p50": 20, "p75": 30, "p90": 40}, {"p10": 10, "p25": 20, "p50": 30, "p75": 40, "p90": 50}]
        svg = str(render.fan(["w1", "w2"], bands, width=300, height=200))
        self.assertEqual(len(re.findall(r'<polygon class="band', svg)), 2)
        med = attrs(re.search(r'<polyline class="med"[^>]*>', svg).group(0))
        ys = [float(p.split(",")[1]) for p in med["points"].split()]
        self.assertGreater(ys[0], ys[1], "30 is drawn above 20")
        self.assertEqual(svg.count("<title>"), 2, "a hover per week")
        self.assertIn('class="tview"', svg)
        no_literal_colour(self, svg)


class TestPctBar(unittest.TestCase):
    def test_width_and_number(self):
        html = str(render.pctbar(0.375))
        self.assertIn("width:37.5%", html.replace(" ", ""))
        self.assertIn("37.5%", re.sub(r"<[^>]+>", " ", html))
        self.assertIn("width:100.0%", str(render.pctbar(1.4)).replace(" ", ""))
        self.assertEqual(str(render.pctbar(None)), "—")


if __name__ == "__main__":
    unittest.main()

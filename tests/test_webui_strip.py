"""
tests.test_webui_strip -- the distribution strip (docs/WEB_UI_ROADMAP.md UI-P2).

A projection here is a distribution, and the strip draws one on a single scale: a 10th-to-90th
percentile line, a 25th-to-75th box, a mean tick, the simulation's histogram behind them, and
a dot for each week already played. `render.strip(v, hi, history)` returns inline SVG; every
mark sits at its value on the one scale 0..hi across `width` pixels.

Not built, stated rather than faked: the roadmap's pip for "the chance of scoring zero".
player_variance.json leaves bye and injury weeks OUT by design (a structural absence is never
a zero there), and its histogram's first bin runs from 0 to 5.3, so no field on disk is the
chance of a zero in a week played.

The done-when's bimodal case: two players with the same 10th and 90th percentiles, one
normal and one a handcuff (nothing most weeks, a starter's line when the starter sits). The
histogram behind the strip is what tells them apart -- one hump against two.
"""
import re
import tempfile
import unittest

from webui import render                        # outside the probe

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

V = {"p10": 4.0, "p25": 6.0, "p50": 10.0, "p75": 15.0, "p90": 22.0, "mean": 12.0}


def attrs(svg, cls):
    m = re.search(r'<[a-z]+ [^>]*class="%s"[^>]*>' % cls, svg)
    assert m, (cls, svg)
    return dict(re.findall(r'([a-z0-9-]+)="([^"]*)"', m.group(0)))


def bars(svg):
    return [float(h) for h in re.findall(r'<rect class="h" [^>]*height="([0-9.]+)"', svg)]


def humps(heights):
    peak = max(heights)
    return sum(1 for i, h in enumerate(heights)
               if h >= 0.3 * peak and h >= (heights[i - 1] if i else 0) and h > (heights[i + 1] if i + 1 < len(heights) else 0))


class TestOneScale(unittest.TestCase):
    """0..40 across 160 px: 4 px a point."""

    def setUp(self):
        self.svg = str(render.strip(V, 40, width=160))

    def test_each_mark_at_its_value(self):
        w = attrs(self.svg, "rng")
        self.assertEqual((float(w["x1"]), float(w["x2"])), (16.0, 88.0), "10th to 90th")
        b = attrs(self.svg, "box")
        self.assertEqual((float(b["x"]), float(b["width"])), (24.0, 36.0), "25th to 75th")
        m = attrs(self.svg, "mean")
        self.assertEqual(float(m["x1"]), 48.0)

    def test_it_reads_as_text_too(self):
        self.assertIn("10th 4.0", self.svg)
        self.assertIn("90th 22.0", self.svg)
        self.assertIn('role="img"', self.svg)

    def test_the_weeks_played_are_dots(self):
        svg = str(render.strip(V, 40, history=[3.0, 25.0], width=160))
        xs = [float(x) for x in re.findall(r'<circle class="wk" cx="([0-9.]+)"', svg)]
        self.assertEqual(xs, [12.0, 100.0])

    def test_a_value_past_the_scale_is_pinned_to_its_edge(self):
        svg = str(render.strip(V, 40, history=[55.0], width=160))
        self.assertEqual(re.findall(r'<circle class="wk" cx="([0-9.]+)"', svg), ["160.0"])

    def test_nothing_to_draw(self):
        self.assertEqual(str(render.strip({}, 40)), "")


class TestTheHandcuff(unittest.TestCase):
    EDGES = [i * 2.0 for i in range(16)]

    def test_same_range_two_humps_against_one(self):
        normal = dict(V, histogram={"bin_edges": self.EDGES, "counts": [1, 3, 8, 15, 22, 26, 22, 15, 8, 4, 2, 1, 1, 0, 0]})
        cuff = dict(V, histogram={"bin_edges": self.EDGES, "counts": [30, 24, 6, 2, 1, 1, 2, 6, 14, 18, 12, 4, 1, 0, 0]})
        a, b = str(render.strip(normal, 40)), str(render.strip(cuff, 40))
        self.assertEqual((humps(bars(a)), humps(bars(b))), (1, 2))
        self.assertEqual(attrs(a, "rng"), attrs(b, "rng"), "the same 10th-90th line")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPages(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def get(self, path, mode):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)

    def test_the_team_roster_and_the_player_page_draw_it_in_both_views(self):
        for path in ("/team/quantum-ferrets", "/player/100"):
            for mode in ("dev", "simple"):
                with self.subTest(path=path, mode=mode):
                    body = self.get(path, mode)
                    self.assertIn('class="dstrip"', body)
                    if mode == "simple":
                        text = visible_text(body)
                        self.assertEqual([t for t in DEV_TERMS if t in text], [])

    def test_the_player_page_marks_the_weeks_played(self):
        body = self.get("/player/100", "simple")
        self.assertEqual(len(re.findall(r'<circle class="wk"', body)), 2, "weeks 1 and 2 on file")


if __name__ == "__main__":
    unittest.main()

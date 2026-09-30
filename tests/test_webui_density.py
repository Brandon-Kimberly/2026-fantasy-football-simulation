"""
tests.test_webui_density -- compact density (docs/WEB_UI_ROADMAP.md UI-V5), the parts a
browser is not needed for. A toggle beside the theme buttons, in both views, sets
data-density="compact" on the page root and remembers it in this browser's localStorage, read
inside a try so a private window or blocked storage renders the normal density. The CSS it
switches touches table-cell padding and nothing else. tests.test_webui_browser's
TestCompactDensity drives it in a real browser. Written before the toggle existed.
"""
import io
from tests.webui_served import base_source, inline_assets  # UI-E2: the page as served
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_objects import plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def base_css():
    with io.StringIO(base_source()) as fh:
        return fh.read()


class TestTheRule(unittest.TestCase):
    def test_compact_changes_only_table_cell_padding(self):
        rules = re.findall(r'(:root\[data-density="compact"\][^{]*)\{([^}]*)\}', base_css())
        self.assertTrue(rules, "a compact rule exists")
        for selector, body in rules:
            for sel in selector.split(","):
                self.assertRegex(sel.strip(), r'^:root\[data-density="compact"\] (td|th)$', sel)
            props = {p.split(":")[0].strip() for p in body.split(";") if p.strip()}
            self.assertLessEqual(props, {"padding-top", "padding-bottom"}, props)

    def test_the_stored_choice_is_read_inside_a_try(self):
        head = base_css().split("</head>")[0]
        m = re.search(r"<script>(.*?)</script>", head, re.S)
        self.assertIsNotNone(m, "an early script applies it before the first paint")
        self.assertIn("try", m.group(1))
        self.assertIn("density", m.group(1))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheToggle(unittest.TestCase):
    def test_both_views_carry_it_beside_the_theme_buttons(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            plant(td)
            root = Root(td)
            for mode in ("dev", "simple"):
                st = Settings(root)
                st.set_mode(mode)
                app = create_app(root, runner=FakeRunner(), csrf_token="tok", settings=st,
                                 live=LiveBoard(root, MY_TEAM, league_id=None, fetch=None))
                app.testing = True
                body = inline_assets(app.test_client().get("/").get_data(as_text=True))
                with self.subTest(mode=mode):
                    theme = body.index('class="themeform"')
                    button = body.index('id="density"')
                    self.assertLess(abs(button - theme), 1500, "beside the theme buttons")
                    self.assertIn('aria-pressed="false"', body[button - 200:button + 200])


if __name__ == "__main__":
    unittest.main()

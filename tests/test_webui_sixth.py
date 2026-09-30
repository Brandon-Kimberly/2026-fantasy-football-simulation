"""
tests.test_webui_sixth -- the audit's Phase 3 (docs/WEB_UI_AUDIT.md): contrast, keyboard
focus, the one-row header, the even chip strips, and the icon set.

The contrast test is the enforcement: it parses the colour tokens out of base.html for
both themes and computes WCAG contrast, so a token that would put 12 px text below
4.5:1 on its own ground fails here rather than on someone's screen. Also pinned: every
tool has a symbol that exists in the sprite; the tools index, the records list and the
jobs list carry it; a global :focus-visible ring exists; the header holds only the brand
and the tabs and the clock sits in the footer; the status chips and the League team
chips are grids, not wrapping rows that orphan the last one; the gold medal wears dark
text; the LIVE badge starts from the readable red.
"""
import io
from tests.webui_served import base_source, inline_assets  # UI-E2: the page as served
import re
import tempfile
import unittest

from webui import render

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.jobs import OK
    from webui.live import LiveBoard
    from webui.settings import Settings
    from webui.tools import ENGINE, TOOLS
    from tests.test_webui_fourth import plant as plant_fourth
    from tests.test_webui_home import enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import build_tree
    from webui.paths import Root
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

BASE = "webui/templates/base.html"
TEXT_TOKENS = ("ink", "ink-2", "muted", "pos", "neg", "turf", "violet", "warm-ink", "gold-ink", "warn")


def _lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def tokens(css_block):
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", css_block))


def theme_blocks():
    css = io.StringIO(base_source()).read().split("</style>")[0]
    light = css.split(":root {", 1)[1].split("@media (prefers-color-scheme: dark)", 1)[0]
    dark = css.split("@media (prefers-color-scheme: dark)", 1)[1].split("} }", 1)[0]
    return tokens(light), tokens(dark)


class TestContrast(unittest.TestCase):
    def test_every_text_token_reads_at_small_sizes_on_both_grounds_in_both_themes(self):
        for theme, toks in zip(("light", "dark"), theme_blocks()):
            for name in TEXT_TOKENS:
                for ground in ("plane", "surface"):
                    with self.subTest(theme=theme, token=name, ground=ground):
                        ratio = contrast(toks[name], toks[ground])
                        self.assertGreaterEqual(ratio, 4.5, f"--{name} {toks[name]} on --{ground} {toks[ground]} is {ratio:.2f}:1")

    def test_white_on_the_gradient_pill_starts_and_dark_on_gold(self):
        light, _dark = theme_blocks()
        css = io.StringIO(base_source()).read()
        for grad in ("--g-turf", "--g-pos"):
            start = re.search(grad + r":\s*linear-gradient\([^,]+,\s*(#[0-9a-fA-F]{6})", css).group(1)
            self.assertGreaterEqual(contrast("#ffffff", start), 4.5, f"white on {grad} start {start}")
        self.assertGreaterEqual(contrast("#ffffff", light["neg"]), 4.5, "the LIVE badge")
        self.assertIn(".rank.r1 { background: var(--g-gold); color: #2b2000;", css)
        self.assertGreaterEqual(contrast("#2b2000", "#c9962a"), 4.5)


class TestIcons(unittest.TestCase):
    def test_every_tool_and_record_prefix_has_a_symbol_in_the_sprite(self):
        sprite = set(re.findall(r'<symbol id="(i-[a-z]+)"', io.StringIO(base_source()).read()))
        for name in list(render.TOOL_ICONS) + ["nonsense"]:
            self.assertIn(render.tool_icon(name), sprite, name)
        self.assertEqual(render.tool_icon("nonsense"), "i-clip")
        if HAS_FLASK:
            for name in list(TOOLS) + list(ENGINE):
                self.assertNotEqual(render.tool_icon(name), "i-clip", f"{name} has no icon of its own")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        plant_fourth(cls.td.name)
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

    def get(self, path, mode="dev", runner=None):
        r = self.client(mode, runner).get(path)
        self.assertEqual(r.status_code, 200, path)
        return inline_assets(r.get_data(as_text=True))

    def test_focus_ring_header_and_footer(self):
        body = self.get("/")
        self.assertIn("a:focus-visible, summary:focus-visible, [tabindex]:focus-visible", body)          # B23
        header = body.split('<header class="top">', 1)[1].split("</header>", 1)[0]
        self.assertNotIn('class="meta"', header, "the header is brand and tabs only")                 # B20
        footer = body.split('<footer class="foot">', 1)[1].split("</footer>", 1)[0]
        self.assertIn('class="meta"', footer)
        self.assertIn("localhost", footer)
        simple = self.get("/", mode="simple")
        sfoot = simple.split('<footer class="foot">', 1)[1].split("</footer>", 1)[0]
        self.assertNotIn("localhost", sfoot)

    def test_chip_strips_are_grids(self):
        css = self.get("/").split("</style>")[0]
        self.assertIn(".chips { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));", css)   # B19
        league = self.get("/league")
        self.assertIn(".roster-nav { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));", league)

    def test_icons_appear_where_a_tool_is_named(self):
        tools = self.get("/tools")
        self.assertIn('<use href="#i-lineup"/>', tools)
        self.assertIn('<use href="#i-compare"/>', tools)
        self.assertIn('<use href="#i-engine"/>', tools)
        self.assertIn('<use href="#i-lineup"/>', self.get("/tools/optimize_lineup"))
        self.assertIn('<use href="#i-book"/>', self.get("/records/week-3"))                              # the digest rows
        runner = FakeRunner()
        jid = runner.launch(["py", "-m", "scripts.luck_ledger", "--json"], "luck_ledger", label="Luck ledger")
        runner.metas[jid].update(state=OK, finished_at="2026-09-26T00:00:05Z", rc=0)
        self.assertIn('<use href="#i-dice"/>', self.get("/jobs", runner=runner))
        self.assertIn('<use href="#i-matchup"/>', self.get("/"))                                         # the quick actions


if __name__ == "__main__":
    unittest.main()

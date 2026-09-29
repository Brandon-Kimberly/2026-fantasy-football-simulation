"""
tests.test_webui_p3_polish -- the recap as a Sleeper chat post (docs/WEB_UI_ROADMAP.md UI-R3).

"Copy for Sleeper" on a played week's review: plain text within a message limit, split into
parts at line breaks when it is longer, copied in the browser with the owner's overlay applied
(real names on when the owner has them on) and nothing written to disk. The limit is
render.SLEEPER_CHAT_LIMIT -- 1000 characters, UNVERIFIED: a conservative figure, not one read
from Sleeper's documentation, which the repo does not carry.

The keys between games (UI-M10) and the two layout fixes (UI-F7, F8) are browser tests in
tests.test_webui_browser.
"""
import tempfile
import unittest

from webui import render


class TestParts(unittest.TestCase):
    def test_a_long_post_splits_at_the_limit_on_line_breaks(self):
        lines = [f"line {i}: " + "x" * 90 for i in range(40)]
        parts = render.chat_parts("\n".join(lines), limit=500)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 500 for p in parts), [len(p) for p in parts])
        self.assertEqual("\n".join(parts).split("\n"), lines, "nothing lost, nothing split mid-line")

    def test_a_single_overlong_line_is_cut_at_a_space(self):
        parts = render.chat_parts(" ".join(["word"] * 400), limit=300)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 300 for p in parts))
        self.assertTrue(all(not p.startswith(" ") and not p.endswith(" ") for p in parts))

    def test_a_short_post_is_one_part(self):
        self.assertEqual(render.chat_parts("hello", limit=render.SLEEPER_CHAT_LIMIT), ["hello"])


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


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheButton(unittest.TestCase):
    def test_a_played_week_offers_copy_for_sleeper_in_both_views(self):
        with tempfile.TemporaryDirectory() as td:
            plant(td)
            root = Root(td)
            for mode in ("dev", "simple"):
                st = Settings(root)
                st.set_mode(mode)
                app = create_app(root, runner=FakeRunner(), csrf_token="tok", settings=st,
                                 live=LiveBoard(root, MY_TEAM, league_id=None, fetch=None))
                app.testing = True
                body = app.test_client().get("/matchups/week-2").get_data(as_text=True)
                with self.subTest(mode=mode):
                    self.assertIn('class="copyp"', body)
                    self.assertIn("Upset of the week", body[body.index('class="copyp"'):], "the post carries the awards")


if __name__ == "__main__":
    unittest.main()

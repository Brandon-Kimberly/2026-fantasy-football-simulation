"""
tests.test_webui_audit_harness -- the crawl's own fixes (docs/WEB_UI_ROADMAP.md UI-E10).

1. The leak check flagged a literal `None` inside quoted engine messages: sync warnings are
   shown verbatim in a <pre> ("pid 6994 (CB, None)"), and that None is the engine's text,
   not a template that failed to fill. Text inside <pre> is quoted output and is left out of
   the leak check; a None anywhere else is still a leak.
2. The screenshots never included the width the owner uses: a 2560-pixel desktop joins the
   default set.
Written before either fix.
"""
import unittest

from scripts import webui_audit


def leaks(html):
    p = webui_audit.Page()
    p.feed(html)
    visible = " ".join(p.text)
    return sorted({t for t in webui_audit.LEAKS if t in visible})


class TestQuotedOutput(unittest.TestCase):
    def test_a_none_inside_a_quoted_warning_is_not_a_leak(self):
        self.assertEqual(leaks("<p>Sync warnings</p><pre>WARNING | NAME COLLISION: pid 6994 (CB, None)</pre>"), [])

    def test_a_none_in_the_page_is_still_a_leak(self):
        self.assertEqual(leaks("<p>Bid: None</p><pre>quoted</pre>"), ["None"])

    def test_text_after_the_pre_is_checked_again(self):
        self.assertEqual(leaks("<pre>(CB, None)</pre><td>None</td>"), ["None"])


class TestScreenshotWidths(unittest.TestCase):
    def test_the_owners_2560_desktop_is_in_the_default_set(self):
        widths = {kind: w for kind, w, _h, _dark in webui_audit.SHOT_KINDS}
        self.assertEqual(widths.get("wide"), 2560)
        self.assertEqual((widths["desktop"], widths["phone"]), (1280, 520), "the existing shots stay")


if __name__ == "__main__":
    unittest.main()

"""
tests.test_webui_access -- accessibility and export (docs/WEB_UI_ROADMAP.md UI-V7, R4).

Every line chart carries a table view (a screen reader reads numbers, not a polyline). The
browser half -- sortable headers reachable and operable from the keyboard, the player card
opening on keyboard focus, and a CSV of any sortable table -- lives in tests.test_webui_browser.
"""
import unittest

from webui import render


class TestChartTableView(unittest.TestCase):
    def test_every_line_chart_has_a_table_view_with_every_value(self):
        html = str(render.line_chart([{"name": "playoff %", "values": [52.3, None, 61.25], "cls": "me"},
                                      {"name": "title %", "values": [10.0, 12.5, 14.0]}],
                                     ["wk 1", "wk 2", "wk 3"], unit="%", nd=1))
        self.assertIn('<details class="tview">', html)
        table = html.split('<details class="tview">', 1)[1]
        for s in ("<th>wk 1</th>", "<th>wk 3</th>", "playoff %", "52.3%", "61.2%", "—", "14.0%"):
            self.assertIn(s, table)


if __name__ == "__main__":
    unittest.main()

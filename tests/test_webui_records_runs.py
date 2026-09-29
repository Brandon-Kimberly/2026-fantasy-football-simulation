"""
tests.test_webui_records_runs -- records grouped by run, with headlines and diffs
(docs/WEB_UI_ROADMAP.md UI-A7; developer view only).

A weekly digest writes several records within seconds, and the Records page listed each as
an unrelated row with a size column. Now records written within two minutes of each other
are one run (a run can straddle a minute boundary, so a fixed clock minute would split it),
each record shows its headline instead of its size, a tool filter narrows the page, and any
JSON record can be compared with the previous record of the same tool: for a lineup, the
slots whose starters changed and nothing else.

The fixture's week 3: a digest run (lineup at 16:53:31, the report at 16:53:37) is one run;
the two archived roster grades three days apart are two.
"""
import json
import os
import tempfile
import unittest

from webui import records_view                   # outside the probe: a missing module must fail, not skip


def lineup(total, **slots):
    return {"tool": "optimize_lineup", "team": "T", "week": 3, "expected_total": total,
            "lineup": [{"slot": s.rstrip("0123456789"), "name": n, "expected": 10.0} for s, n in slots.items()]}


class TestDiff(unittest.TestCase):
    def test_a_lineup_diff_lists_exactly_the_changed_slots(self):
        a = lineup(150.0, QB="Q", RB1="R1", RB2="R2", WR1="W1", TE="T")
        b = lineup(152.5, QB="Q", RB1="R3", RB2="R1", WR1="W9", TE="T")
        d = records_view.diff(a, b)
        self.assertEqual([(s["slot"], s["before"], s["after"]) for s in d["slots"]],
                         [("RB", ["R1", "R2"], ["R1", "R3"]), ("WR", ["W1"], ["W9"])])
        self.assertAlmostEqual(d["total"], 2.5)

    def test_the_same_lineup_in_another_order_is_no_change(self):
        a = lineup(150.0, RB1="R1", RB2="R2")
        b = lineup(150.0, RB1="R2", RB2="R1")
        self.assertEqual(records_view.diff(a, b)["slots"], [])


try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import visible_text
    from tests.test_webui_objects import plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def get(self, path, mode="dev", code=200):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, code, path)
        return r.get_data(as_text=True)


class TestRuns(Case):
    def test_a_digest_run_is_one_run(self):
        runs = records_view.runs(self.root.decisions(3)["canonical"])
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["tools"], ["lineup", "weekly_report"])
        self.assertEqual(len(records_view.runs(self.root.decisions(3)["archive"])), 2)

    def test_the_page_groups_filters_and_drops_the_size_column(self):
        body = self.get("/records/week-3")
        self.assertEqual(body.count('class="run"'), 3)
        text = visible_text(body)
        self.assertNotIn(" KB", text)
        self.assertIn("Optimal lineup", text, "the record's headline")
        only = self.get("/records/week-3?tool=roster_grades")
        self.assertEqual(only.count('class="run"'), 2)
        self.assertNotIn("lineup_20260924T165331Z", only)


class TestCompare(Case):
    def test_compare_with_the_previous_lineup(self):
        d = os.path.join(self.td.name, "data", "decisions", "week_03", "archive")
        with open(os.path.join(d, "lineup_20260923T090000Z_week3.json"), "w", encoding="utf-8") as fh:
            json.dump(lineup(140.0, QB="Player 0 O'Neil", RB1="Old Back"), fh)
        with open(os.path.join(d, "lineup_20260925T090000Z_week3.json"), "w", encoding="utf-8") as fh:
            json.dump(lineup(146.0, QB="Player 0 O'Neil", RB1="New Back"), fh)
        body = self.get("/records/week-3")
        self.assertIn("compare with the previous", body)
        text = visible_text(self.get("/records/compare?a=decisions/week_03/archive/lineup_20260923T090000Z_week3.json"
                                     "&b=decisions/week_03/archive/lineup_20260925T090000Z_week3.json"))
        self.assertIn("Old Back", text)
        self.assertIn("New Back", text)
        self.assertIn("+6.0", text)

    def test_the_simple_view_does_not_serve_it(self):
        self.get("/records/compare?a=x&b=y", "simple", code=404)


if __name__ == "__main__":
    unittest.main()

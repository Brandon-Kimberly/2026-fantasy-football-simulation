"""
tests.test_webui_small -- small roadmap items (docs/WEB_UI_ROADMAP.md).

UI-F3  a betting-lines fetch run after most of the week's games have kicked off finds few
       lines left; that is expected, and the System page says so instead of "failed".
UI-F9  a player on no NFL team reads "no NFL team", not a bare dash or "FA".
UI-F11 the compare tool's week is a pick-list of the season's weeks, not a free number box.
UI-W2  Home says when the next daily waiver run is (09:00 Pacific, docs/WAIVER_MECHANICS.md).
UI-T5  in weeks 9 to 11 Home counts down to the week-11 trade deadline.
UI-P7  an injury and practice report for a roster -- status, body part, practice
       participation, notes and when Sleeper last updated it, most serious first -- read from
       the current roster and Sleeper's cache, not from a tool record that may be stale.
"""
import datetime as _dt
import json
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui.paths import Root
from webui.players_page import next_waiver_run      # outside the probe: a missing name must fail, not skip

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def _u(iso):
    return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


class TestWaiverClock(unittest.TestCase):
    def test_the_next_daily_run(self):
        self.assertEqual(next_waiver_run(_u("2026-09-28T15:00:00Z")), "2026-09-28T16:00:00Z", "08:00 PT -> 09:00 today")
        self.assertEqual(next_waiver_run(_u("2026-09-28T17:00:00Z")), "2026-09-29T16:00:00Z", "10:00 PT -> 09:00 tomorrow")
        self.assertEqual(next_waiver_run(_u("2026-11-02T18:00:00Z")), "2026-11-03T17:00:00Z", "after the clocks change")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def get(self, path, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)

    def set_week(self, week, kickoffs=None, started="2026-09-28T05:30:59Z", vegas_ok=False):
        d = os.path.join(self.td.name, "data", "current")
        with open(os.path.join(d, "sync_manifest.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        m.update(current_week=week, started_at=started,
                 sources={"vegas_odds": {"ok": vegas_ok, "rows": 3, "fallback": "flat 21.5 / no opponent"},
                          "sleeper_rosters": {"ok": True, "rows": 8, "fallback": None}})
        with open(os.path.join(d, "sync_manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        with open(os.path.join(d, "league_state.json"), "w", encoding="utf-8") as fh:
            json.dump({"current_week": week, "season": "2026"}, fh)
        if kickoffs is not None:
            with open(os.path.join(d, "nfl_schedule.json"), "w", encoding="utf-8") as fh:
                json.dump({"_meta": {"kickoffs": {str(week): kickoffs}}}, fh)


class TestLinesLeft(Case):
    def test_a_sync_after_most_kickoffs_is_explained_not_failed(self):
        self.set_week(3, ["2026-09-25T00:15Z"] + ["2026-09-27T17:00Z"] * 12 + ["2026-09-29T00:15Z"] * 3)
        text = visible_text(self.get("/system"))
        self.assertIn("13 of 16", text)
        self.assertIn("expected", text)

    def test_a_sync_before_the_games_that_finds_no_lines_is_a_failure(self):
        self.set_week(3, ["2026-10-01T17:00Z"] * 16)
        self.assertNotIn("13 of 16", visible_text(self.get("/system")))
        self.assertIn("failed", visible_text(self.get("/system")))


class TestNoNflTeam(Case):
    def test_a_player_with_no_team_says_so(self):
        p = os.path.join(self.td.name, "data", "current", "player_baselines.json")
        with open(p, encoding="utf-8") as fh:
            b = json.load(fh)
        b["Player 0 O'Neil"]["team"] = "FA"
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(b, fh)
        for path in ("/team/quantum-ferrets", "/players"):
            with self.subTest(path=path):
                self.assertIn("no NFL team", visible_text(self.get(path, "simple")))


class TestWeekPicker(Case):
    def test_the_compare_week_is_a_list_of_the_seasons_weeks(self):
        body = self.get("/tools/compare_players", "simple")
        self.assertIn('<select id="f_week" name="week"', body)
        self.assertIn('<option value="3" selected>week 3', body)
        self.assertIn('<option value="14"', body)


class TestHomeClocks(Case):
    def test_home_shows_the_next_waiver_run(self):
        self.assertIn("next waiver run", visible_text(self.get("/", "simple")))

    def test_the_trade_deadline_counts_down_in_weeks_9_to_11(self):
        self.set_week(10)
        text = visible_text(self.get("/", "simple"))
        self.assertIn("trade deadline", text)
        self.assertEqual([t for t in DEV_TERMS if t in text], [])
        self.set_week(3)
        self.assertNotIn("trade deadline", visible_text(self.get("/", "simple")))


class TestInjuryReport(Case):
    def test_a_rosters_designations_with_the_detail(self):
        from webui.objects import injury_report
        rows = injury_report(self.root, "Quantum Ferrets")
        r = next(x for x in rows if x["name"] == "Player 0 O'Neil")
        self.assertEqual((r["status"], r["body_part"], r["practice"], r["pid"]), ("Questionable", "Ankle", "Limited", "100"))
        self.assertTrue(r["updated"])

    def test_home_and_the_team_page_show_it(self):
        for path in ("/", "/team/quantum-ferrets"):
            for mode in ("dev", "simple"):
                with self.subTest(path=path, mode=mode):
                    text = visible_text(self.get(path, mode))
                    self.assertIn("Ankle", text)
                    self.assertIn("Limited", text)
                    if mode == "simple":
                        self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

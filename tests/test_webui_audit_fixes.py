"""
tests.test_webui_audit_fixes -- web UI defects found by the post-roadmap review of 2026-09-29,
each confirmed (by probe, by the test client, or in a browser) before this file was written.

1. The TV view never showed the Las Vegas logo: webui.live.ABBR_ALIASES maps "LV" to a legacy
   "OAK", and the gameday route overwrote the real "LV" logo with OAK's (none), then dropped it.
2. /img's one-day cache header was replaced by the app-wide no-store, so every headshot and logo
   was fetched again on every page view.
3. Root's image listing is cached on the folder's mtime; on Windows a file written in the same
   clock tick as the listing left the mtime unchanged and stayed invisible until a restart.
4. A sandbox served no headshots or logos (the copy left the image cache out), and a sync run
   in it fetched every image again.
5. Lineup calls and trade results read a score recorded with no player id (the first-recorded
   log writes null for a player with no baseline) as 0 points; the week-1 starting QB's 17.66
   was lost from both totals on real data. They fall back to the player's name.
6. A running job's own page carried the job bar too, so each job-page tab held two event
   streams; a browser allows six connections per host. The job bar is not shown on the job's
   own page, and it polls rather than streams (tests.test_webui_browser checks both).
7. The reliability section on Accuracy used `.grid2`, a class only Home defines, so from week 5
   the chart would have stretched to the full page width.
8. Smaller ones: a tied matchup counted as a win in the new reliability pairs and Brier-by-week
   (the overall Brier scores it 0.5); a malformed first-recorded-scores row raised on the player
   and Accuracy pages; the `!important` utilities could override a `hidden` attribute; the
   pressed compact button lost its colours on hover; `pctn` printed "1.0%" for True.
"""
import io
from tests.webui_served import base_source, inline_assets  # UI-E2: the page as served
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
PNG = b"\x89PNG\r\n\x1a\n0"

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_objects import QF, plant
    from tests.test_webui_routes import TEAMS
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def base_css():
    with io.StringIO(base_source()) as fh:
        return fh.read()


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def image(self, kind, name):
        d = os.path.join(self.td.name, "data", "images", kind)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(PNG)

    def client(self, mode="dev", runner=None):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=runner or FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def append(self, rel, rows):
        with open(os.path.join(self.td.name, "data", *rel.split("/")), "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


class TestTheLasVegasLogo(Case):
    def test_the_tv_view_carries_it(self):
        for t in ("lv", "was"):
            self.image("teams", t + ".png")
        body = inline_assets(self.client().get("/gameday").get_data(as_text=True))
        self.assertIn('"LV": "/img/teams/lv.png"', body)
        self.assertIn('"WSH": "/img/teams/was.png"', body, "the ESPN alias still works")


class TestImagesAreCacheable(Case):
    def test_an_image_keeps_its_day_and_a_page_stays_no_store(self):
        self.image("teams", "lv.png")
        c = self.client()
        self.assertIn("max-age=86400", c.get("/img/teams/lv.png").headers.get("Cache-Control", ""))
        self.assertEqual(c.get("/league").headers.get("Cache-Control"), "no-store")


class TestANewImageIsSeen(Case):
    def test_a_file_written_in_the_same_tick_appears_after_a_moment(self):
        self.image("teams", "kc.png")
        d = os.path.join(self.td.name, "data", "images", "teams")
        self.assertIsNone(self.root.image("teams", "LV"))
        before = os.stat(d).st_mtime_ns
        with open(os.path.join(d, "lv.png"), "wb") as fh:
            fh.write(PNG)
        os.utime(d, ns=(before, before))                    # the folder's mtime did not move (one clock tick)
        import webui.paths as paths
        later = time.monotonic() + 60
        with patch.object(paths.time, "monotonic", return_value=later):
            self.assertEqual(self.root.image("teams", "LV"), "/img/teams/lv.png")


class TestTheSandboxHasTheImages(Case):
    def test_the_copy_serves_them(self):
        from webui import sandbox
        self.image("teams", "gb.png")
        sb = sandbox.create(self.root)
        try:
            self.assertEqual(sb.image("teams", "GB"), "/img/teams/gb.png")
        finally:
            sandbox.discard(sb)


class TestAScoreWithNoPlayerId(Case):
    def test_lineup_calls_read_it_by_name(self):
        from webui.decision_quality import season
        with open(os.path.join(self.td.name, "data", "current", "weekly_lineups.json"), "w", encoding="utf-8") as fh:
            json.dump({"week_1": {QF: {"starters": ["100", "101"], "players": ["100", "101"]}}}, fh)
        self.append("logs/projection_log.jsonl", [{"player_id": "101", "name": "Player 1 O'Neil", "week": 1, "sleeper_mean": 12.0,
                                                   "synced_at": "2026-09-09T10:00:00Z"}])
        self.append("logs/first_recorded_scores.jsonl", [{"player_id": None, "name": "Player 1 O'Neil", "week": 1, "points": 7.0,
                                                          "recorded_at": "2026-09-15T00:00:00Z"}])
        (row,) = season(self.root, QF, slots=("QB", "QB"))
        self.assertAlmostEqual(row["started_act"], 24.3 + 7.0)

    def test_trade_results_read_it_by_name(self):
        from webui.trade_results import trade_results
        with open(os.path.join(self.td.name, "data", "current", "weekly_lineups.json"), "w", encoding="utf-8") as fh:
            json.dump({"week_3": {TEAMS[2]: {"starters": ["5850"], "players": ["5850"]}}}, fh)
        self.append("logs/first_recorded_scores.jsonl", [{"player_id": None, "name": "Josh Jacobs", "week": 3, "points": 20.0,
                                                          "recorded_at": "2026-09-29T10:00:00Z"}])
        (t,) = trade_results(self.root)
        self.assertEqual(t["sides"][TEAMS[2]]["got_pts"], 20.0)


class TestTheJobBar(Case):
    def running(self):
        r = FakeRunner()
        jid = r.launch(["py", "-m", "scripts.weekly_report"], "weekly_report")
        r.busy = r.metas[jid]
        return r, jid

    def test_the_jobs_own_page_carries_no_job_bar(self):
        r, jid = self.running()
        body = inline_assets(self.client(runner=r).get(f"/jobs/{jid}").get_data(as_text=True))
        self.assertNotIn('id="jobbar"', body)
        self.assertIn('id="jobbar"', self.client(runner=r).get("/league").get_data(as_text=True), "other pages keep it")


class TestTheReliabilityLayout(Case):
    def test_its_wrapper_is_styled_on_the_page(self):
        from webui import accuracy
        old = accuracy.ENOUGH_WEEKS
        accuracy.ENOUGH_WEEKS = 1
        try:
            body = inline_assets(self.client().get("/accuracy").get_data(as_text=True))
        finally:
            accuracy.ENOUGH_WEEKS = old
        self.assertIn('class="relgrid"', body)
        self.assertIn(".relgrid {", body)


class TestATie(Case):
    def test_a_tied_matchup_is_half_a_win_in_the_new_measures(self):
        from webui import accuracy
        real = __import__("webui.results", fromlist=["week_results"]).week_results

        def tied(root):
            got = real(root)
            got[2] = dict(got.get(2) or {})
            for t in (QF, TEAMS[3]):
                got[2][t] = dict(got[2].get(t) or {}, h2h_win=0.5)
            return got
        with patch("webui.results.week_results", side_effect=tied):
            r = accuracy.report(self.root)
        self.assertAlmostEqual(r["brier_weeks"][0]["matchups"], round((0.71 - 0.5) ** 2, 6))


class TestAMalformedScoreRow(Case):
    def test_the_player_and_accuracy_pages_survive_it(self):
        # a spread for the player, so the bad row is reached rather than skipped
        d = os.path.join(self.td.name, "data", "weeks", "week_01")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "player_variance.json"), "w", encoding="utf-8") as fh:
            json.dump({QF: [{"name": "Player 3 O'Neil", "min": 0, "p10": 2, "p25": 5, "p50": 10, "p75": 15, "p90": 22, "max": 80}]}, fh)
        self.append("logs/first_recorded_scores.jsonl", ['{"week": 1, "name": "Player 0', {"week": 1, "name": "Player 3 O'Neil", "points": "abc", "player_id": "103"}])
        from webui.calibration import player_landings
        player_landings(self.root, "Player 3 O'Neil")
        c = self.client()
        self.assertEqual(c.get("/player/100").status_code, 200)
        self.assertEqual(c.get("/accuracy").status_code, 200)


class TestSmallCss(unittest.TestCase):
    def test_hidden_wins_over_the_utilities(self):
        css = base_css()
        last_utility = css.rindex(".u-")
        self.assertRegex(css[last_utility:], r"\[hidden\]\s*\{\s*display:\s*none\s*!important")

    def test_the_pressed_compact_button_keeps_its_colours_on_hover(self):
        self.assertIn('.densitybtn[aria-pressed="true"]:hover', base_css())


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPctn(unittest.TestCase):
    def test_true_is_not_a_percentage(self):
        with tempfile.TemporaryDirectory() as td:
            plant(td)
            app = create_app(td, runner=FakeRunner(), csrf_token="tok", live=LiveBoard(td, MY_TEAM, league_id=None, fetch=None))
            f = app.jinja_env.filters["pctn"]
            self.assertEqual((f(True), f(None), f(12.345, 1)), ("—", "—", "12.3%"))


if __name__ == "__main__":
    unittest.main()

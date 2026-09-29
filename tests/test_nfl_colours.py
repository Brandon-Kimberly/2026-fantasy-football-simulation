"""
tests.test_nfl_colours -- NFL team colours, cached at sync (docs/WEB_UI_ROADMAP.md UI-V4).

ESPN's teams endpoint carries every team's `color` and `alternateColor` (six hex digits, no
"#"; checked 2026-09-29: 32 teams, Washington as WSH). The sync writes them, keyed by this
repo's team codes (WSH -> WAS, as the schedule fetch already maps it), to
data/current/nfl_team_colors.json. Cosmetic, like the image cache: a malformed colour is left
out, and a failed fetch writes nothing, raises nothing and logs at INFO, never WARNING (which
would mark the sync degraded). The pages draw a small swatch, never coloured text, and a
team with no cached colour gets the neutral swatch. Written before any of it existed.
"""
import json
import os
import tempfile
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_charts import SOS
    from tests.test_webui_routes import build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

ESPN = {"sports": [{"leagues": [{"teams": [
    {"team": {"abbreviation": "ARI", "color": "a40227", "alternateColor": "ffffff"}},
    {"team": {"abbreviation": "WSH", "color": "5A1414", "alternateColor": "ffb612"}},
    {"team": {"abbreviation": "KC", "color": "e31837", "alternateColor": "ffb612"}},
    {"team": {"abbreviation": "DEN", "color": "not-a-colour", "alternateColor": "0a2343"}},
    {"team": {"abbreviation": "NE"}},
]}]}]}


class _Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


class TestTheFetch(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, "nfl_team_colors.json")

    def tearDown(self):
        self.td.cleanup()

    def test_the_colours_are_written_under_this_repos_codes(self):
        from fantasy_sim.sync import fetch_nfl_team_colors
        n = fetch_nfl_team_colors(path=self.path, get=lambda url, timeout=None: _Resp(ESPN))
        with open(self.path, encoding="utf-8") as fh:
            got = json.load(fh)
        self.assertEqual(got["WAS"], {"color": "#5a1414", "alt": "#ffb612"}, "WSH is WAS here, and hex is lower case")
        self.assertEqual(got["ARI"]["color"], "#a40227")
        self.assertNotIn("WSH", got)
        self.assertNotIn("NE", got, "no colour, no entry")
        self.assertEqual(got["DEN"], {"color": None, "alt": "#0a2343"}, "a malformed colour is left out, not guessed")
        self.assertEqual(n, 4)

    def test_a_failed_fetch_writes_nothing_raises_nothing_and_warns_nothing(self):
        from fantasy_sim.sync import fetch_nfl_team_colors

        def boom(url, timeout=None):
            raise ConnectionError("offline")
        for get in (boom, lambda url, timeout=None: _Resp({}, 503)):
            with self.assertNoLogs(level="WARNING"):
                self.assertEqual(fetch_nfl_team_colors(path=self.path, get=get), 0)
            self.assertFalse(os.path.exists(self.path))

    def test_the_sync_calls_it_inside_a_guard(self):
        """Structural, stated as one: the full sync is not run by the suite."""
        import inspect
        from fantasy_sim import sync
        src = inspect.getsource(sync._sync_body)
        self.assertIn("fetch_nfl_team_colors(", src)
        self.assertIn("except Exception", src[src.index("fetch_nfl_team_colors("):])

    def test_the_file_lives_in_data_current(self):
        from fantasy_sim.storage import NFL_TEAM_COLORS_FILE
        self.assertEqual(os.path.normpath(NFL_TEAM_COLORS_FILE), os.path.normpath(os.path.join("data", "current", "nfl_team_colors.json")))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestThePages(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        with open(os.path.join(self.td.name, "data", "weeks", "week_03", "strength_of_schedule.json"), "w", encoding="utf-8") as fh:
            json.dump(SOS, fh)
        with open(os.path.join(self.td.name, "data", "current", "nfl_team_colors.json"), "w", encoding="utf-8") as fh:
            json.dump({"KC": {"color": "#e31837", "alt": "#ffb612"}}, fh)
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

    def test_the_schedule_grid_carries_a_swatch_and_a_neutral_fallback(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/forecasts/week-3", mode)
                self.assertIn('<i class="nflsw" style="--team:#e31837"></i><b>KC</b>', body)
                self.assertIn('<i class="nflsw"></i><b>CAR</b>', body, "no cached colour: the neutral swatch")

    def test_the_tv_view_gets_the_colours(self):
        body = self.get("/gameday")
        self.assertIn('"KC": "#e31837"', body)


if __name__ == "__main__":
    unittest.main()

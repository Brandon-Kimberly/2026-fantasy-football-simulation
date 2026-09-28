"""
tests.test_webui_home -- the landing page and the trimmed System page.

The landing page is assembled from what is already on disk -- the week's prediction row,
the league schedule, the forecast export, the trajectory, standings, the newest records,
the sync manifest -- and reads nothing else. What is pinned: the matchup is the right one
with the right probability, the tiles carry the forecast, the sparkline has as many points
as the series, the health strip reflects the manifest, degraded inputs render rather than
500, the old /status redirects, the brand is one constant, and the tree is untouched.
"""
import json
import os
import re
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui import brand, glance
from webui.names import Overlay
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import TEAMS, build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

OPP = "Iron Wombats"


def enrich(root):
    """On top of build_tree: a schedule, a week prediction with matchups, a trajectory, sources."""
    sched = [[[TEAMS[0], TEAMS[4]], [TEAMS[1], TEAMS[5]], [TEAMS[2], TEAMS[7]], [MY_TEAM, OPP]] for _ in range(14)]
    with open(os.path.join(root, "data", "current", "league_schedule.json"), "w", encoding="utf-8") as fh:
        json.dump(sched, fh)
    rows = [{"week": 3, "record_type": "week_predictions", "canonical": False, "logged_at": "2026-09-25T17:24:00Z",
             "matchups": [{"a": MY_TEAM, "b": OPP, "p_a": 0.7123, "p_b": 0.2877, "se": 0.007}],
             "median": {MY_TEAM: {"expected_total": 203.9, "p_beat_median": 0.79, "opponent": OPP},
                        OPP: {"expected_total": 150.2, "p_beat_median": 0.31, "opponent": MY_TEAM}}},
            {"week": 3, "record_type": "week_predictions", "canonical": True, "logged_at": "2026-09-24T16:00:00Z",
             "matchups": [{"a": MY_TEAM, "b": OPP, "p_a": 0.6999, "p_b": 0.3001, "se": 0.007}],
             "median": {MY_TEAM: {"expected_total": 201.0, "p_beat_median": 0.77, "opponent": OPP}}}]
    with open(os.path.join(root, "data", "logs", "predictions_2026.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
    p = os.path.join(root, "data", "weeks", "week_03", "syndicate_comprehensive_matrix_week_3.json")
    m = json.load(open(p, encoding="utf-8"))
    m["weekly_trajectories"] = {t: {"expected_cumulative_wins_by_week": [2.0, 2.0, 3.6, 5.1, 6.4, 7.8, 9.1, 10.5, 11.8, 13.2, 14.5, 15.9, 17.2, 18.6]} for t in TEAMS}
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(m, fh)
    mp = os.path.join(root, "data", "current", "sync_manifest.json")
    man = json.load(open(mp, encoding="utf-8"))
    man["sources"] = {"sleeper_rosters": {"ok": True, "rows": 8, "fallback": None},
                      "vegas_odds": {"ok": True, "rows": 31, "fallback": "flat 21.5 / no opponent"},
                      "weather": {"ok": False, "rows": 0, "fallback": None}}
    with open(mp, "w", encoding="utf-8") as fh:
        json.dump(man, fh)


class TestGlance(unittest.TestCase):
    def test_team_hue_is_stable_and_bounded(self):
        self.assertEqual(glance.team_hue("Quantum Ferrets"), glance.team_hue("Quantum Ferrets"))
        self.assertTrue(0 <= glance.team_hue("x") < 360)
        self.assertNotEqual(glance.team_hue("Quantum Ferrets"), glance.team_hue("Neon Walruses"))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestHome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def client(self, overlay=None, runner=None):
        app = create_app(self.root, runner=runner or FakeRunner(), csrf_token="tok", overlay=overlay)
        app.testing = True
        return app.test_client()

    def test_home_report_reads_the_matchup_the_forecast_and_the_health_strip(self):
        r = glance.home_report(self.root, MY_TEAM)
        self.assertEqual(r["week"], 3)
        self.assertEqual(r["opponent"], OPP)
        self.assertEqual(r["matchup"]["p_win"], 0.6999, "the canonical row is preferred over a newer non-canonical one")
        self.assertEqual(r["matchup"]["mine"], 201.0)
        self.assertEqual(len(r["trajectory"]), 14)
        self.assertEqual(r["my_row"]["team"], MY_TEAM)
        self.assertEqual((r["fresh"]["n_ok"], r["fresh"]["n_fell"], r["fresh"]["n_bad"]), (1, 1, 1))
        self.assertEqual(r["standings"][0]["rank"], 1)

    def test_landing_page_renders_the_glance_and_touches_nothing(self):
        before = self.root.tree_digest()
        body = self.client().get("/").get_data(as_text=True)
        self.assertIn(OPP, body)
        self.assertIn('data-count="70.0"', body)                    # P(win) from the canonical row
        self.assertIn("above the league median", body)
        self.assertIn('class="viz"', body)
        self.assertEqual(body.count("<polyline"), 1)                 # the expected-wins trajectory
        self.assertIn(">now<", body)                                 # the current-week marker
        self.assertIn("1 ok", body)                                  # sources chip
        self.assertIn("fell back", body)
        self.assertIn('class="ring"', body)
        self.assertIn("Watch list", body)
        self.assertIn("/tools/optimize_lineup", body)
        self.assertEqual(self.root.tree_digest(), before)

    def test_the_brand_is_one_constant_and_the_old_status_redirects(self):
        c = self.client()
        body = c.get("/").get_data(as_text=True)
        self.assertIn(brand.NAME, body)
        self.assertIn(brand.TAGLINE, body)
        r = c.get("/status")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/system"))

    def test_system_page_is_the_trimmed_status(self):
        body = self.client().get("/system").get_data(as_text=True)
        self.assertIn("pill DEGRADED", body)
        self.assertIn("vegas odds", body)
        self.assertIn("fell back", body)
        self.assertIn("run a sync to persist them", body)
        self.assertIn("from a terminal", body)
        visible = re.sub(r'<[^>]+>', ' ', re.sub(r'<(script|style).*?</\1>', ' ', body, flags=re.S))
        self.assertLess(len(visible.split()), 900, 'the trimmed status is a page of sentences, not a dump')

    def test_overlay_applies_on_the_landing_page_and_urls_stay_pseudonymous(self):
        body = self.client(overlay=Overlay({MY_TEAM: "Team Alpha", OPP: "Team Omega"})).get("/").get_data(as_text=True)
        self.assertIn("Team Alpha", body)
        self.assertIn("Team Omega", body)
        self.assertNotIn(MY_TEAM, body)
        self.assertNotIn("Team%20Alpha", body)

    def test_a_tree_without_a_prediction_or_schedule_still_renders(self):
        with tempfile.TemporaryDirectory() as td:
            build_tree(td)                                            # no matchups in its prediction row
            os.remove(os.path.join(td, "data", "current", "league_schedule.json"))
            app = create_app(Root(td), runner=FakeRunner(), csrf_token="tok")
            app.testing = True
            body = app.test_client().get("/")
            self.assertEqual(body.status_code, 200, body.data[:300])
            self.assertIn("No matchup on file", body.get_data(as_text=True))
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "data", "current"))
            app = create_app(Root(td), runner=FakeRunner(), csrf_token="tok")
            app.testing = True
            self.assertEqual(app.test_client().get("/").status_code, 200)

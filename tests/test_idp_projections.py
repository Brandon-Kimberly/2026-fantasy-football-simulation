"""UI-P8: IDP category projections (owner request 2026-09-30).

IDP is this league's differentiator and every tool's blind spot: the baselines carry a points
mean, not expected tackles and sacks. Verified the same day, Sleeper's weekly projection payload
-- the one the sync already fetches for the baselines -- carries per-category IDP expectations
(one LB: 4.29 solo tackles, 3.97 assists, 0.37 TFL, 0.13 sacks, 0.26 QB hits, 0.21 passes
defended). Pinned here:
  * the sync keeps them, for defensive players only, and only the categories this league scores,
    each with the points it is worth under the league's settings, in
    data/current/idp_projections.json (display only: no engine input, so the sync golden's
    two hashes do not move -- checked by `py -3.10 -m tests.golden_sync`);
  * the player page shows the line in both views, with its week and its total, and says what it
    is (Sleeper's line scored by this league's rules, not the model's number); an offensive
    player, or no file, shows nothing and breaks nothing;
  * the quick compare shows the lines side by side when the players it compares are defenders.
Written before the code.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import MY_TEAM, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

# the league's IDP settings as the sync logged them on 2026-09-30 (data/logs/scoring_settings.jsonl)
SCORING = {"idp_blk_kick": 2.0, "idp_def_td": 6.0, "idp_ff": 3.0, "idp_fum_rec": 3.0, "idp_fum_ret_yd": 0.0,
           "idp_int": 5.0, "idp_int_ret_yd": 0.0, "idp_pass_def": 1.5, "idp_qb_hit": 0.5, "idp_sack": 2.0,
           "idp_sack_yd": 0.0, "idp_safe": 2.0, "idp_tkl": 0.0, "idp_tkl_ast": 0.75, "idp_tkl_loss": 2.0,
           "idp_tkl_solo": 1.5, "rec": 1.0, "pass_yd": 0.04}
LB_LINE = {"idp_tkl_solo": 4.29, "idp_tkl_ast": 3.97, "idp_tkl": 8.26, "idp_tkl_loss": 0.37, "idp_sack": 0.13,
           "idp_sack_yd": 0.74, "idp_qb_hit": 0.26, "idp_pass_def": 0.21, "idp_ff": 0.11, "idp_fum_rec": 0.11}
LB_TOTAL = 4.29 * 1.5 + 3.97 * 0.75 + 0.37 * 2 + 0.13 * 2 + 0.26 * 0.5 + 0.21 * 1.5 + 0.11 * 3 + 0.11 * 3


class TestTheSyncKeepsTheLine(unittest.TestCase):
    PROJ = {"200": {"stats": dict(LB_LINE, pts_ppr=12.0)},
            "201": {"stats": {"idp_tkl_solo": 0.0, "idp_sack": 0.0}},          # a defender with nothing projected
            "300": {"stats": {"rec": 5.0, "rec_yd": 60.0}},                     # a receiver
            "202": {"stats": {"idp_tkl_solo": 2.0, "idp_sack": 0.5}}}           # a DE
    DB = {"200": {"position": "LB"}, "201": {"position": "CB"}, "300": {"position": "WR"}, "202": {"position": "DE"}}

    def test_defenders_only_scored_categories_only_each_with_its_points(self):
        from fantasy_sim import sync
        out = sync.idp_projection_lines(self.PROJ, self.DB, SCORING)
        self.assertEqual(sorted(out), ["200", "202"], "defenders with a projection; no receiver, no zero line")
        lb = out["200"]
        self.assertEqual(lb["pos"], "LB")
        self.assertEqual(out["202"]["pos"], "DL", "a DE is a lineman")
        self.assertNotIn("idp_tkl", lb["stats"], "combined tackles score nothing here; solo and assists do")
        self.assertNotIn("idp_sack_yd", lb["stats"], "sack yards score nothing here")
        self.assertAlmostEqual(lb["stats"]["idp_tkl_solo"], 4.29)
        self.assertAlmostEqual(lb["points"]["idp_tkl_solo"], 6.44, places=2)
        self.assertAlmostEqual(lb["total"], round(LB_TOTAL, 2), places=2)

    def test_a_season_projection_is_per_game(self):
        from fantasy_sim import sync
        proj = {"200": {"stats": {"idp_tkl_solo": 64.0, "idp_sack": 2.0, "gp": 16.0}}}
        out = sync.idp_projection_lines(proj, self.DB, SCORING, fallback_season=True)
        self.assertAlmostEqual(out["200"]["stats"]["idp_tkl_solo"], 4.0)
        self.assertAlmostEqual(out["200"]["total"], 4.0 * 1.5 + 0.125 * 2, places=2)

    def test_it_is_written_with_its_week_and_source(self):
        from fantasy_sim import storage, sync
        saved = {}
        with patch.object(sync, "save_json", side_effect=lambda p, d, *a, **k: saved.__setitem__(p, d)):
            sync.write_idp_projections(self.PROJ, self.DB, SCORING, week=5, fallback_season=False)
        doc = saved[storage.IDP_PROJECTIONS_FILE]
        self.assertEqual(doc["_meta"]["week"], 5)
        self.assertEqual(doc["_meta"]["source"], "weekly")
        self.assertIn("200", doc["players"])

    def test_baseline_generation_writes_it(self):
        import inspect
        from fantasy_sim import sync
        self.assertIn("write_idp_projections(", inspect.getsource(sync.generate_player_baselines))


def _plant_idp(root, week=3, with_file=True):
    base_path = os.path.join(root, "data", "current", "player_baselines.json")
    with open(base_path, encoding="utf-8") as fh:
        base = json.load(fh)
    base["Test Linebacker"] = {"pos": "LB", "team": "DET", "mean": 11.5, "bye": 8, "on_ir": False, "player_id": "200",
                               "std_aleatoric": 4.0, "std_epistemic": 1.5}
    base["Test Lineman"] = {"pos": "DL", "team": "GB", "mean": 7.0, "bye": 5, "on_ir": False, "player_id": "202",
                            "std_aleatoric": 3.5, "std_epistemic": 1.2}
    with open(base_path, "w", encoding="utf-8") as fh:
        json.dump(base, fh)
    if with_file:                     # written out by hand, so the pages are tested apart from the sync
        lb = {k: v for k, v in LB_LINE.items() if SCORING.get(k)}
        doc = {"_meta": {"week": week, "source": "weekly", "synced_at": "2026-10-01T12:00:00Z"},
               "players": {"200": {"pos": "LB", "stats": lb, "points": {k: round(v * SCORING[k], 2) for k, v in lb.items()},
                                   "total": round(LB_TOTAL, 2)},
                           "202": {"pos": "DL", "stats": {"idp_tkl_solo": 2.0, "idp_sack": 0.5},
                                   "points": {"idp_tkl_solo": 3.0, "idp_sack": 1.0}, "total": 4.0}}}
        with open(os.path.join(root, "data", "current", "idp_projections.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestThePages(unittest.TestCase):
    def app(self, with_file=True, mode="simple"):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        plant(self.td.name)
        _plant_idp(self.td.name, with_file=with_file)
        root = Root(self.td.name)
        st = Settings(root)
        st.set_mode(mode)
        app = create_app(root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def test_the_player_page_shows_the_line_in_both_views(self):
        for mode in ("simple", "dev"):
            with self.subTest(mode=mode):
                html = self.app(mode=mode).get("/player/200").get_data(as_text=True)
                text = visible_text(html)
                self.assertIn('id="idp-line"', html)
                for label in ("Solo tackles", "Assisted tackles", "Tackles for loss", "Sacks", "QB hits", "Passes defended"):
                    self.assertIn(label, text)
                self.assertNotIn("Sack yards", text, "a category the league does not score")
                self.assertIn("4.3", text)
                self.assertIn(f"{LB_TOTAL:.1f}", text, "the line's total under the league's settings")
                self.assertIn("week 3", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])

    def test_nothing_for_an_offensive_player_or_without_the_file(self):
        c = self.app()
        self.assertNotIn('id="idp-line"', c.get("/player/100").get_data(as_text=True))
        r = self.app(with_file=False).get("/player/200")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('id="idp-line"', r.get_data(as_text=True))

    def test_the_quick_compare_shows_the_lines_side_by_side(self):
        html = self.app().get("/tools/compare_players/instant?p=Test+Linebacker&p=Test+Lineman&week=3").get_data(as_text=True)
        self.assertIn('class="inst idp"', html)
        text = visible_text(html)
        self.assertIn("Solo tackles", text)
        self.assertIn("4.3", text)
        self.assertIn("2.0", text, "the lineman's solo tackles")
        html = self.app().get("/tools/compare_players/instant?p=Player+0+O%27Neil&p=Player+1+O%27Neil&week=3").get_data(as_text=True)
        self.assertNotIn('class="inst idp"', html, "no defensive table for two quarterbacks")


if __name__ == "__main__":
    unittest.main()

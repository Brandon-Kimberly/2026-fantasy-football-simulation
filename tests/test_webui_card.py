"""
tests.test_webui_card -- players in the command palette and the player card's second version
(docs/WEB_UI_ROADMAP.md UI-A9, UI-P5).

UI-A9  Typing a player's name in the palette offers that player's page. The palette's own
       items are pages, tools and teams; players come from /api/players, which now carries
       each player's id so a result can link to /player/<id>.
UI-P5  The card adds the distribution strip (UI-P2) for a rostered player and the injury
       detail -- body part, practice participation, when Sleeper last updated it. It already
       opens on keyboard focus (UI-V7). "Rostered by N of 8" is not built: this league has one
       copy of each player, so the card's owner line already says all there is.
"""
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    import tempfile
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
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)
        st = Settings(self.root)
        st.set_mode("simple")
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        self.c = app.test_client()

    def tearDown(self):
        self.td.cleanup()


class TestPaletteFindsPlayers(Case):
    def test_the_player_index_carries_the_id_a_result_links_to(self):
        ps = self.c.get("/api/players?q=Player 0").get_json()["players"]
        self.assertEqual((ps[0]["name"], ps[0]["pid"]), ("Player 0 O'Neil", "100"))


class TestTheCard(Case):
    def test_the_card_carries_the_strip_and_the_injury_detail(self):
        d = self.c.get("/api/player?name=Player 0 O'Neil").get_json()
        self.assertIn('class="dstrip"', d["strip"] or "")
        self.assertEqual((d["body_part"], d["practice"]), ("Ankle", "Limited"))
        self.assertTrue(d["updated"])

    def test_a_player_the_simulation_did_not_see_has_no_strip(self):
        d = self.c.get("/api/player?name=Player 1 O'Neil").get_json()
        self.assertIsNone(d["strip"])


if __name__ == "__main__":
    unittest.main()

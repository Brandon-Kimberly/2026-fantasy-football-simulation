"""
tests.test_webui_images -- headshots and team logos, served from the local cache
(docs/WEB_UI_ROADMAP.md Decision 3, UI-P6 and UI-E7).

The sync writes data/images/players/<id>.jpg and data/images/teams/<code>.png. The UI serves
them through one route, /img/<kind>/<file>, which accepts a digits-only id as .jpg or a two-
or three-letter lower-case team code as .png, and nothing else. A player with a cached headshot
shows it on the player page, in the roster and player lists, on the hover card and on the TV
view. A player without one falls back to the NFL team's logo, and a player with neither falls
back to the lettered mark on the player page. No page asks a third party for a headshot or a
logo.
"""
import os
import re
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
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import QF, plant
    from webui.render import slug
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
THIRD_PARTY = re.compile(r"sleepercdn\.com/(content|images)|team_logos|espncdn")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def image(self, kind, name, data):
        d = os.path.join(self.td.name, "data", "images", kind)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(data)

    def client(self, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def get(self, path, mode="dev"):
        r = self.client(mode).get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)


class TestTheRoute(Case):
    def test_a_cached_image_is_served_with_its_type(self):
        self.image("players", "100.jpg", JPEG)
        self.image("teams", "gb.png", PNG)
        c = self.client()
        r = c.get("/img/players/100.jpg")
        self.assertEqual((r.status_code, r.mimetype, r.data), (200, "image/jpeg", JPEG))
        r = c.get("/img/teams/gb.png")
        self.assertEqual((r.status_code, r.mimetype), (200, "image/png"))

    def test_a_headshot_is_served_as_what_its_bytes_are(self):
        """Sleeper's headshots are PNG bytes under a .jpg name; the type follows the bytes."""
        self.image("players", "104.jpg", PNG)
        r = self.client().get("/img/players/104.jpg")
        self.assertEqual((r.status_code, r.mimetype), (200, "image/png"))

    def test_anything_else_is_refused(self):
        self.image("players", "100.jpg", JPEG)
        c = self.client()
        for bad in ("/img/players/101.jpg", "/img/players/100.png", "/img/players/abc.jpg", "/img/teams/GB.png",
                    "/img/teams/gb.jpg", "/img/local/settings.json", "/img/players/..%2F..%2Fcurrent%2Fleague_state.json"):
            with self.subTest(bad=bad):
                self.assertEqual(c.get(bad).status_code, 404)


class TestThePages(Case):
    def test_the_player_page_shows_the_headshot(self):
        self.image("players", "100.jpg", JPEG)
        body = self.get("/player/100")
        self.assertRegex(body, r'<img class="face lg" src="/img/players/100\.jpg"')

    def test_no_headshot_falls_back_to_the_team_logo_then_the_mark(self):
        self.image("teams", "gb.png", PNG)
        self.assertRegex(self.get("/player/101"), r'<img class="face lg" src="/img/teams/gb\.png"')
        os.remove(os.path.join(self.td.name, "data", "images", "teams", "gb.png"))
        body = self.get("/player/101")
        # a rendered face has a local src; the hover card's script carries the bare tag as a string
        self.assertNotRegex(body, r'<img class="face[^"]*" src="/img/')
        self.assertRegex(body, r'<span class="face lg mark"[^>]*>P1</span>')

    def test_the_rosters_and_the_lists_carry_faces(self):
        self.image("players", "100.jpg", JPEG)
        for path in (f"/team/{slug(QF)}", "/players?owner=all"):
            with self.subTest(path=path):
                self.assertIn('src="/img/players/100.jpg"', self.get(path))

    def test_the_hover_card_carries_the_image(self):
        self.image("players", "100.jpg", JPEG)
        d = self.client().get("/api/player?name=Player 0 O'Neil").get_json()
        self.assertEqual(d["img"], "/img/players/100.jpg")
        self.assertIsNone(self.client().get("/api/player?name=Player 5 O'Neil").get_json()["img"])

    def test_no_page_asks_a_third_party_for_an_image(self):
        self.image("players", "100.jpg", JPEG)
        self.image("teams", "gb.png", PNG)
        for mode in ("dev", "simple"):
            for path in ("/", "/player/100", "/player/101", f"/team/{slug(QF)}", "/players?owner=all", "/gameday", "/current"):
                with self.subTest(mode=mode, path=path):
                    body = self.get(path, mode)
                    self.assertIsNone(THIRD_PARTY.search(body), path)
                    if mode == "simple":
                        self.assertEqual([t for t in DEV_TERMS if t in visible_text(body)], [])


if __name__ == "__main__":
    unittest.main()

"""Link previews for the public site, and two small fixes (owner request 2026-09-30).

  * Link previews. A link to the public site pasted into a group chat showed a bare URL. Every
    public page now carries a text-only preview (Open Graph and Twitter summary): the page's own
    title, a description, the site's name, and the page's absolute address -- and no image, by
    the owner's rule for the public site. The local UI carries none: it is not for sharing.
  * UI-F10. The sync's name-collision warning printed Python's None for a player with no NFL
    team ("pid 6994 (CB, None)"); it reads "no team".
  * UI-A8. Jobs was one long list: it is grouped by day, and filters by tool and by state.
Written before the code.
"""
import logging
import os
import re
import tempfile
import unittest
from unittest import mock

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_objects import MY_TEAM, plant
    from webui.jobs import OK, VOID
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

BASE = "/syndicate-football"
ORIGIN = "https://example.github.io"
META = re.compile(r'<meta (?:property|name)="([^"]+)" content="([^"]*)">')


def meta(html):
    return dict(META.findall(html))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestLinkPreviews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from webui.static_site import export
        cls.td = tempfile.TemporaryDirectory()
        plant(cls.td.name)
        cls.out = tempfile.mkdtemp()
        export(Root(cls.td.name), cls.out, BASE, origin=ORIGIN)
        cls.pages = {}
        for d, _s, fs in os.walk(cls.out):
            for f in fs:
                if f == "index.html":
                    rel = os.path.relpath(os.path.join(d, f), cls.out).replace(os.sep, "/")
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        cls.pages[rel] = fh.read()

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_every_public_page_has_a_text_preview_of_itself(self):
        for rel, html in self.pages.items():
            with self.subTest(page=rel):
                m = meta(html)
                title = re.search(r"<title>(.*?)</title>", html, re.S).group(1).strip()
                self.assertEqual(m.get("og:title"), title, "the preview is titled like the page")
                self.assertEqual(m.get("og:type"), "website")
                self.assertTrue(m.get("og:site_name"))
                self.assertGreater(len(m.get("og:description", "")), 40)
                self.assertEqual(m.get("description"), m.get("og:description"))
                self.assertEqual(m.get("twitter:card"), "summary", "a text card: no image")
                page = "/" + rel[:-len("index.html")]
                self.assertEqual(m.get("og:url"), ORIGIN + BASE + page, "its own absolute address")
                self.assertFalse(any(k.endswith("image") or ":image" in k for k in m), "no image in a preview")

    def test_the_local_ui_carries_no_preview(self):
        root = Root(self.td.name)
        st = Settings(root)
        st.set_mode("simple")
        app = create_app(root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        html = app.test_client().get("/").get_data(as_text=True)
        self.assertFalse(any(k.startswith(("og:", "twitter:")) for k in meta(html)))

    def test_the_build_passes_the_site_origin(self):
        from scripts import build_public_site as bps
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "someone/some-site"}):
            self.assertEqual(bps._default_origin(), "https://someone.github.io")


class TestNoTeamIsNotNone(unittest.TestCase):
    """UI-F10."""

    def test_the_collision_warning_says_no_team(self):
        from fantasy_sim.sync import resolve_player_keys
        db = {"1": {"first_name": "Same", "last_name": "Name", "position": "CB", "team": None},
              "2": {"first_name": "Same", "last_name": "Name", "position": "S", "team": "NYG"}}
        with self.assertLogs(level=logging.WARNING) as logs:
            resolve_player_keys(["1", "2"], db, rostered_pids=set())
        text = " ".join(logs.output)
        self.assertIn("pid 1 (CB, no team)", text)
        self.assertNotIn(", None)", text)

    def test_the_rostered_collision_error_says_no_team_too(self):
        from fantasy_sim.sync import resolve_player_keys
        db = {"1": {"first_name": "Same", "last_name": "Name", "position": "CB", "team": None},
              "2": {"first_name": "Same", "last_name": "Name", "position": "S", "team": "NYG"}}
        with self.assertRaises(ValueError) as ex:
            resolve_player_keys(["1", "2"], db, rostered_pids={"1", "2"})
        self.assertIn("(CB, no team)", str(ex.exception))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestJobsByDay(unittest.TestCase):
    """UI-A8. Four fixture jobs over two days, two tools, two states."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        plant(self.td.name)
        self.root = Root(self.td.name)
        r = FakeRunner()
        for tool, started, state in (("luck_ledger", "2026-09-26T12:00:00Z", OK), ("roster_grades", "2026-09-26T13:00:00Z", VOID),
                                     ("luck_ledger", "2026-09-28T12:00:00Z", VOID), ("roster_grades", "2026-09-28T13:00:00Z", OK)):
            jid = r.launch(["py", "-m", "scripts." + tool], tool)
            r.metas[jid].update(started_at=started, state=state, finished_at=started, rc=0 if state == OK else 1)
        self.runner = r

    def get(self, path):
        st = Settings(self.root)
        st.set_mode("dev")
        app = create_app(self.root, runner=self.runner, csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def rows(self, html):
        return re.findall(r'<tr class="jobrow" data-tool="([^"]+)" data-state="([^"]+)">', html)

    def test_grouped_under_one_heading_per_day_newest_first(self):
        html = self.get("/jobs")
        days = re.findall(r'<h2 class="jobday">(.*?)</h2>', html)
        self.assertEqual(len(days), 2, days)
        self.assertIn("Sep 28", days[0])
        self.assertIn("Sep 26", days[1])
        self.assertEqual(len(self.rows(html)), 4)

    def test_filters_by_tool_and_by_state(self):
        self.assertEqual(sorted(set(t for t, _s in self.rows(self.get("/jobs?tool=luck_ledger")))), ["luck_ledger"])
        self.assertEqual(len(self.rows(self.get("/jobs?tool=luck_ledger"))), 2)
        self.assertEqual(sorted(set(s for _t, s in self.rows(self.get("/jobs?state=VOID")))), ["VOID"])
        both = self.rows(self.get("/jobs?tool=roster_grades&state=OK"))
        self.assertEqual(both, [("roster_grades", "OK")])
        html = self.get("/jobs?tool=luck_ledger")
        self.assertIn('<form method="get" action="/jobs" class="jobfilter"', html)
        self.assertEqual(len(re.findall(r'<h2 class="jobday">', html)), 2, "a filtered list keeps its days")

    def test_an_unknown_filter_value_shows_nothing_rather_than_everything(self):
        self.assertEqual(self.rows(self.get("/jobs?tool=nope")), [])


if __name__ == "__main__":
    unittest.main()

"""
tests.test_webui_browser -- the page scripts, executed (docs/WEB_UI_ROADMAP.md UI-E1).

Every other web test pins a script by its PRESENCE in the served page. That caught nothing
when the Decisions filters broke: the script set `hidden` correctly and a class's
`display: grid` overrode the browser's own rule for it, so every row stayed on screen. Only
a real browser sees that. These tests serve the fixture tree on a loopback port and drive
the installed Edge (or Chrome) headless through Playwright, then assert on what is VISIBLE.

They skip cleanly without Flask, without Playwright (requirements-web.txt), or without a
browser Playwright can launch -- the same precedent as the other optional dependencies.
Nothing here touches the engine or the network beyond 127.0.0.1.
"""
import tempfile
import threading
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from werkzeug.serving import WSGIRequestHandler, make_server
    from playwright.sync_api import sync_playwright
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_fourth import plant as plant_fourth
    from tests.test_webui_home import enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import build_tree
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

# the pages each view links from its top bar; every one must load without a script error
PAGES = {"dev": ("/", "/league", "/forecasts", "/accuracy", "/decisions", "/records", "/tools", "/jobs",
                 "/logs", "/system", "/sync", "/gameday"),
         "simple": ("/", "/league", "/forecasts", "/decisions", "/tools", "/gameday")}


def launch(pw):
    """The installed Edge first (no browser download), then Chrome, then Playwright's own."""
    for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return pw.chromium.launch(headless=True, **kw)
        except Exception:                                    # noqa: BLE001 -- try the next one
            continue
    return None


def _never(url):
    raise AssertionError(f"the board fetched {url}: page renders must not, and the tests intercept /api/live")


def kickoffs(when):
    """A planter: week 3's synced kickoffs all at `when` (ISO)."""
    def plant(root):
        import json
        import os
        p = os.path.join(root, "data", "current", "nfl_schedule.json")
        with open(p, encoding="utf-8") as fh:
            sched = json.load(fh)
        sched.setdefault("_meta", {})["kickoffs"] = {"3": [when, when]}
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(sched, fh)
    return plant


class Quiet(WSGIRequestHandler if HAS_DEPS else object):
    def log(self, *a, **k):                                  # no per-request lines in the test output
        pass


class Served:
    """The fixture tree behind a real HTTP server on an ephemeral loopback port."""

    def __init__(self, plant=None, live_enabled=False):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        enrich(self.td.name)
        plant_fourth(self.td.name)
        if plant:
            plant(self.td.name)
        self.root = Root(self.td.name)
        self.settings = Settings(self.root)
        self.settings.set_theme("system")
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=self.settings,
                         live=LiveBoard(self.root, MY_TEAM, league_id="L" if live_enabled else None,
                                        fetch=_never if live_enabled else None))
        self.srv = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def mode(self, mode):
        self.settings.set_mode(mode)

    def close(self):
        self.srv.shutdown()
        self.td.cleanup()


@unittest.skipUnless(HAS_DEPS, "flask or playwright not installed (requirements-web.txt)")
class BrowserCase(unittest.TestCase):
    """One server and one browser per class; a fresh page per test."""
    plant = None
    live_enabled = False

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = launch(cls.pw)
        if cls.browser is None:
            cls.pw.stop()
            raise unittest.SkipTest("no browser Playwright can launch (Edge, Chrome, or its own)")
        cls.served = Served(cls.plant, cls.live_enabled)

    @classmethod
    def tearDownClass(cls):
        cls.served.close()
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.served.mode("dev")
        self.served.settings.set_theme("system")
        self.ctx = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(8000)
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(f"uncaught: {e}"))
        self.page.on("console", lambda m: m.type == "error" and self.errors.append(f"console: {m.text}"))

    def tearDown(self):
        self.ctx.close()

    def open(self, path, mode="dev"):
        self.served.mode(mode)
        self.page.goto(self.served.base + path, wait_until="load")
        return self.page

    def shown(self, selector):
        """How many elements matching `selector` are actually displayed."""
        return self.page.eval_on_selector_all(
            selector, "els => els.filter(e => getComputedStyle(e).display !== 'none').length")


class TestEveryPageRunsItsScripts(BrowserCase):
    def test_no_script_error_on_any_page_of_either_view(self):
        for mode, pages in PAGES.items():
            for path in pages:
                with self.subTest(mode=mode, path=path):
                    self.errors.clear()
                    self.open(path, mode)
                    self.page.wait_for_timeout(150)
                    self.assertEqual(self.errors, [], f"{mode} {path}")


class TestDecisionsFilters(BrowserCase):
    """The defect that motivated this module (commit b36f933)."""

    def test_each_filter_leaves_only_its_own_rows_on_screen(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                self.open("/decisions", mode)
                total = self.shown("#list .dec")
                self.assertGreater(total, 1, "the fixture must render several moves")
                self.page.click('#filters button[data-f="mine"]')
                mine = self.shown("#list .dec")
                self.assertLess(mine, total, "Mine must hide the other teams' moves")
                self.assertEqual(mine, self.shown('#list .dec[data-mine="1"]'))
                self.assertEqual(self.page.eval_on_selector_all(
                    '#list .dec:not([data-mine="1"])',
                    "els => els.filter(e => getComputedStyle(e).display !== 'none').length"), 0)
                for b in self.page.query_selector_all("#filters button"):
                    key = b.get_attribute("data-f")
                    if key in ("all", "mine", "moved"):
                        continue
                    b.click()
                    self.assertEqual(self.shown("#list .dec"), self.shown(f'#list .dec[data-type="{key}"]'), key)
                self.page.click('#filters button[data-f="all"]')
                self.assertEqual(self.shown("#list .dec"), total, "Everyone must bring every row back")


class TestPaletteAndShortcuts(BrowserCase):
    def test_the_palette_opens_filters_and_goes(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                p = self.open("/", mode)
                p.keyboard.press("Control+k")
                self.assertTrue(p.is_visible("#pal"))
                p.fill("#pal-q", "league")
                self.assertGreaterEqual(self.shown("#pal-list li"), 1)
                self.assertIn("League", p.inner_text("#pal-list li.on"))
                with p.expect_navigation():
                    p.keyboard.press("Enter")
                self.assertTrue(p.url.endswith("/league"), p.url)

    def test_escape_closes_the_palette(self):
        p = self.open("/")
        p.keyboard.press("Control+k")
        p.keyboard.press("Escape")
        self.assertFalse(p.is_visible("#pal"))

    def test_question_mark_toggles_the_shortcut_sheet_and_g_l_goes_to_league(self):
        p = self.open("/")
        p.keyboard.press("?")
        self.assertTrue(p.is_visible("#keys"))
        p.keyboard.press("Escape")
        self.assertFalse(p.is_visible("#keys"))
        with p.expect_navigation():
            p.keyboard.press("g")
            p.keyboard.press("l")
        self.assertTrue(p.url.endswith("/league"), p.url)

    def test_shortcuts_stay_out_of_the_way_while_typing(self):
        p = self.open("/")
        p.keyboard.press("Control+k")
        p.fill("#pal-q", "")
        p.type("#pal-q", "gl?")
        self.assertTrue(p.url.endswith("/"), "typing g then l in a field must not navigate")
        self.assertFalse(p.is_visible("#keys"))


class TestPlayerCard(BrowserCase):
    def test_hovering_a_player_opens_the_card_and_leaving_closes_it(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                p = self.open("/league", mode)
                el = p.locator("[data-player]").first
                name = el.get_attribute("data-player")
                el.hover()
                p.wait_for_selector("#pcard", state="visible")
                self.assertIn(name, p.inner_text("#pcard .n"))
                p.mouse.move(5, 5)
                p.wait_for_selector("#pcard", state="hidden")


class TestTheme(BrowserCase):
    def test_choosing_dark_repaints_the_page_and_system_undoes_it(self):
        p = self.open("/league")
        light = p.evaluate("getComputedStyle(document.body).backgroundColor")
        with p.expect_navigation():
            p.click('.themeform button[value="dark"]')
        self.assertEqual(p.get_attribute("html", "data-theme"), "dark")
        dark = p.evaluate("getComputedStyle(document.body).backgroundColor")
        self.assertNotEqual(light, dark, "the dark tokens must actually apply")
        self.assertTrue(p.url.endswith("/league"), "the choice returns to the page it was made on")
        with p.expect_navigation():
            p.click('.themeform button[value="system"]')
        self.assertIsNone(p.get_attribute("html", "data-theme"))


class _HomeLive(BrowserCase):
    live_enabled = True

    def live_requests(self):
        """Load Home with /api/live intercepted; return the query strings it asked with."""
        seen = []

        def answer(route):
            seen.append(route.request.url.split("/api/live", 1)[1])
            route.fulfill(json={"enabled": True, "snapshot": None, "error": None, "age_seconds": None})
        self.page.route("**/api/live*", answer)
        self.open("/")
        self.page.wait_for_timeout(400)
        return seen


class TestHomeReadsLiveOnceTheWeekHasStarted(_HomeLive):
    """UI-F1: on Monday 2026-09-28 Home led with 76.7% pre-game while the game stood at
    164.0-188.8 -- live data loaded only on Refresh or with auto-refresh ticked."""
    plant = staticmethod(kickoffs("2026-01-01T17:00:00Z"))

    def test_home_asks_for_fresh_scores_on_load(self):
        self.assertIn("?refresh=1", self.live_requests())


class TestHomeLeavesLiveAloneBeforeKickoff(_HomeLive):
    plant = staticmethod(kickoffs("2099-01-01T17:00:00Z"))

    def test_nothing_is_fetched_before_the_first_kickoff(self):
        self.assertEqual(self.live_requests(), [])


def live_payload(decided, p):
    """A real snapshot (the live tests' tree, every game over), then its certainty set by
    hand: the pages must not care how the number was reached, only whether it is settled."""
    from tests.test_webui_live import plant as plant_live
    from tests.test_webui_twelfth import final_fetch
    from webui.live import snapshot
    with tempfile.TemporaryDirectory() as td:
        plant_live(td)
        snap = snapshot(Root(td), 3, MY_TEAM, "L", final_fetch)
    snap.update(decided=decided, p_win=p, p_win_wide=p)
    return {"enabled": True, "snapshot": snap, "error": None, "age_seconds": 5, "min_interval": 45,
            "history": [], "updates": []}


class TestNoCertaintyUntilItIsDecided(BrowserCase):
    """UI-M8, on both pages that show the live number."""
    live_enabled = True
    plant = staticmethod(kickoffs("2026-01-01T17:00:00Z"))

    def shown_with(self, payload, path, selector):
        self.page.route("**/api/live*", lambda route: route.fulfill(json=payload))
        self.open(path)
        self.page.wait_for_function(f"document.querySelector({selector!r}) && /%/.test(document.querySelector({selector!r}).textContent)")
        self.page.wait_for_timeout(900)                      # the hero counts up to its value
        return self.page.inner_text(selector)

    def test_home_says_over_99_9_while_players_are_left(self):
        self.assertEqual(self.shown_with(live_payload(False, 1.0), "/", "#pw-val").strip(), ">99.9%")
        self.page.unroute("**/api/live*")
        self.assertEqual(self.shown_with(live_payload(False, 0.0), "/", "#pw-val").strip(), "<0.1%")

    def test_a_fast_live_answer_is_not_overwritten_by_the_count_up(self):
        """Found while writing the test above: the hero's count-up animates the pre-game
        number for 400 ms, and a live answer arriving inside that window was written and
        then painted over, frame by frame, back to the pre-game number."""
        self.assertEqual(self.shown_with(live_payload(True, 0.9), "/", "#pw-val").strip(), "90.0%")

    def test_home_shows_the_certainty_once_decided(self):
        self.assertEqual(self.shown_with(live_payload(True, 1.0), "/", "#pw-val").strip(), "100%")

    def test_game_day_says_over_99_while_players_are_left(self):
        self.assertIn(">99%", self.shown_with(live_payload(False, 0.998), "/gameday", ".mid b"))
        self.page.unroute("**/api/live*")
        self.assertIn("100%", self.shown_with(live_payload(True, 1.0), "/gameday", ".mid b"))


if __name__ == "__main__":
    unittest.main()

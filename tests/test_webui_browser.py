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


def _plant_players(root):
    from tests.test_webui_live import plant as plant_live
    plant_live(root)


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


class TestLivePanelWithoutLiveScores(BrowserCase):
    """Found 2026-09-28: with live scores not connected, the page script wrote the developer
    message "set SLEEPER_LEAGUE_ID for this server" into the simple view -- invisible to the
    vocabulary guard, which reads the server's HTML, not what the script writes."""

    def test_the_simple_view_is_told_in_plain_words(self):
        self.page.route("**/api/live*", lambda route: route.fulfill(json={"enabled": False, "snapshot": None, "error": None, "age_seconds": None}))
        p = self.open("/", "simple")
        p.evaluate("document.getElementById('live-refresh') && (document.getElementById('live-refresh').disabled = false)")
        p.evaluate("fetch('/api/live').then(r => r.json())")
        p.wait_for_timeout(300)
        self.assertNotIn("SLEEPER_LEAGUE_ID", p.inner_text("#live-meta"))


class TestSortableTables(BrowserCase):
    """League's standings said "click a column to sort", its headers had a pointer cursor and
    sort-arrow CSS -- and no script had ever sorted anything (the markup and styles arrived in
    c329cca; the script never did). Found 2026-09-28 while building the Players page."""

    def column(self, table, i):
        return self.page.eval_on_selector(table, f"t => Array.from(t.tBodies[0].rows).map(r => r.cells[{i}].getAttribute('data-sort'))")

    def test_clicking_a_numeric_header_sorts_and_clicking_again_reverses(self):
        p = self.open("/league", "simple")
        table = "section:has(h2:has-text('Standings')) table"
        heads = p.eval_on_selector_all(table + " thead th", "ths => ths.map(t => t.getAttribute('data-key'))")
        i = heads.index("p")                                               # Points
        p.click(f"{table} thead th[data-key='p']")
        first = [float(x) for x in self.column(table, i)]
        self.assertEqual(first, sorted(first, reverse=True), "a number sorts high to low first")
        p.click(f"{table} thead th[data-key='p']")
        again = [float(x) for x in self.column(table, i)]
        self.assertEqual(again, sorted(again))
        self.assertEqual(p.get_attribute(f"{table} thead th[data-key='p']", "aria-sort"), "ascending")


class TestKeyboardAndExport(BrowserCase):
    """UI-V7 and R4: a sortable header is a keyboard control, a player link's card opens on
    focus as well as hover, and any sortable table downloads as CSV."""

    def test_a_sort_header_is_reached_by_tab_and_sorted_by_enter(self):
        p = self.open("/league", "simple")
        th = "section:has(h2:has-text('Standings')) table thead th[data-key='p']"
        self.assertEqual(p.get_attribute(th, "tabindex"), "0")
        p.focus(th)
        p.keyboard.press("Enter")
        self.assertEqual(p.get_attribute(th, "aria-sort"), "descending")

    def test_a_sortable_table_downloads_as_csv(self):
        import csv
        import io
        p = self.open("/league", "simple")
        btn = "section:has(h2:has-text('Standings')) button.csv"
        with p.expect_download() as dl:
            p.click(btn)
        with open(dl.value.path(), encoding="utf-8") as fh:
            rows = list(csv.reader(io.StringIO(fh.read().lstrip("﻿"))))
        self.assertIn("Points", rows[0])
        self.assertEqual(len(rows), 9, "the header and eight teams")


class TestPlayerCardByKeyboard(BrowserCase):
    """UI-V7: the player card opened only on mouse hover; a keyboard user never saw it."""
    plant = staticmethod(_plant_players)

    def test_the_player_card_opens_on_keyboard_focus_and_escape_closes_it(self):
        p = self.open("/players", "simple")
        p.focus("#ptable a.pl[data-player]")
        p.wait_for_selector("#pcard", state="visible")
        p.keyboard.press("Escape")
        p.wait_for_selector("#pcard", state="hidden")


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
        self.assertEqual(self.shown_with(live_payload(False, 0.9), "/", "#pw-val").strip(), "90.0%")

    def test_the_lineup_callout_prices_the_swap_in_chance_to_win(self):
        """UI-L1: the callout says what the swap is worth in chance to win, not only points."""
        pay = live_payload(False, 0.6)
        pay["snapshot"]["plan"] = {"start": [{"name": "Jordan Love", "pos": "QB", "slot": "QB", "expected": 18.0, "flag": "", "locked": False}],
                                   "bench": [{"name": "Patrick Mahomes", "pos": "QB", "slot": None, "expected": 14.8, "flag": "", "locked": False}],
                                   "delta": 3.2, "stamp": "20260926T120000Z", "total": 186.4, "actionable": True, "locked": 0,
                                   "stakes": {"d_h2h": 0.0247, "d_median": 0.0301, "p_h2h": 0.6946, "p_median": 0.7578,
                                              "se": 0.0065, "stamp": "20260926T120000Z"}}
        self.page.route("**/api/live*", lambda route: route.fulfill(json=pay))
        self.open("/")
        self.page.wait_for_selector(".plan")
        text = self.page.inner_text(".plan")
        self.assertIn("+2.5 points of chance to win the game", text)
        self.assertIn("+3.0 to beat the median", text)

    def test_a_pinned_bar_keeps_the_score_in_view(self):
        """UI-M2: a slim bar -- both scores, the chance now, starters still to play -- stays
        under the header while the long starter tables scroll."""
        pay = live_payload(False, 0.62)
        self.page.route("**/api/live*", lambda route: route.fulfill(json=pay))
        self.open("/")
        self.page.wait_for_selector("#livebar")
        bar = self.page.inner_text("#livebar")
        self.assertIn("31.5", bar)
        self.assertIn("62.0%", bar)
        self.assertIn("to play", bar)
        self.assertEqual(self.page.eval_on_selector("#livebar", "e => getComputedStyle(e).position"), "sticky")

    def test_old_live_data_is_flagged_during_the_games(self):
        """UI-M7: live data more than five minutes old during the games says so."""
        pay = dict(live_payload(False, 0.62), age_seconds=900)
        self.page.route("**/api/live*", lambda route: route.fulfill(json=pay))
        self.open("/")
        self.page.wait_for_function("/15 min/.test(document.getElementById('live-meta').textContent)")
        self.assertIn("older than five minutes", self.page.inner_text("#live-meta"))

    def test_home_states_the_result_once_decided(self):
        """UI-M1 supersedes UI-M8's "100%" here: a decided game shows its result, not a
        probability (disclosed change of this test's expectation)."""
        pay = live_payload(True, 1.0)
        self.page.route("**/api/live*", lambda route: route.fulfill(json=pay))
        self.open("/")
        self.page.wait_for_function("/final/.test(document.getElementById('pw-lab').textContent)")
        self.assertEqual(self.page.inner_text("#pw-val").strip(), "Won")
        self.assertNotIn("%", self.page.inner_text("#pw-val"))
        self.assertIn("31.5", self.page.inner_text("#pw-lab"))

    def test_game_day_says_over_99_while_players_are_left(self):
        self.assertIn(">99%", self.shown_with(live_payload(False, 0.998), "/gameday", ".mid b"))
        self.page.unroute("**/api/live*")
        self.assertIn("100%", self.shown_with(live_payload(True, 1.0), "/gameday", ".mid b"))


class TestMatchupsLive(BrowserCase):
    """UI-A3 live: once the week has kicked off, the Matchups page reads /api/live and draws
    every game's score, projection and chance over its card."""
    live_enabled = True
    plant = staticmethod(kickoffs("2026-01-01T17:00:00Z"))

    def test_a_started_week_shows_each_games_live_score(self):
        from tests.test_webui_routes import TEAMS
        pay = live_payload(False, 0.9)
        a, b = TEAMS[0], TEAMS[4]
        pay["snapshot"]["league"] = [{"a": a, "b": b, "a_banked": 101.5, "b_banked": 88.25, "a_proj": 150.0,
                                      "b_proj": 120.0, "a_to_play": 3, "b_to_play": 5, "a_starters": 13,
                                      "b_starters": 13, "p_a": 0.83, "decided": False}]
        self.page.route("**/api/live*", lambda route: route.fulfill(json=pay))
        self.open("/matchups/week-3", "simple")
        card = self.page.locator(f'.mg[data-a="{a}"]').first
        card.locator(".live-a").wait_for()
        self.assertIn("101.5", card.inner_text())
        self.assertIn("83%", card.inner_text())
        self.assertIn("3 to play", card.inner_text())


class TestInstantCompare(BrowserCase):
    """UI-P4: the compare page answers at once, and redraws as names change."""
    plant = staticmethod(_plant_players)

    def test_a_third_name_redraws_the_estimate_for_three(self):
        p = self.open("/tools/compare_players?a=Jalen+Coker&b=Xavier+Worthy&week=3", "simple")
        self.assertIn("outscores", p.inner_text("#instant-body"))
        p.fill("#inst_c", "Jordan Love")
        p.wait_for_function("document.querySelectorAll('#instant-body table.inst tbody tr').length === 3")
        self.assertIn("Who scores the most", p.inner_text("#instant-body"))


class TestInstantCompareSuggestions(BrowserCase):
    """The owner, 2026-09-28: the third and fourth boxes gave no dropdown, so a name had to be
    typed exactly. They are player fields like A and B now -- and picking from any dropdown
    redraws the estimate (it set the value without an event, so only typing ever did). The
    tests wait for the list to answer what was typed, as a person does; Enter on a list still
    answering an earlier query is ignored rather than picking the wrong player."""
    plant = staticmethod(_plant_players)

    def test_a_partial_name_offers_players_and_picking_one_redraws(self):
        p = self.open("/tools/compare_players?a=Jalen+Coker&b=Xavier+Worthy&week=3", "simple")
        p.click("#inst_c")
        p.type("#inst_c", "Jord")
        p.wait_for_function("(document.querySelector('#s_inst_c div') || {}).textContent && document.querySelector('#s_inst_c div').textContent.indexOf('Jordan Love') === 0")
        p.keyboard.press("Enter")
        self.assertEqual(p.input_value("#inst_c"), "Jordan Love")
        p.wait_for_function("document.querySelectorAll('#instant-body table.inst tbody tr').length === 3")

    def test_picking_player_b_from_the_list_redraws_too(self):
        p = self.open("/tools/compare_players?a=Jalen+Coker&week=3", "simple")
        p.click("#f_b")
        p.type("#f_b", "Xav")
        p.wait_for_function("(document.querySelector('#s_b div') || {}).textContent && document.querySelector('#s_b div').textContent.indexOf('Xavier Worthy') === 0")
        p.keyboard.press("Enter")
        p.wait_for_function("document.querySelectorAll('#instant-body table.inst tbody tr').length === 2")


class TestThreePaneHome(BrowserCase):
    """UI-V1 (owner's choice, 2026-09-28): at 4K widths Home is three panes -- standings and
    the week's games on the left, the matchup and live panel in the centre, the season and
    the watch list on the right. Below the breakpoint nothing moves: the same markup, the
    layout it has always had."""

    PARTS = {"hero": ".hero", "standings": "section:has(h2:has-text('Standings'))",
             "watch": "section:has(h2:has-text('Watch list'))", "season": ".card.outlook"}

    def boxes(self, width):
        self.ctx.close()
        self.ctx = self.browser.new_context(viewport={"width": width, "height": 1400}, reduced_motion="reduce")
        self.page = self.ctx.new_page()
        self.open("/", "simple")
        return {k: self.page.locator(sel).first.bounding_box() for k, sel in self.PARTS.items()}

    def test_at_2560_the_three_panes_sit_side_by_side(self):
        b = self.boxes(2560)
        self.assertLess(b["standings"]["x"] + b["standings"]["width"], b["hero"]["x"] + 1, "standings left of the matchup")
        self.assertLess(b["hero"]["x"] + b["hero"]["width"], b["season"]["x"] + 1, "season right of the matchup")
        self.assertAlmostEqual(b["watch"]["x"], b["season"]["x"], delta=2, msg="watch list under the season, same pane")
        for k in ("standings", "season"):
            self.assertLess(abs(b[k]["y"] - b["hero"]["y"]), 4, f"{k} starts level with the matchup")
        self.assertGreater(b["hero"]["width"], 800, "the centre pane is the widest")

    def test_the_left_pane_stays_in_view_while_the_centre_scrolls(self):
        self.boxes(2560)
        self.page.set_viewport_size({"width": 2560, "height": 700})
        self.page.evaluate("window.scrollTo(0, 600)")
        self.page.wait_for_timeout(100)
        top = self.page.locator(self.PARTS["standings"]).first.bounding_box()["y"]
        self.assertGreaterEqual(top, 0, "the standings pane must not scroll off with the page")

    def test_below_the_breakpoint_the_layout_is_unchanged(self):
        for width in (1280, 1920):
            with self.subTest(width=width):
                b = self.boxes(width)
                self.assertGreater(b["season"]["y"], b["hero"]["y"] + b["hero"]["height"] - 1, "season below the matchup")
                self.assertGreater(b["standings"]["y"], b["season"]["y"], "standings below the season")
                self.assertAlmostEqual(b["standings"]["y"], b["watch"]["y"], delta=2, msg="standings and watch list share a row")

    def test_no_horizontal_scroll_at_any_width(self):
        for width in (520, 1280, 2560, 3840):
            with self.subTest(width=width):
                self.boxes(width)
                over = self.page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                self.assertLessEqual(over, 0)


if __name__ == "__main__":
    unittest.main()

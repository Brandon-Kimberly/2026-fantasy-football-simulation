"""
tests.test_webui_tenth -- the home page pass (docs/WEB_UI.md W13).

The live number leads and the pre-game one becomes a footnote; the two matchup bars are
drawn to ONE scale, so their lengths compare; every starter carries his pre-game
projection and how far he is above or below it; and a scoring feed says what just
happened -- "Jalen Coker +3.4 (+0.5 catch, +2.9 29 rec yds)" -- built by diffing
successive live reads in memory. The season card carries the whole outlook instead of
one ring in a mostly empty box.

The feed is the only new data: `live.stat_parts` scores the change in a player's stat
line with the league's own scoring weights, and `live.diff_updates` turns two snapshots
into entries. Both are pure. Nothing here reaches the network or writes under data/:
the board's updates live in memory beside its history, and the test digests the tree.
"""
from tests.webui_served import inline_assets  # UI-E2: the page as served
import tempfile
import unittest

from webui import glance, live

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_home import enrich
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_live import MY_RID, OPP_RID, plant as plant_live
    from tests.test_webui_routes import build_tree
    from webui.paths import Root
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

SCORING = {"rec": 0.5, "rec_yd": 0.1, "rec_td": 6.0, "rush_yd": 0.1, "pass_yd": 0.04,
           "pass_td": 4.0, "fum_lost": -2.0, "idp_tkl_solo": 1.5, "gp": 0.0}

# two reads of the same week: Coker catches two passes for 29 yards, Mahomes throws
STATE = {"n": 0}
STATS = [
    {"100": {"pass_yd": 200, "pass_td": 1}, "101": {"rec": 1, "rec_yd": 12}},
    {"100": {"pass_yd": 275, "pass_td": 2}, "101": {"rec": 2, "rec_yd": 41}},
]
POINTS = [
    ({"100": 12.0, "101": 1.7}, 13.7),
    ({"100": 19.0, "101": 5.1}, 24.1),
]


def fetch(url):
    """Sleeper and ESPN, faked: the league (for its scoring), the matchups and the week's
    stat lines advance one step each time STATE['n'] is bumped."""
    i = STATE["n"]
    if url.endswith("/league/L"):
        return {"scoring_settings": SCORING, "name": "a league"}
    if "/matchups/" in url:
        pp, total = POINTS[i]
        return [{"roster_id": int(MY_RID), "matchup_id": 1, "points": total, "starters": ["100", "101"], "players_points": pp},
                {"roster_id": int(OPP_RID), "matchup_id": 1, "points": 3.0, "starters": ["200", "201"], "players_points": {"200": 3.0}}]
    if "/stats/nfl/regular/2026/3" in url:
        return STATS[i]
    if "scoreboard" in url:
        return {"events": [
            {"competitions": [{"status": {"type": {"state": "in", "completed": False}, "period": 2, "displayClock": "7:30"},
                               "competitors": [{"team": {"abbreviation": "KC"}, "homeAway": "home", "score": "17"},
                                               {"team": {"abbreviation": "CAR"}, "homeAway": "away", "score": "10"}]}]},
            {"competitions": [{"status": {"type": {"state": "pre", "completed": False}, "period": 0, "displayClock": "15:00"},
                               "competitors": [{"team": {"abbreviation": "GB"}, "homeAway": "away", "score": "0"},
                                               {"team": {"abbreviation": "CHI"}, "homeAway": "home", "score": "0"}]}]}]}
    raise AssertionError("unexpected url " + url)


class TestStatText(unittest.TestCase):
    def test_a_stat_change_reads_as_a_person_would_say_it(self):
        self.assertEqual(live.stat_text("rec", 1), "catch")
        self.assertEqual(live.stat_text("rec", 3), "3 catches")
        self.assertEqual(live.stat_text("rec_yd", 29), "29 rec yds")
        self.assertEqual(live.stat_text("rec_yd", 1), "1 rec yds")          # a yardage always carries its number
        self.assertEqual(live.stat_text("rush_td", 1), "rush TD")
        self.assertEqual(live.stat_text("rush_td", 2), "2 rush TDs")
        self.assertEqual(live.stat_text("idp_tkl_solo", 2), "2 solo tackles")
        self.assertEqual(live.stat_text("fum_lost", 1), "fumble lost")
        self.assertEqual(live.stat_text("nonsense_stat", 1), "nonsense stat")

    def test_parts_are_scored_with_the_leagues_weights_biggest_first(self):
        parts = live.stat_parts({"rec": 1, "rec_yd": 12}, {"rec": 2, "rec_yd": 41}, SCORING)
        self.assertEqual([(p["text"], p["pts"]) for p in parts], [("29 rec yds", 2.9), ("catch", 0.5)])
        self.assertEqual(live.stat_parts({}, {"gp": 1}, SCORING), [], "a stat the league does not score is not a part")
        self.assertEqual(live.stat_parts({}, {"rec": 1}, None), [], "no scoring settings, no breakdown")
        self.assertEqual(live.stat_parts(None, None, SCORING), [])
        self.assertEqual(len(live.stat_parts({}, {"rec": 9, "rec_yd": 90, "rec_td": 2, "rush_yd": 30}, SCORING)), 3,
                         "at most three, so a line stays readable")


class TestSeedReport(unittest.TestCase):
    """The season card's one graphic: where I finish, and where the playoff cut falls."""
    REAL = {"Seed 1": 45.34, "Seed 2": 26.19, "Seed 3": 15.5, "Seed 4": 7.05,
            "Seed 5": 3.25, "Seed 6": 1.78, "Seed 7": 0.72, "Seed 8": 0.17}

    def test_the_cut_is_read_off_where_the_seeds_meet_the_playoff_number(self):
        r = glance.seed_report(self.REAL, 94.1)
        self.assertEqual(r["spots"], 4)
        self.assertEqual([x["seed"] for x in r["rows"]], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual([x["cls"] for x in r["rows"]], ["in"] * 4 + ["out"] * 4)
        self.assertEqual([x["cut"] for x in r["rows"]], [False, False, False, True, False, False, False, False])
        self.assertAlmostEqual(r["make"], 94.08, places=2)
        self.assertAlmostEqual(r["miss"], 5.92, places=2)
        self.assertGreater(r["rows"][0]["shade"], r["rows"][3]["shade"], "the ramp fades away from the top seed")
        self.assertGreaterEqual(min(x["shade"] for x in r["rows"]), 0.3, "never so faint it disappears")

    def test_no_cut_is_claimed_when_the_two_numbers_do_not_agree(self):
        r = glance.seed_report(self.REAL, 50.0)
        self.assertEqual(r["spots"], 0)
        self.assertEqual({x["cls"] for x in r["rows"]}, {"na"})
        self.assertFalse(any(x["cut"] for x in r["rows"]))
        self.assertEqual((r["make"], r["miss"]), (0.0, 0.0))

    def test_nothing_to_draw(self):
        self.assertEqual(glance.seed_report({}, 94.1)["rows"], [])
        self.assertEqual(glance.seed_report(None, None)["rows"], [])
        self.assertEqual(glance.seed_report(self.REAL, None)["spots"], 0)


class TestDiff(unittest.TestCase):
    def snap(self, i):
        return {"ok": True, "week": 3, "fetched_at": f"2026-09-27T20:0{i}:00Z",
                "mine": {"rows": [{"pid": "100", "name": "Patrick Mahomes", "pos": "QB", "nfl": "KC", "status": "Q2 7:30",
                                   "scored": POINTS[i][0]["100"]},
                                  {"pid": "101", "name": "Jalen Coker", "pos": "WR", "nfl": "CAR", "status": "Q2 7:30",
                                   "scored": POINTS[i][0]["101"]}]},
                "theirs": {"rows": [{"pid": "200", "name": "Xavier Worthy", "pos": "WR", "nfl": "KC", "status": "Q2 7:30", "scored": 3.0}]},
                "stats": STATS[i]}

    def test_two_reads_become_entries_newest_and_biggest_first(self):
        ups = live.diff_updates(self.snap(0), self.snap(1), SCORING)
        self.assertEqual([u["name"] for u in ups], ["Patrick Mahomes", "Jalen Coker"])
        coker = ups[1]
        self.assertEqual(coker["delta"], 3.4)
        self.assertEqual(coker["total"], 5.1)
        self.assertEqual(coker["side"], "mine")
        self.assertEqual(coker["at"], "2026-09-27T20:01:00Z")
        self.assertEqual([(p["text"], p["pts"]) for p in coker["parts"]], [("29 rec yds", 2.9), ("catch", 0.5)])

    def test_nothing_from_a_first_read_an_unchanged_read_or_a_failed_one(self):
        self.assertEqual(live.diff_updates(None, self.snap(1), SCORING), [])
        self.assertEqual(live.diff_updates(self.snap(1), self.snap(1), SCORING), [])
        self.assertEqual(live.diff_updates(self.snap(0), {"ok": False}, SCORING), [])

    def test_a_player_the_earlier_read_did_not_have_is_not_an_update(self):
        before = self.snap(0)
        before["mine"]["rows"] = before["mine"]["rows"][:1]
        self.assertEqual([u["name"] for u in live.diff_updates(before, self.snap(1), SCORING)], ["Patrick Mahomes"])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestBoard(unittest.TestCase):
    def setUp(self):
        STATE["n"] = 0
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        plant_live(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_rows_carry_the_player_id_and_the_snapshot_its_stat_lines(self):
        s = live.snapshot(self.root, 3, MY_TEAM, "L", fetch)
        self.assertEqual([r["pid"] for r in s["mine"]["rows"]], ["100", "101"])
        self.assertEqual(s["stats"]["101"], {"rec": 1, "rec_yd": 12})
        self.assertNotIn("300", s["stats"], "only the players in this matchup")

    def test_a_missing_stats_source_is_a_caveat_not_a_failure(self):
        def no_stats(url):
            if "/stats/" in url:
                raise RuntimeError("nope")
            return fetch(url)
        s = live.snapshot(self.root, 3, MY_TEAM, "L", no_stats)
        self.assertTrue(s["ok"])
        self.assertEqual(s["stats"], {})

    def test_the_board_accumulates_updates_caps_them_and_writes_nothing(self):
        before = self.root.tree_digest()
        t = [0.0]
        b = LiveBoard(self.root, MY_TEAM, league_id="L", fetch=fetch, clock=lambda: t[0], max_updates=3)
        b.get(3)
        self.assertEqual(b.peek()["updates"], [], "the first read has nothing to compare against")
        STATE["n"] = 1
        t[0] += 60
        b.get(3, refresh=True)
        ups = b.peek()["updates"]
        self.assertEqual([u["name"] for u in ups], ["Patrick Mahomes", "Jalen Coker"])
        self.assertEqual(ups[1]["delta"], 3.4)
        self.assertEqual([(p["text"], p["pts"]) for p in ups[1]["parts"]], [("29 rec yds", 2.9), ("catch", 0.5)])
        STATE["n"] = 0                                   # the scores move back: two more entries, capped at three
        t[0] += 60
        b.get(3, refresh=True)
        self.assertEqual(len(b.peek()["updates"]), 3)
        self.assertEqual(self.root.tree_digest(), before, "a refresh writes nothing under data/")

    def test_the_league_is_read_once_for_its_scoring_and_a_new_week_starts_a_new_feed(self):
        calls = []

        def counting(url):
            calls.append(url)
            return fetch(url)
        b = LiveBoard(self.root, MY_TEAM, league_id="L", fetch=counting, clock=lambda: 0.0)
        b.get(3)
        STATE["n"] = 1
        b._at = -10_000                                   # past the minimum interval
        b.get(3, refresh=True)
        self.assertEqual(len([u for u in calls if u.endswith("/league/L")]), 1)
        self.assertTrue(b.peek()["updates"])
        b._at = -10_000
        b.get(4, refresh=True)
        self.assertEqual(b.peek()["updates"], [], "a new week starts a new day")

    def test_a_disabled_board_has_an_empty_feed(self):
        self.assertEqual(LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None).peek()["updates"], [])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestHomePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        build_tree(cls.td.name)
        enrich(cls.td.name)
        plant_live(cls.td.name)
        cls.root = Root(cls.td.name)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def get(self, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get("/")
        self.assertEqual(r.status_code, 200)
        return inline_assets(r.get_data(as_text=True))

    def parts(self, mode="dev"):
        """(the shared stylesheet, this page's own stylesheet, its markup, its script).
        The layout's own script is the last one on the page, so the page's is found by
        what it holds rather than by position."""
        body = self.get(mode)
        base_css, rest = body.split("</style>", 1)
        home_css, markup = rest.split("</style>", 1)
        script = next(s for s in body.split("<script>") if "getElementById('live-body')" in s)
        return base_css, home_css, markup, script

    def test_the_live_number_leads_and_the_pre_game_one_is_a_footnote(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                _base, _home, head, script = self.parts(mode)
                self.assertIn('id="pwin"', head)
                self.assertIn('id="pw-val"', head)
                self.assertIn('id="pw-lab"', head)
                self.assertIn('id="pw-note"', head)
                self.assertIn('data-pre="70.0"', head)                     # the fixture's pre-game number
                self.assertIn("to win, now", script)                       # the label the live read swaps in
                self.assertIn("pre-game", script)

    def test_the_two_matchup_bars_share_one_scale(self):
        script = self.parts()[3]
        self.assertIn("function side(st, cls, name, scale)", script)
        self.assertIn("Math.max(me.projected, th.projected", script)
        self.assertNotIn("Math.max(1, st.projected, 1)", script)

    def test_every_starter_shows_his_projection_and_how_far_off_it_he_is(self):
        script = self.parts()[3]
        self.assertIn("r.expected * (1 - r.frac)", script)                 # what he should have by now
        self.assertIn(">Proj<", script)
        self.assertIn("class=\"d pos\"", script)
        self.assertIn("class=\"d neg\"", script)

    def test_the_scoring_feed_is_there_and_collapsed(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                _base, home_css, _markup, script = self.parts(mode)
                self.assertIn("function feed(", script)
                self.assertIn("Scoring updates", script)
                self.assertIn('<details class="feed"', script)
                self.assertIn(".feed", home_css)

    def test_the_season_card_carries_the_whole_outlook(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                head = self.parts(mode)[2]
                self.assertIn('class="season"', head)
                self.assertIn("title odds", head)
                self.assertIn("expected wins", head)
                self.assertIn("to clinch", head)
                self.assertIn('class="ring"', head)
                self.assertNotIn('class="stat a"', head, "the half-empty ring card is gone")

    def test_the_season_card_shows_where_i_finish_and_no_broken_bars(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                base_css, home_css, markup, _script = self.parts(mode)
                card = markup.split('class="card outlook"', 1)[1].split('class="card chartc"', 1)[0]
                self.assertIn('class="sbar"', card)
                self.assertIn("where I finish", card)
                self.assertNotIn("mini", card, "the season card draws no inline bar components")
                self.assertIn(".sbar i", home_css)
                self.assertIn(".mini { display: block;", base_css, "the bar component is a block wherever it is used")

    def test_the_page_is_laid_out_for_a_wide_screen(self):
        base_css, home_css, _markup, _script = self.parts()
        self.assertIn("@media (min-width: 1500px)", base_css)               # the column widens on a big monitor
        self.assertIn(".starters { display: grid;", home_css)               # both rosters side by side


if __name__ == "__main__":
    unittest.main()

"""
tests.test_webui_twelfth -- the web UI roadmap's first wave (docs/WEB_UI_ROADMAP.md),
server side. The browser half lives in tests.test_webui_browser.

UI-F4: "my record 2-2 · head-to-head plus the median game" -- in a median league those are two
contests, and a combined record hides which one a team is winning. The split comes from the
weekly actuals, and is shown only when it accounts for exactly the wins the league's
standings report (F84: the standings are the authority; the actuals can lag a week).

UI-F6: the player card labelled the SEASON baseline "projection" -- the week-versus-season
trap. It now says "season mean", and adds this week's price where a lineup or matchup
record priced the player for the current week (webui.live.expectations' precedence).

UI-E4: every page's odds come from one pair of helpers. Home read the export of the SYNC
week while League, Forecasts and the odds race read the NEWEST export -- so between
Tuesday's sync and that week's simulation, Home showed no odds while League showed last
week's. `odds_now` is the newest export at or before the sync week, and says how far behind.

UI-M8: the live chance to win is a Normal approximation; rounded, it reaches 0.0% or 100.0%
long before a game is settled (Sleeper rebuilt its own for exactly this). The snapshot says
whether the result is DECIDED -- nobody left to play on either side -- and until it is, the
pages say "under 0.1%" / "over 99.9%" rather than a certainty.

UI-V6 / UI-T2: one grammar for uncertainty. "±" means one standard error, everywhere, and
prints through one filter; a score's spread is labelled sd. A paired result is stated in
standard errors: under 2 "no measurable change", 2 to 4 "modest", above 4 "clear" -- the
same line the Decisions "measurably moved" filter draws.

UI-O3: what each result cost or bought -- every team's change in playoff and title odds from
one forecast to the next, beside the week's results that sit between them. Never a zero
where there is no earlier forecast to compare with.

UI-Q2: beside a past result, what the model said at the time -- from the same quoted row the
Accuracy page scores (the newest committed forecast logged before the week's first
kickoff), through one shared helper, so the two can never disagree.

UI-F2: the System page said "30 sources fell back" when one source had failed and the
sync had raised thirty warnings. Sources and warnings are counted apart.

UI-F1: whether the week's games have started is the sync's kickoffs against the clock,
decided once on the server, so Home can read live scores on load instead of leading with
a pre-game number after the games began.
"""
import datetime as _dt
import json
import os
import tempfile
import unittest

from webui.glance import (freshness_report, home_report, kickoff_report, odds_at, odds_moves, odds_now, odds_race,
                          records, sync_phrase)
from fantasy_sim.config import MY_TEAM
from webui import render
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def _plant_kickoffs(root, week, times):
    os.makedirs(os.path.join(root, "data", "current"), exist_ok=True)
    with open(os.path.join(root, "data", "current", "nfl_schedule.json"), "w", encoding="utf-8") as fh:
        json.dump({"_meta": {"kickoffs": {str(week): times}}}, fh)


def _at(iso):
    return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


class TestStarted(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        _plant_kickoffs(self.td.name, 3, ["2026-09-25T00:15Z", "2026-09-27T17:00Z", "2026-09-29T00:15Z"])
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_not_started_before_the_first_kickoff(self):
        self.assertFalse(kickoff_report(self.root, 3, now=_at("2026-09-24T12:00:00Z"))["started"])

    def test_started_between_kickoffs(self):
        self.assertTrue(kickoff_report(self.root, 3, now=_at("2026-09-27T20:00:00Z"))["started"])

    def test_started_after_the_last(self):
        k = kickoff_report(self.root, 3, now=_at("2026-09-30T12:00:00Z"))
        self.assertTrue(k["started"])
        self.assertTrue(k["done"])

    def test_no_kickoffs_synced_is_not_started(self):
        self.assertFalse(kickoff_report(self.root, 4)["started"])


class TestSyncPhrase(unittest.TestCase):
    def test_a_failed_source_and_many_warnings_are_two_numbers(self):
        self.assertEqual(sync_phrase(1, 0, 30), "1 source failed, and the sync raised 30 warnings")

    def test_fallbacks_and_failures_together(self):
        self.assertEqual(sync_phrase(1, 2, 0), "1 source failed and 2 fell back to an older copy")
        self.assertEqual(sync_phrase(0, 2, 1), "2 sources fell back to an older copy, and the sync raised 1 warning")

    def test_every_source_through_with_warnings(self):
        self.assertEqual(sync_phrase(0, 0, 3), "every source came through, and the sync raised 3 warnings")

    def test_nothing_to_say(self):
        self.assertEqual(sync_phrase(0, 0, 0), "every source came through")


def _manifest(root, sources, warnings):
    with open(os.path.join(root, "data", "current", "sync_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"started_at": "2026-09-25T17:12:06Z", "finished_at": "2026-09-25T17:13:20Z", "current_week": 3,
                   "season": "2026", "ok": True, "sources": sources,
                   "degraded": [f"WARNING | name collision {i}" for i in range(warnings)], "files": {}}, fh)


SOURCES = dict({f"src_{i}": {"ok": True, "rows": 10} for i in range(11)},
               vegas_totals={"ok": False, "rows": 0})


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestSystemCountsSourcesAndWarningsApart(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        _manifest(self.td.name, SOURCES, 30)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def get(self, path):
        st = Settings(self.root)
        st.set_mode("dev")
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)

    def test_the_report_counts_warnings(self):
        fr = freshness_report(self.root)
        self.assertEqual((fr["n_ok"], fr["n_bad"], fr["n_fell"], fr["n_warn"]), (11, 1, 0, 30))

    def test_no_page_calls_thirty_warnings_thirty_sources(self):
        for path in ("/system", "/current", "/tools/optimize_lineup"):
            with self.subTest(path=path):
                body = self.get(path)
                self.assertNotIn("30 sources", body)
                self.assertNotIn("30 source ", body)
                self.assertNotIn("What fell back", body)
                self.assertNotIn("Sources that fell back", body)

    def test_the_system_lede_says_what_happened(self):
        self.assertIn("1 source failed, and the sync raised 30 warnings", self.get("/system"))


def _actuals(root, weeks, standings=None):
    """weeks: [{team: (h2h_win, median_win)}]; standings: {team: combined wins}."""
    d = os.path.join(root, "data", "current")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "weekly_actuals.json"), "w", encoding="utf-8") as fh:
        json.dump({f"week_{i + 1}": {"median_cutoff": 140.0, "team_results": {
            t: {"points_scored": 150.0, "h2h_win": h, "median_win": m} for t, (h, m) in wk.items()}}
            for i, wk in enumerate(weeks)}, fh)
    if standings is not None:
        with open(os.path.join(d, "league_standings.json"), "w", encoding="utf-8") as fh:
            json.dump({t: {"h2h_wins": w, "points_scored": 300.0, "remaining_faab": 90} for t, w in standings.items()}, fh)


A, B = "Quantum Ferrets", "Neon Walruses"


class TestRecords(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_head_to_head_median_and_combined_are_counted_apart(self):
        _actuals(self.td.name, [{A: (1.0, 0), B: (0.0, 1)}, {A: (1.0, 0), B: (0.0, 1)}, {A: (0.0, 1), B: (1.0, 0)}],
                 {A: 3, B: 3})
        r = records(self.root)
        self.assertEqual((r[A]["h2h"]["text"], r[A]["median"]["text"], r[A]["combined"]["text"]), ("2–1", "1–2", "3–3"))
        self.assertEqual((r[B]["h2h"]["text"], r[B]["median"]["text"]), ("1–2", "2–1"))
        self.assertTrue(r[A]["agrees"])

    def test_a_tie_is_a_tie(self):
        _actuals(self.td.name, [{A: (0.5, 1), B: (0.5, 0)}], {A: 1, B: 0})
        r = records(self.root)
        self.assertEqual(r[A]["h2h"]["text"], "0–0–1")
        self.assertEqual(r[A]["combined"]["text"], "1–0–1")

    def test_actuals_that_lag_the_standings_do_not_agree(self):
        _actuals(self.td.name, [{A: (1.0, 1)}], {A: 3})          # the standings already count a later week
        self.assertFalse(records(self.root)[A]["agrees"])

    def test_nothing_played(self):
        self.assertEqual(records(self.root), {})


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestLeagueShowsTheSplit(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
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

    def test_my_record_tile_splits_head_to_head_from_the_median(self):
        _actuals(self.td.name, [{A: (1.0, 0), B: (0.0, 1)}, {A: (1.0, 0), B: (0.0, 1)}], {A: 2, B: 2})
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/league", mode)
                self.assertIn("2–0 head-to-head · 0–2 against the median", body)
                self.assertNotIn("head-to-head plus the median game", body)

    def test_the_standings_carry_both_records(self):
        _actuals(self.td.name, [{A: (1.0, 0), B: (0.0, 1)}, {A: (1.0, 0), B: (0.0, 1)}], {A: 2, B: 2})
        body = self.get("/league")
        self.assertIn(">Head-to-head</th>", body)
        self.assertIn(">Median</th>", body)
        self.assertIn('data-sort="2">2–0</td>', body)

    def test_when_the_actuals_lag_the_tile_says_so_instead_of_splitting_wrongly(self):
        _actuals(self.td.name, [{A: (1.0, 0), B: (0.0, 1)}], {A: 3, B: 1})
        body = self.get("/league")
        self.assertNotIn("1–0 head-to-head", body)
        self.assertIn("combined; the split arrives with the next sync", body)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPlayerCardSeparatesWeekFromSeason(unittest.TestCase):
    def setUp(self):
        from tests.test_webui_live import plant as plant_live
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        plant_live(self.td.name)                 # baselines + a week-3 lineup record pricing one QB at 24.0
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def card(self, name):
        st = Settings(self.root)
        st.set_mode("dev")
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get("/api/player", query_string={"name": name})
        self.assertEqual(r.status_code, 200, name)
        return r.get_json()

    def test_a_player_priced_this_week_carries_both_numbers(self):
        c = self.card("Patrick Mahomes")
        self.assertEqual(c["mean"], 20.0, "the season baseline, unchanged")
        self.assertEqual((c["week"], c["week_mean"], c["week_source"]), (3, 24.0, "lineup record"))
        self.assertEqual(c["week_stamp"], "20260924T165331Z")

    def test_a_player_priced_only_by_the_baseline_has_no_week_number(self):
        c = self.card("Jalen Coker")
        self.assertEqual(c["mean"], 10.0)
        self.assertIsNone(c["week_mean"])

    def test_the_card_script_never_calls_the_baseline_a_projection(self):
        with open("webui/templates/base.html", encoding="utf-8") as fh:
            js = fh.read().split("function pcShow", 1)[1].split("function ", 1)[0]
        self.assertNotIn("<span>projection</span>", js)
        self.assertIn("season mean", js)
        self.assertIn("week_mean", js)


def _sync_week(root, week):
    p = os.path.join(root, "data", "current", "sync_manifest.json")
    with open(p, encoding="utf-8") as fh:
        m = json.load(fh)
    m["current_week"] = week
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(m, fh)
    with open(os.path.join(root, "data", "current", "league_state.json"), "w", encoding="utf-8") as fh:
        json.dump({"current_week": week}, fh)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestOneSourceForTheOdds(unittest.TestCase):
    """The fixture's week-3 export prices Quantum Ferrets at 93.5% to make the playoffs and
    35.8% for the title."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_odds_at_reads_one_export(self):
        o = odds_at(self.root, 3)
        self.assertEqual((o[MY_TEAM]["playoff"], o[MY_TEAM]["champ"], o[MY_TEAM]["playoff_se"]), (93.5, 35.8, 0.25))
        self.assertEqual(odds_at(self.root, 9), {})

    def test_odds_now_is_the_newest_export_and_says_when_it_is_behind(self):
        self.assertEqual((odds_now(self.root)["week"], odds_now(self.root)["behind"]), (3, 0))
        _sync_week(self.td.name, 4)
        now = odds_now(self.root)
        self.assertEqual((now["week"], now["behind"]), (3, 1))
        self.assertEqual(now["teams"][MY_TEAM]["playoff"], 93.5)

    def test_every_page_shows_the_same_odds_after_the_sync_moves_on(self):
        from webui.app import current_report
        _sync_week(self.td.name, 4)                   # the week-4 simulation has not run yet
        home = home_report(self.root, MY_TEAM)
        mine = next(r for r in home["standings"] if r["team"] == MY_TEAM)
        race = next(s for s in odds_race(self.root, MY_TEAM)["playoff"] if s["name"] == MY_TEAM)
        seen = {"home hero": (home["forecast"] or {}).get("playoff_probability_pct"),
                "home standings": mine["playoff"], "home title": home["champ"],
                "league": current_report(self.root)["odds"].get(MY_TEAM), "odds race": race["values"][-1]}
        self.assertEqual(seen, {"home hero": 93.5, "home standings": 93.5, "home title": 35.8,
                                "league": 93.5, "odds race": 93.5})
        self.assertEqual(home["odds_week"], 3)

    def test_home_says_which_forecast_it_is_showing_when_it_is_behind(self):
        _sync_week(self.td.name, 4)
        st = Settings(self.root)
        st.set_mode("simple")
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        body = app.test_client().get("/").get_data(as_text=True)
        self.assertIn("from the week-3 forecast", body)
        self.assertNotIn("from this week's forecast", body)


def final_fetch(url):
    """The live tests' fake Sleeper and scoreboard, with every game over."""
    from tests.test_webui_live import fake_fetch
    out = fake_fetch(url)
    if "scoreboard" in url:
        for ev in out["events"]:
            ev["competitions"][0]["status"] = {"type": {"state": "post", "completed": True}, "period": 4,
                                               "displayClock": "0:00"}
    return out


class TestDecided(unittest.TestCase):
    def setUp(self):
        from tests.test_webui_live import plant as plant_live
        self.td = tempfile.TemporaryDirectory()
        plant_live(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_a_game_with_starters_still_to_play_is_not_decided(self):
        from tests.test_webui_live import fake_fetch
        from webui.live import snapshot
        s = snapshot(self.root, 3, MY_TEAM, "L", fake_fetch)
        self.assertGreater(s["theirs"]["to_play"], 0)
        self.assertFalse(s["decided"])

    def test_nobody_left_on_either_side_is_decided_and_certain(self):
        from webui.live import snapshot
        s = snapshot(self.root, 3, MY_TEAM, "L", final_fetch)
        self.assertEqual((s["mine"]["to_play"], s["theirs"]["to_play"]), (0, 0))
        self.assertTrue(s["decided"])
        self.assertEqual(s["p_win"], 1.0, "31.5 banked against 0.0 with nobody left")


class TestUncertaintyGrammar(unittest.TestCase):
    def test_se_prints_one_way(self):
        self.assertEqual(render.fse(0.25), "± 0.25")
        self.assertEqual(render.fse(0.479), "± 0.48")
        self.assertEqual(render.fse(1.234), "± 1.2")
        self.assertEqual(render.fse(None), "")

    def test_verdict_tiers_at_their_boundaries(self):
        tier = lambda d, se: (render.verdict(d, se) or {}).get("tier")
        self.assertEqual([tier(1.99, 1), tier(2.0, 1), tier(4.0, 1), tier(4.01, 1)], ["none", "modest", "modest", "clear"])
        self.assertEqual(render.verdict(-3.0, 1.0)["text"], "a modest loss")
        self.assertEqual(render.verdict(5.0, 1.0)["text"], "a clear gain")
        self.assertEqual(render.verdict(0.5, 1.0)["text"], "no measurable change")
        self.assertIsNone(render.verdict(3.0, None))
        self.assertIsNone(render.verdict(None, 1.0))

    def test_every_plus_minus_in_a_template_goes_through_the_filter(self):
        import glob
        import re
        for path in glob.glob("webui/templates/*.html") + ["webui/render.py"]:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            if path.endswith("render.py"):                          # the one place allowed to print it
                src = re.sub(r"def fse\(.*?\n\n\n", "", src, flags=re.S)
            with self.subTest(path=path):
                self.assertIsNone(re.search(r"±\s*(\{\{|\" ~|\{f)", src),
                                  "print a standard error with |se (render.fse), never a literal ±")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestPagesSpeakTheGrammar(unittest.TestCase):
    def setUp(self):
        from tests.test_webui_fourth import plant as plant_fourth
        from tests.test_webui_home import enrich
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        enrich(self.td.name)
        plant_fourth(self.td.name)
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

    def test_each_evaluated_move_states_its_verdict_and_the_filter_agrees(self):
        import re
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/decisions", mode)
                judged = []
                for chunk in body.split('<div class="dec')[1:]:
                    moved = re.search(r'data-moved="([01])"', chunk)
                    tier = re.search(r'class="verdict (none|modest|clear)[ "]', chunk)
                    if moved and tier:
                        judged.append((moved.group(1), tier.group(1)))
                self.assertTrue(judged, "the fixture's evaluated moves must carry a verdict")
                for moved, tier in judged:
                    self.assertEqual(moved == "1", tier != "none")


def _export(root, n, playoff, champ):
    """A week-n forecast export: {team: playoff %} and {team: title %}."""
    d = os.path.join(root, "data", "weeks", f"week_{n:02d}")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"live_season_forecast_week_{n}.json"), "w", encoding="utf-8") as fh:
        json.dump({t: {"current_state": {"actual_wins_banked": 1.0}, "forecast": {"playoff_probability_pct": p,
                   "playoff_standard_error": 0.3, "expected_final_wins": 15.0}} for t, p in playoff.items()}, fh)
    with open(os.path.join(d, f"syndicate_comprehensive_matrix_week_{n}.json"), "w", encoding="utf-8") as fh:
        json.dump({"metadata": {"week": n, "simulations": 100},
                   "season_outcomes": [{"Team": t, "Champ_Pct": c} for t, c in champ.items()]}, fh)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestWhatEachResultDid(unittest.TestCase):
    """Week 2's export had Quantum Ferrets at 80.0% / 30.0%; week 3's has 93.5% / 35.8%. In
    between, week 2: they won head-to-head and missed the median."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        _export(self.td.name, 2, {A: 80.0, B: 95.0}, {A: 30.0, B: 40.0})
        _export(self.td.name, 3, {A: 93.5, B: 92.5}, {A: 35.8, B: 35.8})
        _actuals(self.td.name, [{A: (1.0, 1), B: (0.0, 0)}, {A: (1.0, 0), B: (0.0, 1)}], {A: 3, B: 1})
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

    def plant_quote(self, logged_at="2026-09-16T10:00:00Z", canonical=True):
        """Week 2 kicked off 2026-09-17T00:15Z; the model quoted A 62% against B, and A 45% to
        beat the median."""
        with open(os.path.join(self.td.name, "data", "current", "nfl_schedule.json"), "w", encoding="utf-8") as fh:
            json.dump({"_meta": {"kickoffs": {"2": ["2026-09-17T00:15Z"], "3": ["2026-09-24T00:15Z"]}}}, fh)
        os.makedirs(os.path.join(self.td.name, "data", "logs"), exist_ok=True)
        row = {"record_type": "week_predictions", "week": 2, "logged_at": logged_at, "canonical": canonical,
               "matchups": [{"a": A, "b": B, "p_a": 0.62, "p_b": 0.38, "se": 0.007}],
               "median": {A: {"p_beat_median": 0.45}, B: {"p_beat_median": 0.55}}}
        with open(os.path.join(self.td.name, "data", "logs", "predictions_2026.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def test_each_result_carries_what_the_model_said_before_kickoff(self):
        from webui import accuracy
        self.plant_quote()
        root = Root(self.td.name)
        self.assertEqual(accuracy.quoted_chances(root, 2, A), {"h2h": 0.62, "median": 0.45})
        self.assertEqual(accuracy.quoted_chances(root, 2, B), {"h2h": 0.38, "median": 0.55})
        r = odds_moves(root)["teams"][A]["results"][0]
        self.assertEqual((r["p_h2h"], r["p_median"]), (0.62, 0.45))

    def test_a_forecast_logged_after_kickoff_is_never_quoted(self):
        from webui import accuracy
        self.plant_quote(logged_at="2026-09-18T10:00:00Z")
        self.assertIsNone(accuracy.quoted_chances(Root(self.td.name), 2, A))
        self.assertIsNone(odds_moves(Root(self.td.name))["teams"][A]["results"][0]["p_h2h"])

    def test_home_puts_the_quote_beside_the_result(self):
        import re
        self.plant_quote()
        text = re.sub(r"<[^>]+>", "", self.get("/", "simple"))
        self.assertIn("won head-to-head (the model had 62%)", text)
        self.assertIn("missed the median (45%)", text)

    def test_the_move_between_two_forecasts_and_the_results_between_them(self):
        m = odds_moves(self.root)
        self.assertEqual((m["week"], m["prev"]), (3, 2))
        a = m["teams"][A]
        self.assertEqual((a["d_playoff"], a["d_champ"], a["playoff_was"], a["playoff"]), (13.5, 5.8, 80.0, 93.5))
        self.assertEqual(a["results"], [{"week": 2, "h2h": "W", "median": "L", "p_h2h": None, "p_median": None}])
        self.assertEqual(m["teams"][B]["d_playoff"], -2.5)
        self.assertEqual([r["team"] for r in m["rows"]][:2], [A, B], "biggest gain first")

    def test_no_earlier_forecast_means_no_move_not_a_zero(self):
        import shutil
        shutil.rmtree(os.path.join(self.td.name, "data", "weeks", "week_02"))
        m = odds_moves(Root(self.td.name))
        self.assertIsNone(m["prev"])
        self.assertEqual(m["teams"], {})

    def test_forecasts_page_shows_what_the_week_did(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/forecasts", mode)
                self.assertIn("What week 2 did", body)
                self.assertIn("+13.5", body)
                self.assertIn("−2.5", body.replace("-2.5", "−2.5"))

    def test_home_says_what_the_last_week_did_to_my_odds(self):
        import re
        text = re.sub(r"<[^>]+>", "", self.get("/", "simple"))          # what the reader sees
        self.assertIn("+13.5 since the week-2 forecast", text)


if __name__ == "__main__":
    unittest.main()

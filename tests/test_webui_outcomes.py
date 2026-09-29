"""
tests.test_webui_outcomes -- what the web UI does with the per-simulation outcome export
(docs/WEB_UI_ROADMAP.md Decision 1; UI-E5, O6, O7, O8, O9, O10).

The export (tests.test_sim_outcomes) records, for every simulated season, each remaining game,
each team against each week's median, the final seeds and the champion. The web UI never
re-simulates: every conditional number here is a FILTER over those seasons, so each one
carries the count of seasons behind it and its standard error, and below a minimum count
(200) it is refused, not shown as precise.

Every test plants a relationship in a synthetic export and checks it comes back:
  UI-E5  the filter's conditional odds, counts and standard errors; the refusal.
  UI-O8  a hand-computed swing, 2 x p x (1 - p) x (odds if won - odds if lost), and a
         leverage index that averages 1.0 across the season's games by construction.
  UI-O9  one decisive game ranks first in the rooting guide, with the right side.
  UI-O6  the playoff probability for each final win total, thin bars flagged.
  UI-O10 a team in the playoffs in every simulated season but not provably in reads
         "over 99.9%", never "clinched"; "clinched" and "eliminated" only from a bound on
         the remaining schedule.
One end-to-end test decodes a real engine run (the golden master's hermetic sandbox) and
must reproduce the engine's own playoff and title rates.
"""
import json
import math
import os
import tempfile
import unittest
from unittest.mock import patch

from webui import outcomes                        # outside the probe: a missing module must fail, not skip
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

T = [f"T{i}" for i in range(8)]
M3 = [["T0", "T1"], ["T2", "T3"], ["T4", "T5"], ["T6", "T7"]]
M4 = [["T0", "T2"], ["T1", "T3"], ["T4", "T6"], ["T5", "T7"]]
IN0 = [0, 1, 2, 3, 4, 5, 6, 7]          # T0 seeded first
OUT0 = [1, 2, 3, 4, 5, 6, 7, 0]         # T0 last; T4 takes the fourth place


def export(rows, teams=T, weeks=(3, 4), matchups=None, median_enabled=True):
    matchups = matchups or {3: M3, 4: M4}
    return {"_meta": {"week": weeks[0], "sims": len(rows)}, "teams": list(teams), "weeks": list(weeks),
            "median_hex": 2, "median_enabled": median_enabled,
            "matchups": {str(w): matchups[w] for w in weeks}, "seasons": rows}


def row(codes, seeds, champ):
    return "|".join(codes) + ";" + ",".join(str(i) for i in seeds) + ";" + ("" if champ is None else str(champ))


def code(results, bits=0):
    return "".join(results) + format(bits, "02x")


def planted_filter():
    """1,000 seasons. T0 beats T1 in week 3 in the first 600; T0 makes the playoffs in all of
    those and in the even-numbered rest (so 800 overall, 200 of 400 after a loss). T0 beats the
    week-3 median in every fourth season. T0 is champion in the first 300."""
    rows = []
    for s in range(1000):
        won = s < 600
        rows.append(row([code(("1" if won else "0") + "111", 1 if s % 4 == 0 else 0), code("1111")],
                        IN0 if (won or s % 2 == 0) else OUT0, 0 if s < 300 else 1))
    return outcomes.parse(export(rows))


def planted_leverage():
    """1,000 seasons. T0 wins its week-3 game in the even seasons and makes the playoffs exactly
    then; when it loses T4 takes the fourth place. Every other week-3 game follows (s // 2) % 2,
    independent of that. Week 4 is decided the same way every season (no branch to compare), and
    T2 beats the week-3 median in the even seasons."""
    rows = []
    for s in range(1000):
        other = "1" if (s // 2) % 2 == 0 else "0"
        won = s % 2 == 0
        rows.append(row([code(("1" if won else "0") + other * 3, (1 << 2) if won else 0), code("1111")],
                        IN0 if won else OUT0, 0))
    return outcomes.parse(export(rows))


class TestFilter(unittest.TestCase):
    """UI-E5."""

    @classmethod
    def setUpClass(cls):
        cls.o = planted_filter()

    def test_unconditioned(self):
        r = outcomes.conditional(self.o, [])
        self.assertEqual((r["n"], r["total"], r["refused"]), (1000, 1000, False))
        t0 = r["teams"]["T0"]
        self.assertAlmostEqual(t0["playoff"], 0.8)
        self.assertAlmostEqual(t0["playoff_se"], math.sqrt(0.8 * 0.2 / 1000))
        self.assertAlmostEqual(t0["champ"], 0.3)
        self.assertAlmostEqual(t0["seeds"][0], 0.8)
        self.assertAlmostEqual(t0["seeds"][7], 0.2)

    def test_a_pinned_game_returns_the_planted_conditional_odds(self):
        won = outcomes.conditional(self.o, [("g", 3, 0, "a")])
        self.assertEqual(won["n"], 600)
        self.assertAlmostEqual(won["teams"]["T0"]["playoff"], 1.0)
        self.assertAlmostEqual(won["teams"]["T0"]["playoff_se"], 0.0)
        self.assertAlmostEqual(won["teams"]["T0"]["champ"], 0.5)
        lost = outcomes.conditional(self.o, [("g", 3, 0, "b")])
        self.assertEqual(lost["n"], 400)
        self.assertAlmostEqual(lost["teams"]["T0"]["playoff"], 0.5)
        self.assertAlmostEqual(lost["teams"]["T0"]["playoff_se"], 0.025)

    def test_a_pinned_median_result(self):
        r = outcomes.conditional(self.o, [("m", 3, 0, True)])
        self.assertEqual(r["n"], 250)
        self.assertAlmostEqual(r["teams"]["T0"]["playoff"], 1.0)
        self.assertEqual(outcomes.conditional(self.o, [("m", 3, 0, False)])["n"], 750)

    def test_too_few_matching_seasons_is_refused_not_shown(self):
        r = outcomes.conditional(self.o, [("g", 3, 0, "b"), ("m", 3, 0, True)])
        self.assertEqual((r["n"], r["refused"]), (100, True))
        self.assertIsNone(r["teams"]["T0"]["playoff"])
        self.assertEqual(outcomes.MIN_SEASONS, 200)

    def test_picks_from_a_query_string(self):
        picks = outcomes.picks_from_args(self.o, {"g3.0": "a", "g4.2": "b", "m3.5": "0", "m4.1": "1",
                                                  "g3.9": "a", "g9.0": "a", "x": "1", "g3.1": ""})
        self.assertEqual(sorted(picks), sorted([("g", 3, 0, "a"), ("g", 4, 2, "b"), ("m", 3, 5, False), ("m", 4, 1, True)]))


class TestLeverage(unittest.TestCase):
    """UI-O8."""

    @classmethod
    def setUpClass(cls):
        cls.lev = outcomes.leverage(planted_leverage())

    def test_a_hand_computed_swing(self):
        c = self.lev["cells"]["T0"][3]["h2h"]
        self.assertAlmostEqual(c["p"], 0.5)
        self.assertAlmostEqual((c["if_won"], c["if_lost"]), (1.0, 0.0))
        self.assertAlmostEqual(c["swing"], 2 * 0.5 * 0.5 * 1.0)
        self.assertAlmostEqual(self.lev["cells"]["T1"][3]["h2h"]["swing"], 0.0, msg="T1 is in either way")

    def test_the_index_averages_one_across_the_season(self):
        idx = [c["h2h"]["index"] for wk in self.lev["cells"].values() for c in wk.values()
               if c["h2h"]["index"] is not None]
        self.assertAlmostEqual(sum(idx) / len(idx), 1.0)
        self.assertAlmostEqual(self.lev["cells"]["T0"][3]["h2h"]["index"], 8.0)

    def test_a_result_that_never_varies_is_refused(self):
        self.assertIsNone(self.lev["cells"]["T0"][4]["h2h"]["swing"], "T0 wins week 4 in every season")

    def test_the_biggest_games_of_the_week(self):
        g = self.lev["games"][3]
        self.assertEqual((g[0]["a"], g[0]["b"]), ("T0", "T1"))
        self.assertAlmostEqual(g[0]["total"], 1.0, msg="T0 and T4 each swing 0.5")


class TestRooting(unittest.TestCase):
    """UI-O9: the owner here is T4, whose place depends on T0 losing."""

    @classmethod
    def setUpClass(cls):
        cls.r = outcomes.rooting(planted_leverage(), "T4", 3)

    def test_the_decisive_game_ranks_first_with_the_right_side(self):
        g = self.r["games"][0]
        self.assertEqual(({g["a"], g["b"]}, g["root_for"]), ({"T0", "T1"}, "T1"))
        self.assertAlmostEqual(g["change"]["T1"], 0.5)
        self.assertAlmostEqual(g["change"]["T0"], -0.5)
        self.assertNotIn("T4", {x["a"] for x in self.r["games"]} | {x["b"] for x in self.r["games"]}, "not my own game")

    def test_the_median_team_to_want_low(self):
        m = self.r["median"][0]
        self.assertEqual((m["team"], m["want"]), ("T2", "low"))
        self.assertAlmostEqual(m["if_misses"] - m["if_beats"], 1.0)


class TestWinsCurve(unittest.TestCase):
    """UI-O6: T0 finishes on 3 wins after a week-3 loss, 4 after a win (banked 2, week 4 won)."""

    def test_the_planted_curve(self):
        o = planted_leverage()
        curve = outcomes.wins_curve(o, {t: 2.0 for t in T}, "T0")
        self.assertEqual([(b["wins"], b["n"]) for b in curve], [(3.0, 500), (4.0, 500)])
        self.assertEqual([b["p"] for b in curve], [0.0, 1.0])
        self.assertFalse(any(b["thin"] for b in curve))

    def test_bars_behind_too_few_seasons_are_flagged(self):
        doc = export(planted_leverage_rows()[:300])
        curve = outcomes.wins_curve(outcomes.parse(doc), {t: 2.0 for t in T}, "T0")
        self.assertEqual([b["n"] for b in curve], [150, 150])
        self.assertTrue(all(b["thin"] for b in curve))

    def test_no_banked_wins_no_curve(self):
        self.assertEqual(outcomes.wins_curve(planted_leverage(), {}, "T0"), [])


def planted_leverage_rows():
    return planted_leverage().rows


class TestMarkers(unittest.TestCase):
    """UI-O10."""

    def always(self, n=3000):
        return outcomes.parse(export([row([code("1111"), code("1111")], IN0, 0)] * n))

    def test_in_every_simulated_season_but_not_proven_reads_over_99_9(self):
        m = outcomes.markers(self.always(), {t: 2.0 for t in T})
        self.assertEqual(m["T0"]["label"], "over 99.9%")
        self.assertEqual(m["T7"]["label"], "under 0.1%")
        self.assertNotIn("clinched", json.dumps(m))
        self.assertNotIn("eliminated", json.dumps(m))

    def test_too_few_seasons_claim_no_near_certainty(self):
        self.assertIsNone(outcomes.markers(self.always(500), {t: 2.0 for t in T})["T0"]["label"])

    def test_clinched_and_eliminated_come_from_the_schedule_bound(self):
        banked = {t: 0.0 for t in T}
        banked.update(T0=20.0, T1=20.0, T2=20.0, T3=20.0)
        m = outcomes.markers(self.always(), banked)
        self.assertEqual(m["T0"]["label"], "clinched", "only T1-T3 can still reach 20: three, not four")
        self.assertEqual(m["T7"]["label"], "eliminated", "four teams are already out of reach")

    def test_controls_its_own_destiny(self):
        banked = {t: 0.0 for t in T}
        banked.update(T0=4.0, T1=4.0)
        m = outcomes.markers(self.always(), banked)
        self.assertTrue(m["T0"]["destiny"], "T0 winning out reaches 8; T1 then tops out at 7")
        self.assertNotEqual(m["T0"]["label"], "clinched")
        self.assertTrue(m["T1"]["destiny"], "T1 winning out beats T0 in week 3, so T0 tops out at 7")
        self.assertFalse(m["T2"]["destiny"], "T2 tops out at 4, and seven teams can reach 4")


class TestAgainstTheEngine(unittest.TestCase):
    """The plumbing, end to end: a real (sandboxed) engine run's export, decoded by the web
    UI's loader, reproduces the engine's own playoff and title rates."""

    def test_the_unconditioned_odds_are_the_engines(self):
        import matplotlib.pyplot as plt
        from fantasy_sim.simulation import FantasySimulationEngine
        from tests.golden_master import _sandbox
        # Headless, as the web UI and CI run it: on matplotlib's default Windows backend the
        # engine's first figure starts a Tk interpreter, whose objects a later browser test's
        # server thread then finalises -- Tk refuses off its own thread and that test times out.
        plt.switch_backend("Agg")
        rec, real = {}, FantasySimulationEngine.export_and_visualize

        def recording(self, *args):
            rec.setdefault("args", args)
            return real(self, *args)
        with _sandbox("week06", 2, 30) as saved:
            eng = FantasySimulationEngine()
            with patch.object(FantasySimulationEngine, "export_and_visualize", recording):
                eng.run_simulation()
            doc = dict(saved)[f"sim_outcomes_week_{eng.current_week}.json"]
        r = outcomes.conditional(outcomes.parse(doc), [], min_n=1)
        b_playoffs, b_champs = rec["args"][2], rec["args"][3]
        for t in doc["teams"]:
            self.assertAlmostEqual(r["teams"][t]["playoff"], sum(b_playoffs[t]) / 2, places=9, msg=t)
            self.assertAlmostEqual(r["teams"][t]["champ"], sum(b_champs[t]) / 2, places=9, msg=t)


class TestLoader(unittest.TestCase):
    def test_the_newest_export_at_or_before_the_week_and_nothing_without_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = Root(td)
            self.assertIsNone(outcomes.load(root))
            for w in (3, 5):
                p = os.path.join(td, "data", "weeks", f"week_{w:02d}")
                os.makedirs(p)
                with open(os.path.join(p, f"sim_outcomes_week_{w}.json"), "w", encoding="utf-8") as fh:
                    json.dump(export([row([code("1111"), code("1111")], IN0, 0)] * 5, weeks=(3, 4)), fh)
            self.assertEqual(outcomes.load(root).week, 5)
            self.assertEqual(outcomes.load(root, at_most=4).week, 3)
            self.assertIs(outcomes.load(root), outcomes.load(root), "cached until the file changes")


# ---------------------------------------------------------------------------------- the page
def plant_export(root):
    """The fixture league's week-3 export (also served to tests.test_webui_browser)."""
    from tests.test_webui_routes import TEAMS as fix
    weeks = list(range(3, 15))
    later = [[fix[0], fix[6]], [fix[1], fix[7]], [fix[2], fix[4]], [fix[3], fix[5]]]
    rows = []
    for s in range(1000):
        won = s < 600
        codes = [code(("1" if won else "0") + "111", 1 if s % 4 == 0 else 0)] + [code("1111")] * (len(weeks) - 1)
        rows.append(row(codes, IN0 if (won or s % 2 == 0) else OUT0, 0))
    doc = export(rows, teams=fix, weeks=weeks, matchups={w: later for w in weeks})
    with open(os.path.join(root, "data", "weeks", "week_03", "sim_outcomes_week_3.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class PageCase(unittest.TestCase):
    """The fixture league at week 3 with a synthetic 1,000-season export: the owner's team
    (fixture team 0) beats its week-3 opponent in the first 600 seasons and makes the playoffs
    in all of those and in half of the rest."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)
        plant_export(self.td.name)

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


class TestPlayoffPage(PageCase):
    def test_both_views_render_the_machine_leverage_rooting_and_the_curve(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                text = visible_text(self.get("/playoffs", mode))
                for s in ("The playoff machine", "1,000 simulated seasons", "Which games matter most",
                          "Who to root for", "How many wins", "± 1.3"):
                    self.assertIn(s, text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])

    def test_a_pin_shows_its_count_and_standard_error(self):
        text = visible_text(self.get("/playoffs?g3.0=a"))
        self.assertIn("600 of 1,000 seasons match", text)
        self.assertIn("100.0%", text)

    def test_too_few_seasons_are_refused_and_say_why(self):
        text = visible_text(self.get("/playoffs?g3.0=b&m3.0=1", "simple"))
        self.assertIn("100 of 1,000 seasons match", text)
        self.assertIn("too few", text)
        self.assertEqual([t for t in DEV_TERMS if t in text], [])

    def test_the_result_alone_for_the_page_script(self):
        body = self.get("/playoffs/result?g3.0=a")
        self.assertIn("600 of 1,000 seasons match", body)
        self.assertNotIn("<html", body)

    def test_the_page_is_in_the_navigation(self):
        for mode in ("dev", "simple"):
            self.assertIn('href="/playoffs"', self.get("/", mode))

    def test_an_export_for_other_teams_is_not_shown_as_this_league(self):
        """A test run once leaked a two-season export for teams A-H into the real tree
        (fixed in the tests' patches); the page must not present such a file as the league."""
        p = os.path.join(self.td.name, "data", "weeks", "week_03", "sim_outcomes_week_3.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(export([row([code("1111"), code("1111")], IN0, 0)] * 300), fh)
        text = visible_text(self.get("/playoffs", "simple"))
        self.assertNotIn("T0", text)
        self.assertIn("next forecast", text)

    def test_without_an_export_the_page_says_when_it_will_appear(self):
        os.remove(os.path.join(self.td.name, "data", "weeks", "week_03", "sim_outcomes_week_3.json"))
        text = visible_text(self.get("/playoffs", "simple"))
        self.assertIn("next forecast", text)
        self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

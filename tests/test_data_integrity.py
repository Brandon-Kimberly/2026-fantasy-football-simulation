"""
tests.test_data_integrity -- numbers the site shows against what the league itself counted
(owner report of 2026-09-29, and the audit it prompted).

Ground truth, fetched from Sleeper the same day: in week 2 the league counted Quantum Ferrets'
game a LOSS, but Sleeper has since re-scored that week under a later IDP setting and its box
score now reads a 148.52-144.19 win. Pages that judged results from box scores inherited the
rewrite. Each fix here reads the league's own record instead.

1. Points against: the standings summed opponents' re-scored box scores (Quantum Ferrets 509.98
   against Sleeper's 521.20; 7 of 8 teams wrong). Sleeper keeps each team's points against as
   counted (settings.fpts_against); the sync now stores it and the standings read it.
2. Sleeper's points are split into whole points and hundredths (fpts, fpts_decimal), and they
   were joined as text: 509 and 5 read 509.5, not 509.05. No team's hundredths are a single
   digit today, so no stored value is wrong yet; a future one would be.
3. Home's expected-wins curve took its completed weeks from the forecast export, which counts
   week 2's re-scored win: 2, 3, 3 where the league banked 2, 2, 3. Completed weeks now come
   from the league's record.
4. The luck ledger judged winners from box scores: week 2 counted as a close WIN (or, where the
   banked check caught the mismatch, the measure was withheld). It now takes winners from the
   league's record; margins stay the box scores'. Opponent luck reads the same points against
   the standings show.
5. The week in review paired as-played winners with re-scored points, so a team could "lose
   with the week's best losing score" while its box score beat the winner's. A game whose box
   score contradicts its result is left out of the points awards.
6. Margins under a point printed to one decimal: the league's closest game, 0.04 points,
   read "0.0 points" -- a tie that never happened.
Written before any fix.
"""
import json
import os
import tempfile
import unittest


class TestSleeperDecimals(unittest.TestCase):
    def test_hundredths_are_hundredths(self):
        from fantasy_sim.sync import build_standings
        st = build_standings({"waiver_budget": 100}, [{"roster_id": 1, "settings": {
            "wins": 3, "fpts": 509, "fpts_decimal": 5, "fpts_against": 521, "fpts_against_decimal": 20,
            "waiver_budget_used": 52}}], {1: "Quantum Ferrets"})
        self.assertEqual(st["Quantum Ferrets"]["points_scored"], 509.05)
        self.assertEqual(st["Quantum Ferrets"]["points_against"], 521.20)

    def test_the_other_two_readers(self):
        from fantasy_sim.sync import sleeper_points
        self.assertEqual(sleeper_points({"fpts": 1721, "fpts_decimal": 32}), 1721.32)
        self.assertEqual(sleeper_points({"fpts": 12, "fpts_decimal": 7}), 12.07)
        self.assertEqual(sleeper_points({}), 0.0)
        self.assertEqual(sleeper_points({"fpts_against": 521, "fpts_against_decimal": 2}, "fpts_against"), 521.02)
        import inspect
        from fantasy_sim import backtest_season, sync
        for mod in (sync, backtest_season):
            self.assertNotIn('f"{st.get(\'fpts\', 0)}.{', inspect.getsource(mod))
            self.assertNotIn("f\"{settings.get('fpts', 0)}.{", inspect.getsource(mod))


class TestLedgerTakesTheLeaguesResults(unittest.TestCase):
    """fantasy_sim.luck_ledger.ledger gains `results`: {week: {team: (h2h_win, median_win)}}.
    Without it nothing changes; with it, who won comes from the record."""

    SCORES = {1: {"A": 180.0, "B": 140.0, "C": 150.0, "D": 145.0}, 2: {"A": 148.52, "B": 150.0, "C": 144.19, "D": 160.0}}
    PAIRS = {1: [("A", "B"), ("C", "D")], 2: [("A", "C"), ("B", "D")]}
    RESULTS = {1: {"A": (1.0, 1), "B": (0.0, 0), "C": (1.0, 1), "D": (0.0, 0)},
               2: {"A": (0.0, 0), "C": (1.0, 0), "B": (0.0, 1), "D": (1.0, 1)}}

    def test_close_games_follow_the_record(self):
        from fantasy_sim.luck_ledger import ledger
        before = ledger(self.SCORES, self.PAIRS, "A")
        self.assertEqual((before["close_games"]["wins"], before["close_games"]["losses"]), (1, 0), "box scores: a close win")
        after = ledger(self.SCORES, self.PAIRS, "A", results=self.RESULTS, banked_wins=2)
        self.assertNotIn("banked_disagreement", after, "the record agrees with the banked 2")
        self.assertEqual((after["close_games"]["wins"], after["close_games"]["losses"]), (0, 1))
        self.assertEqual(after["schedule_luck"]["actual_wins"], 1)

    def test_points_against_can_be_the_leagues(self):
        from fantasy_sim.luck_ledger import ledger
        r = ledger(self.SCORES, self.PAIRS, "A", points_against={"A": 400.0, "B": 300.0, "C": 300.0, "D": 300.0})
        self.assertEqual(r["opponent_luck"]["my_pa_per_game"], 200.0)
        self.assertEqual(r["opponent_luck"]["league_avg_pa_per_game"], 162.5)


try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import visible_text
    from tests.test_webui_objects import CB, IW, QF, plant
    from tests.test_webui_routes import TEAMS
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    """The fixture carries the same shape as the real league: Quantum Ferrets won week 1 (180-140,
    and the median), and week 2's box score reads a 148.52-144.19 win over Cosmic Badgers that
    the league counted as a loss (logs/as_played_results_2026.json)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def rw(self, rel, fn):
        p = os.path.join(self.td.name, "data", *rel.split("/"))
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(fn(d), fh)


class TestPointsAgainstAreTheLeagues(Case):
    def test_the_standings_read_the_stored_total(self):
        self.rw("current/league_standings.json", lambda d: {t: dict(v, points_against=500.0 + i) for i, (t, v) in enumerate(d.items())})
        from webui.standings import table
        rows = {r["team"]: r for r in table(self.root)}
        order = list(json.load(open(os.path.join(self.td.name, "data", "current", "league_standings.json"))))
        self.assertEqual(rows[order[0]]["points_against"], 500.0)
        self.assertEqual(rows[QF]["points_against"], 500.0 + order.index(QF))

    def test_the_streak_is_the_leagues(self):
        # Sleeper's streak runs through both games a week, head-to-head then median, in the
        # order of its own record string (Quantum Ferrets, live on 2026-09-29: WWLLLW, streak
        # 1W); the page counted head-to-head only and printed L2. Fixture: Quantum Ferrets
        # W W / L L, Cosmic Badgers W L / W L, Iron Wombats L L / L L
        from webui.standings import table
        rows = {r["team"]: r for r in table(self.root)}
        self.assertEqual((rows[QF]["streak"], rows[CB]["streak"], rows[IW]["streak"]), ("L2", "L1", "L4"))


class TestTheCurveBanksWhatTheLeagueBanked(Case):
    def test_completed_weeks_follow_the_record(self):
        self.rw("weeks/week_03/syndicate_comprehensive_matrix_week_3.json",
                lambda d: dict(d, weekly_trajectories={QF: {"expected_cumulative_wins_by_week": [2.0, 3.0, 4.4, 5.6]}}))
        from webui.glance import home_report
        traj = home_report(self.root, QF, FakeRunner())["trajectory"]
        self.assertEqual(traj[:2], [2.0, 2.0], "week 1 two wins, week 2 none, as the league counted")
        self.assertEqual(traj[2:], [4.4, 5.6], "the forecast's weeks to come are untouched")

    def test_the_forecast_pages_curves_bank_what_the_league_banked(self):
        # the Forecasts page draws every team's curve from the same export: Quantum Ferrets'
        # read 2, 3 and Cosmic Badgers' carried the week-2 win the league gave them one short.
        # An export for week n banks the weeks before n; those come from the record, the rest
        # stay the forecast's
        self.rw("weeks/week_03/syndicate_comprehensive_matrix_week_3.json",
                lambda d: dict(d, weekly_trajectories={QF: {"expected_cumulative_wins_by_week": [2.0, 3.0, 4.4, 5.6]}}))
        from webui.app import week_report
        traj = week_report(self.root, 3)["traj"][QF]["expected_cumulative_wins_by_week"]
        self.assertEqual(traj, [2.0, 2.0, 4.4, 5.6])

    def test_the_week_and_the_record_are_left_alone(self):
        # found in the screenshots of the fix above: its loop reused `wk`, so Home announced
        # the last completed week ("Week 3", "every week-3 game has kicked off") and counted
        # losses from it (3-1 for a 3-3 team)
        from webui.glance import freshness_report, home_report
        rep = home_report(self.root, QF, FakeRunner())
        wk = int(freshness_report(self.root)["week"])
        self.assertEqual(rep["week"], wk)
        self.assertEqual(rep["losses"], 2 * (wk - 1) - int(rep["my_row"]["wins"]))


class TestLuckReadsTheRecord(Case):
    def test_the_close_game_is_the_loss_the_league_counted(self):
        from webui.luck import report
        r = report(self.root, QF)
        self.assertIsNone(r["disagreement"])
        cg = next(x for x in r["rows"] if x["key"] == "close_games")["metric"]
        self.assertEqual((cg["wins"], cg["losses"]), (0, 1))


class TestTheReviewDoesNotContradictItself(Case):
    def test_no_points_award_from_a_game_the_box_score_disagrees_with(self):
        # Quantum Ferrets' re-scored week 2 becomes the best losing score: "lost with 175.0 to a
        # team that scored 144.19" is the contradiction this must not print
        def up(d):
            d["week_2"]["team_results"][QF]["points_scored"] = 175.0
            return d
        self.rw("current/weekly_actuals.json", up)
        from webui.recap import week_recap
        awards = {a["key"]: a for a in week_recap(self.root, 2, MY_TEAM)["awards"]}
        for key in ("low_win", "high_loss"):
            if key in awards:
                self.assertNotIn(awards[key]["team"], (QF, CB), key)


class TestSmallMarginsKeepTheirHundredths(Case):
    def test_the_closest_game(self):
        weeks = {"1": [{"roster_id": 1, "matchup_id": 1, "points": 119.90}, {"roster_id": 2, "matchup_id": 1, "points": 119.94}]
                 + [{"roster_id": i + 3, "matchup_id": i // 2 + 2, "points": 100.0 + 10 * i} for i in range(6)]}
        bundle = {"league_id": "", "season": "2025", "status": "complete", "settings": {"playoff_week_start": 15},
                  "roster_map": {str(i + 1): t for i, t in enumerate(TEAMS)},
                  "final_standings": {t: {"wins": 1, "losses": 0, "ties": 0, "points_scored": 100.0} for t in TEAMS}, "matchups": weeks}
        with open(os.path.join(self.td.name, "data", "logs", "season_2025.json"), "w", encoding="utf-8") as fh:
            json.dump(bundle, fh)
        st = Settings(self.root)
        st.set_mode("simple")
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        text = visible_text(app.test_client().get("/history").get_data(as_text=True))
        i = text.index("Closest game")
        self.assertIn("0.04", text[i:i + 60])


class TestTheRecordBookDoesNotContradictItself(unittest.TestCase):
    """Same class as the week in review, found auditing it: history's record book pairs the
    as-played winner with re-scored points, so a game the league counted a 0.24-point loss
    could be the "highest score in a loss" for the team whose box score now WINS by 4.33, or
    the season's biggest win for a team that scored less. A contradicted game still counts
    for the points-only records (highest, lowest); it is left out of the result records."""

    def test_result_records_skip_the_contradicted_game(self):
        from webui.history import record_book
        g = lambda wk, a, b, pa, pb, w: {"season": "2026", "week": wk, "a": a, "b": b, "pa": pa, "pb": pb, "winner": w,
                                          "margin": round(abs(pa - pb), 2), "rescored": w == b and pa > pb, "rescaled": False}
        gs = [g(2, "A", "B", 190.0, 100.0, "B"),       # the league counted B; the box score says A by 90
              g(1, "C", "D", 150.0, 120.0, "C"), g(1, "E", "F", 140.0, 139.0, "E")]
        book = {r["key"]: r for r in record_book(gs)}
        self.assertEqual(book["high"]["team"], "A", "the points record keeps the re-scored game")
        for key in ("margin", "high_loss", "low_win", "close"):
            self.assertNotEqual(book[key]["week"], 2, key)


class TestTheLuckToolReadsTheRecord(unittest.TestCase):
    """The Luck Ledger TOOL (scripts.luck_ledger) is where "won both close games" came from:
    run on 2026-09-29 it printed close games 2-0 and 2.0 head-to-head wins for a team the
    league counted 1-2. It reads Sleeper's box scores live, so week 2's re-scored "win"
    counted, and its banked cross-check stayed silent: it only fires once Sleeper's `leg`
    has moved past the last counted week, and `leg` still read that week on the Tuesday
    after it. The tool now takes the as-played record (data/logs/as_played_results_<season>.json)
    for results, and checks against the banked record -- and reads the league's points
    against -- whenever the league's own record covers exactly the weeks it counted."""

    SCORES = {1: {"A": 180.0, "B": 140.0, "C": 150.0, "D": 145.0}, 2: {"A": 148.52, "B": 150.0, "C": 144.19, "D": 160.0}}
    PAIRS = {1: [("A", "B"), ("C", "D")], 2: [("A", "C"), ("B", "D")]}
    ROSTERS = [{"roster_id": i + 1, "settings": {"wins": w, "losses": 4 - w, "fpts_against": pa, "fpts_against_decimal": 50}}
               for i, (w, pa) in enumerate(((2, 290), (1, 324), (3, 328), (2, 300)))]

    def test_the_tool_passes_the_record_the_banked_wins_and_the_points_against(self):
        from unittest.mock import patch
        import scripts.luck_ledger as sl
        from fantasy_sim.config import LEAGUE_ID
        names = {1: "A", 2: "B", 3: "C", 4: "D"}
        results = {1: {"A": (1.0, 1), "B": (0.0, 0), "C": (1.0, 1), "D": (0.0, 0)},
                   2: {"A": (0.0, 0), "C": (1.0, 0), "B": (0.0, 1), "D": (1.0, 1)}}
        seen = {}

        def fake_ledger(*args, **kw):
            seen.update(kw)
            return {"schedule_luck": None, "opponent_luck": None, "close_games": None, "dnp_luck": None, "scoring_luck": None}
        info = {"season": "2026", "settings": {"league_average_match": 1, "leg": 2}}
        with patch.object(sl, "_league_chain", return_value=[("2026", LEAGUE_ID, info)]),              patch.object(sl, "_team_names", return_value=names),              patch.object(sl, "_season_data", return_value=(self.SCORES, self.PAIRS, {})),              patch.object(sl, "_get", return_value=self.ROSTERS),              patch.object(sl, "_projections", return_value=None),              patch.object(sl, "_as_played", return_value=results),              patch.object(sl, "ledger", side_effect=fake_ledger),              patch.object(sl, "render"), patch.object(sl, "real_name_overlay", return_value={}):
            sl.main(["--team", "A"])
        self.assertEqual(seen.get("results"), results)
        self.assertEqual(seen.get("banked_wins"), 2, "the league's record covers both counted weeks")
        self.assertEqual(seen.get("points_against"), {"A": 290.5, "B": 324.5, "C": 328.5, "D": 300.5})

    def test_the_as_played_file(self):
        import scripts.luck_ledger as sl
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "as_played_results_2026.json"), "w", encoding="utf-8") as fh:
                json.dump({"_meta": {"weeks": [1]}, "week_1": {"A": {"h2h_win": 1.0, "median_win": 0}}}, fh)
            self.assertEqual(sl._as_played("2026", logs_dir=td), {1: {"A": (1.0, 0)}})
            self.assertEqual(sl._as_played("2025", logs_dir=td), {})


if __name__ == "__main__":
    unittest.main()

"""
tests.test_webui_objects -- the pages a manager thinks in (docs/WEB_UI_ROADMAP.md UI-A1, A2,
A3): a team, a player, a week's games. Every competitor has all three; this UI had none --
a team link jumped to an anchor on League, a player name opened a hover card and went
nowhere, and only my own game had a page.

The planted season: Quantum Ferrets (mine) beat Neon Walruses in week 1; in week 2 the
re-scored box score says they beat Cosmic Badgers but the league played it as a loss
(F83 -- the as-played record decides, owner ruling 2026-09-28); week 3 is under way against
Iron Wombats. Player 0 O'Neil (id 100) is my questionable quarterback.
"""
import json
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui import objects
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_fourth import plant as plant_fourth
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_routes import TEAMS, build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

QF, NW, RP, TL, CB, PY, IW, CM = ("Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Turbo Llamas",
                                  "Cosmic Badgers", "Polar Yetis", "Iron Wombats", "Crimson Marmots")


def _w(root, rel, obj):
    p = os.path.join(root, "data", *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        if isinstance(obj, str):
            fh.write(obj)
        else:
            json.dump(obj, fh)


def plant(root):
    build_tree(root)
    plant_fourth(root)                                        # evaluated moves for three teams
    week1 = [[QF, NW], [RP, TL], [CB, PY], [IW, CM]]
    week2 = [[QF, CB], [NW, RP], [TL, PY], [IW, CM]]
    later = [[QF, IW], [NW, CM], [RP, CB], [TL, PY]]
    _w(root, "current/league_schedule.json", [week1, week2] + [later] * 12)
    res1 = {QF: (180.0, 1.0, 1), NW: (140.0, 0.0, 0), RP: (170.0, 0.0, 1), TL: (175.0, 1.0, 1),
            CB: (150.0, 1.0, 0), PY: (145.0, 0.0, 0), IW: (130.0, 0.0, 0), CM: (160.0, 1.0, 1)}
    res2 = {QF: (148.52, 1.0, 0), CB: (144.19, 0.0, 0), NW: (170.0, 1.0, 1), RP: (160.0, 0.0, 1),
            TL: (182.0, 1.0, 1), PY: (159.0, 0.0, 1), IW: (126.0, 0.0, 0), CM: (142.0, 1.0, 0)}
    _w(root, "current/weekly_actuals.json", {
        f"week_{n}": {"median_cutoff": 155.0, "team_results": {t: {"points_scored": p, "h2h_win": h, "median_win": m}
                                                              for t, (p, h, m) in res.items()}}
        for n, res in ((1, res1), (2, res2))})
    played = {t: {"h2h_win": h, "median_win": m} for t, (_p, h, m) in res2.items()}
    played[QF], played[CB] = {"h2h_win": 0.0, "median_win": 0}, {"h2h_win": 1.0, "median_win": 0}
    _w(root, "logs/as_played_results_2026.json", {"_meta": {"weeks": [1, 2]}, "week_2": played})
    wins = {t: res1[t][1] + res1[t][2] + played[t]["h2h_win"] + played[t]["median_win"] for t in res1}
    _w(root, "current/league_standings.json", {t: {"h2h_wins": wins[t], "points_scored": 300.0 + i, "remaining_faab": 90 - i}
                                               for i, t in enumerate(TEAMS)})
    _w(root, "current/nfl_schedule.json", {"_meta": {"kickoffs": {"1": ["2026-09-11T00:20Z"], "2": ["2026-09-18T00:15Z"],
                                                                  "3": ["2026-09-25T00:15Z"]}}})
    _w(root, "logs/predictions_2026.jsonl", "\n".join(json.dumps(r) for r in [
        {"record_type": "week_predictions", "week": 2, "logged_at": "2026-09-17T10:00:00Z", "canonical": True,
         "matchups": [{"a": QF, "b": CB, "p_a": 0.71, "p_b": 0.29}], "median": {}},
        {"record_type": "week_predictions", "week": 3, "logged_at": "2026-09-24T10:00:00Z", "canonical": True,
         "matchups": [{"a": QF, "b": IW, "p_a": 0.77, "p_b": 0.23}], "median": {}}]) + "\n")
    # the week-3 export: a head-to-head matrix and each roster's simulated ranges
    m = os.path.join(root, "data", "weeks", "week_03", "syndicate_comprehensive_matrix_week_3.json")
    with open(m, encoding="utf-8") as fh:
        mx = json.load(fh)
    mx["h2h_win_probability_matrix"] = {QF: {IW: 64.0, CB: 70.0, QF: float("nan")}, IW: {QF: 36.0}}
    with open(m, "w", encoding="utf-8") as fh:
        json.dump(mx, fh)
    _w(root, "weeks/week_03/player_variance.json", {QF: [{"name": "Player 0 O'Neil", "pos": "QB", "mean": 17.4,
                                                          "p10": 8.1, "p25": 12.0, "p50": 16.9, "p75": 22.0, "p90": 27.3}]})
    base = {f"Player {i} O'Neil": {"pos": "QB", "team": "GB", "mean": 17.2 + i, "bye": 11, "on_ir": False,
                                   "injury_status": "Questionable" if i == 0 else None, "player_id": str(100 + i),
                                   "std_aleatoric": 6.0, "std_epistemic": 2.0} for i in range(8)}
    _w(root, "current/player_baselines.json", base)
    _w(root, "current/sleeper_players_cache.json", {"100": {
        "player_id": "100", "full_name": "Player 0 O'Neil", "position": "QB", "team": "GB", "number": 12, "age": 27,
        "college": "Nowhere State", "height": "74", "weight": "221", "years_exp": 5, "depth_chart_position": "QB",
        "depth_chart_order": 1, "injury_status": "Questionable", "injury_body_part": "Ankle",
        "injury_notes": "Limited in practice", "practice_participation": "Limited", "practice_description": "ankle",
        "news_updated": 1758900000000}})
    _w(root, "logs/projection_log.jsonl", "\n".join(json.dumps(r) for r in [
        {"player_id": "100", "name": "Player 0 O'Neil", "week": 1, "sleeper_mean": 18.0, "espn_mean": 17.0, "synced_at": "2026-09-09T10:00:00Z"},
        {"player_id": "100", "name": "Player 0 O'Neil", "week": 1, "sleeper_mean": 19.5, "espn_mean": 17.0, "synced_at": "2026-09-12T10:00:00Z"},
        {"player_id": "100", "name": "Player 0 O'Neil", "week": 2, "sleeper_mean": 17.0, "espn_mean": None, "synced_at": "2026-09-16T10:00:00Z"},
        {"player_id": "999", "name": "Someone Else", "week": 1, "sleeper_mean": 5.0, "espn_mean": None, "synced_at": "2026-09-09T10:00:00Z"}]) + "\n")
    _w(root, "logs/first_recorded_scores.jsonl", "\n".join(json.dumps(r) for r in [
        {"player_id": "100", "name": "Player 0 O'Neil", "week": 1, "points": 24.3, "recorded_at": "2026-09-15T00:00:00Z"},
        {"player_id": "100", "name": "Player 0 O'Neil", "week": 2, "points": 9.8, "recorded_at": "2026-09-22T00:00:00Z"}]) + "\n")


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class ObjectCase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def get(self, path, mode="dev", status=200):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, status, path)
        return r.get_data(as_text=True)


class TestTeam(ObjectCase):
    def test_a_team_is_found_by_its_slug(self):
        self.assertEqual(objects.team_for_slug(self.root, "quantum-ferrets"), QF)
        self.assertIsNone(objects.team_for_slug(self.root, "nobody-at-all"))

    def test_the_schedule_carries_results_as_played_quotes_and_future_chances(self):
        rep = objects.team_report(self.root, QF, MY_TEAM)
        s = rep["schedule"]
        self.assertEqual(len(s), 14)
        self.assertEqual((s[0]["opponent"], s[0]["result"], s[0]["mine"], s[0]["theirs"]), (NW, "W", 180.0, 140.0))
        self.assertEqual((s[1]["opponent"], s[1]["result"], s[1]["rescored"]), (CB, "L", True))
        self.assertEqual((s[0]["rescored"], s[0]["rescaled"]), (False, True), "week 1's points are re-scored too (F83)")
        self.assertEqual(s[1]["quote"], 0.71, "the model's pre-kickoff quote for the game")
        self.assertTrue(s[2]["current"])
        self.assertIsNone(s[2]["result"])
        self.assertEqual(s[3]["p_win"], 0.64, "a future week's chance comes from the head-to-head matrix")

    def test_the_header_and_roster(self):
        rep = objects.team_report(self.root, QF, MY_TEAM)
        self.assertEqual(rep["odds"]["playoff"], 93.5)
        self.assertEqual(rep["record"]["h2h"]["text"], "1–1")
        p0 = next(r for r in rep["roster"] if r["name"] == "Player 0 O'Neil")
        self.assertEqual((p0["pid"], p0["p10"], p0["p90"], p0["status"]), ("100", 8.1, 27.3, "Questionable"))

    def test_a_teams_moves_and_its_meetings_with_mine(self):
        other = objects.team_report(self.root, TEAMS[2], MY_TEAM)
        self.assertTrue(other["moves"], "the fixture's evaluated moves for this team")
        self.assertTrue(all(TEAMS[2] in d["teams"] for d in other["moves"]))
        self.assertIsNotNone(objects.team_report(self.root, CB, MY_TEAM)["h2h"])
        self.assertIsNone(objects.team_report(self.root, QF, MY_TEAM)["h2h"], "no meeting with myself")


class TestPlayer(ObjectCase):
    def test_identity_availability_and_owner(self):
        p = objects.player_report(self.root, "100", MY_TEAM)
        self.assertEqual((p["name"], p["pos"], p["nfl"], p["owner"]), ("Player 0 O'Neil", "QB", "GB", QF))
        self.assertEqual((p["age"], p["college"], p["number"], p["depth"]), (27, "Nowhere State", 12, "QB1"))
        self.assertEqual((p["status"], p["body_part"], p["practice"]), ("Questionable", "Ankle", "Limited"))

    def test_prices_are_labelled_week_and_season(self):
        p = objects.player_report(self.root, "100", MY_TEAM)
        self.assertEqual(p["season_mean"], 17.2)
        self.assertEqual((p["range"]["p10"], p["range"]["p90"]), (8.1, 27.3))

    def test_history_is_the_last_projection_before_kickoff_against_the_points(self):
        h = {r["week"]: r for r in objects.player_report(self.root, "100", MY_TEAM)["history"]}
        self.assertEqual((h[1]["projected"], h[1]["points"]), (18.0, 24.3), "19.5 was logged after week 1 kicked off")
        self.assertEqual((h[2]["projected"], h[2]["points"]), (17.0, 9.8))

    def test_an_unknown_player_is_none(self):
        self.assertIsNone(objects.player_report(self.root, "999999", MY_TEAM))


class TestWeekGames(ObjectCase):
    def test_a_played_week_counts_as_the_league_played_it_and_marks_the_upset(self):
        games = objects.week_games(self.root, 2, MY_TEAM)
        self.assertEqual(len(games["games"]), 4)
        g = next(g for g in games["games"] if QF in (g["a"], g["b"]))
        self.assertEqual((g["winner"], g["rescored"]), (CB, True))
        self.assertTrue(g["rescaled"])
        self.assertTrue(g["upset"], "the model had Quantum Ferrets at 71%")
        self.assertTrue(g["mine"])

    def test_a_future_week_carries_chances(self):
        g = next(g for g in objects.week_games(self.root, 5, MY_TEAM)["games"] if QF in (g["a"], g["b"]))
        self.assertIsNone(g["winner"])
        self.assertEqual(g["p_a"] if g["a"] == QF else g["p_b"], 0.64)

    def test_the_weeks_of_the_season(self):
        w = objects.week_games(self.root, 3, MY_TEAM)
        self.assertEqual((w["week"], w["current"], w["weeks"]), (3, 3, list(range(1, 15))))


class TestLiveLeague(unittest.TestCase):
    """UI-A3 live: the snapshot already reads every roster's matchup from Sleeper; it now keeps
    every game, not only mine, so the Matchups page can show all four as they happen."""

    def setUp(self):
        from tests.test_webui_live import plant as plant_live
        self.td = tempfile.TemporaryDirectory()
        plant_live(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_the_snapshot_carries_every_game(self):
        from tests.test_webui_live import OPP, fake_fetch
        from webui.live import snapshot
        s = snapshot(self.root, 3, MY_TEAM, "L", fake_fetch)
        self.assertEqual(len(s["league"]), 1, "the fake league has one game")
        g = s["league"][0]
        mine = "a" if g["a"] == MY_TEAM else "b"
        theirs = "b" if mine == "a" else "a"
        self.assertEqual({g["a"], g["b"]}, {MY_TEAM, OPP})
        self.assertEqual((g[mine + "_banked"], g[theirs + "_banked"]), (31.5, 0.0))
        self.assertAlmostEqual(g["p_a"] if mine == "a" else 1 - g["p_a"], s["p_win"], places=3,
                               msg="the same estimate as my own panel")
        self.assertFalse(g["decided"])
        self.assertGreater(g[theirs + "_to_play"], 0)


class TestPages(ObjectCase):
    PAGES = ("/team/quantum-ferrets", "/team/cosmic-badgers", "/player/100", "/matchups", "/matchups/week-2",
             "/matchups/week-5")

    def test_every_page_renders_in_both_views_in_plain_words(self):
        for mode in ("dev", "simple"):
            for path in self.PAGES:
                with self.subTest(mode=mode, path=path):
                    body = self.get(path, mode)
                    if mode == "simple":
                        hits = [t for t in DEV_TERMS if t in visible_text(body)]
                        self.assertEqual(hits, [], f"{path} shows developer vocabulary: {hits}")

    def test_unknown_things_are_404(self):
        for path in ("/team/nobody-at-all", "/player/999999", "/matchups/week-99"):
            with self.subTest(path=path):
                self.get(path, status=404)

    def test_team_links_go_to_the_team_page_and_player_names_to_the_player_page(self):
        league = self.get("/league")
        self.assertIn('href="/team/quantum-ferrets"', league)
        self.assertNotIn('href="/league#t-', league)
        team = self.get("/team/quantum-ferrets")
        self.assertIn('href="/player/100"', team)

    def test_matchups_is_in_both_views_nav(self):
        for mode in ("dev", "simple"):
            self.assertIn('href="/matchups"', self.get("/", mode))

    def test_the_team_page_says_what_the_week_2_result_was(self):
        text = visible_text(self.get("/team/quantum-ferrets", "simple"))
        self.assertIn("Cosmic Badgers", text)
        self.assertIn("re-scored", text)

    def test_the_current_week_is_ready_for_live_scores(self):
        st = Settings(self.root)
        st.set_mode("simple")
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id="L", fetch=lambda url: {}))
        app.testing = True
        body = app.test_client().get("/matchups/week-3").get_data(as_text=True)
        self.assertNotIn('id="mlive"', self.get("/matchups/week-3"), "no live board configured, no live read")
        self.assertIn('data-a="Quantum Ferrets"', body)
        self.assertIn('id="mlive"', body)
        self.assertIn("/api/live", body)

    def test_the_player_page_shows_the_injury_and_the_two_prices(self):
        text = visible_text(self.get("/player/100", "simple"))
        for s in ("Questionable", "Ankle", "Limited", "season mean", "Nowhere State"):
            self.assertIn(s, text)


if __name__ == "__main__":
    unittest.main()

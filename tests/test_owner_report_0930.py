"""
tests.test_owner_report_0930 -- the owner's report of 2026-09-30, and the audit behind it.

1. Forecasts' "Every run" listed weeks 5, 6, 7, 13, 14 and 15 with nothing in them. They were
   EMPTY folders under data/weeks (created 2026-09-28 by a test run), and the site listed every
   week folder by name. A week with no file in it has no run and is not listed.
2. History's record book said a team scored 0.00 in 2024 week 1. It did not: in 2024 roster 8
   had no owner (an empty slot, 0.00 every week, 0-28), and the 2024 league is not the renewal
   of today's (B20), so Sleeper numbered its rosters differently. Mapping 2024's rosters to
   today's teams by roster number put five of the seven 2024 seasons on the wrong team --
   the owner's own among them -- and the empty slot on a team that did not play that year.
   Past rosters are now matched to today's teams by OWNER; an ownerless roster is no team,
   and its forfeits are nobody's records.
3. (browser, tests.test_webui_browser) Home's alert boxes.
4. Home's standings put a number beside each team's odds line that was the count of PLACES it
   moved in the playoff-odds order ("up 4"), which read as four points of odds for a team whose
   odds rose 40. The marker is now the change in odds, in percentage points.
Written before any fix.
"""
import json
import os
import tempfile
import unittest

from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_objects import plant
    from tests.test_webui_routes import TEAMS
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def _client(root, mode="dev"):
    st = Settings(root)
    st.set_mode(mode)
    app = create_app(root, runner=FakeRunner(), csrf_token="tok", settings=st,
                     live=LiveBoard(root, MY_TEAM, league_id=None, fetch=None))
    app.testing = True
    return app.test_client()


class TestAnEmptyWeekFolderIsNoRun(unittest.TestCase):
    def test_the_listing(self):
        with tempfile.TemporaryDirectory() as td:
            for top in ("weeks", "decisions"):
                os.makedirs(os.path.join(td, "data", top, "week_03"))
                with open(os.path.join(td, "data", top, "week_03", "x.json"), "w") as fh:
                    fh.write("{}")
                os.makedirs(os.path.join(td, "data", top, "week_09"))                # empty
                os.makedirs(os.path.join(td, "data", top, "week_11", "archive"))     # only an empty folder
            root = Root(td)
            self.assertEqual(root.weeks(), [3])
            self.assertEqual(root.decision_weeks(), [3])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheForecastsPageListsOnlyRuns(unittest.TestCase):
    def test_every_run(self):
        with tempfile.TemporaryDirectory() as td:
            plant(td)
            for w in (5, 13, 15):
                os.makedirs(os.path.join(td, "data", "weeks", f"week_{w:02d}"), exist_ok=True)
            body = _client(Root(td)).get("/forecasts").get_data(as_text=True)
            for w in (5, 13, 15):
                self.assertNotIn(f'href="/forecasts/week-{w}"', body)


# ---- 2: past rosters belong to their owners -------------------------------------------------

PAST = [{"roster_id": 1, "owner_id": "u1", "settings": {"wins": 5, "losses": 9, "fpts": 1500}},
        {"roster_id": 2, "owner_id": "u3", "settings": {"wins": 9, "losses": 5, "fpts": 1700}},
        {"roster_id": 3, "owner_id": "u2", "settings": {"wins": 14, "losses": 0, "fpts": 1900}},
        {"roster_id": 4, "owner_id": None, "settings": {"wins": 0, "losses": 14, "fpts": 0}},
        {"roster_id": 5, "owner_id": "gone", "settings": {"wins": 7, "losses": 7, "fpts": 1600}}]
NOW = [{"roster_id": 1, "owner_id": "u1"}, {"roster_id": 2, "owner_id": "u2"},
       {"roster_id": 3, "owner_id": "u3"}, {"roster_id": 4, "owner_id": "new"}]
NAMES = {"1": "Neon Walruses", "2": "Rocket Pandas", "3": "Turbo Llamas", "4": "Cosmic Badgers"}


class TestOwnersNotRosterNumbers(unittest.TestCase):
    def test_the_map(self):
        from fantasy_sim.sync import owner_roster_map
        rmap, unowned = owner_roster_map(PAST, NOW, NAMES)
        self.assertEqual(rmap, {"1": "Neon Walruses", "2": "Turbo Llamas", "3": "Rocket Pandas",
                                "5": "Former manager (roster 5)"})
        self.assertEqual(unowned, ["4"])
        self.assertNotIn("Cosmic Badgers", rmap.values(), "roster 4's owner now did not play then")

    def test_ingest_uses_it(self):
        from unittest.mock import patch
        from fantasy_sim import sync

        class R:
            status_code = 200

            def __init__(self, d):
                self.d = d

            def json(self):
                return self.d

        def get(url, timeout=None):
            if url.endswith("/league/PAST"):
                return R({"season": "2024", "settings": {"playoff_week_start": 15, "league_average_match": 1}})
            if url.endswith("/league/PAST/rosters"):
                return R(PAST)
            if url.endswith("/rosters"):
                return R(NOW)
            if "/matchups/1" in url:
                return R([{"roster_id": 4, "matchup_id": 1, "points": 0.0}, {"roster_id": 3, "matchup_id": 1, "points": 150.0}])
            return R([])
        saved = {}
        with tempfile.TemporaryDirectory() as td, patch.object(sync.requests, "get", side_effect=get), \
                patch.object(sync, "TEAM_NAME_MAP", NAMES), patch.object(sync, "LEAGUE_ID", "NOW"), \
                patch.object(sync, "save_json", side_effect=lambda p, d, **k: saved.update({"doc": d})):
            self.assertEqual(sync.ingest_season("PAST", path_fn=lambda s: os.path.join(td, f"season_{s}.json")), 1)
        doc = saved["doc"]
        self.assertEqual(doc["roster_map"]["3"], "Rocket Pandas")
        self.assertNotIn("4", doc["roster_map"])
        self.assertEqual(doc["unowned_rosters"], ["4"])
        self.assertEqual(set(doc["final_standings"]), {"Neon Walruses", "Turbo Llamas", "Rocket Pandas", "Former manager (roster 5)"})
        self.assertNotIn("u1", json.dumps(doc), "no owner id is written")

    def test_an_existing_bundle_is_remapped(self):
        from fantasy_sim.sync import remap_season_bundle
        bundle = {"season": "2024", "roster_map": {"1": "Neon Walruses", "2": "Rocket Pandas", "3": "Turbo Llamas",
                                                   "4": "Cosmic Badgers", "5": "Polar Yetis"},
                  "final_standings": {"Neon Walruses": {"wins": 5}, "Rocket Pandas": {"wins": 9}, "Turbo Llamas": {"wins": 14},
                                      "Cosmic Badgers": {"wins": 0}, "Polar Yetis": {"wins": 7}},
                  "matchups": {"1": []}}
        out = remap_season_bundle(bundle, PAST, NOW, NAMES)
        self.assertEqual(out["roster_map"], {"1": "Neon Walruses", "2": "Turbo Llamas", "3": "Rocket Pandas",
                                             "5": "Former manager (roster 5)"})
        self.assertEqual(out["final_standings"]["Rocket Pandas"], {"wins": 14})
        self.assertEqual(out["final_standings"]["Turbo Llamas"], {"wins": 9})
        self.assertNotIn("Cosmic Badgers", out["final_standings"])
        self.assertEqual(out["unowned_rosters"], ["4"])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestAnEmptySlotHoldsNoRecord(unittest.TestCase):
    def test_the_record_book(self):
        from webui.history import games, record_book
        with tempfile.TemporaryDirectory() as td:
            plant(td)
            weeks = {"1": [{"roster_id": 1, "matchup_id": 1, "points": 120.0}, {"roster_id": 8, "matchup_id": 1, "points": 0.0}]
                     + [{"roster_id": i + 2, "matchup_id": i // 2 + 2, "points": 100.0 + 10 * i} for i in range(6)]}
            bundle = {"league_id": "", "season": "2024", "status": "complete", "settings": {"playoff_week_start": 15},
                      "roster_map": {str(i + 1): t for i, t in enumerate(TEAMS[:7])}, "unowned_rosters": ["8"],
                      "final_standings": {t: {"wins": 1, "losses": 0, "ties": 0, "points_scored": 100.0} for t in TEAMS[:7]},
                      "matchups": weeks}
            with open(os.path.join(td, "data", "logs", "season_2024.json"), "w", encoding="utf-8") as fh:
                json.dump(bundle, fh)
            gs = [g for g in games(Root(td)) if g["season"] == "2024"]
            self.assertEqual(len(gs), 3, "the forfeit against the empty slot is no game")
            book = {r["key"]: r for r in record_book(gs)}
            self.assertNotEqual(book["low"]["value"], 0.0)


# ---- 4: the change beside each team's odds is the change in odds -----------------------------

@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheOddsMarkerIsTheOddsMove(unittest.TestCase):
    def test_home_standings(self):
        from tests.test_webui_seventh import TestPages as Seventh
        Seventh.setUpClass()
        try:
            case = Seventh("test_odds_race_report")
            from webui.glance import home_report
            rows = {r["team"]: r for r in home_report(case.root, MY_TEAM)["standings"]}
            for t, r in rows.items():
                self.assertIn("odds_move", r)
                if len(r["spark"]) == 2 and r["playoff"] is not None:
                    self.assertAlmostEqual(r["odds_move"], round(float(r["playoff"]) - float(r["spark"][0]), 1), places=6, msg=t)
            first = rows[TEAMS[0]]
            page = case.get("/", "simple")
            self.assertIn(f"{first['odds_move']:+.1f}", page)
            self.assertIn("points of playoff odds since the week 2 forecast", page)
        finally:
            Seventh.tearDownClass()

if __name__ == "__main__":
    unittest.main()

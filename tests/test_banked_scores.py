"""
tests.test_banked_scores -- every past week's score as the league BANKED it (owner report of
2026-09-29).

The owner's matchup pages read 185.9 for a week-1 score Sleeper shows as 187.36, and 164.4
for one it shows as 168.36. Sleeper's /matchups does not store a finished week: it recomputes
each player's current stat line against the league's CURRENT scoring settings. The league
changed its IDP scoring before week 2 closed, so the API now serves week 1 on the new scale,
while the league banked it (its standings, and what Sleeper's own app shows) on the old one.
And a commissioner override (`custom_points`) on one week-2 score was ignored altogether.

Measured on the live league the same day: scoring each starter's CURRENT stat line (so the
official stat corrections are in) under the settings in force when the week was banked --
week 1 old, weeks 2-3 new -- plus the override, reproduces every team's banked points for to
the cent, 8 of 8; of the eight ways to assign old/new to weeks 1-3, exactly one does.

So fantasy_sim.banked_scores reproduces the banked score of every completed week from:
  * the league's scoring settings as they were over time (a log the sync appends to whenever
    the league's settings change: data/logs/scoring_settings.jsonl),
  * each week's current stat lines and matchups, and any commissioner override,
and finds which weeks were banked under which settings by the one assignment that reproduces
the league's own totals -- then checks points against and every team's win-loss string too.
Nothing unverified is shown as the league's: without a match the file says why, and the site
keeps the box scores with their caveat.

Written before the module exists.
"""
import json
import os
import tempfile
import unittest

OLD = {"pass_td": 4.0, "idp_sack": 4.0, "idp_tkl_solo": 1.5}
NEW = {"pass_td": 4.0, "idp_sack": 2.0, "idp_tkl_solo": 1.5}

# four teams, two weeks; roster 1 is A, and so on. p1..p4 are quarterbacks, d1..d4 pass rushers
TEAM_MAP = {"1": "A", "2": "B", "3": "C", "4": "D"}
STATS = {
    1: {"p1": {"pass_td": 3}, "d1": {"idp_sack": 2, "idp_tkl_solo": 1}, "p2": {"pass_td": 2}, "d2": {"idp_tkl_solo": 3},
        "p3": {"pass_td": 1}, "d3": {"idp_sack": 1}, "p4": {"pass_td": 4}, "d4": {"idp_tkl_solo": 2}},
    2: {"p1": {"pass_td": 1}, "d1": {"idp_sack": 1}, "p2": {"pass_td": 2}, "d2": {"idp_sack": 1, "idp_tkl_solo": 1},
        "p3": {"pass_td": 3}, "d3": {"idp_tkl_solo": 1}, "p4": {"pass_td": 1}, "d4": {"idp_sack": 3}},
}


def matchups(week, override=None):
    rows = []
    for rid, team in TEAM_MAP.items():
        n = rid
        rows.append({"roster_id": int(rid), "matchup_id": 1 if rid in ("1", "2") else 2,
                     "starters": [f"p{n}", f"d{n}", "0"], "players": [f"p{n}", f"d{n}"],
                     "points": 0.0, "custom_points": (override or {}).get(team)})
    return rows


class TestScoring(unittest.TestCase):
    def test_a_stat_line_under_settings(self):
        from fantasy_sim.banked_scores import player_points
        self.assertEqual(player_points({"idp_sack": 2, "idp_tkl_solo": 1}, OLD), 9.5)
        self.assertEqual(player_points({"idp_sack": 2, "idp_tkl_solo": 1}, NEW), 5.5)
        self.assertEqual(player_points({"rec_yd": 37, "gms_active": 1}, {"rec_yd": 0.1}), 3.7)
        self.assertEqual(player_points(None, NEW), 0.0)

    def test_a_commissioner_override_wins(self):
        from fantasy_sim.banked_scores import week_table
        t = week_table(matchups(2, override={"B": 150.41}), STATS[2], NEW, TEAM_MAP)
        self.assertEqual(t["B"]["points"], 150.41)
        self.assertTrue(t["B"]["override"])
        self.assertEqual(t["A"]["points"], 4.0 + 2.0)         # an empty slot ("0") scores nothing
        self.assertFalse(t["A"]["override"])
        self.assertEqual(t["A"]["starters"], {"p1": 4.0, "d1": 2.0})


def _totals(assign, override=None):
    """(fpts, fpts_against) per team for an assignment of settings to weeks 1 and 2."""
    from fantasy_sim.banked_scores import week_table
    pf, pa = {}, {}
    for w, s in assign.items():
        t = week_table(matchups(w, override if w == 2 else None), STATS[w], s, TEAM_MAP)
        for team, r in t.items():
            pf[team] = round(pf.get(team, 0.0) + r["points"], 2)
            opp = next(o for o, x in t.items() if o != team and x["matchup_id"] == r["matchup_id"])
            pa[team] = round(pa.get(team, 0.0) + t[opp]["points"], 2)
    return pf, pa


class TestResolve(unittest.TestCase):
    ERAS = [{"observed_at": "2026-09-01T00:00:00Z", "settings": OLD}, {"observed_at": "2026-09-23T00:00:00Z", "settings": NEW}]

    def weeks(self, override=None):
        return {w: {"matchups": matchups(w, override if w == 2 else None), "stats": STATS[w]} for w in (1, 2)}

    def test_the_one_assignment_that_reproduces_the_league(self):
        from fantasy_sim.banked_scores import resolve
        pf, pa = _totals({1: OLD, 2: NEW}, override={"B": 20.0})
        got = resolve(self.weeks({"B": 20.0}), self.ERAS, TEAM_MAP, pf, points_against=pa, median=False)
        self.assertTrue(got["verified"], got.get("why"))
        self.assertEqual(got["eras"], {1: "2026-09-01T00:00:00Z", 2: "2026-09-23T00:00:00Z"})
        self.assertEqual(got["weeks"][1]["A"]["points"], 12.0 + 9.5)    # old sacks
        self.assertEqual(got["weeks"][2]["B"]["points"], 20.0)          # the override
        self.assertEqual(got["weeks"][2]["A"]["h2h_win"], 0.0)          # A 6.0 against B's 20.0
        self.assertEqual(got["checks"], {"points_for": True, "points_against": True, "records": None})

    def test_nothing_reproduces_the_league(self):
        from fantasy_sim.banked_scores import resolve
        pf, _pa = _totals({1: OLD, 2: NEW})
        pf["C"] += 1.0                                                   # an adjustment nobody logged
        got = resolve(self.weeks(), self.ERAS, TEAM_MAP, pf, median=False)
        self.assertFalse(got["verified"])
        self.assertIn("C", got["why"])

    def test_the_records_are_checked_too(self):
        from fantasy_sim.banked_scores import resolve
        pf, _pa = _totals({1: OLD, 2: NEW})
        # week 1 (old): A 21.5 beats B 12.5, C 8.0 loses to D 19.0; week 2 (new): A 6.0 loses to
        # B 11.5, C 13.5 beats D 10.0
        right = {"A": "WL", "B": "LW", "C": "LW", "D": "WL"}
        got = resolve(self.weeks(), self.ERAS, TEAM_MAP, pf, records=right, median=False)
        self.assertTrue(got["verified"], got.get("why"))
        self.assertTrue(got["checks"]["records"])
        wrong = dict(right, A="WW")
        got = resolve(self.weeks(), self.ERAS, TEAM_MAP, pf, records=wrong, median=False)
        self.assertFalse(got["verified"])
        self.assertIn("A", got["why"])

    def test_a_week_the_league_has_not_banked_yet_is_left_out(self):
        # Sleeper's record strings say how many games are banked; a finished week not yet
        # processed is not the league's yet, and must not break the check of the ones that are
        from fantasy_sim.banked_scores import resolve
        pf, _pa = _totals({1: OLD})
        got = resolve(self.weeks(), self.ERAS, TEAM_MAP, pf, records={"A": "W", "B": "L", "C": "L", "D": "W"}, median=False)
        self.assertTrue(got["verified"], got.get("why"))
        self.assertEqual(sorted(got["weeks"]), [1])

    def test_the_median_game(self):
        from fantasy_sim.banked_scores import resolve
        pf, _pa = _totals({1: OLD, 2: NEW})
        got = resolve(self.weeks(), self.ERAS, TEAM_MAP, pf, median=True)
        # week 1: A 21.5, D 19.0 top half; B 12.5, C 8.0 bottom
        self.assertEqual({t: r["median_win"] for t, r in got["weeks"][1].items()}, {"A": 1, "D": 1, "B": 0, "C": 0})


class TestTheSettingsLog(unittest.TestCase):
    def test_a_change_is_appended_and_a_repeat_is_not(self):
        from fantasy_sim.banked_scores import eras, record_settings
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "scoring_settings.jsonl")
            self.assertTrue(record_settings(p, "2026", 1, OLD, now="2026-09-01T00:00:00Z"))
            self.assertFalse(record_settings(p, "2026", 2, dict(OLD), now="2026-09-10T00:00:00Z"))
            self.assertTrue(record_settings(p, "2026", 3, NEW, now="2026-09-23T00:00:00Z"))
            self.assertTrue(record_settings(p, "2027", 1, NEW, now="2027-09-01T00:00:00Z"))
            got = eras(p, "2026")
            self.assertEqual([e["observed_at"] for e in got], ["2026-09-01T00:00:00Z", "2026-09-23T00:00:00Z"])
            self.assertEqual(got[1]["settings"], NEW)

    def test_a_union_merged_duplicate_is_absorbed(self):
        from fantasy_sim.banked_scores import eras
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "scoring_settings.jsonl")
            rows = [{"season": "2026", "observed_at": "2026-09-23T00:00:00Z", "week": 3, "settings": NEW},
                    {"season": "2026", "observed_at": "2026-09-01T00:00:00Z", "week": 1, "settings": OLD},
                    {"season": "2026", "observed_at": "2026-09-23T00:05:00Z", "week": 3, "settings": NEW}]
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
            self.assertEqual([e["observed_at"] for e in eras(p, "2026")], ["2026-09-01T00:00:00Z", "2026-09-23T00:00:00Z"])


class TestTheSyncWritesTheFile(unittest.TestCase):
    """fantasy_sim.sync.build_banked_scores: the league's rosters (fpts, fpts_against and the
    record string), each completed week's matchups, and the stats fetched per week."""

    def test_the_file(self):
        from fantasy_sim.sync import build_banked_scores
        pf, pa = _totals({1: OLD, 2: NEW}, override={"B": 20.0})
        rosters = []
        for rid, team in TEAM_MAP.items():
            whole_pf, whole_pa = int(pf[team]), int(pa[team])
            rosters.append({"roster_id": int(rid), "metadata": {},
                            "settings": {"fpts": whole_pf, "fpts_decimal": round((pf[team] - whole_pf) * 100),
                                         "fpts_against": whole_pa, "fpts_against_decimal": round((pa[team] - whole_pa) * 100)}})
        roster_map = {int(k): v for k, v in TEAM_MAP.items()}
        fetched = []

        def fetch(url):
            fetched.append(url)
            return STATS[int(url.rstrip("/").rsplit("/", 1)[-1])]
        doc = build_banked_scores("2026", {1: matchups(1), 2: matchups(2, {"B": 20.0})}, rosters, roster_map,
                                  {"league_average_match": 0}, TestResolve.ERAS, fetch=fetch)
        self.assertTrue(doc["_meta"]["verified"], doc["_meta"].get("why"))
        self.assertEqual(doc["week_1"]["A"]["points"], 21.5)
        self.assertEqual(doc["week_2"]["B"]["points"], 20.0)
        self.assertTrue(doc["week_2"]["B"]["override"])
        self.assertEqual(doc["_meta"]["eras"], {"1": "2026-09-01T00:00:00Z", "2": "2026-09-23T00:00:00Z"})
        self.assertTrue(all("/stats/nfl/regular/2026/" in u for u in fetched))

    def test_a_stats_feed_that_fails_writes_nothing(self):
        from fantasy_sim.sync import build_banked_scores

        def boom(url):
            raise OSError("down")
        self.assertIsNone(build_banked_scores("2026", {1: matchups(1)}, [], {}, {}, TestResolve.ERAS, fetch=boom))


try:
    import flask  # noqa: F401 -- availability probe
    from tests.test_webui_objects import CB, NW, QF, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheSiteShowsTheBankedScores(unittest.TestCase):
    """The fixture's week 1 box score reads Quantum Ferrets 180.0; the league banked 181.5.
    Week 2's reads 148.52-144.19, and the commissioner's override made Cosmic Badgers 150.41."""

    def setUp(self):
        from webui.paths import Root
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def bank(self, verified=True):
        from webui.results import week_results
        doc = {"_meta": {"verified": verified, "season": "2026", "eras": {"1": "old", "2": "new"}}}
        for w, teams in week_results(self.root).items():
            doc[f"week_{w}"] = {t: {"points": r["points_scored"], "h2h_win": r["h2h_win"], "median_win": r["median_win"],
                                    "override": False} for t, r in teams.items()}
        doc["week_1"][QF]["points"] = 181.5
        doc["week_2"][CB].update(points=150.41, override=True)
        with open(os.path.join(self.td.name, "data", "current", "banked_scores.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def test_the_points_are_the_leagues(self):
        from webui.results import rescaled_weeks, week_results
        self.bank()
        res = week_results(self.root)
        self.assertEqual(res[1][QF]["points_scored"], 181.5)
        self.assertEqual(res[2][CB]["points_scored"], 150.41)
        self.assertEqual((res[2][QF]["h2h_win"], res[2][CB]["h2h_win"]), (0.0, 1.0))
        self.assertFalse(res[2][QF]["rescored"], "the score shown is the league's, so it agrees with the result")
        self.assertEqual(rescaled_weeks(self.root), set(), "no caveat: nothing shown is on another scale")
        self.assertEqual(res[1][NW]["points_scored"], 140.0)

    def test_an_unverified_file_is_not_shown_as_the_leagues(self):
        from webui.results import rescaled_weeks, week_results
        self.bank(verified=False)
        res = week_results(self.root)
        self.assertEqual(res[1][QF]["points_scored"], 180.0)
        self.assertEqual(res[2][CB]["points_scored"], 144.19)
        self.assertEqual(rescaled_weeks(self.root), {1, 2})

    def test_the_week_page_and_the_luck_page_read_them(self):
        from webui.luck import _inputs
        from webui.objects import week_games
        from fantasy_sim.config import MY_TEAM
        self.bank()
        g = next(x for x in week_games(self.root, 2, MY_TEAM)["games"] if QF in (x["a"], x["b"]))
        self.assertEqual({g["a"]: g["pts_a"], g["b"]: g["pts_b"]}[CB], 150.41)
        scores = _inputs(self.root)[0]
        self.assertEqual(scores[1][QF], 181.5)


if __name__ == "__main__":
    unittest.main()

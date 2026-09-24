"""T5: the "what to watch" brief, which was assembled by hand twice in one evening.

Every input is mechanical -- both lineups grouped by NFL game, the Vegas line per game, the
stacks, the designations, the windiest game -- and it was rebuilt from scratch the second
time because a pending trade and the opponent's empty DL slot changed the answer.

NEW CAPABILITY, NOT A DEFECT FIX, so there is no red characterisation to separate and none
is claimed. The acceptance criterion from the backlog is what these tests assert: one
fixture carrying a three-man stack, one Questionable opponent starter, and one shared game
produces all five blocks. Every assertion below was confirmed load-bearing by mutating
`fantasy_sim/matchup_watch.py` (see the commit message for which mutations and what broke).

**The fixture had to be rebuilt once, and why is worth recording.** The first version put
13 starters across three NFL games and asserted exactly one three-man stack per side. That
is arithmetically impossible -- 13 men in 3 games averages 4.3 per game, so almost every
game is a stack -- and the test failed for that reason rather than because the code was
wrong. Six games (twelve NFL teams) is the smallest layout where "3 + five 2s" gives one
deliberate stack per side, one game the two rosters share a side of, and five they oppose
in. The assertion was not loosened to fit the old fixture.
"""
import logging
import unittest
from unittest.mock import patch

from fantasy_sim.matchup_watch import NO_GAME, game_key, render_lines, watch
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)

TEAMS = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis"]
ME, OPP = TEAMS[0], TEAMS[1]
WEEK = 3

# Six real NFL games. My three KC men are the one stack on my side; their three WAS men are
# the one on theirs. BAL vs DAL is the single game where both rosters own men on the SAME
# NFL team (correlated); the other five have the two rosters on opposite sides (opposed).
GAMES = {"KC": "MIA", "MIA": "KC", "BAL": "DAL", "DAL": "BAL", "SF": "SEA", "SEA": "SF",
         "BUF": "NYJ", "NYJ": "BUF", "GB": "CHI", "CHI": "GB", "PHI": "WAS", "WAS": "PHI"}
IMPLIED = {"KC": 27.5, "MIA": 20.0, "BAL": 24.0, "DAL": 22.5, "SF": 23.0, "SEA": 21.0,
           "BUF": 26.0, "NYJ": 17.0, "GB": 23.5, "CHI": 19.0, "PHI": 25.0, "WAS": 24.5}
SPREAD = {"KC": -7.5, "MIA": 7.5, "BAL": -1.5, "DAL": 1.5, "SF": -2.0, "SEA": 2.0,
          "BUF": -9.0, "NYJ": 9.0, "GB": -4.5, "CHI": 4.5, "PHI": -0.5, "WAS": 0.5}
# Weather belongs to the GAME, so both sides of a pair carry the same forecast. KC vs MIA
# is the windy one, which is the "windiest game" the hand-built brief always named.
#
# `precip_prob` IS A PERCENTAGE, 0-100. sync.game_window_weather stores Open-Meteo's
# `precipitation_probability` (window max) unscaled, and the live file ranges 0.0 to 35.0.
# The first version of this fixture used fractions, so every test passed while the live
# run printed "2800%". Units are pinned below precisely because getting them wrong here
# made the suite agree with a broken renderer.
_W = {("KC", "MIA"): (14.0, 0.2, 60.0), ("BAL", "DAL"): (3.0, 0.0, 5.0),
      ("SF", "SEA"): (8.0, 0.1, 30.0), ("BUF", "NYJ"): (11.0, 0.0, 10.0),
      ("GB", "CHI"): (6.0, 0.0, 5.0), ("PHI", "WAS"): (4.0, 0.0, 0.0)}
WEATHER = {t: v for pair, v in _W.items() for t in pair}

# name -> (pos, mean, nfl team, injury status).
MINE = [("QB1", "QB", 20.0, "KC", None), ("WR1", "WR", 16.0, "KC", None),
        ("TE1", "TE", 12.0, "KC", None),
        ("RB1", "RB", 15.0, "BAL", None), ("DB1", "DB", 8.5, "BAL", None),
        ("RB2", "RB", 11.0, "SF", None), ("WR2", "WR", 10.0, "SF", None),
        ("K1", "K", 8.0, "BUF", None), ("DL1", "DL", 9.0, "BUF", None),
        ("LB1", "LB", 9.5, "GB", None), ("WR3", "WR", 9.0, "GB", None),
        ("RB3", "RB", 8.0, "PHI", None), ("TE2", "TE", 7.0, "PHI", None)]
THEIRS = [("oQB", "QB", 18.0, "MIA", None), ("oWR", "WR", 14.0, "MIA", None),
          ("oRB", "RB", 14.5, "BAL", None), ("oK", "K", 8.0, "BAL", None),
          ("oTE", "TE", 11.0, "SEA", None), ("oWR2", "WR", 9.5, "SEA", None),
          ("oDL", "DL", 9.0, "NYJ", "Questionable"), ("oLB", "LB", 9.0, "NYJ", None),
          ("oDB", "DB", 8.0, "CHI", None), ("oWR3", "WR", 8.5, "CHI", None),
          ("oRB2", "RB", 16.0, "WAS", None), ("oRB3", "RB", 14.0, "WAS", None),
          ("oTE2", "TE", 12.0, "WAS", None)]
# On their BENCH, not in the starter list above: the one DAL man in the fixture. He exists
# so the "both" classification -- a game that is correlated for one pair of men and hedged
# for another -- can be exercised without distorting the six-game layout the other tests
# depend on.
THEIR_BENCH = [("oDAL", "WR", 6.0, "DAL", None)]
FILLER = [("QB", 15.0), ("RB", 13.0), ("RB", 12.0), ("WR", 12.0), ("WR", 11.0),
          ("TE", 9.0), ("K", 7.0), ("DL", 8.0), ("LB", 8.0), ("DB", 8.0),
          ("RB", 7.0), ("WR", 7.0), ("WR", 6.0)]


def _fs():
    base, rosters, pid = {}, {}, 5000
    for label, spec in ((ME, MINE), (OPP, THEIRS + THEIR_BENCH)):
        entries = []
        for nm, pos, mu, nfl, status in spec:
            pid += 1
            base[nm] = {"mean": mu, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
                        "team": nfl, "bye": 0, "player_id": str(pid)}
            if status:
                base[nm]["injury_status"] = status
            entries.append({"name": nm, "pos": pos, "team": nfl})
        rosters[label] = entries
    for t in TEAMS[2:]:
        entries = []
        for i, (pos, mu) in enumerate(FILLER):
            pid += 1
            nm = f"{t[:2]}_{pos}_{i}"
            base[nm] = {"mean": mu, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
                        "team": "BAL", "bye": 0, "player_id": str(pid)}
            entries.append({"name": nm, "pos": pos, "team": "BAL"})
        rosters[t] = entries
    vegas = {"_meta": {"week": WEEK, "source": "odds_api", "fetched_at": "x"}}
    for nfl, opp in GAMES.items():
        wind, pin, pprob = WEATHER[nfl]
        vegas[nfl] = {"total": IMPLIED[nfl], "spread": SPREAD[nfl], "opponent": opp,
                      "wind_mph": wind, "precip_in": pin, "precip_prob": pprob,
                      "weather_source": "forecast"}
    return {
        LEAGUE_STATE_FILE: {"current_week": WEEK},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: vegas,
        LIVE_ROSTERS_FILE: rosters,
        BASELINES_FILE: base,
        TEAM_RATINGS_FILE: {n: {"off_rating": 22} for n in GAMES},
        DEFENSIVE_RATINGS_FILE: {n: {"points_allowed_estimate": 21.5, "games_sampled": 0}
                                 for n in GAMES},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[TEAMS[0], TEAMS[1]], [TEAMS[2], TEAMS[3]]]] * 14,
        NFL_SCHEDULE_FILE: {str(w): dict(GAMES) for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: {},
    }


def _engine():
    fs = _fs()
    pre = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.ERROR)
    with patch("os.path.exists", side_effect=lambda p: p in fs), \
         patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]):
        e = FantasySimulationEngine()
    logging.getLogger().setLevel(pre)
    return e


MY_STARTERS = [n for n, *_ in MINE]
OPP_STARTERS = [n for n, *_ in THEIRS]


class TestGameKey(unittest.TestCase):
    def test_both_sides_of_a_game_produce_the_same_key(self):
        self.assertEqual(game_key("KC", "MIA"), game_key("MIA", "KC"))

    def test_a_player_with_no_game_is_not_given_one(self):
        for args in (("FA", "KC"), ("KC", "FA"), ("KC", None), (None, "KC"), ("KC", "KC")):
            self.assertEqual(game_key(*args), NO_GAME, args)


class TestTheFiveBlocks(unittest.TestCase):
    """The backlog's acceptance criterion, one test per block."""

    @classmethod
    def setUpClass(cls):
        cls.w = watch(_engine(), ME, OPP, WEEK, MY_STARTERS, OPP_STARTERS)

    def test_block_1_games_carry_the_market_and_the_weather(self):
        g = next(x for x in self.w["games"] if x["game"] == "KC vs MIA")
        self.assertAlmostEqual(g["game_total"], IMPLIED["KC"] + IMPLIED["MIA"], places=6)
        self.assertAlmostEqual(g["wind_mph"], 14.0)
        self.assertAlmostEqual(g["precip_prob"], 60.0,
                               msg="precip_prob is a PERCENTAGE and is carried through "
                                   "unscaled; sync stores Open-Meteo's 0-100 value")
        self.assertEqual(g["weather_source"], "forecast")

    def test_a_game_only_one_roster_touches_still_gets_its_full_total(self):
        """Found on the first live run. The implied total is per NFL TEAM, so a game total
        needs BOTH sides -- and looking up only the teams that happen to field a starter
        left seven of twelve games on the real page with no total at all."""
        w = watch(_engine(), ME, OPP, WEEK, ["QB1", "WR1", "TE1"], [])   # KC only, no MIA
        g = w["games"][0]
        self.assertEqual(g["game"], "KC vs MIA")
        self.assertAlmostEqual(g["game_total"], IMPLIED["KC"] + IMPLIED["MIA"], places=6)

    def test_the_rendered_precipitation_is_not_rescaled(self):
        w = watch(_engine(), ME, OPP, WEEK, ["QB1"], [])
        self.assertIn("60%", " ".join(render_lines(w)))

    def test_a_game_with_only_one_side_PRICED_reports_no_game_total(self):
        """Distinct from the test above: there, the other side simply had no starter and
        its line is still on file. Here the LINE itself is missing. Half a game's implied
        points is not a game total, and printing it as one is wrong by about 21 points."""
        from fantasy_sim import matchup_watch
        e = _engine()
        with patch.object(matchup_watch, "_env",
                          side_effect=lambda eng, wk, t: ({"opponent": "MIA", "total": 27.5}
                                                          if t == "KC" else {})):
            w = watch(e, ME, OPP, WEEK, ["QB1"], [])
        self.assertIsNone(w["games"][0]["game_total"])

    def test_block_2_a_three_man_stack_is_named_with_its_sum(self):
        mine = [x for x in self.w["stacks"] if x["side"] == "mine"]
        theirs = [x for x in self.w["stacks"] if x["side"] == "theirs"]
        self.assertEqual(len(mine), 1, "one game holds three of my starters, five hold two")
        self.assertEqual(mine[0]["game"], "KC vs MIA")
        self.assertEqual(sorted(mine[0]["players"]), ["QB1", "TE1", "WR1"])
        self.assertGreater(mine[0]["sum"], 0)
        self.assertEqual(len(theirs), 1, "the opponent's stack is found too, not just mine")
        self.assertEqual(theirs[0]["game"], "PHI vs WAS")

    def test_block_3_designations_cover_BOTH_rosters(self):
        """B4's rule. Checking only your own roster is how you get surprised at 12:58."""
        d = self.w["designations"]
        self.assertEqual([(x["name"], x["side"], x["flag"]) for x in d],
                         [("oDL", "theirs", "Questionable")])

    def test_block_4_a_shared_game_is_classified_correlated_or_opposed(self):
        kinds = {g["game"]: g["shared"] for g in self.w["shared_games"]}
        self.assertEqual(kinds.get("BAL vs DAL"), "correlated",
                         "both rosters own men on BAL: we rise and fall together there")
        # Game keys are SORTED, not home@away: the Vegas payload names an opponent but not
        # which side is home, and inventing a direction would claim what the data does not.
        self.assertEqual(sorted(k for k, v in kinds.items() if v == "opposed"),
                         ["BUF vs NYJ", "CHI vs GB", "KC vs MIA", "PHI vs WAS",
                          "SEA vs SF"])

    def test_a_game_only_one_roster_touches_is_not_shared(self):
        from fantasy_sim import matchup_watch
        w = watch(_engine(), ME, OPP, WEEK, ["QB1", "WR1"], ["oRB"])
        by = {g["game"]: g["shared"] for g in w["games"]}
        self.assertIsNone(by["KC vs MIA"], "only my men are in that game")
        self.assertEqual(matchup_watch.NO_GAME, NO_GAME)

    def test_two_rosters_on_the_same_nfl_team_only_are_correlated(self):
        w = watch(_engine(), ME, OPP, WEEK, ["RB1"], ["oRB"])      # both on BAL
        self.assertEqual(w["games"][0]["shared"], "correlated")

    def test_two_rosters_on_opposite_sides_only_are_opposed(self):
        w = watch(_engine(), ME, OPP, WEEK, ["RB2"], ["oTE"])      # SF vs SEA -> "SEA vs SF"
        self.assertEqual(w["games"][0]["shared"], "opposed")

    def test_both_at_once_is_reported_as_both(self):
        """One roster on each side AND a shared NFL team is a real shape: the game is
        correlated for the men who share a team and hedged for the rest."""
        w = watch(_engine(), ME, OPP, WEEK, ["RB1"], ["oRB", "oDAL"])  # BAL / BAL + DAL
        self.assertEqual(w["games"][0]["shared"], "both")

    def test_block_5_their_losing_script_is_the_game_they_lean_on_most(self):
        ls = self.w["their_losing_script"]
        self.assertIsNotNone(ls)
        sums = {g["game"]: g["theirs_sum"] for g in self.w["games"] if g["theirs"]}
        self.assertEqual(ls["game"], "PHI vs WAS",
                         "three of their starters and their three biggest means are there")
        self.assertEqual(ls["game"], max(sums, key=sums.get))
        self.assertAlmostEqual(ls["share"], sums[ls["game"]] / sum(sums.values()), places=3)


class TestItGroupsWithoutChangingAnyNumber(unittest.TestCase):
    def test_every_starters_expectation_is_week_expectation_verbatim(self):
        from fantasy_sim.decisions import week_expectation
        e = _engine()
        w = watch(e, ME, OPP, WEEK, MY_STARTERS, OPP_STARTERS)
        got = {r["name"]: r["expected"] for g in w["games"] for r in g["mine"] + g["theirs"]}
        for nm in MY_STARTERS + OPP_STARTERS:
            self.assertAlmostEqual(got[nm], week_expectation(e, nm, WEEK), places=9, msg=nm)

    def test_the_side_sums_are_the_side_totals(self):
        w = watch(_engine(), ME, OPP, WEEK, MY_STARTERS, OPP_STARTERS)
        mine = sum(g["mine_sum"] for g in w["games"])
        each = sum(r["expected"] for g in w["games"] for r in g["mine"])
        self.assertAlmostEqual(mine, each, places=1)

    def test_the_weather_is_labelled_as_unmodelled(self):
        """F55 is open. A reader who treats the wind as an extra discount double-counts
        what the Vegas total already prices."""
        w = watch(_engine(), ME, OPP, WEEK, MY_STARTERS, OPP_STARTERS)
        self.assertIn("not modelled", w["weather_note"])
        self.assertIn("F55", w["weather_note"])


class TestRendering(unittest.TestCase):
    def test_all_five_blocks_reach_the_text(self):
        w = watch(_engine(), ME, OPP, WEEK, MY_STARTERS, OPP_STARTERS)
        text = "\n".join(render_lines(w))
        for marker in ("WHAT TO WATCH", "KC vs MIA", "STACK:", "DESIGNATION",
                       "SHARED:", "THEIR LOSING SCRIPT:"):
            self.assertIn(marker, text, marker)

    def test_the_overlay_is_the_only_way_a_display_name_changes(self):
        """The report is pseudonymous on the runner; the library never knows a real name,
        it is handed a mapping by the caller."""
        w = watch(_engine(), ME, OPP, WEEK, MY_STARTERS, OPP_STARTERS)
        text = "\n".join(render_lines(w, name_of={ME: "Local Alias"}))
        self.assertIn("Local Alias", text)
        self.assertNotIn(ME, text)

    def test_a_missing_line_renders_without_raising(self):
        """Vegas can be absent or a team can have no game; the brief must still print."""
        from fantasy_sim import matchup_watch
        with patch.object(matchup_watch, "_env", return_value={}):
            w = watch(_engine(), ME, OPP, WEEK, MY_STARTERS, OPP_STARTERS)
        self.assertEqual([g["game"] for g in w["games"]], [NO_GAME])
        self.assertTrue(render_lines(w))


class TestItReachesTheWeeklyReport(unittest.TestCase):
    """T5 asks for the brief IN the report's matchup section, not only in the tool."""

    @classmethod
    def setUpClass(cls):
        cls.w = watch(_engine(), ME, OPP, WEEK, MY_STARTERS, OPP_STARTERS)

    def _report(self, with_watch):
        from tests.test_weekly_report import _fixture_results
        res = _fixture_results()
        if with_watch:
            res["matchup"]["watch"] = self.w
        return {"status": "OK", "failed_step": None, "error": None, "results": res,
                "started_at": "t0", "finished_at": "t1"}

    def test_the_markdown_matchup_section_carries_all_five_blocks(self):
        from fantasy_sim.weekly_report import render_digest
        md = render_digest(self._report(True), ME, WEEK)
        for marker in ("What to watch", "KC vs MIA", "**Stack:**", "**Designation",
                       "**Shared:**", "**Their losing script:**", "not modelled"):
            self.assertIn(marker, md, marker)

    def test_the_html_matchup_section_carries_them_too(self):
        from fantasy_sim.weekly_report import render_html
        html = render_html(self._report(True), ME, WEEK)
        for marker in ("What to watch", "KC vs MIA", "<b>Stack:</b>",
                       "<b>Their losing script:</b>"):
            self.assertIn(marker, html, marker)

    def test_a_record_without_the_brief_renders_exactly_as_before(self):
        """Every matchup record written before T5 has no `watch` key. Back-compat is not
        optional: the report reads whatever the tool last wrote to data/decisions/."""
        from fantasy_sim.weekly_report import render_digest, render_html
        self.assertNotIn("What to watch", render_digest(self._report(False), ME, WEEK))
        self.assertNotIn("What to watch", render_html(self._report(False), ME, WEEK))

    def test_the_two_renderers_show_the_same_games(self):
        """One row builder, so Markdown and HTML cannot drift into disagreeing about which
        games this matchup turns on."""
        from fantasy_sim.weekly_report import _watch_rows
        self.assertEqual([r[0] for r in _watch_rows(self.w)],
                         [g["game"] for g in self.w["games"]])


if __name__ == "__main__":
    unittest.main()

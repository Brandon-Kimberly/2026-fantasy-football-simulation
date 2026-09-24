"""C2: the matchup tool scores an opponent's unfillable slot as ZERO.

On 2026-09-23 `scripts.matchup_lineup` printed this week's opponent with **12 starters**
against a 13-slot league — their DL slot was empty, because they owned no DL — and
reported 81.9% to win with a +48.9 margin. Adding a plausible DL to their roster by hand
gave **79.2% / +43.1**. The tool overstated the edge by 2.7 points of win probability and
6 points of margin, for the whole week, because it assumed an opponent would take a zero.

**A real opponent never takes a zero.** They claim somebody off the wire before kickoff,
which is exactly what the season simulation already assumes: `run_simulation` injects
`STREAMER_<POS>_0` for any unfillable slot and scores it
`max(0, N(m_str, 2.2))` where `m_str = max(replacement * 0.8, BASE_STREAMER_MEANS[pos])`.
The matchup tool and the season simulation disagreed about the same roster.

**USE THE ENGINE'S OWN STREAMER, NOT THE BEST FREE AGENT.** C2's trap: claiming the best
available man is a roster decision the opponent has not made, and assuming it would
overstate them in the other direction. The streamer constant is the engine's existing
assumption about what a hole is worth, so borrowing it makes the two tools agree by
construction. (Whether that constant is right is a separate item — C5 measures it.)

**EVERY ROSTER, NOT JUST THE OPPONENT.** `p_beat_median` is computed against the other six
teams' totals, so a hole anywhere in the league biases the median low and flatters
everyone's "beat the median" number. The same fill has to apply to all of them.

**`league_week_outlook` shares the defect** — it solves with the same
`_solve_optimal_assignment` call and discards `unfilled` the same way. It drives the
League table in the weekly report, so the same understatement reaches the digest.

Written before the change and confirmed failing (rule 1).
"""
import logging
import unittest
from unittest.mock import patch

from fantasy_sim.config import BASE_STREAMER_MEANS
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)

TEAMS = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis"]
ME, OPP = TEAMS[0], TEAMS[1]


def _p(mean, pos, pid):
    return {"mean": mean, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
            "team": "DET", "bye": 0, "player_id": pid}


def _fs(strip_opponent_dl=True):
    base, rosters = {}, {}
    filler = [("QB", 18.0), ("RB", 17.0), ("RB", 15.0), ("WR", 16.0), ("WR", 13.0),
              ("TE", 12.0), ("K", 9.0), ("DL", 9.0), ("LB", 9.0), ("DB", 9.0),
              ("RB", 11.0), ("WR", 10.5), ("WR", 10.0)]
    pid = 3000
    for t in TEAMS:
        entries = []
        for i, (pos, mu) in enumerate(filler):
            pid += 1
            n = f"{t[:2]}_{pos}_{i}"
            base[n] = _p(mu, pos, str(pid))
            entries.append({"name": n, "pos": pos, "team": "DET"})
        rosters[t] = entries
    if strip_opponent_dl:
        rosters[OPP] = [x for x in rosters[OPP] if not x["name"].endswith("_DL_7")]
    return {
        LEAGUE_STATE_FILE: {"current_week": 3},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: {"_meta": {"week": 3, "source": "odds_api", "fetched_at": "x"},
                     "DET": {"total": 22.0, "spread": 0.0, "opponent": "CHI"},
                     "CHI": {"total": 22.0, "spread": 0.0, "opponent": "DET"}},
        LIVE_ROSTERS_FILE: rosters,
        BASELINES_FILE: base,
        TEAM_RATINGS_FILE: {"DET": {"off_rating": 22}, "CHI": {"off_rating": 22}},
        DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                 "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[TEAMS[0], TEAMS[1]], [TEAMS[2], TEAMS[3]]]] * 14,
        NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: {},
    }


def _engine(strip_opponent_dl=True):
    fs = _fs(strip_opponent_dl)
    pre = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.ERROR)
    with patch("os.path.exists", side_effect=lambda p: p in fs), \
         patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]):
        e = FantasySimulationEngine()
    logging.getLogger().setLevel(pre)
    return e


class TestTheFixtureReproducesTheLiveShape(unittest.TestCase):
    """Sanity: without this, a green run proves nothing."""

    def test_the_opponent_really_cannot_fill_DL(self):
        from fantasy_sim.market import roster_gaps
        e = _engine()
        self.assertNotIn("DL", roster_gaps(e, OPP, weeks=(3,))[3]["starters"],
                         "fixture must leave the opponent with an unfillable DL slot")


class TestTheHoleIsFilledWithTheEnginesStreamer(unittest.TestCase):
    def test_the_result_names_the_streamed_slot(self):
        from fantasy_sim.decisions import matchup_lineups
        r = matchup_lineups(_engine(), ME, 3, sims=400, seed=5)
        slots = [s["slot"] for s in r.get("opponent_streamers") or []]
        self.assertEqual(slots, ["DL"],
                         "the caller has to be able to say 'modelled at the streamer, "
                         "not zero'")

    def test_the_streamer_mean_is_the_engines_own_assumption(self):
        from fantasy_sim.decisions import matchup_lineups
        e = _engine()
        r = matchup_lineups(e, ME, 3, sims=400, seed=5)
        got = (r["opponent_streamers"] or [])[0]
        expected = max(e.replacement_levels.get("DL", 4.0) * 0.8,
                       BASE_STREAMER_MEANS.get("DL", 8.0))
        self.assertAlmostEqual(got["mean"], expected, places=6,
                               msg="borrow the engine's streamer so the matchup tool and "
                                   "the season simulation agree by construction")

    def test_the_opponents_total_actually_includes_the_streamer(self):
        """The defect, measured on the number that carries it.

        A first version of this test compared P(win) with and without the hole and
        asserted the gap was small -- it passed BEFORE the fix, because this fixture's DL
        is only 9.0 against ~170-point totals, so a missing starter moves P(win) by less
        than the threshold. It proved nothing. This asserts the opponent's implied mean
        total directly, which is where the streamer must show up or not at all.
        """
        from fantasy_sim.decisions import matchup_lineups
        e = _engine(strip_opponent_dl=True)
        r = matchup_lineups(e, ME, 3, sims=3000, seed=11)
        c = r["constructions"]["max_mean"]
        opp_mean = c["mean"] - c["margin_mean"]          # my mean minus (mine - theirs)
        twelve = sum(x["expected"] for x in r["opponent_lineup"])
        m_str = max(e.replacement_levels.get("DL", 4.0) * 0.8,
                    BASE_STREAMER_MEANS.get("DL", 8.0))
        self.assertGreater(opp_mean, twelve + 0.5 * m_str,
                           f"their 12 starters project {twelve:.1f}; the total came to "
                           f"{opp_mean:.1f}, so the {m_str:.1f} streamer is missing")

    def test_a_full_opponent_reports_no_streamers(self):
        from fantasy_sim.decisions import matchup_lineups
        r = matchup_lineups(_engine(strip_opponent_dl=False), ME, 3, sims=400, seed=5)
        self.assertEqual(r.get("opponent_streamers") or [], [])

    def test_the_streamer_is_not_smuggled_into_the_lineup_as_a_player(self):
        """It is not a rostered man and must not appear where a name is expected --
        printing `STREAMER_DL_0` as an opponent starter would read as a real claim."""
        from fantasy_sim.decisions import matchup_lineups
        r = matchup_lineups(_engine(), ME, 3, sims=400, seed=5)
        for nm in r.get("opponent_lineup") or []:
            self.assertNotIn("STREAMER", str(nm))


class TestTheMedianUsesFilledRostersToo(unittest.TestCase):
    """`p_beat_median` is computed against the other teams' totals. A hole anywhere in the
    league biases that median low and flatters everyone's beat-the-median number."""

    def test_a_hole_on_a_THIRD_team_is_also_filled(self):
        from fantasy_sim.decisions import matchup_lineups
        fs = _fs(strip_opponent_dl=False)
        third = TEAMS[2]
        fs[LIVE_ROSTERS_FILE][third] = [x for x in fs[LIVE_ROSTERS_FILE][third]
                                        if not x["name"].endswith("_DL_7")]
        pre = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        with patch("os.path.exists", side_effect=lambda p: p in fs), \
             patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]):
            e = FantasySimulationEngine()
        logging.getLogger().setLevel(pre)
        r = matchup_lineups(e, ME, 3, sims=400, seed=5)
        self.assertIn(third, r.get("streamed_teams") or {},
                      "a hole on a bystander still moves the league median")


class TestLeagueWeekOutlookSharesTheFill(unittest.TestCase):
    """The same defect reaches the weekly report's League table."""

    def test_a_team_with_a_hole_is_not_scored_as_taking_a_zero(self):
        from fantasy_sim.decisions import league_week_outlook
        holed = league_week_outlook(_engine(strip_opponent_dl=True), week=3,
                                    sims=2500, seed=9)
        full = league_week_outlook(_engine(strip_opponent_dl=False), week=3,
                                   sims=2500, seed=9)

        def expected_for(res, team):
            for m in res["matchups"]:
                if m["a"] == team:
                    return m["a_expected"]
                if m["b"] == team:
                    return m["b_expected"]
            raise AssertionError(f"{team} missing from the outlook")

        gap = expected_for(full, OPP) - expected_for(holed, OPP)
        self.assertLess(gap, 6.0,
                        "an unfillable slot must cost about a streamer, not a whole "
                        f"starter; the League table lost {gap:.1f} points")


if __name__ == "__main__":
    unittest.main()

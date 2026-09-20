"""scripts.live_matchup: in-game tracking (2026-09-14, after week 1 was tracked all day
from throwaway scratch scripts).

The arithmetic that matters is the remaining-time model -- points banked are certain,
and only the rest of the clock carries variance. Every function below is pure; the two
network calls sit behind an injectable `fetch` seam and are exercised by running the
tool, not by the suite (hermetic by design, like every other test here).

One number is deliberately pinned by a hand-computed case rather than by re-deriving the
formula in the assertion: a test that recomputes what it is testing proves nothing.
"""
import math
import unittest

from scripts.live_matchup import (
    clock_fraction, game_clocks, median_leg, remaining, team_states, win_probability,
)


class TestClockFraction(unittest.TestCase):
    def test_pregame_is_the_whole_game_and_final_is_none_of_it(self):
        self.assertEqual(clock_fraction(None, None, "pre", False), 1.0)
        self.assertEqual(clock_fraction(4, "0:00", "post", True), 0.0)

    def test_halftime_is_exactly_half(self):
        # end of Q2: 0 seconds left in the period, two full quarters to come = 1800s.
        self.assertAlmostEqual(clock_fraction(2, "0:00", "in", False), 0.5)

    def test_midway_through_the_first_quarter(self):
        # 12:00 left in Q1 -> 720s + 3*900s = 3420s of 3600.
        self.assertAlmostEqual(clock_fraction(1, "12:00", "in", False), 3420 / 3600)

    def test_overtime_is_capped_at_ten_minutes_not_another_quarter(self):
        """A team in OT has already played a full game; crediting it 15 more minutes
        would give it more upside than a team that has not kicked off."""
        self.assertAlmostEqual(clock_fraction(5, "10:00", "in", False), 600 / 3600)
        self.assertAlmostEqual(clock_fraction(5, "5:00", "in", False), 300 / 3600)

    def test_a_garbled_clock_does_not_crash_and_assumes_a_full_period(self):
        self.assertAlmostEqual(clock_fraction(3, None, "in", False), (900 + 900) / 3600)
        self.assertAlmostEqual(clock_fraction(3, "nonsense", "in", False), 1800 / 3600)

    def test_the_fraction_never_leaves_the_unit_interval(self):
        for period in (1, 2, 3, 4, 5, 6):
            for clk in ("15:00", "0:00", "99:99"):
                f = clock_fraction(period, clk, "in", False)
                self.assertGreaterEqual(f, 0.0)
                self.assertLessEqual(f, 1.0)


class TestRemaining(unittest.TestCase):
    def test_expectation_scales_with_time_and_sd_with_its_square_root(self):
        """Scoring accrues over game time, so variance is linear in time and sd is not.
        Halving the clock halves the mean but only cuts sd by ~29%."""
        mu, sd = remaining(20.0, 8.0, 0.5)
        self.assertAlmostEqual(mu, 10.0)
        self.assertAlmostEqual(sd, 8.0 * math.sqrt(0.5))

    def test_a_finished_player_contributes_no_uncertainty(self):
        self.assertEqual(remaining(20.0, 8.0, 0.0), (0.0, 0.0))


class TestWinProbability(unittest.TestCase):
    def test_a_dead_heat_is_a_coin_flip(self):
        self.assertAlmostEqual(win_probability(150.0, 10.0, 150.0, 10.0), 0.5)

    def test_one_sigma_ahead_is_the_normal_tail(self):
        # margin +10 with margin sd 10 (hypot(6,8)) -> Phi(1) = 0.8413.
        self.assertAlmostEqual(win_probability(160.0, 6.0, 150.0, 8.0), 0.8413, places=3)

    def test_inflating_the_margin_sd_pulls_toward_a_coin_flip(self):
        tight = win_probability(160.0, 6.0, 150.0, 8.0)
        loose = win_probability(160.0, 6.0, 150.0, 8.0, inflate=1.6)
        self.assertLess(loose, tight)
        self.assertGreater(loose, 0.5)

    def test_with_nothing_left_to_play_the_answer_is_certain(self):
        self.assertEqual(win_probability(160.0, 0.0, 150.0, 0.0), 1.0)
        self.assertEqual(win_probability(150.0, 0.0, 160.0, 0.0), 0.0)


def _event(home, away, state, completed, period=None, clock=None):
    return {"competitions": [{
        "status": {"period": period, "displayClock": clock,
                   "type": {"state": state, "completed": completed}},
        "competitors": [{"team": {"abbreviation": home}}, {"team": {"abbreviation": away}}],
    }]}


class TestGameClocks(unittest.TestCase):
    PAYLOAD = {"events": [
        _event("DET", "NO", "in", False, 2, "1:41"),
        _event("WSH", "PHI", "pre", False),
        _event("LAR", "SF", "post", True, 4, "0:00"),
    ]}

    def _clocks(self):
        return game_clocks(1, fetch=lambda _url: self.PAYLOAD)

    def test_each_team_carries_its_own_game_state(self):
        c = self._clocks()
        self.assertEqual(c["LAR"][1], "final")
        self.assertEqual(c["PHI"][1], "pregame")
        self.assertEqual(c["DET"][0], c["NO"][0])
        self.assertAlmostEqual(c["DET"][0], (101 + 900 * 2) / 3600)

    def test_the_sleeper_spelling_of_a_team_resolves_to_the_same_game(self):
        """ESPN says WSH, Sleeper says WAS. Without the alias the lookup misses and the
        default credits a FULL game -- silently handing a finished player fresh upside."""
        c = self._clocks()
        self.assertIn("WAS", c)
        self.assertEqual(c["WAS"], c["WSH"])


ROSTERS = [{"roster_id": 1}, {"roster_id": 2}]
PLAYERS = {
    "10": {"first_name": "Done", "last_name": "Back", "position": "RB", "team": "LAR"},
    "20": {"first_name": "Yet", "last_name": "Toplay", "position": "WR", "team": "PHI"},
    "30": {"first_name": "Mid", "last_name": "Game", "position": "TE", "team": "DET"},
}
# pid -> week-adjusted mean and full predictive sd, as week_projections() builds it.
# NOT the raw baselines file: the tracker must never read that again (F50).
BASELINES = {
    "10": {"mean": 12.0, "sd": 6.0},
    "20": {"mean": 10.0, "sd": 5.0},
    "30": {"mean": 8.0, "sd": 4.0},
}
CLOCKS = {"LAR": (0.0, "final"), "PHI": (1.0, "pregame"), "DET": (0.5, "Q2 0:00")}
MATCHUPS = [
    {"roster_id": 1, "matchup_id": 1, "points": 31.5, "starters": ["10", "20", "30"],
     "players": ["10", "20", "30", "40"], "players_points": {"10": 31.5, "20": 0.0, "30": 0.0}},
    {"roster_id": 2, "matchup_id": 1, "points": 0.0, "starters": ["20"],
     "players": ["20"], "players_points": {"20": 0.0}},
]


class TestTeamStates(unittest.TestCase):
    def setUp(self):
        self.states = team_states(MATCHUPS, ROSTERS, CLOCKS, PLAYERS, BASELINES)

    def test_banked_points_are_certain_and_only_the_remainder_carries_variance(self):
        """Hand-computed: the finished RB adds nothing, the pregame WR adds its whole
        10.0 +/- 5.0, the halftime TE adds 4.0 +/- 4*sqrt(.5). Projection = 31.5 + 14.0."""
        st = next(s for s in self.states.values() if s["banked"] == 31.5)
        self.assertAlmostEqual(st["rem_mu"], 10.0 + 4.0)
        self.assertAlmostEqual(st["proj"], 45.5)
        self.assertAlmostEqual(st["rem_sd"], math.hypot(5.0, 4.0 * math.sqrt(0.5)))
        self.assertEqual(st["left"], 2, "the finished RB is not 'still to play'")

    def test_every_starter_keeps_its_own_sd_for_the_joint_draw(self):
        st = next(s for s in self.states.values() if s["banked"] == 31.5)
        self.assertEqual({r["name"]: r["sd"] for r in st["rows"]},
                         {"Done Back": 6.0, "Yet Toplay": 5.0, "Mid Game": 4.0})

    def test_a_player_with_no_baseline_is_carried_at_zero_not_dropped(self):
        """A just-added free agent has no baseline yet. Treating him as absent from the
        lineup would silently understate the roster; zero is the honest placeholder."""
        matchups = [dict(MATCHUPS[0], starters=["10", "99"], players=["10", "99"])]
        players = dict(PLAYERS, **{"99": {"first_name": "New", "last_name": "Guy",
                                          "position": "WR", "team": "PHI"}})
        st = next(iter(team_states(matchups, ROSTERS, CLOCKS, players, BASELINES).values()))
        self.assertEqual(len(st["rows"]), 2)
        self.assertAlmostEqual(st["rem_mu"], 0.0)


class TestMedianLeg(unittest.TestCase):
    def test_the_stronger_roster_beats_the_median_more_often(self):
        states = {
            "Strong": {"banked": 100.0, "rows": [
                {"mean": 40.0, "sd": 5.0, "frac": 1.0}], "mid": 1},
            "Middle": {"banked": 100.0, "rows": [
                {"mean": 20.0, "sd": 5.0, "frac": 1.0}], "mid": 1},
            "Weak": {"banked": 100.0, "rows": [
                {"mean": 5.0, "sd": 5.0, "frac": 1.0}], "mid": 2},
        }
        med, _m, _n = median_leg(states, sims=4000, seed=7)
        self.assertGreater(med["Strong"], 0.9)
        self.assertLess(med["Weak"], 0.1)

    def test_a_settled_league_gives_certain_answers(self):
        """Nothing left to play: the median is a constant and every probability is 0 or 1."""
        states = {
            "High": {"banked": 200.0, "rows": [], "mid": 1},
            "Mid": {"banked": 150.0, "rows": [], "mid": 1},
            "Low": {"banked": 100.0, "rows": [], "mid": 2},
        }
        med, _m, _n = median_leg(states, sims=500, seed=3)
        self.assertEqual(med["High"], 1.0)
        self.assertEqual(med["Low"], 0.0)

    def test_the_draw_is_reproducible_under_a_seed(self):
        states = {"A": {"banked": 0.0, "rows": [{"mean": 20.0, "sd": 8.0, "frac": 1.0}], "mid": 1},
                  "B": {"banked": 0.0, "rows": [{"mean": 18.0, "sd": 8.0, "frac": 1.0}], "mid": 1},
                  "C": {"banked": 0.0, "rows": [{"mean": 16.0, "sd": 8.0, "frac": 1.0}], "mid": 2}}
        first, _m, _n = median_leg(states, sims=2000, seed=11)
        again, _m2, _n2 = median_leg(states, sims=2000, seed=11)
        self.assertEqual(first, again)


class _StubEngine:
    """Just enough engine for the projection builder: the baselines mapping."""

    def __init__(self, baselines):
        self.baselines = baselines


class TestLiveTrackerQuotesTheWeekAdjustedNumber(unittest.TestCase):
    """CHARACTERISATION (F50, 2026-09-20). The live tracker loaded
    player_baselines.json and used the raw season `mean` verbatim, which made it the
    only decision tool in the repo quoting a number no other tool quotes. It missed
    BOTH corrections that stand between that file and a week-specific answer:

      1. the engine's 4:1 Bayesian blend against observed scores, applied at init
      2. week_expectation()'s environment ratio and script multiplier

    Measured on live week-2 data the gap reached 9.9 points on ONE starter (Kenneth
    Walker: file 15.26, engine 18.73, week-adjusted 25.14) and it is not symmetric
    between rosters -- Nacua moves the other way (16.55 -> 15.52 -> 18.81) -- so the
    head-to-head margin was wrong, not just the totals. The sd was understated too:
    the tracker used std_aleatoric alone while decisions._sample_week_scores carries
    aleatoric AND epistemic, because for a single week the player's true mean is
    itself unknown.
    """

    PREGAME = [{"roster_id": 2, "matchup_id": 1, "points": 0.0, "starters": ["20"],
                "players": ["20"], "players_points": {"20": 0.0}}]

    def test_team_states_takes_a_pid_keyed_projection_map(self):
        """The fifth argument must be week-adjusted projections keyed by player id,
        not the raw baselines file keyed by name."""
        states = team_states(self.PREGAME, ROSTERS, CLOCKS, PLAYERS,
                             {"20": {"mean": 18.0, "sd": 7.0}})
        st = next(iter(states.values()))
        self.assertAlmostEqual(st["rem_mu"], 18.0)
        self.assertAlmostEqual(st["rem_sd"], 7.0)

    def test_the_projection_builder_applies_week_expectation(self):
        """week_projections must quote week_expectation(), never the season mean that
        happens to sit in the same record."""
        from scripts import live_matchup as lm
        self.assertTrue(hasattr(lm, "week_projections"),
                        "F50: the tracker needs a projection builder that runs every "
                        "player through week_expectation()")
        eng = _StubEngine({"Yet Toplay": {"player_id": "20", "mean": 10.0,
                                          "std_aleatoric": 5.0, "std_epistemic": 3.0,
                                          "pos": "WR", "team": "PHI"}})
        proj = lm.week_projections(eng, 2, expect=lambda _e, _n, _w: 17.5)
        self.assertAlmostEqual(proj["20"]["mean"], 17.5,
                               msg="the raw season mean 10.0 must not survive")

    def test_the_predictive_sd_carries_epistemic_uncertainty_too(self):
        from scripts import live_matchup as lm
        self.assertTrue(hasattr(lm, "week_projections"), "F50: see above")
        eng = _StubEngine({"Yet Toplay": {"player_id": "20", "mean": 10.0,
                                          "std_aleatoric": 5.0, "std_epistemic": 3.0,
                                          "pos": "WR", "team": "PHI"}})
        proj = lm.week_projections(eng, 2, expect=lambda _e, _n, _w: 17.5)
        self.assertAlmostEqual(proj["20"]["sd"], math.hypot(5.0, 3.0),
                               msg="std_aleatoric alone understates a one-week spread")

    def test_a_player_with_no_player_id_is_skipped_not_crashed_on(self):
        from scripts import live_matchup as lm
        self.assertTrue(hasattr(lm, "week_projections"), "F50: see above")
        eng = _StubEngine({"Ghost": {"mean": 9.0, "std_aleatoric": 4.0},
                           "Real": {"player_id": "20", "mean": 9.0,
                                    "std_aleatoric": 4.0, "std_epistemic": 2.0}})
        proj = lm.week_projections(eng, 2, expect=lambda _e, _n, _w: 11.0)
        self.assertEqual(set(proj), {"20"})


if __name__ == "__main__":
    unittest.main()

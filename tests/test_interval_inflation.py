"""B7: every interval this model quotes is about 27% too narrow.

`data/logs/points_backtest.jsonl`, latest entry after B8:

    cover80  0.67   against a nominal 0.80
    cover50  0.36   against a nominal 0.50
    sd_z_opt 1.27   against a nominal 1.00

Every probability the model states -- win%, champ%, "1 in 83" -- is sharper than the data
supports. The owner was once told a 1.2% last-place figure that is more honestly 3-4%.

B7 offers two fixes and requires the entry to choose. This takes **(a) global inflation**:
one constant, applied to `std_aleatoric` at engine init, derived from the backtest's own
`sd_z_opt`. (b) -- finding WHICH variance is understated -- is better and is deliberately
deferred, because attributing it needs F25's week 5-6 data and (a) fixes every quoted
probability this season.

WHAT IT WILL DO, stated here because it is the point and not a side effect: every win
probability moves toward 50% and every champ% toward 12.5%. The owner's 44% will read
lower. That is the honest number.

EPISTEMIC VARIANCE IS NOT TOUCHED. B7 scopes the constant to `std_aleatoric`, and the two
are not interchangeable: aleatoric is redrawn weekly, epistemic is drawn ONCE per
simulated season and held fixed to propagate parameter uncertainty to season-level
outcomes (CLAUDE.md, statistical conventions). Inflating the season-constant term would
widen season outcomes by a different mechanism than the one the backtest measured.

MAJOR: the goldens cannot detect an init-time constant (learned from F28), so they are
regenerated deliberately.

Written before the constant existed and confirmed failing (rule 1).
"""
import logging
import re
import unittest
from unittest.mock import patch

from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)

TEAMS = ['Quantum Ferrets', 'Neon Walruses', 'Rocket Pandas', 'Polar Yetis']
RAW_ALEATORIC, RAW_EPISTEMIC = 5.0, 3.0


def _fs():
    base, rosters = {}, {}
    filler = [("QB", 18.0), ("RB", 17.0), ("RB", 15.0), ("WR", 16.0), ("WR", 13.0),
              ("TE", 12.0), ("K", 9.0), ("DL", 9.0), ("LB", 9.0), ("DB", 9.0),
              ("RB", 11.0), ("WR", 10.5), ("WR", 10.0)]
    for t in TEAMS:
        entries = []
        for i, (pos, mu) in enumerate(filler):
            n = f"{t[:2]}_{pos}_{i}"
            base[n] = {"mean": mu, "std_aleatoric": RAW_ALEATORIC,
                       "std_epistemic": RAW_EPISTEMIC, "pos": pos, "team": "DET", "bye": 0}
            entries.append({"name": n, "pos": pos, "team": "DET"})
        rosters[t] = entries
    return {
        LEAGUE_STATE_FILE: {"current_week": 1},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: {"_meta": {"week": 1, "source": "odds_api", "fetched_at": "x"},
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


def _engine():
    fs = _fs()
    pre = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.ERROR)
    with patch('os.path.exists', side_effect=lambda p: p in fs), \
         patch('fantasy_sim.simulation.load_json', side_effect=lambda p: fs[p]):
        e = FantasySimulationEngine()
    logging.getLogger().setLevel(pre)
    return e


class TestTheConstant(unittest.TestCase):
    def test_it_exists_and_widens_rather_than_narrows(self):
        from fantasy_sim.config import INTERVAL_INFLATION
        self.assertIsInstance(INTERVAL_INFLATION, float)
        self.assertGreater(INTERVAL_INFLATION, 1.0,
                           "the intervals are too NARROW; a value below 1 would sharpen "
                           "an already overconfident model")

    def test_it_cites_the_backtest_it_came_from(self):
        """Rule 5, and B7 says explicitly: cite the backtest entry in the comment."""
        import inspect
        from fantasy_sim import config
        src = inspect.getsource(config)
        m = re.search(r"^INTERVAL_INFLATION\s*=", src, re.M)
        self.assertIsNotNone(m)
        # The CONTIGUOUS comment block immediately above it, not a fixed byte window. The
        # first version took the preceding 2200 characters and broke the moment the
        # sourcing comment grew -- which is the wrong failure for a test whose subject is
        # "is this constant sourced": a longer derivation made it fail.
        lines = src[:m.start()].splitlines()
        block_lines = []
        for ln in reversed(lines):
            if ln.startswith("#"):
                block_lines.append(ln)
            elif ln.strip() == "" and block_lines:
                break
            elif ln.strip():
                break
        block = "\n".join(reversed(block_lines))
        self.assertTrue(block_lines, "no comment block above the constant at all")
        self.assertIn("sd_z_opt", block, "name the statistic it was derived from")
        self.assertIn("cover80", block, "and the coverage it is meant to repair")
        self.assertIn("points_backtest", block, "and the log entry it came from")


class TestItIsAppliedAtInit(unittest.TestCase):
    def test_stored_aleatoric_is_the_inflated_value(self):
        from fantasy_sim.config import INTERVAL_INFLATION
        e = _engine()
        got = float(e.baselines["Qu_QB_0"]["std_aleatoric"])
        self.assertAlmostEqual(got, RAW_ALEATORIC * INTERVAL_INFLATION, places=9)

    def test_every_player_is_inflated_not_just_starters(self):
        from fantasy_sim.config import INTERVAL_INFLATION
        e = _engine()
        for name, b in e.baselines.items():
            if isinstance(b, dict) and b.get("std_aleatoric"):
                self.assertAlmostEqual(float(b["std_aleatoric"]),
                                       RAW_ALEATORIC * INTERVAL_INFLATION, places=9,
                                       msg=f"{name} was missed")

    def test_epistemic_variance_is_left_alone(self):
        """Redrawn weekly vs drawn once per season: not interchangeable, and B7 scopes
        the constant to aleatoric."""
        e = _engine()
        self.assertAlmostEqual(float(e.baselines["Qu_QB_0"]["std_epistemic"]),
                               RAW_EPISTEMIC, places=9)

    def test_it_is_applied_exactly_once(self):
        """Two engines off the same file must agree. Inflating a dict the loader shares
        would compound it on the second construction."""
        a, b = _engine(), _engine()
        self.assertAlmostEqual(float(a.baselines["Qu_QB_0"]["std_aleatoric"]),
                               float(b.baselines["Qu_QB_0"]["std_aleatoric"]), places=9)

    def test_a_player_without_an_aleatoric_term_does_not_crash(self):
        from fantasy_sim.simulation import _inflate_aleatoric
        entry = {"mean": 5.0, "pos": "WR"}
        _inflate_aleatoric({"x": entry}, 1.27)
        self.assertNotIn("std_aleatoric", entry)

    def test_an_inflation_of_one_is_a_no_op(self):
        """So the change is auditable: the constant alone accounts for the difference."""
        from fantasy_sim.simulation import _inflate_aleatoric
        d = {"x": {"std_aleatoric": 4.0}}
        _inflate_aleatoric(d, 1.0)
        self.assertAlmostEqual(d["x"]["std_aleatoric"], 4.0)


if __name__ == "__main__":
    unittest.main()

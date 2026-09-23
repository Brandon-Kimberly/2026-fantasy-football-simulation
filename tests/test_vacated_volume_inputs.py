"""B8: vacated-volume apportionment still reads PRE-BLEND means.

F54 moved `_calc_replacement_levels()` after `_apply_bayesian_updates()` and recorded the
rest as its open half. `_build_pass_catcher_hierarchy()` and `_build_nfl_position_groups()`
still run two lines BEFORE the blend:

    self.pass_catchers_meta  = self._build_pass_catcher_hierarchy()   # pre-blend
    self.nfl_position_groups = self._build_nfl_position_groups()      # pre-blend
    self.calibration_report  = self._apply_bayesian_updates()         # the blend

Both snapshot `p_info['mean']` into a sorted list. So when a starter goes down, his
vacated volume is apportioned across teammates using their PRESEASON means. A backup who
has been producing for three weeks gets exactly the same share as one who has not,
because the posterior that knows the difference has not run yet.

WHAT THIS DOES *NOT* TOUCH, and the distinction is the whole of B8's trap. F24 measured
MEAN-WEIGHTED apportionment as correct on 8 real 2025 lead-RB absences, and `CLAUDE.md`
lists it as a deliberate decision not to be "fixed". This changes the INPUTS to that
mechanism -- which means it reads -- not the weighting rule itself. A guard below asserts
the rule is untouched: if a later session finds itself editing the weighting, it has gone
wrong.

MAJOR. The apportionment shifts, so predictions shift, and the goldens are regenerated
deliberately.

Written before the change and confirmed failing (rule 1).
"""
import logging
import unittest
from unittest.mock import patch

from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)

TEAMS = ['Quantum Ferrets', 'Neon Walruses', 'Rocket Pandas', 'Polar Yetis']
# Two backups on the SAME NFL team with IDENTICAL priors. One has produced.
PRODUCER, QUIET = "Producer Back", "Quiet Back"


def _p(mean, pos, team="DET", sd_e=6.0):
    return {"mean": mean, "std_aleatoric": 5.0, "std_epistemic": sd_e, "pos": pos,
            "team": team, "bye": 0}


def _fs(observed):
    """`observed` is {name: [weekly scores]} feeding the Bayesian posterior."""
    base, rosters = {}, {}
    filler = [("QB", 18.0), ("RB", 17.0), ("RB", 15.0), ("WR", 16.0), ("WR", 13.0),
              ("TE", 12.0), ("K", 9.0), ("DL", 9.0), ("LB", 9.0), ("DB", 9.0),
              ("RB", 11.0), ("WR", 10.5), ("WR", 10.0)]
    for t in TEAMS:
        entries = []
        for i, (pos, mu) in enumerate(filler):
            n = f"{t[:2]}_{pos}_{i}"
            base[n] = _p(mu, pos, team="CHI")
            entries.append({"name": n, "pos": pos, "team": "CHI"})
        rosters[t] = entries
    # The lead back and his two identical handcuffs, all on DET.
    base["Lead Back"] = _p(20.0, "RB")
    rosters[TEAMS[0]].append({"name": "Lead Back", "pos": "RB", "team": "DET"})
    for nm in (PRODUCER, QUIET):
        base[nm] = _p(6.0, "RB")
        rosters[TEAMS[0]].append({"name": nm, "pos": "RB", "team": "DET"})

    weekly = {}
    for wk in (1, 2):
        scores = {n: v[wk - 1] for n, v in (observed or {}).items() if len(v) >= wk}
        weekly[f"week_{wk}"] = {
            "median_cutoff": 100.0,
            "team_results": {t: {"points_scored": 100.0, "h2h_win": 0.5,
                                 "median_win": 1, "remaining_faab": 100} for t in TEAMS},
            "player_scores": scores,
        }
    return {
        LEAGUE_STATE_FILE: {"current_week": 3},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: {"_meta": {"week": 3, "source": "odds_api", "fetched_at": "x"},
                     "DET": {"total": 24.0, "spread": -3.0, "opponent": "CHI"},
                     "CHI": {"total": 22.0, "spread": 3.0, "opponent": "DET"}},
        LIVE_ROSTERS_FILE: rosters,
        BASELINES_FILE: base,
        TEAM_RATINGS_FILE: {"DET": {"off_rating": 24}, "CHI": {"off_rating": 22}},
        DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                 "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[TEAMS[0], TEAMS[1]], [TEAMS[2], TEAMS[3]]]] * 14,
        NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: weekly,
    }


def _engine(observed):
    fs = _fs(observed)
    pre = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.ERROR)
    with patch('os.path.exists', side_effect=lambda p: p in fs), \
         patch('fantasy_sim.simulation.load_json', side_effect=lambda p: fs[p]):
        e = FantasySimulationEngine()
    logging.getLogger().setLevel(pre)
    return e


OBSERVED = {PRODUCER: [18.0, 20.0], QUIET: [2.0, 1.0]}


class TestTheBlendSeparatesThem(unittest.TestCase):
    """Sanity: the posterior really does distinguish these two. If it did not, the rest
    of this file would be testing nothing."""

    def test_the_producer_is_marked_up_and_the_quiet_one_down(self):
        e = _engine(OBSERVED)
        self.assertGreater(e.baselines[PRODUCER]["mean"], 6.0)
        self.assertLess(e.baselines[QUIET]["mean"], 6.0)
        self.assertGreater(e.baselines[PRODUCER]["mean"], e.baselines[QUIET]["mean"] + 2.0)


class TestVacatedVolumeReadsTheBlend(unittest.TestCase):
    def test_the_position_group_orders_by_the_CORRECTED_mean(self):
        """This is the structure vacated volume is apportioned across."""
        e = _engine(OBSERVED)
        det_rbs = e.nfl_position_groups.get(("RB", "DET")) or []
        got = {n: m for n, m in det_rbs}
        self.assertIn(PRODUCER, got)
        self.assertGreater(got[PRODUCER], got[QUIET],
                           "B8: the producer must carry the larger share, and before the "
                           "fix both carry an identical preseason 6.0")

    def test_the_stored_means_match_the_posterior_not_the_prior(self):
        e = _engine(OBSERVED)
        got = {n: m for n, m in (e.nfl_position_groups.get(("RB", "DET")) or [])}
        self.assertAlmostEqual(got[PRODUCER], e.baselines[PRODUCER]["mean"], places=9)
        self.assertAlmostEqual(got[QUIET], e.baselines[QUIET]["mean"], places=9)

    def test_the_pass_catcher_hierarchy_also_reads_the_blend(self):
        e = _engine({f"Qu_WR_3": [24.0, 26.0], f"Qu_WR_4": [1.0, 2.0]})
        chi = dict(e.pass_catchers_meta.get("CHI") or [])
        self.assertAlmostEqual(chi.get("Qu_WR_3"), e.baselines["Qu_WR_3"]["mean"], places=9)

    def test_identical_priors_with_no_evidence_stay_identical(self):
        """No false movement: with nothing observed, the two must still tie."""
        e = _engine({})
        got = {n: m for n, m in (e.nfl_position_groups.get(("RB", "DET")) or [])}
        self.assertAlmostEqual(got[PRODUCER], got[QUIET], places=9)


class TestTheWeightingRuleIsUntouched(unittest.TestCase):
    """B8's trap. F24 MEASURED mean-weighting as correct on 8 real 2025 absences and
    CLAUDE.md lists it as a deliberate decision. This item changes what that rule READS,
    never the rule."""

    def test_apportionment_is_still_mean_weighted_not_depth_weighted(self):
        import inspect
        src = inspect.getsource(FantasySimulationEngine)
        self.assertNotIn("depth_chart_order", src,
                         "F24: do not switch to depth weighting; B8 changes the inputs")

    def test_the_builders_still_sort_descending_by_mean(self):
        import inspect
        src = inspect.getsource(FantasySimulationEngine._build_pass_catcher_hierarchy)
        self.assertIn("reverse=True", src)
        self.assertIn("mean", src)


if __name__ == "__main__":
    unittest.main()

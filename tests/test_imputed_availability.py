"""F60: a whitelisted missing asset is imputed as HEALTHY AND AVAILABLE, whatever the
roster says.

Found live on 2026-09-22 while evaluating a three-way trade. `decisions.apply_trade`
refused every leg with "Turbo Llamas would carry 20 active players (limit 19)" — before
any trade. That team's real Sleeper roster is 20 players with one on IR, i.e. 19
active, which is legal.

THE MECHANISM. A rostered player with no usable projection is imputed at engine init from
`SIM_CONFIG['KNOWN_MISSING_ASSETS']`. That whitelist is hand-typed and carries no
availability fields, and `self.meta` is built with only `pos` and `team` — so `on_ir` and
`injury_status` reach `engine.baselines` from NOWHERE. Both read as absent/None:

  - `decisions._active_count` reads `on_ir` off `engine.baselines`, so the team is
    overcounted and every trade involving it is refused;
  - `_initial_absence_clock(status, on_ir)` (simulation.py:1120) falls back to the
    baselines too, because `p_meta` has no such key — so the player gets NO absence clock
    and is simulated as fully available all season.

The second is the one that touches the distribution.

THE PRECEDENT THIS FOLLOWS. The same imputation block already refuses to trust the
whitelist for `bye`, taking it from `nfl_schedule._meta.byes` instead, and cross-checks
`team`/`pos` against the roster file "from Sleeper" and warns on mismatch. Availability is
the same class of fact and belongs to the same authority: the roster file, not the
hand-typed constant.

Written before the fix and confirmed failing (rule 1).
"""
import logging
import unittest
from unittest.mock import patch

from fantasy_sim.simulation import FantasySimulationEngine, SIM_CONFIG
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)
from fantasy_sim.decisions import _active_count

TEAMS = ['Quantum Ferrets', 'Neon Walruses', 'Rocket Pandas', 'Polar Yetis']
GHOST = "Ghost Asset"

# The whitelist entry a human typed. Deliberately says nothing about availability -- that
# is the whole point: there is no field here for the roster's IR flag to come from.
WHITELIST = {GHOST: {"mean": 6.5, "std_aleatoric": 4.0, "std_epistemic": 3.0,
                     "pos": "WR", "team": "DET"}}


def _fs(ghost_roster_entry):
    """A 4-team league where Quantum Ferrets roster GHOST, who has no baseline at all and
    is therefore imputed from the whitelist. `ghost_roster_entry` is what the ROSTER FILE
    (Sleeper's record) says about him."""
    qb = lambda n, tm: {"mean": 18.0, "std_aleatoric": 2.0, "std_epistemic": 1.5,
                        "pos": "QB", "team": tm, "bye": 0}
    return {
        LEAGUE_STATE_FILE: {"current_week": 1},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: {"_meta": {"week": 1, "source": "odds_api", "fetched_at": "x"},
                     "DET": {"total": 24.0, "spread": -4.0, "opponent": "CHI"},
                     "CHI": {"total": 20.0, "spread": 4.0, "opponent": "DET"}},
        LIVE_ROSTERS_FILE: {
            TEAMS[0]: [{"name": "QB_1", "pos": "QB", "team": "DET"}, ghost_roster_entry],
            TEAMS[1]: [{"name": "QB_2", "pos": "QB", "team": "CHI"}],
            TEAMS[2]: [{"name": "QB_3", "pos": "QB", "team": "FA"}],
            TEAMS[3]: [{"name": "QB_4", "pos": "QB", "team": "FA"}],
        },
        BASELINES_FILE: {"QB_1": qb("QB_1", "DET"), "QB_2": qb("QB_2", "CHI"),
                         "QB_3": qb("QB_3", "FA"), "QB_4": qb("QB_4", "FA")},
        TEAM_RATINGS_FILE: {"DET": {"off_rating": 25}, "CHI": {"off_rating": 20}},
        DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                 "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[TEAMS[0], TEAMS[1]], [TEAMS[2], TEAMS[3]]]] * 14,
        NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: {},
    }


class _Case(unittest.TestCase):
    GHOST_ENTRY = {"name": GHOST, "pos": "WR", "team": "DET"}

    def setUp(self):
        self.fs = _fs(self.GHOST_ENTRY)
        self.prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        self.p_exists = patch('os.path.exists', side_effect=lambda p: p in self.fs)
        self.p_load = patch('fantasy_sim.simulation.load_json', side_effect=lambda p: self.fs[p])
        self.p_wl = patch.dict(SIM_CONFIG["KNOWN_MISSING_ASSETS"], WHITELIST, clear=True)
        self.p_exists.start(); self.p_load.start(); self.p_wl.start()
        self.engine = FantasySimulationEngine()

    def tearDown(self):
        self.p_wl.stop(); self.p_exists.stop(); self.p_load.stop()
        logging.getLogger().setLevel(self.prev)


class TestImputedPlayerOnIR(_Case):
    """The roster says he is on IR. The whitelist cannot say so. The engine must believe
    the roster -- exactly as it already does for `bye`, `team` and `pos`."""
    GHOST_ENTRY = {"name": GHOST, "pos": "WR", "team": "DET",
                   "on_ir": True, "injury_status": "IR"}

    def test_the_imputed_baseline_carries_the_rosters_ir_flag(self):
        self.assertTrue(
            self.engine.baselines[GHOST].get("on_ir"),
            "F60: the roster file marks this player on IR; the imputed baseline says "
            "nothing, so every consumer reads him as active")

    def test_the_imputed_baseline_carries_the_rosters_injury_status(self):
        self.assertEqual(self.engine.baselines[GHOST].get("injury_status"), "IR")

    def test_he_does_not_count_against_the_active_roster_limit(self):
        """The symptom that surfaced this: apply_trade refused legal trades because the
        team read one over the limit."""
        self.assertEqual(
            _active_count(self.engine, TEAMS[0]), 1,
            "F60: an IR'd player must not consume an active roster slot")

    def test_he_starts_the_season_on_an_absence_clock(self):
        """The half that touches the DISTRIBUTION. _initial_absence_clock reads
        p_meta first and falls back to baselines; meta carries only pos/team, so with
        the baseline silent he is simulated as available all season."""
        b = self.engine.baselines[GHOST]
        clock = self.engine._initial_absence_clock(
            b.get("injury_status"), bool(b.get("on_ir", False)))
        self.assertTrue(clock, "F60: an IR'd player must not be simulated as available")


class TestImputedPlayerWhoIsHealthy(_Case):
    """No false positives: the fix must not invent absence for a whitelisted player the
    roster reports as fine. This is what keeps the three engine golden fixtures -- whose
    imputed player has on_ir=None -- byte-identical."""

    def test_a_healthy_imputed_player_is_unaffected(self):
        b = self.engine.baselines[GHOST]
        self.assertFalse(bool(b.get("on_ir", False)))
        self.assertFalse(self.engine._initial_absence_clock(
            b.get("injury_status"), bool(b.get("on_ir", False))))
        self.assertEqual(_active_count(self.engine, TEAMS[0]), 2)


class TestTheRosterOutranksTheWhitelist(unittest.TestCase):
    """If someone hand-types availability into the whitelist, the roster still wins --
    the same precedence the block already applies to `bye`. A stale `on_ir: True` left in
    config after a player is activated must not bench him forever."""

    def setUp(self):
        self.fs = _fs({"name": GHOST, "pos": "WR", "team": "DET",
                       "on_ir": False, "injury_status": None})
        self.prev = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.ERROR)
        stale = {GHOST: dict(WHITELIST[GHOST], on_ir=True, injury_status="IR")}
        self.p_exists = patch('os.path.exists', side_effect=lambda p: p in self.fs)
        self.p_load = patch('fantasy_sim.simulation.load_json', side_effect=lambda p: self.fs[p])
        self.p_wl = patch.dict(SIM_CONFIG["KNOWN_MISSING_ASSETS"], stale, clear=True)
        self.p_exists.start(); self.p_load.start(); self.p_wl.start()
        self.engine = FantasySimulationEngine()

    def tearDown(self):
        self.p_wl.stop(); self.p_exists.stop(); self.p_load.stop()
        logging.getLogger().setLevel(self.prev)

    def test_a_stale_whitelist_ir_flag_does_not_bench_an_active_player(self):
        self.assertFalse(bool(self.engine.baselines[GHOST].get("on_ir", False)),
                         "F60: the roster file is the authority on availability, not the "
                         "hand-typed whitelist")


if __name__ == "__main__":
    unittest.main()

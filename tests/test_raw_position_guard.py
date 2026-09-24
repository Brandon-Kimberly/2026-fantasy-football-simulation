"""T4: raw NFL positions still reach slot-position matching, and nothing stops it.

Phase 3 finding 3 fixed this inside the engine (`config.normalize_position`: DE/DT/NT -> DL,
CB/S/FS/SS -> DB, OLB/ILB/MLB -> LB, FB -> RB). Two ad-hoc queries on 2026-09-23 wrote
`pos == 'DL'` anyway and silently excluded every DE and DT -- missing two DL-eligible free
agents, one of them the best at the position. The sweep T4 asked for found the tools clean
on that exact pattern, but it found this instead:

**`scripts.season_retrospective._positions` and the identical block in
`scripts.run_points_backtest` fall back to the RAW `position` when a cached player carries
no `fantasy_positions`.** 325 of 12,228 cached entries are in that state. The raw string
then goes to `FantasySimulationEngine._solve_optimal_assignment`, which matches
`slot_pos in pos_opts` literally -- so a defensive end is not eligible at DL, and his points
are dropped from the "realized optimal lineup" that is the points-backtest's own target and
season_retrospective's lineup-efficiency denominator. Measured:

    _positions({"1": {"position": "DE"}})                 -> {'1': ['DE']}
    real_optimal_points(QB+DL slots, QB 20.0, DE 14.0)    -> 20.0, the DL slot left EMPTY

It is latent for 2025 (that season was non-IDP, so no DL/LB/DB slot exists to miss) and
live for 2026. Understating the optimal makes lineup efficiency look BETTER than it was --
the flattering direction, which is why nobody would have noticed it from the output.

**The obvious fix is wrong and a test pins that.** Mapping every raw position through
`normalize_position` would give an offensive lineman `'FLEX'` -- that function's return for
anything it does not recognise is its UNKNOWN sentinel, not an eligibility claim -- and
`_solve_optimal_assignment` would then happily start a left tackle at FLEX. It would also
destroy team defenses: `normalize_position('DEF')` is `'FLEX'` too.

**Sleeper's own `fantasy_positions` is already slot-shaped** and must keep winning when it
is present: DE -> ['DL'], CB -> ['DB'], FB -> ['RB'], OLB -> ['LB'], and it carries real
dual eligibility (114 cached linebackers list ['DL','LB']) that the fallback cannot know.

Written before the change. Three of the six below are red against current behaviour; the
other three are green BY DESIGN -- they are regression guards on the fix, not
characterisations, and are marked as such so no one reads them as evidence of a defect.
"""
import unittest


def _pos_of(db):
    from scripts.season_retrospective import _positions
    return _positions(db)


class TestTheRawFallbackLosesEligibility(unittest.TestCase):
    """Red. The defect itself."""

    def test_a_defensive_end_with_no_fantasy_positions_is_eligible_at_DL(self):
        self.assertEqual(_pos_of({"1": {"position": "DE"}}), {"1": ["DL"]})

    def test_a_cornerback_with_no_fantasy_positions_is_eligible_at_DB(self):
        self.assertEqual(_pos_of({"1": {"position": "CB"}}), {"1": ["DB"]})

    def test_the_optimal_lineup_actually_counts_him(self):
        """End to end, on the number that carries the defect: the points-backtest's own
        optimal target. A 14.0 defensive end must fill the DL slot, not vanish."""
        from scripts.run_points_backtest import real_optimal_points
        bundle = {"roster_positions": ["QB", "DL", "BN"],
                  "settings": {"playoff_week_start": 15},
                  "roster_map": {"1": "A"},
                  "matchups": {"3": [{"roster_id": 1,
                                      "players_points": {"q1": 20.0, "d1": 14.0}}]}}
        positions = _pos_of({"q1": {"position": "QB"}, "d1": {"position": "DE"}})
        out = real_optimal_points(bundle, positions)
        self.assertAlmostEqual(out["A"][3], 34.0,
                               msg="QB 20 + DE 14 at the DL slot; 20.0 means the DL slot "
                                   "was left empty because 'DE' != 'DL'")


class TestTheObviousFixWouldBeWorse(unittest.TestCase):
    """GREEN BY DESIGN -- regression guards on the fix, not characterisations of a defect.
    Each pins a way that a blanket `normalize_position` over the raw position would break
    something that works today."""

    def test_an_offensive_lineman_gets_no_eligibility_not_FLEX(self):
        got = _pos_of({"1": {"position": "OL"}}).get("1", [])
        self.assertNotIn("FLEX", got,
                         "normalize_position returns 'FLEX' for anything it does not "
                         "recognise; that is UNKNOWN, not FLEX eligibility, and "
                         "_solve_optimal_assignment would start a tackle at FLEX")

    def test_a_team_defense_keeps_its_DEF_eligibility(self):
        """normalize_position('DEF') is also 'FLEX'. 2025 had a DEF slot."""
        self.assertEqual(_pos_of({"1": {"position": "DEF"}}), {"1": ["DEF"]})

    def test_sleepers_own_dual_eligibility_wins_when_present(self):
        """114 cached linebackers list ['DL','LB']. The raw `position` cannot know that, so
        the field must keep priority over the fallback."""
        self.assertEqual(
            _pos_of({"1": {"position": "DE", "fantasy_positions": ["DL", "LB"]}}),
            {"1": ["DL", "LB"]})


if __name__ == "__main__":
    unittest.main()

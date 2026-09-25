"""Slot eligibility is hand-maintained in a name-keyed dict, and Sleeper ships the truth.

`config.DUAL_ELIGIBILITY` lists eight players by NAME. Every engine site that needs to know
which slots a player can fill reads it and falls back to the single normalised `pos`:

    DUAL_ELIGIBILITY.get(p, [normalize_position(entry.get('pos', 'FLEX'))])

Sleeper's own player payload carries `fantasy_positions`, a LIST, for every player, and sync
already fetches it on every run -- it is simply discarded when the baseline is written, which
keeps one `pos` string. `config.fantasy_slot_positions` (built for T4) reads that list
correctly and nothing in the engine path calls it.

**Measured on the live league, 2026-09-24 -- six rostered players whose eligibility the engine
has wrong, in BOTH directions:**

    Andrew Van Ginkel   engine ['LB']        Sleeper ['DL','LB']    missing
    Dallas Turner       engine ['LB']        Sleeper ['DL','LB']    missing
    Greg Rousseau       engine ['DL']        Sleeper ['DL','LB']    missing
    Will Anderson       engine ['DL']        Sleeper ['DL','LB']    missing
    Tuli Tuipulotu      engine ['DL']        Sleeper ['DL','LB']    missing
    Maxx Crosby         engine ['DL','LB']   Sleeper ['DL']         WRONG -- grants a slot
                                                                    he is not eligible for

The missing half makes the engine believe two teams have no DL-eligible player at all, so it
injects a replacement-level `STREAMER_DL_0` in place of a real starter. The wrong half is
worse in kind: a hand-typed entry that was never true, or is no longer true, silently widens
one player's eligibility and can seat him in a slot the real league would reject. A
hand-maintained list of a changing fact drifts in both directions, and only one of those
directions is visible as a warning.

**The fix is to stop maintaining it by hand.** Sleeper is authoritative and is already on
disk every sync: record `fantasy_slot_positions` into the baseline as `slots`, and read that.
DUAL_ELIGIBILITY stays as the fallback for entries with no `slots` key -- the golden fixtures
have none, so they must keep resolving exactly as they do today.

This is a change to an engine INPUT that the goldens structurally cannot see, which is the
third MAJOR trigger (CLAUDE.md, added at v8.0.0 for F84).

Player names here are NFL domain data, not league identities.

Written before the change.
"""
import unittest
from unittest.mock import patch, mock_open


class TestSyncRecordsWhatSleeperSaid(unittest.TestCase):
    """The eligibility list is on disk at sync time and is thrown away."""

    @staticmethod
    def _run(players_db, live_rosters):
        from fantasy_sim.sync import generate_player_baselines
        from unittest.mock import MagicMock

        def fake_get(url, timeout=None):
            resp = MagicMock()
            if "/projections/nfl/regular/2026/1" in url:
                resp.status_code = 200
                resp.json.return_value = {pid: {"stats": {"idp_tkl_solo": 4}}
                                          for pid in players_db}
            else:
                resp.status_code = 404
                resp.json.return_value = {}
            return resp

        with patch("os.path.exists", return_value=False), \
             patch("requests.get", side_effect=fake_get), \
             patch("fantasy_sim.sync.fetch_espn_projection_data", return_value=({}, {})), \
             patch("builtins.open", mock_open()), \
             patch("json.dump"):
            return generate_player_baselines({"idp_tkl_solo": 1.5}, players_db, live_rosters,
                                             current_year="2026", week=1)

    def test_a_dual_eligible_defender_records_both_slots(self):
        db = {"77": {"first_name": "Dual", "last_name": "Rusher", "position": "LB",
                     "fantasy_positions": ["DL", "LB"], "team": "DET", "team_bye": 5}}
        rosters = {"SomeTeam": [{"name": "Dual Rusher", "pos": "LB", "team": "DET"}]}
        entry = self._run(db, rosters)["Dual Rusher"]
        self.assertEqual(sorted(entry.get("slots") or []), ["DL", "LB"])

    def test_a_single_position_player_records_his_one_slot(self):
        db = {"78": {"first_name": "Plain", "last_name": "Receiver", "position": "WR",
                     "fantasy_positions": ["WR"], "team": "DET", "team_bye": 5}}
        rosters = {"SomeTeam": [{"name": "Plain Receiver", "pos": "WR", "team": "DET"}]}
        entry = self._run(db, rosters)["Plain Receiver"]
        self.assertEqual(entry.get("slots"), ["WR"])

    def test_raw_sleeper_positions_are_normalised_into_slots(self):
        """Sleeper reports DE/DT/CB/S; the slots are the league's nine."""
        db = {"79": {"first_name": "Edge", "last_name": "Player", "position": "DE",
                     "fantasy_positions": ["DE", "LB"], "team": "DET", "team_bye": 5}}
        rosters = {"SomeTeam": [{"name": "Edge Player", "pos": "DE", "team": "DET"}]}
        entry = self._run(db, rosters)["Edge Player"]
        self.assertEqual(sorted(entry.get("slots") or []), ["DL", "LB"])


class TestEligibilityPrefersTheSyncedTruth(unittest.TestCase):
    def test_synced_slots_win_over_the_hand_list(self):
        """The Maxx Crosby case. The hand-list says DL/LB; Sleeper says DL only. A stale
        hand entry must not widen a player's eligibility past what the league allows."""
        from fantasy_sim.config import eligible_slots
        entry = {"pos": "DL", "slots": ["DL"]}
        self.assertEqual(eligible_slots("Maxx Crosby", entry), ["DL"])

    def test_synced_slots_add_what_the_hand_list_missed(self):
        """The Van Ginkel case: LB by `pos`, DL-eligible in fact, absent from the hand-list."""
        from fantasy_sim.config import eligible_slots
        entry = {"pos": "LB", "slots": ["DL", "LB"]}
        self.assertEqual(sorted(eligible_slots("Andrew Van Ginkel", entry)), ["DL", "LB"])


class TestTheFallbackIsPreserved(unittest.TestCase):
    """GREEN BY DESIGN. The golden fixtures carry no `slots` key, so every entry without one
    must resolve exactly as it does today or the goldens move for a reason unrelated to the
    defect."""

    def test_no_slots_falls_back_to_the_hand_list(self):
        from fantasy_sim.config import eligible_slots
        self.assertEqual(sorted(eligible_slots("T.J. Watt", {"pos": "LB"})), ["DL", "LB"])

    def test_no_slots_and_no_hand_entry_falls_back_to_the_normalised_pos(self):
        from fantasy_sim.config import eligible_slots
        self.assertEqual(eligible_slots("Some Cornerback", {"pos": "CB"}), ["DB"])

    def test_an_empty_slots_list_is_not_trusted(self):
        """An empty list is missing data, not 'eligible for nothing' -- a player eligible for
        no slot would be silently unplayable."""
        from fantasy_sim.config import eligible_slots
        self.assertEqual(eligible_slots("Some Cornerback", {"pos": "CB", "slots": []}), ["DB"])

    def test_a_missing_entry_does_not_raise(self):
        from fantasy_sim.config import eligible_slots
        self.assertEqual(eligible_slots("Nobody", {}), ["FLEX"])


class TestTheEngineSeatsHimInTheSlot(unittest.TestCase):
    """The consequence. A roster whose only DL-eligible body is listed LB must fill the DL
    slot with him rather than leave it open for a streamer."""

    def test_a_dual_eligible_linebacker_fills_the_empty_DL_slot(self):
        from fantasy_sim.simulation import FantasySimulationEngine
        from fantasy_sim.config import eligible_slots
        entries = {
            "Dual Rusher": {"pos": "LB", "slots": ["DL", "LB"], "mean": 9.0},
            "Pure Backer": {"pos": "LB", "slots": ["LB"], "mean": 12.0},
        }
        cands = [(n, eligible_slots(n, e), e["mean"]) for n, e in entries.items()]
        assigned, unfilled = FantasySimulationEngine._solve_optimal_assignment(
            cands, slots=["DL", "LB"])
        seated = {slot: name for name, _v, slot in assigned}
        self.assertEqual(seated.get("DL"), "Dual Rusher")
        self.assertEqual(seated.get("LB"), "Pure Backer")
        self.assertEqual(unfilled, [])

    def test_the_OLD_expression_leaves_that_DL_slot_unfilled(self):
        """Pins the defect in today's terms, without reference to the new helper: the exact
        expression every engine call site uses right now, on a roster whose only DL-eligible
        body is listed LB, leaves the DL slot empty. That empty slot is the roster hole the
        live warnings report and the streamer fills.

        This stays green after the change -- it characterises the FALLBACK, which is what
        golden fixtures (no `slots` key, no hand-list entry) keep resolving through.
        """
        from fantasy_sim.simulation import FantasySimulationEngine
        from fantasy_sim.config import DUAL_ELIGIBILITY, normalize_position
        entries = {"Dual Rusher": {"pos": "LB", "mean": 9.0},
                   "Pure Backer": {"pos": "LB", "mean": 12.0}}
        cands = [(n, DUAL_ELIGIBILITY.get(n, [normalize_position(e.get("pos", "FLEX"))]),
                  e["mean"]) for n, e in entries.items()]
        assigned, unfilled = FantasySimulationEngine._solve_optimal_assignment(
            cands, slots=["DL", "LB"])
        seated = {slot: name for name, _v, slot in assigned}
        self.assertIsNone(seated.get("DL"),
                          "the old expression cannot seat a DL-eligible linebacker at DL")
        self.assertEqual(unfilled, ["DL"], "this empty slot is what the streamer fills")


if __name__ == "__main__":
    unittest.main()

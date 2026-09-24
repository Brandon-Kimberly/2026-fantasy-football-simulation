"""The starting FAAB budget is hardcoded at 100, and commissioner adjustments are invisible.

Raised by the owner on 2026-09-24: the commissioner had granted one team 3 FAAB and taken
1 from another "as a joke". The worry was that `remaining_faab` would be wrong. Measuring
the live league first, before changing anything, gave a more useful answer than the worry:

**`waiver_budget_used` IS AUTHORITATIVE AND ALREADY FOLDS EVERYTHING IN.** Modelling it
independently as `bids + faab_sent - faab_received` matches Sleeper exactly on 6 of 8
rosters. The two that differ are the two adjustments:

    rid  used  bids  sent  recv   model   used - model
      2    44    43     0     0      43         +1      <- 1 taken away
      4     2    54     0    48       6         -4      <- 4 granted
      5    56     8    48     0      56          0      <- a 48-FAAB TRADE, already in `used`

So the live numbers are RIGHT today, and the feared defect is not there. Two real ones are:

**1. The starting budget is hardcoded.** `sync` computes `max(0, 100.0 - used)`. The real
value is `league.settings.waiver_budget` -- 100 in this league today, but it is a league
SETTING. A different season (the 2025 league the backtests ingest) or a rule change moves
it, and the failure is silent: every budget in the league is wrong by a constant and
nothing says so.

**2. A commissioner adjustment leaves NO transaction record.** rid 2's -1 and rid 4's +4
appear nowhere in `/transactions`; they exist only as a shift inside `waiver_budget_used`.
This is the same shape as B14's premise -- a lost waiver claim never becomes a transaction,
so the decision log can never show it -- and it means the bid ledger and the manager FAAB
profiles can never explain where a budget went. The disagreement is COMPUTABLE, so sync
warns and lets a human judge, exactly as F24's depth watchdog does rather than silently
picking a side.

NOT A BUG, and a test pins it so nobody "fixes" it: FAAB moved by TRADE is already inside
`waiver_budget_used` (rid 5 matches to the point). Adding the `waiver_budget` transaction
flow on top would double-count it.

Written before the change.
"""
import unittest


class TestTheStartingBudgetComesFromTheLeague(unittest.TestCase):
    """RED. 100 is a league setting, not a constant."""

    @staticmethod
    def _standings(settings, rosters):
        from fantasy_sim.sync import build_standings
        return build_standings(settings, rosters, {r["roster_id"]: f"team{r['roster_id']}"
                                                   for r in rosters})

    def test_a_league_with_a_different_budget_is_read_not_assumed(self):
        rosters = [{"roster_id": 1, "settings": {"wins": 1, "fpts": 10, "waiver_budget_used": 25}}]
        got = self._standings({"waiver_budget": 200}, rosters)
        self.assertAlmostEqual(got["team1"]["remaining_faab"], 175.0,
                               msg="200 - 25; the budget is league.settings.waiver_budget")

    def test_a_league_that_states_no_budget_falls_back_to_100(self):
        """The historical default, and the value every existing record was written under."""
        rosters = [{"roster_id": 1, "settings": {"wins": 0, "fpts": 0, "waiver_budget_used": 8}}]
        self.assertAlmostEqual(self._standings({}, rosters)["team1"]["remaining_faab"], 92.0)

    def test_receiving_faab_can_push_a_team_ABOVE_the_budget(self):
        """`waiver_budget_used` goes NEGATIVE for a team that received more than it spent.
        Capping the result at the budget would erase a real advantage -- one live roster is
        carrying 48 traded FAAB."""
        rosters = [{"roster_id": 1, "settings": {"wins": 0, "fpts": 0, "waiver_budget_used": -48}}]
        self.assertAlmostEqual(self._standings({"waiver_budget": 100}, rosters)["team1"]["remaining_faab"],
                               148.0)

    def test_it_never_goes_below_zero(self):
        rosters = [{"roster_id": 1, "settings": {"wins": 0, "fpts": 0, "waiver_budget_used": 140}}]
        self.assertAlmostEqual(self._standings({"waiver_budget": 100}, rosters)["team1"]["remaining_faab"],
                               0.0)

    def test_the_rest_of_the_standings_row_is_unchanged(self):
        rosters = [{"roster_id": 1, "settings": {"wins": 3, "fpts": 412, "fpts_decimal": 56,
                                                 "waiver_budget_used": 8}}]
        row = self._standings({"waiver_budget": 100}, rosters)["team1"]
        self.assertEqual(row["h2h_wins"], 3)
        self.assertAlmostEqual(row["points_scored"], 412.56)


class TestTheAdjustmentWatchdog(unittest.TestCase):
    """RED. A commissioner adjustment has no transaction record; the disagreement is the
    only signal it happened."""

    @staticmethod
    def _check(used, bids, sent, recv):
        from fantasy_sim.sync import faab_adjustments
        return faab_adjustments({1: "team1"},
                                {1: {"used": used, "bids": bids, "sent": sent, "recv": recv}})

    def test_a_roster_whose_history_explains_its_budget_is_silent(self):
        self.assertEqual(self._check(used=56, bids=8, sent=48, recv=0), [])

    def test_faab_TAKEN_AWAY_is_reported_with_its_size(self):
        got = self._check(used=44, bids=43, sent=0, recv=0)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["team"], "team1")
        self.assertAlmostEqual(got[0]["delta"], -1.0, msg="1 removed: used is 1 higher")

    def test_faab_GRANTED_is_reported_too(self):
        got = self._check(used=2, bids=54, sent=0, recv=48)
        self.assertAlmostEqual(got[0]["delta"], +4.0)

    def test_a_trade_alone_is_not_an_adjustment(self):
        """FAAB moved by trade is ALREADY inside waiver_budget_used -- the live 48-point
        trade matches to the point. Counting it again would report a phantom adjustment on
        both sides of every FAAB trade."""
        self.assertEqual(self._check(used=48, bids=0, sent=48, recv=0), [])
        self.assertEqual(self._check(used=-48, bids=0, sent=0, recv=48), [])


if __name__ == "__main__":
    unittest.main()

"""B23: the matchup section is a pre-kickoff view and says so nowhere.

B23 is listed as *"Covered by B3. Listed for completeness: the fix is the mid-week banner,
not a new section."* B3 built that banner, and it does carry the right sentence -- *"The
matchup numbers below are still a fresh-week solve and ignore points already banked"*.

**It is gated on the wrong thing.** The banner renders only when
`lu["pinned"] or lu["locked_excluded"]`, and both are counted from the OWNER'S OWN roster
(`decisions.optimize_lineup`: `pinned_names` is drawn from `names`, this team's players).
So on a Sunday afternoon where the opponent has four players already playing and this
roster has none -- an early-slate opponent against a late-slate roster, which is ordinary
-- `pinned` is 0, `locked_excluded` is 0, no banner renders, and the report states a
matchup probability computed as though nothing had happened yet.

That is exactly B23's complaint surviving B3: the matchup number is a pre-kickoff view,
and in the case that matters most it still says so nowhere.

**TWO SEPARATE FACTS, and conflating them is what caused this.**

  * *Is MY lineup constrained?* -> `pinned` / `locked_excluded`. Owner-scoped, and right
    for the lineup section, which is about what this owner can still change.
  * *Have any games started at all?* -> league-scoped. That is what makes a MATCHUP number
    stale, because the opponent's banked points are in it too.

`optimize_lineup` was handed `locked_teams` and threw the answer away after using it. It
now reports `locks_active`, and the matchup caveat keys off THAT.

**The unconditional line is the other half.** B23 says the section says so *nowhere* --
not "says so too late". Pre-kickoff a fresh-week solve is correct, but a reader still has
to be told that is what they are looking at, so a short statement renders always and the
`live_matchup` pointer escalates on top of it once games are under way.

Written before `locks_active` existed and confirmed failing (rule 1).
"""
import unittest

from fantasy_sim.weekly_report import render_digest, render_html
from tests.test_weekly_report import _fixture_results


def _report(pinned=0, excluded=0, locks_active=False):
    results = _fixture_results()
    lu = results.get("lineup") or {}
    lu.update({"pinned": pinned, "locked_excluded": excluded, "locks_active": locks_active})
    results["lineup"] = lu
    return {"status": "OK", "failed_step": None, "error": None, "results": results,
            "started_at": "x", "finished_at": "y"}


def _both(report):
    return (render_digest(report, team="Quantum Ferrets", week=3),
            render_html(report, team="Quantum Ferrets", week=3))


class TestTheSectionAlwaysSaysWhatItIs(unittest.TestCase):
    """B23 literally: 'says so nowhere'. Pre-kickoff too."""

    def test_pre_kickoff_the_matchup_section_still_declares_itself(self):
        for out in _both(_report()):
            self.assertIn("pre-kickoff", out.lower(),
                          "a reader must be told what this number is, not left to infer it")

    def test_it_names_the_thing_the_number_ignores(self):
        for out in _both(_report()):
            self.assertIn("banked", out.lower())


class TestTheStaleWarningIsLeagueScopedNotOwnerScoped(unittest.TestCase):
    """The defect. An early-slate opponent against a late-slate roster leaves pinned=0."""

    def test_games_started_but_none_of_mine_still_warns(self):
        md, html = _both(_report(pinned=0, excluded=0, locks_active=True))
        for out in (md, html):
            self.assertIn("live_matchup", out,
                          "the opponent's banked points make this number stale even when "
                          "not one of my own players has kicked off")

    def test_my_own_slots_pinned_still_warns(self):
        md, html = _both(_report(pinned=2, excluded=1, locks_active=True))
        for out in (md, html):
            self.assertIn("live_matchup", out)

    def test_pre_kickoff_does_not_claim_games_have_started(self):
        """The guard against fixing this by warning always, which would make the warning
        wallpaper -- the failure CLAUDE.md names for gates that cry wolf."""
        md, html = _both(_report(locks_active=False))
        for out in (md, html):
            self.assertNotIn("live_matchup", out)


class TestTheLineupSectionStaysOwnerScoped(unittest.TestCase):
    """B23's trap in reverse. 'How constrained is MY answer' is a different question from
    'have any games started', and the lineup banner must keep asking the first one -- a
    league-wide lock says nothing about whether this owner can still change anything."""

    def test_locks_elsewhere_do_not_claim_my_slots_are_pinned(self):
        md, _ = _both(_report(pinned=0, excluded=0, locks_active=True))
        self.assertNotIn("0 lineup slot(s) are pinned", md)


class TestOptimizeLineupReportsWhetherLocksWereActive(unittest.TestCase):
    def test_it_is_true_when_locked_teams_is_non_empty(self):
        import inspect
        from fantasy_sim import decisions
        src = inspect.getsource(decisions.optimize_lineup)
        self.assertIn("locks_active", src,
                      "optimize_lineup is handed locked_teams and threw it away after use")

    def test_the_flag_is_independent_of_whether_my_players_were_hit(self):
        """The whole point: locks_active must not be derived from pinned/excluded."""
        import inspect
        from fantasy_sim import decisions
        src = inspect.getsource(decisions.optimize_lineup)
        for wrong in ('"locks_active": bool(pinned)',
                      '"locks_active": bool(excluded)',
                      '"locks_active": bool(pinned or excluded)'):
            self.assertNotIn(wrong, src)


if __name__ == "__main__":
    unittest.main()

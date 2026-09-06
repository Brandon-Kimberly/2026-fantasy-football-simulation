"""scripts.gameday: the one-command pre-lock check (owner usability request,
2026-09-06). Pure logic tested here; the full run is exercised live, not in the suite
(it runs the engine)."""
import unittest

from scripts.gameday import status_changes, render_page


class TestStatusChanges(unittest.TestCase):
    def test_transitions_on_my_roster_are_reported_and_others_ignored(self):
        old = {"A": {"injury_status": None}, "B": {"injury_status": "Questionable"},
               "C": {"injury_status": None}, "X": {"injury_status": None}}
        new = {"A": {"injury_status": "Out"}, "B": {"injury_status": "Questionable"},
               "C": {"injury_status": "Doubtful"}, "X": {"injury_status": "Out"}}
        out = status_changes(old, new, roster=["A", "B", "C"])
        self.assertEqual({(c["player"], c["was"], c["now"]) for c in out},
                         {("A", None, "Out"), ("C", None, "Doubtful")})

    def test_a_player_vanishing_from_baselines_is_flagged_loudly(self):
        old = {"A": {"injury_status": None}}
        out = status_changes(old, {}, roster=["A"])
        self.assertEqual(out[0]["now"], "MISSING FROM BASELINES")


class TestRenderPage(unittest.TestCase):
    def test_page_carries_the_sections_and_the_change_alerts(self):
        html = render_page(
            week=3, synced="2026-09-20T12:00:00Z",
            changes=[{"player": "A", "was": None, "now": "Out"}],
            legend="<div>LOCAL VIEW</div>",
            blocks=[("Optimal lineup", "LINEUP TEXT"), ("Matchup constructions", "MATCHUP TEXT")])
        for needle in ("Gameday", "week 3", "LOCAL VIEW", "Out",
                       "Optimal lineup", "LINEUP TEXT", "MATCHUP TEXT"):
            self.assertIn(needle, html, needle)

    def test_no_changes_says_so_instead_of_an_empty_section(self):
        html = render_page(week=3, synced="t", changes=[], legend="", blocks=[])
        self.assertIn("No status changes", html)


class TestSelfPushingCapture(unittest.TestCase):
    def test_a_gameday_sync_pushes_its_own_log_capture(self):
        """Owner request (2026-09-06): every gameday sync appends irreplaceable
        projection rows, and leaving them for a manual sweep recreated the exact
        human-memory dependency the log-push machinery exists to remove. The sync
        branch must invoke the same warn-never-fail, pathspec-scoped push canonical
        runs use (weekly_report.commit_and_push_logs)."""
        import inspect
        import scripts.gameday as g
        src = inspect.getsource(g.main)
        self.assertIn("commit_and_push_logs", src)
        self.assertLess(src.index("sync_all()"), src.index("commit_and_push_logs("),
                        "the push must follow the sync, inside the synced branch")



if __name__ == "__main__":
    unittest.main()

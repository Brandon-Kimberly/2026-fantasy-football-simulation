"""
tests.test_webui_sunday -- what is still changeable on Sunday (docs/WEB_UI_ROADMAP.md UI-L3).

Sleeper locks each player when his game starts. During the games the useful question is what
can still move: live.changeable takes the newest lineup record's starters and their best
alternatives, and the live clocks (a team whose game has started has less than all of it
left), and marks each slot locked or changeable. A changeable slot offers its alternative
only if the alternative's game has not started either. The live panel draws it once any
game has kicked off.
"""
import unittest

from webui import live                            # outside the probe


class TestChangeable(unittest.TestCase):
    """QB A (KC, under way) is locked; RB C (BUF, not started) is changeable but its
    alternative D plays for KC and is locked too; WR E (DAL) can swap to F (NYG) for 2.5."""

    REC = {"lineup": [{"slot": "QB", "name": "A", "expected": 20.0, "alternative": "B"},
                      {"slot": "RB", "name": "C", "expected": 15.0, "alternative": "D"},
                      {"slot": "WR", "name": "E", "expected": 11.0, "alternative": "F"}],
           "bench": [{"name": "B", "expected": 14.0}, {"name": "D", "expected": 12.0}, {"name": "F", "expected": 8.5}]}
    TEAMS = {"A": "KC", "B": "DAL", "C": "BUF", "D": "KC", "E": "DAL", "F": "NYG"}
    CLOCKS = {"KC": (0.4, "Q3 5:12"), "BUF": (1.0, ""), "DAL": (1.0, ""), "NYG": (1.0, "")}

    def setUp(self):
        self.rows = {r["slot"]: r for r in live.changeable(self.REC, self.CLOCKS, self.TEAMS)}

    def test_only_the_unlocked_slots_are_changeable(self):
        self.assertEqual({s for s, r in self.rows.items() if r["changeable"]}, {"RB", "WR"})
        self.assertTrue(self.rows["QB"]["locked"])

    def test_an_alternative_is_offered_only_if_it_is_unlocked(self):
        self.assertIsNone(self.rows["RB"]["alternative"], "D plays for KC, under way")
        self.assertEqual((self.rows["WR"]["alternative"], self.rows["WR"]["cost"]), ("F", 2.5))

    def test_before_any_kickoff_nothing_is_locked(self):
        rows = live.changeable(self.REC, {}, self.TEAMS)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["changeable"] for r in rows))




if __name__ == "__main__":
    unittest.main()

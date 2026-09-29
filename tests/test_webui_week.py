"""
tests.test_webui_week -- this week's other games and the lineup's three objectives
(docs/WEB_UI_ROADMAP.md UI-M6, UI-L2).

UI-M6  Home carries a strip under the hero with the other three games of the week: each
       with its pre-game chance (the prediction log) and, when this week's forecast wrote
       its per-season record, which side the owner should want (UI-O9's rooting guide).
       Home's live poller redraws each game's score and chance from the live snapshot,
       which already carries every league game (UI-A3).
UI-L2  The matchup tool's record already prices each lineup it builds against the opponent
       AND against the median. Three columns: the lineup with the most expected points, the
       one with the best chance against this opponent, and -- of the lineups the tool built
       -- the one with the best chance against the median. Rows where they differ are
       marked; "all three agree" when none do, as most weeks.
"""
import json
import os
import tempfile
import unittest

from webui import lineups                      # outside the probe: a missing module must fail, not skip


def rec(**lineups_by_key):
    """A matchup_lineup record: {key: (players by slot, mean, p_beat_opponent, p_beat_median)}."""
    return {"tool": "matchup_lineup", "week": 3, "timestamp_utc": "20260927T170804Z",
            "constructions": {k: {"lineup": [{"slot": s, "name": n, "expected": 10.0} for s, n in slots],
                                  "mean": m, "p_beat_opponent": po, "p_beat_median": pm, "se": 0.006}
                              for k, (slots, m, po, pm) in lineups_by_key.items()}}


class TestThreeObjectives(unittest.TestCase):
    def test_the_differing_slots_are_marked(self):
        r = rec(max_mean=([("QB", "Q"), ("RB", "Y"), ("FLEX", "Z")], 150.0, 0.60, 0.55),
                p_max=([("QB", "Q"), ("RB", "Y"), ("FLEX", "W")], 148.0, 0.64, 0.54),
                safe=([("QB", "Q"), ("RB", "V"), ("FLEX", "Z")], 146.0, 0.58, 0.61))
        o = lineups.objectives(r)
        self.assertEqual([c["key"] for c in o["columns"]], ["max_mean", "p_max", "safe"])
        self.assertEqual([(row["slot"], row["differs"]) for row in o["rows"]], [("QB", False), ("RB", True), ("FLEX", True)])
        self.assertFalse(o["agree"])

    def test_most_weeks_all_three_agree(self):
        same = [("QB", "Q"), ("RB", "Y"), ("RB", "X"), ("FLEX", "Z")]
        o = lineups.objectives(rec(max_mean=(same, 150.0, 0.6, 0.7), p_max=(list(reversed(same)), 150.0, 0.6, 0.7)))
        self.assertTrue(o["agree"])
        self.assertFalse(any(row["differs"] for row in o["rows"]), "the same players in a different order agree")


try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.glance import home_report
    from webui.live import LiveBoard
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import CB, CM, NW, PY, RP, TL, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def get(self, path, mode="dev"):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        r = app.test_client().get(path)
        self.assertEqual(r.status_code, 200, path)
        return r.get_data(as_text=True)


class TestOtherGames(Case):
    def test_home_knows_the_other_three_games(self):
        others = home_report(self.root, MY_TEAM)["others"]
        self.assertEqual(sorted(tuple(sorted((g["a"], g["b"]))) for g in others),
                         sorted([tuple(sorted(p)) for p in ((NW, CM), (RP, CB), (TL, PY))]))

    def test_the_strip_renders_in_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                body = self.get("/", mode)
                self.assertIn('id="others"', body)
                self.assertEqual(body.count('class="og"'), 3)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in visible_text(body)], [])


class TestLineupsOnMatchups(Case):
    def test_the_current_week_shows_the_three_objectives(self):
        r = rec(max_mean=([("QB", "Player 0 O'Neil")], 150.0, 0.60, 0.55), p_max=([("QB", "Player 0 O'Neil")], 150.0, 0.60, 0.55))
        r.update(team=MY_TEAM, opponent="Iron Wombats")
        with open(os.path.join(self.td.name, "data", "decisions", "week_03", "matchup_20260927T170804Z_week3.json"), "w", encoding="utf-8") as fh:
            json.dump(r, fh)
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                text = visible_text(self.get("/matchups/week-3", mode))
                self.assertIn("Three ways to set your lineup", text)
                self.assertIn("all three agree", text)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in text], [])


if __name__ == "__main__":
    unittest.main()

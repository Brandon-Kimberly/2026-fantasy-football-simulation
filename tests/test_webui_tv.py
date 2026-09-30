"""
tests.test_webui_tv -- the game-day TV view, second version (docs/WEB_UI_ROADMAP.md UI-M9).

The scoreboard the live panel already reads carries far more than scores: each game's
DraftKings line and total, the weather for an outdoor game, the stat leaders (the season's
before kickoff, the game's once it starts), and each team's colour. The parser now keeps them,
and the TV view shows them with the local logos (Decision 3), in three panes on a wide screen.

The recorded payload: ESPN's week-4 scoreboard of 2026-09-29, trimmed to two games --
PIT @ CLE outdoors (Cloudy, 77 degrees; DraftKings PIT -2.5, over/under 38.5) and IND vs WSH
with no weather block (IND -3.5, 47.5). A payload with none of these renders none of them and
raises nothing.
"""
import json
import os
import unittest

from webui.live import scoreboard

HERE = os.path.dirname(os.path.abspath(__file__))


def recorded():
    with open(os.path.join(HERE, "fixtures", "scoreboard_week4.json"), encoding="utf-8") as fh:
        return json.load(fh)


def fetch_recorded(url):
    return recorded()


class TestTheParser(unittest.TestCase):
    def games(self):
        return {g["away"] + "@" + g["home"]: g for g in scoreboard(4, fetch_recorded)[1]}

    def test_lines_weather_leaders_and_colours(self):
        g = self.games()["PIT@CLE"]
        self.assertEqual((g["line"], g["total"], g["book"]), ("PIT -2.5", 38.5, "Draft Kings"))
        self.assertEqual(g["weather"], "Cloudy, 77°")
        self.assertEqual(g["colors"], {"CLE": "#472a08", "PIT": "#000000"})
        self.assertTrue(g["leaders"] and all(set(x) == {"stat", "name", "value"} for x in g["leaders"]))
        self.assertTrue(g["kickoff"].startswith("2026-10-0"))

    def test_an_indoor_game_has_no_weather(self):
        g = self.games()["IND@WSH"] if "IND@WSH" in self.games() else self.games()["WSH@IND"]
        self.assertIsNone(g["weather"])
        self.assertEqual(g["line"], "IND -3.5")

    def test_a_bare_payload_renders_nothing_extra_and_raises_nothing(self):
        bare = {"events": [{"competitions": [{"status": {"type": {"state": "pre"}},
                                              "competitors": [{"homeAway": "home", "team": {"abbreviation": "KC"}},
                                                              {"homeAway": "away", "team": {"abbreviation": "LV"}}]}]}]}
        (g,) = scoreboard(4, lambda url: bare)[1]
        self.assertEqual((g["line"], g["total"], g["weather"], g["leaders"], g["colors"]), (None, None, None, [], {}))


if __name__ == "__main__":
    unittest.main()

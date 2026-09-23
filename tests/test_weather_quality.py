"""B18 / F55: the three data faults, fixed BEFORE the study, not after.

F55 measured that weather is fetched every sync and read by nothing, recorded the
offseason measurement plan, and named three faults that must be repaired first -- because
a study run on this data would measure the faults rather than the weather:

1. **`precip_prob` is a PROBABILITY, not an amount.** `precipitation_probability_max`
   cannot distinguish a certain drizzle from a certain flood; both read 100. The Chicago
   downpour that started all this is exactly the case it cannot see.
2. **Both values are DAILY maxima, not game-time.** A 1pm kickoff inherits the whole
   day's peak wind. For a study that has to detect a modest residual, that is a large
   smear.
3. **A failed fetch is indistinguishable from a calm day.** F57 made the LOG loud, which
   was half the fix: the stored VALUE is still `0.0`, so anyone reading the file -- or the
   offseason study reading it back -- sees perfect conditions. Unknown must not be
   spelled the same way as calm.

**THE STUDY ITSELF IS NOT STARTED, DELIBERATELY.** B18 says *"Do not start it before the
season ends"*, and F55 fixes the adoption bar in advance precisely so the result cannot be
fitted to a preferred answer. Nothing here touches `_script_multiplier`, and nothing in
the engine reads these fields. The standing decision holds: do NOT wire weather into the
environment model on intuition -- the team-level effect is already in the line, and the
genuine gap is positional.

**THE KEYS ARE ADDED, NOT RENAMED.** `wind_mph` and `precip_prob` appear in some thirty
default-dict literals across `config.py` and the golden-pinned monoliths in
`simulation.py`. Renaming them would edit code the golden master pins byte-exactly for no
behavioural gain (rule 4). `precip_in` and `weather_source` are new keys beside them.

`weather_source` also closes F55's LIVE HAZARD -- *"a populated field that nothing reads is
indistinguishable from a working feature"*. A reader opening `vegas_totals.json` can now
tell a real forecast from a dome from a failed lookup without reading the sync log.

Written before the helper existed and confirmed failing (rule 1).
"""
import unittest

# Open-Meteo hourly, timezone=UTC. Kickoff 17:00Z; the day's PEAK is at 22:00Z, hours
# after the game ended -- which is exactly what the daily-max fault was picking up.
HOURLY = {
    "time": ["2026-09-28T15:00", "2026-09-28T16:00", "2026-09-28T17:00",
             "2026-09-28T18:00", "2026-09-28T19:00", "2026-09-28T20:00",
             "2026-09-28T21:00", "2026-09-28T22:00"],
    "wind_speed_10m": [10.0, 12.0, 16.09, 16.09, 16.09, 20.0, 30.0, 60.0],
    "precipitation": [0.0, 0.0, 12.7, 12.7, 0.0, 0.0, 0.0, 50.0],
    "precipitation_probability": [5, 10, 80, 90, 40, 20, 10, 100],
}
KICKOFF = "2026-09-28T17:00:00Z"


class TestTheGameWindowNotTheDay(unittest.TestCase):
    """Fault 2."""

    def test_wind_is_the_game_window_mean_not_the_daily_peak(self):
        from fantasy_sim.sync import game_window_weather
        got = game_window_weather(HOURLY, KICKOFF, window_hours=3)
        # 16.09 km/h across all three game hours = 10.0 mph. The day peaks at 60 km/h.
        self.assertAlmostEqual(got["wind_mph"], 10.0, places=1)

    def test_the_window_is_three_hours_from_kickoff(self):
        from fantasy_sim.sync import game_window_weather
        got = game_window_weather(HOURLY, KICKOFF, window_hours=3)
        self.assertEqual(got["hours_used"], 3)

    def test_a_later_kickoff_reads_different_weather(self):
        """If the window were ignored every kickoff would return the same number."""
        from fantasy_sim.sync import game_window_weather
        early = game_window_weather(HOURLY, "2026-09-28T15:00:00Z", window_hours=3)
        late = game_window_weather(HOURLY, "2026-09-28T20:00:00Z", window_hours=3)
        self.assertLess(early["wind_mph"], late["wind_mph"])


class TestPrecipitationIsAnAmount(unittest.TestCase):
    """Fault 1. A certain drizzle and a certain flood both read 100 as a probability."""

    def test_it_reports_inches_accumulated_in_the_window(self):
        from fantasy_sim.sync import game_window_weather
        got = game_window_weather(HOURLY, KICKOFF, window_hours=3)
        # 12.7 + 12.7 + 0.0 mm = 25.4 mm = exactly 1 inch.
        self.assertAlmostEqual(got["precip_in"], 1.0, places=3)

    def test_the_amount_excludes_rain_outside_the_window(self):
        """50mm falls at 22:00, long after the whistle."""
        from fantasy_sim.sync import game_window_weather
        got = game_window_weather(HOURLY, KICKOFF, window_hours=3)
        self.assertLess(got["precip_in"], 2.0)

    def test_the_probability_is_kept_too_and_is_the_window_max(self):
        """Not a replacement -- the study wants both, and they answer different
        questions."""
        from fantasy_sim.sync import game_window_weather
        got = game_window_weather(HOURLY, KICKOFF, window_hours=3)
        self.assertEqual(got["precip_prob"], 90.0)

    def test_a_certain_drizzle_and_a_certain_flood_are_now_distinguishable(self):
        from fantasy_sim.sync import game_window_weather
        drizzle = dict(HOURLY, precipitation=[0.0] * 2 + [0.2, 0.2, 0.2] + [0.0] * 3,
                       precipitation_probability=[100] * 8)
        flood = dict(HOURLY, precipitation=[0.0] * 2 + [25.0, 25.0, 25.0] + [0.0] * 3,
                     precipitation_probability=[100] * 8)
        a = game_window_weather(drizzle, KICKOFF, window_hours=3)
        b = game_window_weather(flood, KICKOFF, window_hours=3)
        self.assertEqual(a["precip_prob"], b["precip_prob"], "identical as probabilities")
        self.assertGreater(b["precip_in"], a["precip_in"] * 50, "and unmistakable as amounts")


class TestUnknownIsNotCalm(unittest.TestCase):
    """Fault 3, at the DATA level. F57 made the log loud; the stored value still said 0.0,
    which the offseason study would read back as a perfect day."""

    def test_a_window_the_data_does_not_cover_returns_none(self):
        from fantasy_sim.sync import game_window_weather
        self.assertIsNone(game_window_weather(HOURLY, "2026-09-29T13:00:00Z"))

    def test_an_empty_payload_returns_none_rather_than_zeros(self):
        from fantasy_sim.sync import game_window_weather
        self.assertIsNone(game_window_weather({}, KICKOFF))
        self.assertIsNone(game_window_weather(None, KICKOFF))

    def test_an_unparseable_kickoff_returns_none(self):
        from fantasy_sim.sync import game_window_weather
        self.assertIsNone(game_window_weather(HOURLY, "not-a-timestamp"))
        self.assertIsNone(game_window_weather(HOURLY, ""))

    def test_a_partially_covered_window_is_still_none(self):
        """Two of three hours is not the game. Averaging what is there would silently
        shrink the window and report it as if it were full."""
        from fantasy_sim.sync import game_window_weather
        short = {k: (v[:7] if isinstance(v, list) else v) for k, v in HOURLY.items()}
        self.assertIsNone(game_window_weather(short, "2026-09-28T20:00:00Z", window_hours=3))


class TestTheWindowCrossesMidnight(unittest.TestCase):
    """A Sunday-night kickoff is 00:20Z the next day, so the hours live in two API days.
    Getting this wrong silently drops every night game from the study."""

    def test_a_late_kickoff_spanning_two_dates_resolves(self):
        from fantasy_sim.sync import game_window_weather
        hourly = {
            "time": ["2026-09-28T23:00", "2026-09-29T00:00", "2026-09-29T01:00",
                     "2026-09-29T02:00"],
            "wind_speed_10m": [16.09, 16.09, 16.09, 16.09],
            "precipitation": [0.0, 0.0, 0.0, 0.0],
            "precipitation_probability": [10, 10, 10, 10],
        }
        got = game_window_weather(hourly, "2026-09-29T00:00:00Z", window_hours=3)
        self.assertIsNotNone(got, "a night game must not fall off the end of the day")
        self.assertAlmostEqual(got["wind_mph"], 10.0, places=1)

    def test_the_request_covers_the_day_after_kickoff(self):
        """The URL must ask for both dates or the hours above will not exist."""
        from fantasy_sim.sync import weather_request_dates
        self.assertEqual(weather_request_dates("2026-09-29T00:20:00Z"),
                         ("2026-09-29", "2026-09-29"))
        self.assertEqual(weather_request_dates("2026-09-28T23:30:00Z"),
                         ("2026-09-28", "2026-09-29"))


class TestTheFileSaysWhereItsWeatherCameFrom(unittest.TestCase):
    """F55's live hazard: 'a populated field that nothing reads is indistinguishable from
    a working feature'. A reader must be able to tell a forecast from a dome from a
    failure without going to the sync log."""

    def test_the_source_labels_are_distinct_and_documented(self):
        from fantasy_sim.sync import WEATHER_SOURCES
        self.assertEqual(set(WEATHER_SOURCES),
                         {"forecast", "dome", "unavailable", "no_game"})

    def test_a_dome_is_known_calm_not_unknown(self):
        """An indoor game really is 0.0 wind. That is a fact, not a fallback, and it must
        not be spelled the same way as a failed lookup."""
        from fantasy_sim.sync import dome_weather
        got = dome_weather()
        self.assertEqual(got["wind_mph"], 0.0)
        self.assertEqual(got["precip_in"], 0.0)
        self.assertEqual(got["weather_source"], "dome")

    def test_an_unavailable_lookup_carries_nulls_and_says_so(self):
        from fantasy_sim.sync import unknown_weather
        got = unknown_weather()
        self.assertIsNone(got["wind_mph"])
        self.assertIsNone(got["precip_in"])
        self.assertIsNone(got["precip_prob"])
        self.assertEqual(got["weather_source"], "unavailable")


class TestTheStudyIsNotStarted(unittest.TestCase):
    """B18: 'Do not start it before the season ends.' F55's standing decision: do NOT wire
    weather into the environment model on intuition. These guard the boundary."""

    def test_the_script_multiplier_still_reads_no_weather(self):
        import inspect
        from fantasy_sim.simulation import FantasySimulationEngine
        src = inspect.getsource(FantasySimulationEngine._script_multiplier)
        for key in ("wind_mph", "precip_prob", "precip_in", "weather"):
            self.assertNotIn(key, src,
                             "B18/F55: adoption is offseason and needs a holdout; the "
                             "team-level effect is already in the line")

    def test_week_expectation_still_reads_no_weather(self):
        """`week_expectation` is a free function in `decisions`, not an engine method --
        it is the tools' shared entry point to a week-level number, so it is the other
        place a weather term would have to appear."""
        import inspect
        from fantasy_sim import decisions
        src = inspect.getsource(decisions.week_expectation)
        for key in ("wind_mph", "precip_prob", "precip_in"):
            self.assertNotIn(key, src)


if __name__ == "__main__":
    unittest.main()

"""B12: promote the scratchpad data-health script into a real tool.

`health.py` checks every source the model consumes and prints PASS / DEGRADED / FAIL, so
a silent fallback -- the failure mode that produced F52 and F54 -- cannot hide behind a
successful-looking sync.

HOW THIS DIFFERS FROM WHAT B6 ALREADY BUILT, because the overlap is real and worth being
explicit about. F57's `sources` block and `check_freshness` answer *"did the last sync
succeed, and what did each source deliver during it?"* This answers *"is the data I am
about to make decisions on healthy?"* -- which is a different question with different
evidence:

  - ESPN blend coverage **per week across the season**, from the projection log, not just
    this sync. That history is what makes F52's fortnight-long outage visible after the
    fact; a per-sync counter cannot show it.
  - F54's posterior reach: how many distinct players have an observed score at all.
  - Vegas fallback detection by VALUE (a team sitting on the flat 21.5) rather than by
    the sync reporting an error, because F52's whole lesson is that a source can succeed
    and still deliver nothing usable.

So the tool defers sync-time status to `check_freshness` and does not restate it.

The checks are pure -- loaded data in, verdicts out -- so the tests need no files, no
network and no engine.

Written before the module existed and confirmed failing (rule 1).
"""
import unittest

PASS, DEGRADED, FAIL = "PASS", "DEGRADED", "FAIL"


class TestVerdictOrdering(unittest.TestCase):
    def test_the_worst_verdict_wins(self):
        from fantasy_sim.data_health import worst
        self.assertEqual(worst([PASS, PASS]), PASS)
        self.assertEqual(worst([PASS, DEGRADED]), DEGRADED)
        self.assertEqual(worst([DEGRADED, FAIL, PASS]), FAIL)

    def test_no_checks_is_not_silently_a_pass(self):
        from fantasy_sim.data_health import worst
        self.assertEqual(worst([]), FAIL,
                         "an empty report means the checks did not run, which is a "
                         "failure, not a clean bill of health")


class TestProjectionCoverage(unittest.TestCase):
    def test_a_healthy_baseline_file_passes(self):
        from fantasy_sim.data_health import projection_coverage
        base = {f"p{i}": {"mean": 10.0} for i in range(900)}
        self.assertEqual(projection_coverage(base)["verdict"], PASS)

    def test_a_slightly_short_baseline_file_is_degraded(self):
        from fantasy_sim.data_health import projection_coverage
        base = {f"p{i}": {"mean": 10.0} for i in range(500)}
        self.assertEqual(projection_coverage(base)["verdict"], DEGRADED)

    def test_a_badly_short_baseline_file_fails(self):
        """300 of the ~888 a healthy sync writes is not a degradation, it is a broken
        file -- the band matters, so both sides of it are pinned."""
        from fantasy_sim.data_health import projection_coverage
        base = {f"p{i}": {"mean": 10.0} for i in range(300)}
        self.assertEqual(projection_coverage(base)["verdict"], FAIL)

    def test_an_empty_baseline_file_fails(self):
        """F58's wipe: this is what the file looks like after it."""
        from fantasy_sim.data_health import projection_coverage
        self.assertEqual(projection_coverage({})["verdict"], FAIL)

    def test_entries_with_a_zero_mean_do_not_count_as_coverage(self):
        from fantasy_sim.data_health import projection_coverage
        base = {f"p{i}": {"mean": 0.0} for i in range(900)}
        self.assertEqual(projection_coverage(base)["verdict"], FAIL)


class TestEspnCoverageByWeek(unittest.TestCase):
    ROWS = [
        {"season": "2026", "week": 1, "espn_mean": 12.0},
        {"season": "2026", "week": 1, "espn_mean": None},
        {"season": "2026", "week": 2, "espn_mean": None},
        {"season": "2026", "week": 2, "espn_mean": None},
        {"season": "2026", "week": 3, "espn_mean": 9.0},
        {"season": "2026", "week": 3, "espn_mean": 8.0},
        {"season": "2025", "week": 9, "espn_mean": 5.0},      # another season, ignored
    ]

    def test_it_reports_one_ratio_per_week(self):
        from fantasy_sim.data_health import espn_coverage_by_week
        r = espn_coverage_by_week(self.ROWS, "2026")
        self.assertEqual(r["by_week"], {1: (1, 2), 2: (0, 2), 3: (2, 2)})

    def test_a_dead_week_is_visible_even_when_the_latest_week_is_fine(self):
        """This is the whole point: F52 killed the blend for a fortnight and the CURRENT
        sync looked healthy throughout."""
        from fantasy_sim.data_health import espn_coverage_by_week
        r = espn_coverage_by_week(self.ROWS, "2026")
        self.assertIn(2, r["dead_weeks"])
        self.assertEqual(r["verdict"], DEGRADED)

    def test_all_weeks_covered_passes(self):
        from fantasy_sim.data_health import espn_coverage_by_week
        rows = [{"season": "2026", "week": w, "espn_mean": 1.0} for w in (1, 2, 3)]
        self.assertEqual(espn_coverage_by_week(rows, "2026")["verdict"], PASS)

    def test_no_rows_at_all_fails(self):
        from fantasy_sim.data_health import espn_coverage_by_week
        self.assertEqual(espn_coverage_by_week([], "2026")["verdict"], FAIL)


class TestBlendCoverage(unittest.TestCase):
    def test_it_counts_distinct_players_with_an_observed_score(self):
        from fantasy_sim.data_health import blend_coverage
        wa = {"week_1": {"player_scores": {str(i): 5.0 for i in range(600)}},
              "week_2": {"player_scores": {str(i): 5.0 for i in range(400, 1100)}}}
        r = blend_coverage(wa)
        self.assertEqual(r["players"], 1100)
        self.assertEqual(r["verdict"], PASS)

    def test_the_pre_f54_number_is_degraded_not_passing(self):
        """157 players was the broken state; it must not read as healthy."""
        from fantasy_sim.data_health import blend_coverage
        wa = {"week_1": {"player_scores": {str(i): 5.0 for i in range(157)}}}
        self.assertEqual(blend_coverage(wa)["verdict"], DEGRADED)

    def test_no_completed_weeks_is_not_a_failure_in_week_one(self):
        """Before any game is played there is nothing to observe, and saying FAIL would
        cry wolf every preseason."""
        from fantasy_sim.data_health import blend_coverage
        r = blend_coverage({})
        self.assertEqual(r["verdict"], PASS)
        self.assertIn("no completed weeks", r["detail"])


class TestVegasHealth(unittest.TestCase):
    def test_real_lines_pass(self):
        from fantasy_sim.data_health import vegas_health
        veg = {"_meta": {"week": 3, "source": "odds_api"}, "FA": {"total": 20.0},
               "KC": {"total": 24.5, "opponent": "BUF"},
               "BUF": {"total": 26.0, "opponent": "KC"}}
        self.assertEqual(vegas_health(veg, week=3)["verdict"], PASS)

    # A test asserting fallbacks were detected BY TOTAL == 21.5 used to sit here. It was
    # written an hour before the tool was first run against live data, which showed DEN
    # holding a REAL 21.5 line. Detection moved to `opponent`, and
    # TestVegasFallbackIsDetectedByOpponentNotByTotal below supersedes it.

    def test_a_stale_week_stamp_fails(self):
        from fantasy_sim.data_health import vegas_health
        veg = {"_meta": {"week": 1, "source": "odds_api"}, "KC": {"total": 24.5}}
        self.assertEqual(vegas_health(veg, week=3)["verdict"], FAIL)

    def test_everything_on_the_fallback_fails(self):
        from fantasy_sim.data_health import vegas_health
        veg = {"_meta": {"week": 3, "source": "fallback_no_api_key"},
               "KC": {"total": 21.5}, "BUF": {"total": 21.5}}
        self.assertEqual(vegas_health(veg, week=3)["verdict"], FAIL)


class TestScheduleHealth(unittest.TestCase):
    def test_a_complete_schedule_passes(self):
        from fantasy_sim.data_health import schedule_health
        s = {"_meta": {"failed_weeks": [], "byes": {f"T{i}": 5 for i in range(32)}}}
        self.assertEqual(schedule_health(s)["verdict"], PASS)

    def test_missing_byes_are_degraded(self):
        from fantasy_sim.data_health import schedule_health
        s = {"_meta": {"failed_weeks": [], "byes": {f"T{i}": 5 for i in range(20)}}}
        self.assertEqual(schedule_health(s)["verdict"], DEGRADED)

    def test_a_failed_week_fails(self):
        from fantasy_sim.data_health import schedule_health
        s = {"_meta": {"failed_weeks": [7], "byes": {f"T{i}": 5 for i in range(32)}}}
        self.assertEqual(schedule_health(s)["verdict"], FAIL)


class TestItDefersToCheckFreshness(unittest.TestCase):
    """The overlap with B6/F57 is handled by NOT restating it."""

    def test_the_module_points_at_check_freshness_rather_than_duplicating_it(self):
        import inspect
        from fantasy_sim import data_health
        self.assertIn("check_freshness", inspect.getsource(data_health))


if __name__ == "__main__":
    unittest.main()


class TestVegasFallbackIsDetectedByOpponentNotByTotal(unittest.TestCase):
    """Found by running the tool live (2026-09-23).

    The first version called a team "on the fallback" whenever its total equalled 21.5.
    DEN's real week-3 line WAS 21.5 -- with a real opponent, a 2.5 spread and live
    weather -- so a genuine market line was reported as a fallback. That is a false
    positive in a tool whose entire job is telling real data from filler.

    The fallback path in sync.fetch_vegas_implied_totals sets `opponent: "FA"` and
    `spread: 0.0`; a real line never does. So the opponent is the discriminator and the
    total is not.
    """

    def test_a_real_line_that_happens_to_be_21_5_is_not_a_fallback(self):
        from fantasy_sim.data_health import vegas_health
        veg = {"_meta": {"week": 3, "source": "odds_api"},
               "DEN": {"total": 21.5, "spread": 2.5, "opponent": "LAR"},
               "KC": {"total": 24.5, "spread": -3.0, "opponent": "BUF"}}
        r = vegas_health(veg, week=3)
        self.assertEqual(r["fallback"], 0, "DEN has an opponent; that is a real line")
        self.assertEqual(r["verdict"], PASS)

    def test_a_team_with_no_opponent_is_a_fallback_whatever_its_total(self):
        from fantasy_sim.data_health import vegas_health
        veg = {"_meta": {"week": 3, "source": "odds_api"},
               "KC": {"total": 24.5, "spread": -3.0, "opponent": "BUF"},
               "NYJ": {"total": 21.5, "spread": 0.0, "opponent": "FA"}}
        r = vegas_health(veg, week=3)
        self.assertEqual(r["fallback"], 1)

    def test_teams_on_bye_are_separated_from_missing_lines(self):
        """A bye leaves a team legitimately without a game. Counting that as degraded
        would cry wolf every single bye week -- F41's shape."""
        from fantasy_sim.data_health import vegas_health
        veg = {"_meta": {"week": 3, "source": "odds_api"},
               "KC": {"total": 24.5, "spread": -3.0, "opponent": "BUF"},
               "NYJ": {"total": 21.5, "spread": 0.0, "opponent": "FA"}}
        r = vegas_health(veg, week=3, byes={"NYJ": 3})
        self.assertEqual(r["on_bye"], 1)
        self.assertEqual(r["fallback"], 0)
        self.assertEqual(r["verdict"], PASS)

    def test_a_non_bye_team_with_no_line_is_still_degraded(self):
        from fantasy_sim.data_health import vegas_health
        veg = {"_meta": {"week": 3, "source": "odds_api"},
               "KC": {"total": 24.5, "spread": -3.0, "opponent": "BUF"},
               "NYJ": {"total": 21.5, "spread": 0.0, "opponent": "FA"}}
        r = vegas_health(veg, week=3, byes={"NYJ": 9})
        self.assertEqual(r["fallback"], 1)
        self.assertEqual(r["verdict"], DEGRADED)

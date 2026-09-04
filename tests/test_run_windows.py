"""Canonical-run window computation (scheduling ASSISTANT, not a runner -- R1 makes
unattended engine runs unsafe). All cases hand-computed against America/Los_Angeles:
week 1 opens WEDNESDAY 2026-09-09 17:20 PDT (2026-09-10T00:20Z, the real opener), so the
shifted-first-kickoff case is the live one. Written before fantasy_sim.run_windows existed."""
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fantasy_sim.run_windows import compute_windows, parse_canonical_digest, release_advice

PT = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc


def u(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


W1 = [u("2026-09-10T00:20:00"), u("2026-09-13T17:00:00"), u("2026-09-15T00:15:00")]
W2 = [u("2026-09-18T00:15:00"), u("2026-09-20T17:00:00"), u("2026-09-22T00:15:00")]
KICKS = {1: W1, 2: W2}


class TestComputeWindows(unittest.TestCase):
    def _by_name(self, r):
        return {w["name"]: w for w in r["windows"]}

    def test_week1_lead_in_run1_open_with_the_wednesday_kickoff_deadline(self):
        r = compute_windows(u("2026-09-02T18:00:00"), KICKS, [])
        self.assertEqual(r["target_week"], 1)
        w = self._by_name(r)
        self.assertEqual(w["run1_pre_kickoff"]["status"], "OPEN")
        self.assertEqual(w["run1_pre_kickoff"]["start"], datetime(2026, 9, 2, 0, 0, tzinfo=PT))
        self.assertEqual(w["run1_pre_kickoff"]["deadline"], u("2026-09-10T00:20:00"))
        self.assertEqual(w["run2_sunday"]["status"], "UPCOMING")
        self.assertEqual(w["run2_sunday"]["deadline"], datetime(2026, 9, 13, 10, 0, tzinfo=PT))
        self.assertEqual(w["run3_tuesday"]["status"], "UPCOMING")
        self.assertEqual(w["run3_tuesday"]["deadline"], datetime(2026, 9, 16, 0, 0, tzinfo=PT))

    def test_a_canonical_run_before_the_cycle_start_is_listed_outside_every_window(self):
        stamps = [("weekly_report_week1_20260902T024723Z.md", u("2026-09-02T02:47:23"))]
        r = compute_windows(u("2026-09-02T18:00:00"), KICKS, stamps)
        w = self._by_name(r)
        self.assertEqual(w["run1_pre_kickoff"]["status"], "OPEN", "the pre-cycle run does not cover run 1")
        self.assertEqual(r["outside_windows"], ["weekly_report_week1_20260902T024723Z.md"])

    def test_a_stamp_inside_a_window_covers_it(self):
        stamps = [("weekly_report_week1_20260905T120000Z.md", u("2026-09-05T12:00:00"))]
        r = compute_windows(u("2026-09-06T18:00:00"), KICKS, stamps)
        w = self._by_name(r)
        self.assertEqual(w["run1_pre_kickoff"]["status"], "COVERED")
        self.assertEqual(w["run1_pre_kickoff"]["covered_by"], "weekly_report_week1_20260905T120000Z.md")

    def test_uncovered_windows_past_their_deadline_are_missed(self):
        r = compute_windows(u("2026-09-13T18:00:00"), KICKS, [])   # Sunday 11:00 PDT
        w = self._by_name(r)
        self.assertEqual(w["run1_pre_kickoff"]["status"], "MISSED")
        self.assertEqual(w["run2_sunday"]["status"], "MISSED")
        self.assertEqual(w["run3_tuesday"]["status"], "UPCOMING")

    def test_normal_week_cycle_starts_the_wednesday_after_the_previous_monday_game(self):
        r = compute_windows(u("2026-09-17T00:00:00"), KICKS, [])   # Wed Sep 16, 17:00 PDT
        self.assertEqual(r["target_week"], 2)
        w = self._by_name(r)
        self.assertEqual(w["run1_pre_kickoff"]["start"], datetime(2026, 9, 16, 0, 0, tzinfo=PT))
        self.assertEqual(w["run1_pre_kickoff"]["deadline"], u("2026-09-18T00:15:00"))
        self.assertEqual(w["run1_pre_kickoff"]["status"], "OPEN")

    def test_an_early_sunday_kickoff_is_flagged_but_the_deadline_stays_ten_am(self):
        kicks = {1: W1, 2: sorted(W2 + [u("2026-09-20T13:30:00")])}  # London: 06:30 PDT
        r = compute_windows(u("2026-09-17T00:00:00"), kicks, [])
        w = self._by_name(r)
        self.assertEqual(w["run2_sunday"]["deadline"], datetime(2026, 9, 20, 10, 0, tzinfo=PT))
        self.assertTrue(any("06:30" in f for f in r["flags"]),
                        "the London game is visible even though the 10:00 rule stands")

    def test_the_tuesday_week_roll_flag_fires_only_in_run3_with_an_unrolled_state(self):
        tue = u("2026-09-15T20:00:00")                             # Tuesday 13:00 PDT
        r = compute_windows(tue, KICKS, [], state_week=1)
        self.assertTrue(any("roll" in f.lower() for f in r["flags"]))
        r = compute_windows(tue, KICKS, [], state_week=2)
        self.assertFalse(any("roll" in f.lower() for f in r["flags"]))
        r = compute_windows(u("2026-09-13T15:00:00"), KICKS, [], state_week=1)  # Sunday: not run3
        self.assertFalse(any("roll" in f.lower() for f in r["flags"]))

    def test_pst_weeks_localize_correctly_after_the_dst_change(self):
        kicks = {9: [u("2026-11-06T01:15:00"), u("2026-11-08T18:05:00"), u("2026-11-10T01:15:00")]}
        r = compute_windows(u("2026-11-04T12:00:00"), kicks, [])
        w = self._by_name(r)
        self.assertEqual(w["run2_sunday"]["deadline"].astimezone(UTC),
                         u("2026-11-08T18:00:00"), "10:00 PST, not PDT")


class TestParseCanonicalDigest(unittest.TestCase):
    """Coverage detection must accept both digest name shapes -- plain stamps and the
    window-infixed canonical names -- and never count a _FAILED digest. Written before
    parse_canonical_digest existed."""

    def test_both_name_shapes_parse_and_failures_and_wrong_weeks_do_not(self):
        dt = parse_canonical_digest("weekly_report_week1_20260909T001000Z.md", 1)
        self.assertEqual((dt.year, dt.hour, dt.minute), (2026, 0, 10))
        dt = parse_canonical_digest("weekly_report_week1_run1_pre_kickoff_20260909T001000Z.md", 1)
        self.assertIsNotNone(dt)
        self.assertIsNone(parse_canonical_digest("weekly_report_week1_20260909T001000Z_FAILED.md", 1))
        self.assertIsNone(parse_canonical_digest("weekly_report_week2_20260909T001000Z.md", 1))
        self.assertIsNone(parse_canonical_digest("lineup_20260909T001000Z_week1.json", 1))


class TestReleaseAdvice(unittest.TestCase):
    """The release policy's reminder (CLAUDE.md): pure advice from (latest local tag,
    goldens-changed-since-tag, current week). Small and fires where the owner already
    looks (the run_windows status line), never a commit-time gate -- tagging is a
    release-time act. Written before release_advice existed."""

    def test_no_local_tags_says_fetch_rather_than_untagged(self):
        # The exact live state this was built in: v1.0.0 existed on GitHub (UI-created,
        # server-side tag) while the clone saw nothing. Silently reporting "untagged"
        # would be wrong twice.
        msgs = release_advice(None, False, 3)
        self.assertEqual(len(msgs), 1)
        self.assertIn("git fetch --tags", msgs[0])

    def test_goldens_regenerated_since_tag_flags_a_pending_major(self):
        msgs = release_advice("v1.0.0", True, 3)
        self.assertTrue(any("MAJOR" in m and "v1.0.0" in m for m in msgs))
        self.assertEqual(release_advice("v1.0.0", False, 3), [], "quiet when nothing is due")

    def test_milestone_weeks_fire_in_their_window_and_go_quiet_after(self):
        self.assertTrue(any("F25" in m for m in release_advice("v1.0.0", False, 5)))
        self.assertTrue(any("F25" in m for m in release_advice("v1.0.0", False, 6)))
        self.assertEqual(release_advice("v1.0.0", False, 8), [], "week 8: no nagging")
        self.assertTrue(any("deadline" in m for m in release_advice("v1.0.0", False, 11)))
        self.assertTrue(any("playoff" in m.lower() for m in release_advice("v1.0.0", False, 15)))
        self.assertTrue(any("season end" in m.lower() for m in release_advice("v1.0.0", False, 17)))

    def test_no_remaining_windows_reads_as_season_end(self):
        msgs = release_advice("v1.0.0", False, None)
        self.assertTrue(any("season end" in m.lower() for m in msgs))


class TestStampsFromPredictionsRows(unittest.TestCase):
    """Coverage detection for GitHub Actions (tier 1, 2026-09-04): a runner has no
    data/decisions (untracked), so coverage comes from the COMMITTED predictions log's
    canonical rows -- pushed at canonical time by weekly_report's logs_push step."""

    ROWS = [
        {"record_type": "week_predictions", "week": 1, "canonical": True,
         "logged_at": "2026-09-09T18:00:00Z"},
        {"record_type": "week_predictions", "week": 1, "canonical": False,
         "logged_at": "2026-09-09T19:00:00Z"},          # ad-hoc run: not a stamp
        {"record_type": "week_predictions", "week": 2, "canonical": True,
         "logged_at": "2026-09-16T18:00:00Z"},          # other week
        {"record_type": "week_predictions", "week": 1, "canonical": True,
         "logged_at": "not-a-time"},                    # malformed: skipped, not fatal
    ]

    def test_only_canonical_rows_for_the_week_become_stamps(self):
        from fantasy_sim.run_windows import stamps_from_predictions_rows
        stamps = stamps_from_predictions_rows(self.ROWS, 1)
        self.assertEqual(len(stamps), 1)
        name, dt = stamps[0]
        self.assertIn("2026-09-09T18:00:00Z", name)
        self.assertEqual(dt, u("2026-09-09T18:00:00"))

    def test_stamps_cover_a_window_through_compute_windows(self):
        from fantasy_sim.run_windows import stamps_from_predictions_rows
        stamps = stamps_from_predictions_rows(self.ROWS, 1)
        r = compute_windows(u("2026-09-09T20:00:00"), KICKS, stamps)
        by = {w["name"]: w for w in r["windows"]}
        self.assertIsNotNone(by["run1_pre_kickoff"]["covered_by"])


class TestWatchVerdict(unittest.TestCase):
    """The pure decision the Actions watcher makes: which windows need a human NOW.
    Issues open for OPEN-uncovered windows inside the horizon and for MISSED windows;
    quiet otherwise -- a check that fires three times a week on nothing trains itself
    to be ignored (the repo's wallpaper principle)."""

    def _result(self, now):
        return compute_windows(now, KICKS, [])

    def test_open_window_inside_the_horizon_is_actionable(self):
        from fantasy_sim.run_windows import watch_verdict
        now = u("2026-09-09T20:00:00")   # run1 deadline 09-10T00:20Z: ~4.3h away
        v = watch_verdict(self._result(now), now, horizon_hours=24.0)
        self.assertEqual([a["name"] for a in v["actionable"]], ["run1_pre_kickoff"])
        self.assertAlmostEqual(v["actionable"][0]["hours_left"], 4.3, places=1)

    def test_open_window_beyond_the_horizon_is_quiet(self):
        from fantasy_sim.run_windows import watch_verdict
        now = u("2026-09-07T00:00:00")   # run1 open but ~3 days out
        v = watch_verdict(self._result(now), now, horizon_hours=24.0)
        self.assertEqual(v["actionable"], []); self.assertEqual(v["missed"], [])

    def test_covered_window_is_quiet_and_missed_window_is_reported(self):
        from fantasy_sim.run_windows import stamps_from_predictions_rows, watch_verdict
        rows = [{"record_type": "week_predictions", "week": 1, "canonical": True,
                 "logged_at": "2026-09-09T18:00:00Z"}]
        now = u("2026-09-13T12:00:00")   # run1 covered+past, run2 (sun 17:00Z) not yet open? deadline 17:00Z
        r = compute_windows(now, KICKS, stamps_from_predictions_rows(rows, 1))
        v = watch_verdict(r, now, horizon_hours=24.0)
        self.assertNotIn("run1_pre_kickoff", [a["name"] for a in v["actionable"]])
        self.assertNotIn("run1_pre_kickoff", [m["name"] for m in v["missed"]])
        now2 = u("2026-09-14T12:00:00")  # sunday window gone, uncovered -> MISSED
        r2 = compute_windows(now2, KICKS, stamps_from_predictions_rows(rows, 1))
        v2 = watch_verdict(r2, now2, horizon_hours=24.0)
        self.assertIn("run2_sunday", [m["name"] for m in v2["missed"]])



if __name__ == "__main__":
    unittest.main()

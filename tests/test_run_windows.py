"""Canonical-run window computation (scheduling ASSISTANT, not a runner -- R1 makes
unattended engine runs unsafe). All cases hand-computed against America/Los_Angeles:
week 1 opens WEDNESDAY 2026-09-09 17:20 PDT (2026-09-10T00:20Z, the real opener), so the
shifted-first-kickoff case is the live one. Written before fantasy_sim.run_windows existed."""
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fantasy_sim.run_windows import compute_windows

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


if __name__ == "__main__":
    unittest.main()

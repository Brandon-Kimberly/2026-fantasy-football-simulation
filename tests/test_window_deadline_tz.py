"""The window watcher stamps a PACIFIC time with a `Z` and calls it UTC.

The GitHub issue opened for week 3 on 2026-09-24 read:

    Window run1_pre_kickoff for week 3 is open, uncovered, and closes at
    **2026-09-24T17:15:00Z UTC** (~12.4 h left).

Those two numbers contradict each other. The issue was created at 04:53Z, so "12.4 h
left" puts the deadline at ~17:18 **UTC** — but `hours_left` is computed from a
timezone-aware subtraction and is correct, while the string beside it is not. The real
deadline is 17:15 **PDT**, the Thursday-night kickoff, which is 00:15 UTC the next day.
Seven hours out.

**The cause.** `compute_windows` builds every deadline in `PT`
(`ZoneInfo("America/Los_Angeles")`, `fantasy_sim/run_windows.py`), and `watch_verdict`
renders it with `strftime("%Y-%m-%dT%H:%M:%SZ")`. `strftime` prints the datetime's own
wall-clock fields and the `Z` is a literal character in the format string — it performs no
conversion and cannot fail. The value is Pacific; the label says UTC.

**Why it matters more than a cosmetic slip.** This string is the entire content of a
reminder that arrives on a phone. A deadline overstated by seven hours reads as "you have
45 minutes" when there are eight hours, which either causes a panic run or — worse — makes
the reader conclude the window is already gone and skip it. A missed canonical window
leaves no predictions row for that week, which is the series R1 renders and the record
F18/F19 partition the season on. The reminder exists precisely to stop that.

`hours_left` is correct and is left alone. The fix is to convert before formatting, and the
test below is the consistency check that should have existed: a `Z`-suffixed string parsed
as UTC must equal `now + hours_left`.

Written before the change.
"""
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")


def _result(deadline, status="OPEN", name="run1_pre_kickoff"):
    return {"target_week": 3,
            "windows": [{"name": name, "status": status, "deadline": deadline,
                         "covered_by": None}]}


class TestTheDeadlineStringIsActuallyUTC(unittest.TestCase):
    def test_a_pacific_deadline_renders_as_its_UTC_equivalent(self):
        """The live case: 17:15 PDT is 00:15 UTC the NEXT day, not 17:15 UTC."""
        from fantasy_sim.run_windows import watch_verdict
        deadline = datetime(2026, 9, 24, 17, 15, tzinfo=PT)
        now = datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc)
        got = watch_verdict(_result(deadline), now)["actionable"][0]
        self.assertEqual(got["deadline"], "2026-09-25T00:15:00Z")

    def test_the_string_and_hours_left_agree(self):
        """The consistency check that would have caught this. `hours_left` was always
        right -- it comes from an aware subtraction -- so the two disagreeing is the
        signature of the bug."""
        from fantasy_sim.run_windows import watch_verdict
        deadline = datetime(2026, 9, 24, 17, 15, tzinfo=PT)
        now = datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc)
        got = watch_verdict(_result(deadline), now)["actionable"][0]
        parsed = datetime.strptime(got["deadline"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        self.assertAlmostEqual((parsed - now).total_seconds() / 3600.0,
                               got["hours_left"], places=1,
                               msg="a Z-suffixed deadline parsed as UTC must equal "
                                   "now + hours_left")

    def test_a_missed_window_is_converted_too(self):
        """`missed` uses the same formatter and had the same defect. A missed-window
        notice naming the wrong hour is how a post-mortem gets the timeline wrong."""
        from fantasy_sim.run_windows import watch_verdict
        deadline = datetime(2026, 9, 21, 10, 0, tzinfo=PT)
        now = datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc)
        got = watch_verdict(_result(deadline, status="MISSED"), now)["missed"][0]
        self.assertEqual(got["deadline"], "2026-09-21T17:00:00Z")

    def test_a_deadline_already_in_UTC_is_unchanged(self):
        """Converting an aware UTC datetime to UTC is a no-op, so the fix must not
        disturb any caller that already passes UTC."""
        from fantasy_sim.run_windows import watch_verdict
        deadline = datetime(2026, 9, 25, 0, 15, tzinfo=timezone.utc)
        now = datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc)
        got = watch_verdict(_result(deadline), now)["actionable"][0]
        self.assertEqual(got["deadline"], "2026-09-25T00:15:00Z")

    def test_it_holds_across_the_standard_time_boundary(self):
        """PST is UTC-8 and PDT is UTC-7. A fixed offset would be right for half the
        season and wrong for the playoffs; `astimezone` reads the zone, not a constant."""
        from fantasy_sim.run_windows import watch_verdict
        deadline = datetime(2026, 12, 9, 17, 15, tzinfo=PT)      # PST
        now = datetime(2026, 12, 9, 16, 30, tzinfo=timezone.utc)
        got = watch_verdict(_result(deadline), now)["actionable"][0]
        self.assertEqual(got["deadline"], "2026-12-10T01:15:00Z")


class TestWhatMustNotChange(unittest.TestCase):
    """GREEN BY DESIGN -- `hours_left` was never wrong and the selection logic is
    untouched."""

    def test_hours_left_is_still_the_aware_difference(self):
        from fantasy_sim.run_windows import watch_verdict
        deadline = datetime(2026, 9, 24, 17, 15, tzinfo=PT)
        now = datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc)
        got = watch_verdict(_result(deadline), now)["actionable"][0]
        self.assertAlmostEqual(got["hours_left"], 7.8, places=1)

    def test_a_covered_window_is_still_silent(self):
        from fantasy_sim.run_windows import watch_verdict
        r = _result(datetime(2026, 9, 24, 17, 15, tzinfo=PT))
        r["windows"][0]["covered_by"] = "predictions@2026-09-24T10:00:00Z"
        now = datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc)
        v = watch_verdict(r, now)
        self.assertEqual((v["actionable"], v["missed"]), ([], []))

    def test_a_window_beyond_the_horizon_is_still_quiet(self):
        from fantasy_sim.run_windows import watch_verdict
        deadline = datetime(2026, 9, 24, 17, 15, tzinfo=PT)
        now = deadline - timedelta(hours=40)
        self.assertEqual(watch_verdict(_result(deadline), now, horizon_hours=24.0)
                         ["actionable"], [])


if __name__ == "__main__":
    unittest.main()

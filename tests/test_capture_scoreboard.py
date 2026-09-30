"""The live-scoreboard capture (UI-M5's prerequisite, owner request 2026-09-30).

UI-M5 -- a red-zone marker beside a starter whose team has the ball inside the 20, a possession
marker on the game strip -- reads ESPN's `situation` block, which exists only while a game is
live. The recorded week-4 scoreboard (tests/fixtures/scoreboard_week4.json) was taken before
kickoff and has none, so the item cannot be built or tested against real data yet. This pins the
recorder that fixes that: scripts.capture_scoreboard polls ESPN's public scoreboard for a set
number of minutes and keeps every distinct snapshot in which a game is live, gzipped; and a
workflow runs it inside Sunday's game windows (in both DST regimes) and keeps the files as an
artifact. It reads a public endpoint, spends nothing, needs no secret, and writes nothing tracked.
Written before the code.
"""
import gzip
import json
import os
import re
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def board(situation=None, clock="10:00"):
    comp = {"competitors": [], "status": {"type": {"name": "STATUS_IN_PROGRESS" if situation else "STATUS_SCHEDULED"},
                                          "displayClock": clock}}
    if situation:
        comp["situation"] = situation
    return {"events": [{"id": "1", "competitions": [comp]}]}


class TestTheRecorder(unittest.TestCase):
    def run_capture(self, boards, minutes=5, every=60):
        from scripts import capture_scoreboard as cap
        seq, now = list(boards), [0.0]

        def fetch():
            return seq.pop(0) if seq else boards[-1]

        def sleep(s):
            now[0] += s
        out = tempfile.mkdtemp()
        n = cap.capture(out, minutes=minutes, every=every, fetch=fetch, clock=lambda: now[0], sleep=sleep)
        files = sorted(os.listdir(out))
        docs = []
        for f in files:
            with gzip.open(os.path.join(out, f), "rt", encoding="utf-8") as fh:
                docs.append(json.load(fh))
        return n, files, docs

    def test_keeps_only_distinct_live_snapshots(self):
        live_a = board({"possession": "12", "isRedZone": True, "downDistanceText": "2nd & 4 at DAL 12"})
        live_b = board({"possession": "6", "isRedZone": False, "downDistanceText": "1st & 10 at NYG 30"}, clock="9:12")
        n, files, docs = self.run_capture([board(), live_a, live_a, live_b, board()])
        self.assertEqual(n, 2, "two distinct live snapshots; the pre-game and the repeat are dropped")
        self.assertTrue(all(re.fullmatch(r"scoreboard_\d{8}T\d{6}Z_[0-9a-f]{8}\.json\.gz", f) for f in files), files)
        self.assertEqual(docs[0]["board"]["events"][0]["competitions"][0]["situation"]["isRedZone"], True)
        self.assertIn("captured_at", docs[0])

    def test_stops_after_its_minutes(self):
        calls = []
        from scripts import capture_scoreboard as cap
        now = [0.0]

        def fetch():
            calls.append(now[0])
            return board()
        cap.capture(tempfile.mkdtemp(), minutes=3, every=60, fetch=fetch, clock=lambda: now[0],
                    sleep=lambda s: now.__setitem__(0, now[0] + s))
        self.assertEqual(len(calls), 3)

    def test_a_failed_fetch_is_skipped_not_fatal(self):
        from scripts import capture_scoreboard as cap
        now, seq = [0.0], [OSError("down"), board({"possession": "1", "isRedZone": True})]

        def fetch():
            x = seq.pop(0) if seq else board()
            if isinstance(x, Exception):
                raise x
            return x
        out = tempfile.mkdtemp()
        n = cap.capture(out, minutes=3, every=60, fetch=fetch, clock=lambda: now[0],
                        sleep=lambda s: now.__setitem__(0, now[0] + s))
        self.assertEqual(n, 1)


class TestTheWorkflow(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, ".github", "workflows", "scoreboard-capture.yml"), encoding="utf-8") as fh:
            self.wf = fh.read()

    def test_runs_inside_sunday_game_windows_in_both_dst_regimes(self):
        crons = re.findall(r'cron: "(\d+) (\d+) \* \* 0"', self.wf)
        self.assertGreaterEqual(len(crons), 2, crons)
        for minute, hour in crons:
            t = int(hour) * 60 + int(minute)
            # 1:00 pm ET kickoffs run about 17:00-20:15 UTC in EDT and 18:00-21:15 in EST;
            # the late window 20:25-23:40 / 21:25-00:40. A firing must be inside one window in both.
            early = 18 * 60 <= t <= 20 * 60
            late = 21 * 60 + 25 <= t <= 23 * 60 + 30
            self.assertTrue(early or late, f"{hour}:{minute} UTC is not inside a game window in both regimes")

    def test_keeps_the_files_and_needs_no_secret(self):
        self.assertIn("python -m scripts.capture_scoreboard", self.wf)
        self.assertIn("actions/upload-artifact", self.wf)
        self.assertNotIn("secrets.", self.wf)
        self.assertIn("contents: read", self.wf)


if __name__ == "__main__":
    unittest.main()

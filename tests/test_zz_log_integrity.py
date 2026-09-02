"""F11-class regression guard for data/logs/ -- the one dataset that cannot be refetched.

F11 (AUDIT_PLAN.md): three tests mocked json.dump instead of save_json, so save_json's
open(path, "w") TRUNCATED five real production files on every full-suite run, silently,
since the initial commit. This module makes that class of bug loud for data/logs/:

- At IMPORT time (unittest discovery imports every test module BEFORE running any test),
  _snapshot records size, mtime_ns and sha256 of every file under data/logs/.
- TestZZDataLogsUntouched runs the comparison. This module's name sorts LAST among
  tests/test_*.py, so under `unittest discover tests` (alphabetical) its test runs after
  every other test in the suite. A future test that forgets to mock a write to any log
  fails THIS test with the changed files named, instead of silently destroying data.
- The mechanism is itself tested on temp directories (TestGuardMechanism): content change,
  same-size rewrite, added file, removed file -- all detected. A same-content rewrite is
  also flagged via mtime_ns: an unmocked write path is the F11 class even when the bytes
  survive.

Scope: full-suite runs (the F11 vector was "every run of unittest discover tests"). A
single-module run does not import this guard and is not covered.
"""
import hashlib
import os
import unittest

from fantasy_sim.storage import _log

LOGS_DIR = os.path.dirname(_log("x"))


def _snapshot(directory):
    """{relative path: (size, mtime_ns, sha256)} for every file under directory."""
    out = {}
    for root, _dirs, files in os.walk(directory):
        for name in files:
            path = os.path.join(root, name)
            st = os.stat(path)
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            out[os.path.relpath(path, directory)] = (st.st_size, st.st_mtime_ns, h.hexdigest())
    return out


def _diff(before, after):
    """Human-readable list of changes between two snapshots; empty means untouched."""
    problems = []
    for rel in sorted(set(before) | set(after)):
        if rel not in after:
            problems.append(f"DELETED: {rel}")
        elif rel not in before:
            problems.append(f"CREATED: {rel}")
        else:
            b_size, b_mtime, b_sha = before[rel]
            a_size, a_mtime, a_sha = after[rel]
            if b_sha != a_sha:
                problems.append(f"CONTENT CHANGED: {rel} ({b_size} -> {a_size} bytes)")
            elif b_mtime != a_mtime:
                problems.append(f"REWRITTEN with identical content: {rel} "
                                "(mtime moved -- still an unmocked write path)")
    return problems


# Taken while unittest discovery imports this module -- before ANY test in the suite runs.
SNAPSHOT_AT_DISCOVERY = _snapshot(LOGS_DIR)


class TestGuardMechanism(unittest.TestCase):
    """The guard must be proven to DETECT, not merely to pass. All on temp dirs."""

    def _tmp(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        with open(os.path.join(d, "log.jsonl"), "w", encoding="utf-8") as f:
            f.write("row one" + chr(10))
        return d

    def test_content_change_and_truncation_are_detected(self):
        d = self._tmp()
        before = _snapshot(d)
        with open(os.path.join(d, "log.jsonl"), "w", encoding="utf-8") as f:
            f.write("")           # exactly F11: open("w") truncates
        problems = _diff(before, _snapshot(d))
        self.assertEqual(len(problems), 1)
        self.assertIn("CONTENT CHANGED", problems[0])

    def test_same_size_rewrite_is_detected_by_hash(self):
        d = self._tmp()
        before = _snapshot(d)
        with open(os.path.join(d, "log.jsonl"), "w", encoding="utf-8") as f:
            f.write("row two" + chr(10))   # same byte count, different bytes
        problems = _diff(before, _snapshot(d))
        self.assertEqual(len(problems), 1)
        self.assertIn("CONTENT CHANGED", problems[0])

    def test_identical_rewrite_is_still_flagged_via_mtime(self):
        d = self._tmp()
        before = _snapshot(d)
        path = os.path.join(d, "log.jsonl")
        os.utime(path, ns=(os.stat(path).st_atime_ns, os.stat(path).st_mtime_ns + 1_000_000))
        problems = _diff(before, _snapshot(d))
        self.assertEqual(len(problems), 1)
        self.assertIn("REWRITTEN", problems[0])

    def test_created_and_deleted_files_are_detected(self):
        d = self._tmp()
        before = _snapshot(d)
        with open(os.path.join(d, "extra.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        os.remove(os.path.join(d, "log.jsonl"))
        problems = _diff(before, _snapshot(d))
        self.assertEqual(sorted(p.split(":")[0] for p in problems), ["CREATED", "DELETED"])

    def test_untouched_directory_reports_nothing(self):
        d = self._tmp()
        self.assertEqual(_diff(_snapshot(d), _snapshot(d)), [])


class TestZZDataLogsUntouched(unittest.TestCase):
    """The guard itself: data/logs/ must be byte-identical to its state at discovery."""

    def test_no_test_in_this_suite_touched_data_logs(self):
        if not SNAPSHOT_AT_DISCOVERY:
            self.skipTest(f"no files under {LOGS_DIR} -- nothing to guard in this checkout")
        problems = _diff(SNAPSHOT_AT_DISCOVERY, _snapshot(LOGS_DIR))
        self.assertEqual(problems, [], chr(10).join(
            ["A test in this suite wrote to data/logs/ -- the F11 class of bug "
             "(AUDIT_PLAN.md F11: a mock at the wrong layer let open(path, 'w') truncate "
             "real production data on every suite run). The logs are the one dataset that "
             "cannot be refetched. Changed:"] + problems))


if __name__ == "__main__":
    unittest.main()

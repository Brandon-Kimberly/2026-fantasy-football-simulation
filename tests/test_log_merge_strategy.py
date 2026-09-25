"""The union-merge list in `.gitattributes` is hand-maintained, and it went stale.

Four append-only logs were given `merge=union` on 2026-09-04, each verified individually.
Every log added since was not, because nothing checks. On 2026-09-24 the `evaluate-moves`
workflow failed twice at "Commit and push the evaluation records":

    Auto-merging data/logs/designations.jsonl
    CONFLICT (content): Merge conflict in data/logs/designations.jsonl
    Auto-merging data/logs/projection_log.jsonl          <- has merge=union, merged clean
    CONFLICT (content): Merge conflict in data/logs/sync_provenance.jsonl
    error: could not apply ... Logs: automated move evaluations (actions)

`projection_log.jsonl` auto-merged; the two without the attribute conflicted. A third push
was rejected the same way during the v9.0.0 release. The evaluation work itself had already
succeeded every time -- only the push died.

**`run_sync` is invoked by four workflows** (canonical-run, data-capture, evaluate-moves,
pages-sample) and appends to `designations.jsonl`, `sync_provenance.jsonl`,
`first_recorded_scores.jsonl` and `projection_log.jsonl`. Any two of those runs overlapping
produces exactly this collision. `bid_ledger.jsonl` and `streamer_levels.jsonl` have no
automated writer at all, so they cannot race -- they are excluded ON PURPOSE below, with
that reason recorded rather than left to be rediscovered.

**Union is not free, and it is not right for every log.** It keeps BOTH sides of a
conflicting hunk, so a reader that counts rows double-counts and a reader that takes the
LAST row per key silently changes which value wins. That is why the original four were
verified one at a time, and why this file verifies the new ones the same way:

  designations         readers key into SETS ((week,pid) and weeks_by_pid[pid].add(wk)), so
                       a duplicate row is absorbed. Only the CLI's cosmetic row count moves.
  sync_provenance      pure append-only provenance; the sole reader builds a set of stamps.
  first_recorded_scores  NOT safe as it stood. The file exists to freeze the FIRST score seen
                       for a (week, name) -- that is what made the F83 reconstruction
                       possible -- but both readers were LAST-row-wins, so union would have
                       handed back the SECOND capture and quietly inverted the guarantee.
                       The readers are changed to first-row-wins here, which is the same
                       treatment `decision_log` got when it was unioned.

And the list itself is now guarded: any future `data/logs/*.jsonl` must either carry the
attribute or be named in the exclusion set with a reason, so this cannot drift again
silently. That guard is the real fix; the three added lines are what it caught.

Written before the change.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTRS = os.path.join(ROOT, ".gitattributes")

# Logs with NO automated writer: nothing but the owner appends to them, so two processes
# cannot race and a conflict cannot arise. Named here so the guard stays honest rather than
# silently passing over them.
NO_AUTOMATED_WRITER = {
    "data/logs/bid_ledger.jsonl",        # record_bid, at bid time, by hand
    "data/logs/streamer_levels.jsonl",   # scripts.streamer_study, run by hand
}


def _attr_lines():
    with open(ATTRS, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]


def _tracked_jsonl():
    out = subprocess.run(["git", "ls-files", "data/logs/*.jsonl"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return sorted(p.strip() for p in out.splitlines() if p.strip())


def _declared_jsonl():
    """Every jsonl log DECLARED in storage.py, tracked or not.

    `git ls-files` sees only what is already committed, so a brand-new log slips the guard
    for exactly as long as it takes to write it once -- which is when the omission is
    cheapest to make and most expensive to notice. Found by mutation when B28's
    `faab_adjustments.jsonl` was added: deleting its `merge=union` line left this file
    green. Declaring a log is the moment to check it.
    """
    import fantasy_sim.storage as storage
    out = set()
    for name in dir(storage):
        val = getattr(storage, name)
        if not isinstance(val, str) or not val.endswith(".jsonl"):
            continue
        rel = os.path.relpath(val, ROOT).replace(os.sep, "/")
        if rel.startswith("data/logs/"):
            out.add(rel)
    return sorted(out)


def _all_jsonl():
    return sorted(set(_tracked_jsonl()) | set(_declared_jsonl()))


def _has_union(path, lines):
    """Does any `.gitattributes` pattern with merge=union cover this path?"""
    import fnmatch
    for ln in lines:
        if "merge=union" not in ln:
            continue
        pattern = ln.split()[0]
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


class TestEveryRacingLogIsUnioned(unittest.TestCase):
    def test_no_jsonl_log_is_left_to_conflict(self):
        """The guard. A new append-only log must be declared safe or declared unraced.

        Covers logs DECLARED in storage.py as well as tracked ones -- a log that has never
        been written is invisible to `git ls-files`, and that is precisely when its
        `merge=union` line is easiest to forget."""
        lines = _attr_lines()
        missing = [p for p in _all_jsonl()
                   if p not in NO_AUTOMATED_WRITER and not _has_union(p, lines)]
        self.assertEqual(missing, [],
                         "these tracked jsonl logs can be written by two processes and have "
                         "no merge=union, so a concurrent push conflicts and the workflow "
                         "dies at the commit step")

    def test_the_three_logs_that_actually_broke_the_workflow(self):
        lines = _attr_lines()
        for path in ("data/logs/designations.jsonl",
                     "data/logs/sync_provenance.jsonl",
                     "data/logs/first_recorded_scores.jsonl"):
            with self.subTest(path=path):
                self.assertTrue(_has_union(path, lines))

    def test_whole_document_json_is_NOT_unioned(self):
        """`data/logs` also holds whole-document .json (draft_2026, season_2025, ...).
        Union-merging those concatenates two JSON documents into an unparseable file, so the
        pattern must stay `*.jsonl` and never `data/logs/*`."""
        lines = _attr_lines()
        for bad in ("data/logs/draft_2026.json", "data/logs/season_2025.json"):
            with self.subTest(path=bad):
                self.assertFalse(_has_union(bad, lines),
                                 "a union merge would produce invalid JSON here")


class TestUnionActuallyResolvesTheRace(unittest.TestCase):
    """Not a claim about git's documented behaviour -- an actual divergent merge, run."""

    def _git(self, *args, cwd, check=True):
        """`check` by default: a git call that silently fails makes the whole scenario
        vacuous. The first draft of this test used `git init -b`, which git 2.27 does not
        support; every later call then failed into a non-repo and the 'no conflict' test
        passed for the wrong reason."""
        r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
        if check and r.returncode != 0:
            self.fail(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r

    def _diverge(self, attrs_line):
        """Two branches each append a different row to the same log, then merge."""
        d = tempfile.mkdtemp()
        g = lambda *a, **kw: self._git(*a, cwd=d, **kw)          # noqa: E731
        g("init", "-q")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        os.makedirs(os.path.join(d, "data", "logs"))
        log = os.path.join(d, "data", "logs", "designations.jsonl")
        with open(log, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('{"week": 1, "pid": "1"}\n')
        if attrs_line:
            with open(os.path.join(d, ".gitattributes"), "w", encoding="utf-8",
                      newline="\n") as fh:
                fh.write(attrs_line + "\n")
        g("add", "-A")
        g("commit", "-qm", "base")
        base = g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()   # master or main
        g("checkout", "-q", "-b", "other")
        with open(log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write('{"week": 2, "pid": "OTHER"}\n')
        g("commit", "-qam", "other row")
        g("checkout", "-q", base)
        with open(log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write('{"week": 2, "pid": "MINE"}\n')
        g("commit", "-qam", "my row")
        merged = g("merge", "--no-edit", "other", check=False)   # a conflict is an outcome
        with open(log, encoding="utf-8") as fh:
            body = fh.read()
        return merged, body

    def test_without_the_attribute_the_merge_conflicts(self):
        merged, body = self._diverge(attrs_line=None)
        self.assertNotEqual(merged.returncode, 0, "this is the failure the workflow hit")
        self.assertIn("<<<<<<<", body)

    def test_with_the_attribute_both_rows_survive_and_there_is_no_conflict(self):
        merged, body = self._diverge(
            attrs_line="data/logs/designations.jsonl merge=union")
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertNotIn("<<<<<<<", body)
        self.assertIn('"MINE"', body)
        self.assertIn('"OTHER"', body)


class TestTheFrozenScoreStaysTheFIRSTOne(unittest.TestCase):
    """Union keeps both rows, so whichever reader rule applies decides which score wins.
    `first_recorded_scores` exists to preserve the FIRST value seen -- last-row-wins would
    hand back the second capture and invert exactly the guarantee the log is for."""

    def test_the_scorecard_keeps_the_first_row_for_a_week_and_name(self):
        from scripts.decision_scorecard import _frozen_scores
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "first.jsonl")
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write('{"week": 1, "name": "A Player", "points": 34.5}\n')
                fh.write('{"week": 1, "name": "A Player", "points": 29.5}\n')
            self.assertAlmostEqual(_frozen_scores(1, path=p)["A Player"], 34.5,
                                   msg="the FIRST capture is the frozen one")

    def test_dnp_flags_keeps_the_first_row_too(self):
        from fantasy_sim.durability import dnp_flags
        rows = [{"week": 1, "player_id": "7", "points": 0.0},
                {"week": 1, "player_id": "7", "points": 12.0}]
        self.assertTrue(dnp_flags(rows, 1)["7"],
                        "the first capture said he did not play; a later re-score must not "
                        "silently overwrite the frozen observation")


if __name__ == "__main__":
    unittest.main()

"""The FAAB watchdog fires into nothing that is kept.

`sync.warn_faab_adjustments` reconciles every roster's budget against its transaction history
and logs a warning for each mismatch — a commissioner adjustment leaves no transaction, so
this reconciliation is the only place it is visible at all (F80). It then returns a COUNT and
throws the rows away. `sync_provenance.jsonl` carries only
`espn_rows, git_commit, schema_version, season, synced_at, total_rows, week`, so nothing on
disk records what the watchdog saw.

**Demonstrated failure, 2026-09-24.** The owner asked whether an agreed 3-FAAB grant had
landed. The live watchdog read +4 for that roster and −1 for another. Answering needed the
PREVIOUS reading, and the only surviving copy of it was a table quoted by hand inside F80's
audit entry, written the day before. Without that accident the question had no answer, and
next time there will be no accident. A watchdog whose readings are not retained cannot answer
"did this change?" — which is the entire question a watchdog exists for.

**Why a log and not a field on the provenance row.** The series is per ROSTER, not per sync,
and the useful shape is a transition record: when did this roster's unexplained balance
change, and to what. That is the same shape `designations.jsonl` has, and it dedupes the same
way — one row per DISTINCT state per week, so a stable adjustment does not rewrite itself on
every one of the day's syncs while a change is recorded the moment it happens.

**A cleared adjustment must be written explicitly.** If a roster's delta returns to zero it
simply stops appearing in the watchdog's rows, and a reader then cannot tell "the commissioner
undid it" from "no sync has run since". A zero row with `cleared` closes the series honestly.

Team names here are the repository's fictional ones.

Written before the change.
"""
import json
import os
import tempfile
import unittest


def _rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def _adj(team, delta, used=2.0, explained=6.0):
    return {"team": team, "roster_id": 4, "delta": delta,
            "used": used, "explained_by_history": explained}


class TestTheReadingIsWrittenDown(unittest.TestCase):
    def test_an_adjustment_is_recorded(self):
        from fantasy_sim.sync import append_faab_adjustments
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "faab.jsonl")
            n = append_faab_adjustments([_adj("Cosmic Badgers", 4.0)], week=3, path=p)
            self.assertEqual(n, 1)
            got = _rows(p)
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["team"], "Cosmic Badgers")
            self.assertAlmostEqual(got[0]["delta"], 4.0)
            self.assertEqual(got[0]["week"], 3)

    def test_the_reading_is_reconstructible_not_just_the_delta(self):
        """`used` and `explained_by_history` are what make an old row auditable; a bare
        delta cannot be checked against anything later."""
        from fantasy_sim.sync import append_faab_adjustments
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "faab.jsonl")
            append_faab_adjustments([_adj("Cosmic Badgers", 4.0, used=2.0, explained=6.0)],
                                    week=3, path=p)
            r = _rows(p)[0]
            self.assertAlmostEqual(r["used"], 2.0)
            self.assertAlmostEqual(r["explained_by_history"], 6.0)
            self.assertIn("recorded_at", r)


class TestItRecordsTransitionsNotEverySync(unittest.TestCase):
    def test_a_stable_adjustment_writes_once_per_week(self):
        """Sync runs many times a day. A watchdog that appends an identical row every run
        buries the transition it exists to show."""
        from fantasy_sim.sync import append_faab_adjustments
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "faab.jsonl")
            for _ in range(4):
                append_faab_adjustments([_adj("Cosmic Badgers", 4.0)], week=3, path=p)
            self.assertEqual(len(_rows(p)), 1)

    def test_a_changed_delta_is_recorded_immediately(self):
        """The 3-FAAB question: +4 becoming +7 must appear the moment it happens."""
        from fantasy_sim.sync import append_faab_adjustments
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "faab.jsonl")
            append_faab_adjustments([_adj("Cosmic Badgers", 4.0)], week=3, path=p)
            append_faab_adjustments([_adj("Cosmic Badgers", 7.0)], week=3, path=p)
            deltas = [r["delta"] for r in _rows(p)]
            self.assertEqual(deltas, [4.0, 7.0])

    def test_the_same_delta_in_a_LATER_week_is_recorded_again(self):
        """Weeks are the unit the series is read in; carrying a +4 into week 4 is a fact
        about week 4."""
        from fantasy_sim.sync import append_faab_adjustments
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "faab.jsonl")
            append_faab_adjustments([_adj("Cosmic Badgers", 4.0)], week=3, path=p)
            append_faab_adjustments([_adj("Cosmic Badgers", 4.0)], week=4, path=p)
            self.assertEqual([r["week"] for r in _rows(p)], [3, 4])

    def test_two_rosters_are_tracked_independently(self):
        from fantasy_sim.sync import append_faab_adjustments
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "faab.jsonl")
            append_faab_adjustments([_adj("Cosmic Badgers", 4.0),
                                     _adj("Rocket Pandas", -1.0)], week=3, path=p)
            append_faab_adjustments([_adj("Cosmic Badgers", 7.0),
                                     _adj("Rocket Pandas", -1.0)], week=3, path=p)
            got = [(r["team"], r["delta"]) for r in _rows(p)]
            self.assertEqual(got, [("Cosmic Badgers", 4.0), ("Rocket Pandas", -1.0),
                                   ("Cosmic Badgers", 7.0)])


class TestAClearedAdjustmentIsWrittenDown(unittest.TestCase):
    def test_a_vanished_adjustment_closes_the_series_with_a_zero(self):
        """Otherwise 'the commissioner undid it' and 'no sync has run' look identical."""
        from fantasy_sim.sync import append_faab_adjustments
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "faab.jsonl")
            append_faab_adjustments([_adj("Cosmic Badgers", 4.0)], week=3, path=p)
            append_faab_adjustments([], week=3, path=p)
            got = _rows(p)
            self.assertEqual(len(got), 2)
            self.assertAlmostEqual(got[1]["delta"], 0.0)
            self.assertTrue(got[1].get("cleared"))

    def test_a_clear_is_written_once_not_every_sync(self):
        from fantasy_sim.sync import append_faab_adjustments
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "faab.jsonl")
            append_faab_adjustments([_adj("Cosmic Badgers", 4.0)], week=3, path=p)
            for _ in range(3):
                append_faab_adjustments([], week=3, path=p)
            self.assertEqual(len(_rows(p)), 2)

    def test_nothing_is_written_when_there_was_never_an_adjustment(self):
        from fantasy_sim.sync import append_faab_adjustments
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "faab.jsonl")
            self.assertEqual(append_faab_adjustments([], week=3, path=p), 0)
            self.assertEqual(_rows(p), [])


class TestARecordIsNotADependency(unittest.TestCase):
    """The repository's standing contract for every log: a failure here must never cost a
    sync. `record_bid`, `append_projection_log` and `append_designations` all hold it."""

    def test_an_unwritable_path_returns_zero_and_does_not_raise(self):
        from fantasy_sim.sync import append_faab_adjustments
        bad = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "no_such_dir\x00bad", "faab.jsonl")
        self.assertEqual(append_faab_adjustments([_adj("Cosmic Badgers", 4.0)], 3, path=bad), 0)


class TestTheLogIsTrackedAndMergeSafe(unittest.TestCase):
    """F87: sync writes it and four workflows invoke sync, so it races. It must carry the
    union attribute, and it must not be swallowed by the data/ ignore rules."""

    def test_gitignore_keeps_it(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".gitignore"), encoding="utf-8") as fh:
            self.assertIn("!data/logs/faab_adjustments.jsonl", fh.read())

    def test_it_is_declared_merge_union(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".gitattributes"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("data/logs/faab_adjustments.jsonl merge=union", body)


if __name__ == "__main__":
    unittest.main()

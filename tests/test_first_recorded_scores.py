"""B19: stat corrections are invisible, because the evidence overwrites itself.

`weekly_actuals.json` is regenerated on every sync. A Tuesday stat correction therefore
overwrites the number it corrected, and the repo cannot answer "how often does a
correction flip a result?" -- a question the owner asked and nothing here could address.

It also means January's evaluation can shift underneath itself: the REALIZED side of
every quoted-vs-realized comparison is mutable, so a calibration measured in January
need not match the same measurement taken in December.

The fix is a write-once record. On the first sync after a week COMPLETES, one row per
scored player, appended and never touched again.

THREE THINGS THAT WOULD DESTROY THE EVIDENCE, and are therefore pinned here:

  * the CURRENT week must not be recorded -- it is still accruing, and freezing a
    half-played week as "first recorded" would make every later update look like a
    correction;
  * a second sync must add NOTHING for a week already recorded. Idempotence is the whole
    feature: the second write is exactly the overwrite this exists to prevent;
  * a changed score must NOT update the stored row. That difference IS the finding.

KEYING. `player_scores` is keyed by NAME, and those names are already disambiguated by
`sync.resolve_player_keys` (a colliding player is stored "Name (pid)"), so (week, name)
is a sound unique key. The pid is carried alongside where resolvable, because a log meant
to outlive the season should not depend on a naming convention staying put.

Written before the function existed and confirmed failing (rule 1).
"""
import json
import os
import tempfile
import unittest

WEEKLY = {
    "week_1": {"player_scores": {"Alpha Back": 18.5, "Beta Wideout": 7.2}},
    "week_2": {"player_scores": {"Alpha Back": 11.0}},
    "week_3": {"player_scores": {"Alpha Back": 4.4}},          # the CURRENT week
}
BASELINES = {"Alpha Back": {"player_id": "111", "pos": "RB"},
             "Beta Wideout": {"player_id": "222", "pos": "WR"}}


def _read(p):
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


class TestItRecordsCompletedWeeksOnly(unittest.TestCase):
    def test_the_current_week_is_not_frozen(self):
        from fantasy_sim.sync import append_first_recorded_scores
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.jsonl")
            append_first_recorded_scores(WEEKLY, current_week=3, baselines=BASELINES, path=p)
            rows = _read(p)
        self.assertEqual({r["week"] for r in rows}, {1, 2},
                         "week 3 is still accruing; freezing it would make every later "
                         "update look like a correction")

    def test_one_row_per_scored_player(self):
        from fantasy_sim.sync import append_first_recorded_scores
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.jsonl")
            n = append_first_recorded_scores(WEEKLY, 3, BASELINES, path=p)
            rows = _read(p)
        self.assertEqual(n, 3)
        self.assertEqual({(r["week"], r["name"]) for r in rows},
                         {(1, "Alpha Back"), (1, "Beta Wideout"), (2, "Alpha Back")})

    def test_each_row_carries_points_a_stamp_and_a_pid(self):
        from fantasy_sim.sync import append_first_recorded_scores
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.jsonl")
            append_first_recorded_scores(WEEKLY, 3, BASELINES, path=p)
            row = next(r for r in _read(p) if r["week"] == 1 and r["name"] == "Alpha Back")
        self.assertAlmostEqual(row["points"], 18.5)
        self.assertEqual(row["player_id"], "111")
        self.assertTrue(row["recorded_at"].endswith("Z"))

    def test_a_player_with_no_baseline_still_records_with_a_null_pid(self):
        """Dropping him would lose the very score a correction might later change."""
        from fantasy_sim.sync import append_first_recorded_scores
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.jsonl")
            append_first_recorded_scores({"week_1": {"player_scores": {"Ghost": 5.0}}},
                                         2, {}, path=p)
            rows = _read(p)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["player_id"])


class TestItNeverOverwrites(unittest.TestCase):
    """The whole feature. A second write IS the overwrite this exists to prevent."""

    def test_a_second_sync_adds_nothing(self):
        from fantasy_sim.sync import append_first_recorded_scores
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.jsonl")
            append_first_recorded_scores(WEEKLY, 3, BASELINES, path=p)
            second = append_first_recorded_scores(WEEKLY, 3, BASELINES, path=p)
            rows = _read(p)
        self.assertEqual(second, 0)
        self.assertEqual(len(rows), 3)

    def test_a_corrected_score_does_not_update_the_stored_row(self):
        """That difference is the finding, not a problem to be tidied away."""
        from fantasy_sim.sync import append_first_recorded_scores
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.jsonl")
            append_first_recorded_scores(WEEKLY, 3, BASELINES, path=p)
            corrected = {"week_1": {"player_scores": {"Alpha Back": 25.0}}}
            append_first_recorded_scores(corrected, 3, BASELINES, path=p)
            rows = [r for r in _read(p) if r["week"] == 1 and r["name"] == "Alpha Back"]
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["points"], 18.5, msg="the FIRST value stands")

    def test_a_new_week_appends_without_disturbing_the_old(self):
        from fantasy_sim.sync import append_first_recorded_scores
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.jsonl")
            append_first_recorded_scores(WEEKLY, 3, BASELINES, path=p)
            append_first_recorded_scores(WEEKLY, 4, BASELINES, path=p)   # wk3 now complete
            rows = _read(p)
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(1 for r in rows if r["week"] == 3), 1)


class TestTheDiff(unittest.TestCase):
    FIRST = [{"week": 1, "name": "Alpha Back", "player_id": "111", "points": 18.5,
              "recorded_at": "2026-09-15T12:00:00Z"},
             {"week": 1, "name": "Beta Wideout", "player_id": "222", "points": 7.2,
              "recorded_at": "2026-09-15T12:00:00Z"}]

    def test_an_unchanged_score_is_not_a_correction(self):
        from fantasy_sim.corrections import diff_corrections
        now = {"week_1": {"player_scores": {"Alpha Back": 18.5, "Beta Wideout": 7.2}}}
        self.assertEqual(diff_corrections(self.FIRST, now)["corrections"], [])

    def test_a_changed_score_is_reported_with_its_magnitude_and_direction(self):
        from fantasy_sim.corrections import diff_corrections
        now = {"week_1": {"player_scores": {"Alpha Back": 21.0, "Beta Wideout": 7.2}}}
        c = diff_corrections(self.FIRST, now)["corrections"]
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["name"], "Alpha Back")
        self.assertAlmostEqual(c[0]["first"], 18.5)
        self.assertAlmostEqual(c[0]["now"], 21.0)
        self.assertAlmostEqual(c[0]["delta"], 2.5)

    def test_a_player_who_has_since_vanished_is_reported_not_skipped(self):
        """A score that disappears entirely is the largest correction there is."""
        from fantasy_sim.corrections import diff_corrections
        now = {"week_1": {"player_scores": {"Alpha Back": 18.5}}}
        c = diff_corrections(self.FIRST, now)["corrections"]
        self.assertEqual([x["name"] for x in c], ["Beta Wideout"])
        self.assertIsNone(c[0]["now"])

    def test_corrections_are_ranked_by_magnitude(self):
        from fantasy_sim.corrections import diff_corrections
        now = {"week_1": {"player_scores": {"Alpha Back": 19.0, "Beta Wideout": 0.2}}}
        c = diff_corrections(self.FIRST, now)["corrections"]
        self.assertEqual([x["name"] for x in c], ["Beta Wideout", "Alpha Back"])

    def test_it_summarises_how_often_and_how_big(self):
        from fantasy_sim.corrections import diff_corrections
        now = {"week_1": {"player_scores": {"Alpha Back": 19.0, "Beta Wideout": 7.2}}}
        r = diff_corrections(self.FIRST, now)
        self.assertEqual(r["n_recorded"], 2)
        self.assertEqual(r["n_corrected"], 1)
        self.assertAlmostEqual(r["largest"], 0.5)


if __name__ == "__main__":
    unittest.main()

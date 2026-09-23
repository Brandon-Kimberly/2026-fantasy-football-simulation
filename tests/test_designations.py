"""B21: the player cache holds TODAY's injury status and no history.

`sleeper_players_cache.json` is overwritten every sync. B10's study needs "how many times
was this player Questionable in the trailing N weeks", and that question has no answer
for 2026 and only a partial one for 2025, because nothing ever wrote the series down.

It is also the evidence B4 would need to move from SURFACING a Questionable tag to
PRICING one. B4 refused to price it -- rightly, on no evidence. This is how the evidence
starts existing.

A DESIGN CHOICE WORTH STATING, because the three options differ and the obvious one is
wrong. Dedupe on:

  * (week, pid)                  -- one row per player per week. Loses a Friday
                                    Questionable that became a Sunday Out, which is
                                    exactly the transition worth studying.
  * nothing                      -- a row every sync. Twenty-four syncs happened in week
                                    2 alone; the file becomes mostly duplicates.
  * (week, pid, injury_status)   -- the first appearance of each DISTINCT status in a
                                    week. Captures the transition, writes nothing for a
                                    player whose status has not moved. This is what is
                                    implemented.

Written before the function existed and confirmed failing (rule 1).
"""
import json
import os
import tempfile
import unittest

ROSTERS = {
    "Quantum Ferrets": [
        {"name": "Hurt Guy", "pos": "WR", "team": "DET", "injury_status": "Questionable"},
        {"name": "Fine Guy", "pos": "RB", "team": "DET", "injury_status": None},
    ],
    "Neon Walruses": [
        {"name": "Out Guy", "pos": "TE", "team": "CHI", "injury_status": "Out"},
    ],
}
CACHE = {
    "1": {"first_name": "Hurt", "last_name": "Guy", "injury_status": "Questionable",
          "injury_body_part": "Ribs", "practice_participation": "Limited"},
    "2": {"first_name": "Fine", "last_name": "Guy", "injury_status": None,
          "injury_body_part": None, "practice_participation": None},
    "3": {"first_name": "Out", "last_name": "Guy", "injury_status": "Out",
          "injury_body_part": "Hamstring", "practice_participation": "DNP"},
}
BASELINES = {"Hurt Guy": {"player_id": "1"}, "Fine Guy": {"player_id": "2"},
             "Out Guy": {"player_id": "3"}}


def _read(p):
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


class TestItRecordsRosteredPlayers(unittest.TestCase):
    def test_one_row_per_rostered_player_with_a_designation(self):
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            n = append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
            rows = _read(p)
        self.assertEqual(n, 2, "only the two with a designation")
        self.assertEqual({r["name"] for r in rows}, {"Hurt Guy", "Out Guy"})

    def test_a_healthy_player_writes_nothing(self):
        """A row per healthy player every week would be 150 rows a week of 'nothing
        happened', and the question is about designations, not roll call."""
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
            self.assertNotIn("Fine Guy", {r["name"] for r in _read(p)})

    def test_each_row_carries_the_body_part_and_practice_status(self):
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
            row = next(r for r in _read(p) if r["name"] == "Hurt Guy")
        self.assertEqual(row["injury_status"], "Questionable")
        self.assertEqual(row["injury_body_part"], "Ribs")
        self.assertEqual(row["practice_participation"], "Limited")
        self.assertEqual(row["player_id"], "1")
        self.assertEqual(row["week"], 3)
        self.assertTrue(row["recorded_at"].endswith("Z"))

    def test_the_owning_team_is_recorded(self):
        """B10 will want per-roster exposure, not just a league-wide count."""
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
            row = next(r for r in _read(p) if r["name"] == "Out Guy")
        self.assertEqual(row["team"], "Neon Walruses")


class TestTheDedupeRule(unittest.TestCase):
    """(week, pid, status): capture a transition, ignore a repeat."""

    def test_a_second_sync_with_no_change_writes_nothing(self):
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
            second = append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
        self.assertEqual(second, 0, "24 syncs happened in week 2; this must not write 24 rows")

    def test_a_status_change_within_the_week_is_captured(self):
        """Friday Questionable -> Sunday Out is the transition B10 exists to study."""
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
            worse = dict(CACHE)
            worse["1"] = dict(CACHE["1"], injury_status="Out")
            n = append_designations(ROSTERS, worse, BASELINES, week=3, path=p)
            rows = [r for r in _read(p) if r["name"] == "Hurt Guy"]
        self.assertEqual(n, 1)
        self.assertEqual([r["injury_status"] for r in rows], ["Questionable", "Out"])

    def test_the_same_status_in_a_new_week_is_a_new_row(self):
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
            append_designations(ROSTERS, CACHE, BASELINES, week=4, path=p)
            rows = [r for r in _read(p) if r["name"] == "Hurt Guy"]
        self.assertEqual([r["week"] for r in rows], [3, 4],
                         "the count of Questionable WEEKS is the whole point")


class TestItNeverBreaksASync(unittest.TestCase):
    def test_an_unwritable_path_returns_zero(self):
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "not-a-dir.txt")
            open(bad, "w").close()
            self.assertEqual(
                append_designations(ROSTERS, CACHE, BASELINES, week=3,
                                    path=os.path.join(bad, "x.jsonl")), 0)

    def test_a_player_missing_from_the_cache_is_skipped_not_fatal(self):
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            n = append_designations(ROSTERS, {}, BASELINES, week=3, path=p)
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()


class TestTheRollCall(unittest.TestCase):
    """F63 (2026-09-23), found while working B10.

    B21 exists to feed B10, and B10's test is *"does designation-count predict subsequent
    DNP **above the positional base rate**"*. A base rate is a rate among the
    UNDESIGNATED. This log wrote only the designated, on purpose -- the docstring called
    a healthy row "150 rows of 'nothing happened'" -- so the denominator was never
    recorded, and it is not recoverable afterwards: `live_rosters.json` is overwritten
    every sync and the players cache holds only today's status.

    Without the roll call the study has to borrow its denominator from
    `first_recorded_scores`, which is the LEAGUE-WIDE stats feed (~800 players a week,
    not the ~152 rostered). Players nobody tracked then sit in the "undesignated" group
    carrying designations that were never logged, which contaminates the comparison
    group and biases the measured lift DOWNWARD. A study that can only understate its
    effect is not a study worth running.

    Cost of fixing it: ~152 rows a week instead of ~28, about 2,700 rows a season. That
    is the whole price of having a denominator.
    """

    def test_a_healthy_rostered_player_is_recorded_with_a_null_status(self):
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
            rows = _read(p)
        fine = [r for r in rows if r["name"] == "Fine Guy"]
        self.assertEqual(len(fine), 1, "the roll call is the denominator B10 needs")
        self.assertIsNone(fine[0]["injury_status"])

    def test_the_roll_call_covers_every_rostered_player(self):
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
            rows = _read(p)
        self.assertEqual({r["name"] for r in rows},
                         {"Hurt Guy", "Fine Guy", "Out Guy"})

    def test_a_healthy_player_still_writes_only_once_a_week(self):
        """The dedupe rule has to keep holding, or the roll call turns the file into one
        row per player per SYNC -- twenty-four of them in week 2 alone."""
        from fantasy_sim.sync import append_designations
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
            second = append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
        self.assertEqual(second, 0)

    def test_a_healthy_player_who_becomes_questionable_writes_a_second_row(self):
        """The transition is the thing worth studying, and the roll-call row must not
        swallow it."""
        from fantasy_sim.sync import append_designations
        hurt = {"Quantum Ferrets": [{"name": "Fine Guy", "pos": "RB", "team": "DET"}]}
        cache2 = dict(CACHE, **{"2": dict(CACHE["2"], injury_status="Questionable")})
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "des.jsonl")
            append_designations(ROSTERS, CACHE, BASELINES, week=3, path=p)
            append_designations(hurt, cache2, BASELINES, week=3, path=p)
            got = [r["injury_status"] for r in _read(p) if r["name"] == "Fine Guy"]
        self.assertEqual(got, [None, "Questionable"])

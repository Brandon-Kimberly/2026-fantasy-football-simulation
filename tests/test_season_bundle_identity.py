"""F73: the season bundle carries the league's own real name into a tracked file.

Found by `scripts.scan_real_names` on its first real run (H1), which is the entire
argument for building it.

`sync.ingest_season` writes `"name": info.get("name")` and `"league_id": league_id`
straight from Sleeper's league object into `data/logs/season_<year>.json` -- a file that is
deliberately TRACKED (`.gitignore` un-excludes it) because Sleeper ages seasons out and the
on-disk copy becomes the source. The committed 2025 bundle therefore carries the league's
real, owner-chosen name in plain text.

**Why F37's migration did not catch it.** `scripts.migrate_identity` replaced real TEAM
names, usernames, owner ids and league ids. The league's own NAME was in none of those
maps, so it survived a migration that was otherwise thorough -- and then survived a literal
scan, because nobody thought to search for a string they were not looking for. The bundle's
`roster_map` is fully pseudonymised, which is exactly what makes the file look clean.

**The field is dead weight.** Nothing reads `bundle["name"]`: not
`fantasy_sim.season_retrospective`, not `scripts.run_points_backtest`, not
`scripts.free_add_study`. It was written because it was in the payload.

**`league_id` is the same class and is already handled inconsistently.** The code writes
the raw id; the committed file holds `""`, because the F37 migration blanked it after the
fact. So the next bundle written -- 2026's, at season end -- would carry the raw id again.
A one-time migration cannot fix a line that keeps re-emitting.

**Git history keeps the pre-fix record, by policy.** `migrate_identity`'s own docstring
states it: this project does not rewrite history; HEAD is the presentation, history is the
record (F37). Fixing HEAD is the fix; the history decision is the owner's and is unchanged.

Written before the change. Three of the four tests below are red; the fourth --
`test_the_committed_bundles_have_no_raw_league_id` -- is GREEN BY DESIGN, because F37's
migration already blanked that field in the committed file. It is a regression guard on
what the migration achieved, not a characterisation, and the red test one class up is the
one that shows the code would re-emit it tomorrow.
"""
import json
import os
import unittest
from unittest.mock import patch


def _repo(*parts):
    import fantasy_sim
    root = os.path.dirname(os.path.dirname(os.path.abspath(fantasy_sim.__file__)))
    return os.path.join(root, *parts)


class TestTheIngestorDoesNotEmitLeagueIdentity(unittest.TestCase):
    """A one-time migration cannot fix a line that keeps re-emitting. This is the line."""

    LEAGUE = {"season": "2099", "name": "Some Real League Name 2099", "status": "complete",
              "roster_positions": ["QB", "BN"],
              "settings": {"playoff_week_start": 15, "league_average_match": 1}}

    def _ingest(self, tmpdir):
        from fantasy_sim import sync

        class R:
            status_code = 200

            def __init__(self, payload):
                self._p = payload

            def json(self):
                return self._p

        def fake_get(url, timeout=None):
            if url.endswith("/rosters"):
                return R([{"roster_id": 1, "settings": {"wins": 1, "losses": 0, "ties": 0,
                                                        "fpts": 100, "fpts_decimal": 0}}])
            if "/matchups/" in url:
                return R([] if not url.endswith("/1") else
                         [{"roster_id": 1, "matchup_id": 1, "points": 100.0,
                           "players": ["p1"], "starters": ["p1"],
                           "players_points": {"p1": 100.0}}])
            return R(self.LEAGUE)

        path = os.path.join(tmpdir, "season_2099.json")
        with patch.object(sync.requests, "get", side_effect=fake_get):
            sync.ingest_season("REAL_LEAGUE_ID_9999", path_fn=lambda s: path)
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_the_bundle_does_not_carry_the_leagues_real_name(self):
        import tempfile
        bundle = self._ingest(tempfile.mkdtemp())
        self.assertNotIn("Some Real League Name 2099", json.dumps(bundle),
                         "the league's own name is an identity and this file is tracked")

    def test_the_bundle_does_not_carry_the_raw_league_id(self):
        """The committed 2025 file holds "" only because F37's migration blanked it
        afterwards. The code still writes the raw id, so the next bundle would leak it."""
        import tempfile
        bundle = self._ingest(tempfile.mkdtemp())
        self.assertNotIn("REAL_LEAGUE_ID_9999", json.dumps(bundle))


class TestNoTrackedSeasonBundleCarriesALeagueName(unittest.TestCase):
    """The committed artifact, not just the code that writes the next one."""

    def test_the_committed_bundles_have_no_league_name_field(self):
        import glob
        found = []
        for path in sorted(glob.glob(_repo("data", "logs", "season_*.json"))):
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("name"):
                found.append(os.path.basename(path))
        self.assertEqual(found, [],
                         f"tracked season bundle(s) carry a league name: {found}")

    def test_the_committed_bundles_have_no_raw_league_id(self):
        import glob
        found = []
        for path in sorted(glob.glob(_repo("data", "logs", "season_*.json"))):
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("league_id"):
                found.append(os.path.basename(path))
        self.assertEqual(found, [], f"tracked season bundle(s) carry a league id: {found}")


if __name__ == "__main__":
    unittest.main()

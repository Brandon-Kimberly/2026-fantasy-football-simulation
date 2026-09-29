"""
tests.test_image_cache -- the local image cache the sync fills (docs/WEB_UI_ROADMAP.md
Decision 3, UI-E7; owner ruling 2026-09-29: cache at sync).

At sync, each rostered player's Sleeper headshot and the 32 NFL team logos are downloaded
into data/images/, once: a file already there is not fetched again. The images are cosmetic,
so nothing about them can fail a sync -- a missing headshot (Sleeper answers 403 for a player
with none), a network error, or a response that is not an image is counted and skipped, and
only bytes that open as a JPEG or PNG are written. A defence's id is its team code, which has
no headshot; its logo covers it. Written before fantasy_sim.images existed.
"""
import os
import tempfile
import unittest

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class _Resp:
    def __init__(self, status=200, content=b"", ctype="image/jpeg"):
        self.status_code, self.content, self.headers = status, content, {"Content-Type": ctype}


class TestCacheImages(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dest = os.path.join(self.td.name, "images")
        self.urls = []

    def tearDown(self):
        self.td.cleanup()

    def get(self, url, timeout=None):
        self.urls.append(url)
        if url.endswith("/100.jpg"):
            return _Resp(200, JPEG, "image/jpeg")
        if url.endswith("/101.jpg"):
            return _Resp(403, b"<html>no</html>", "text/html")
        if url.endswith("/102.jpg"):
            raise ConnectionError("offline")
        if url.endswith("/104.jpg"):
            return _Resp(200, PNG, "image/jpeg")          # what Sleeper really sends (checked 2026-09-29)
        if url.endswith("/103.jpg"):
            return _Resp(200, b"<html>a login page</html>", "image/jpeg")      # says image, is not one
        if url.endswith(".png"):
            return _Resp(200, PNG, "image/png")
        return _Resp(404)

    def run_cache(self, pids=("100", "101", "102", "103", "DET"), teams=("DET", "GB")):
        from fantasy_sim.images import cache_images
        return cache_images(pids, teams=teams, dest=self.dest, get=self.get)

    def test_headshots_and_logos_are_written_under_their_own_folders(self):
        n = self.run_cache()
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "players", "100.jpg")))
        with open(os.path.join(self.dest, "teams", "det.png"), "rb") as fh:
            self.assertEqual(fh.read(), PNG)
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "teams", "gb.png")))
        self.assertIn("https://sleepercdn.com/content/nfl/players/thumb/100.jpg", self.urls)
        self.assertIn("https://sleepercdn.com/images/team_logos/nfl/det.png", self.urls, "Sleeper's logo names are lower case")
        self.assertEqual((n["fetched"], n["missing"]), (3, 3))

    def test_a_failure_is_counted_never_raised_and_writes_nothing(self):
        self.run_cache()
        written = sorted(os.listdir(os.path.join(self.dest, "players")))
        self.assertEqual(written, ["100.jpg"], "a 403, an error and a non-image leave no file behind")

    def test_a_headshot_sent_as_png_bytes_is_kept(self):
        """Found filling the real cache (2026-09-29): Sleeper serves every headshot as PNG
        bytes at a .jpg URL, labelled image/jpeg. A JPEG-only check refused all 163."""
        self.run_cache(pids=("104",), teams=())
        with open(os.path.join(self.dest, "players", "104.jpg"), "rb") as fh:
            self.assertEqual(fh.read(), PNG)

    def test_a_defence_has_no_headshot_to_fetch(self):
        self.run_cache()
        self.assertFalse(any("/thumb/DET" in u for u in self.urls))

    def test_an_image_already_cached_is_not_fetched_again(self):
        self.run_cache()
        self.urls.clear()
        n = self.run_cache()
        self.assertFalse(any(u.endswith("/100.jpg") or u.endswith("det.png") for u in self.urls))
        self.assertEqual(n["cached"], 3)

    def test_the_defaults_are_all_32_teams_and_data_images(self):
        from fantasy_sim import images
        from fantasy_sim.config import NFL_TEAMS
        self.assertEqual(len(images.default_teams()), 32)
        self.assertEqual(sorted(images.default_teams()), sorted(NFL_TEAMS))
        self.assertEqual(os.path.normpath(images.IMAGES_DIR), os.path.normpath(os.path.join("data", "images")))


class TestTheSyncCallsIt(unittest.TestCase):
    """A structural check, stated as one: the sync body calls the cache with the rostered ids,
    inside a guard, after every data file is written. A full sync is not run by the suite."""

    def test_the_call_is_guarded_and_last(self):
        import inspect
        from fantasy_sim import sync
        src = inspect.getsource(sync._sync_body)
        self.assertIn("cache_images(rostered_pids", src)
        tail = src[src.index("cache_images(rostered_pids"):]
        self.assertIn("except Exception", tail)
        self.assertLess(src.index("warn_faab_adjustments("), src.index("cache_images(rostered_pids"))


if __name__ == "__main__":
    unittest.main()

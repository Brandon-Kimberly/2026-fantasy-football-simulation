"""
tests.test_webui_sync -- syncing from the UI (docs/WEB_UI.md W4, reopened 2026-09-27).

The three safeguards, each pinned: the key is read from the User scope before the
process environment and never appears in a page, a job record or a log; the probe's
verdicts follow H5 and only `ok` launches -- rejected, absent and unreachable write
nothing and launch nothing; a backup of data/current/ is taken before the launch, is
restorable, and is pruned; the verified key reaches the child only through its
environment; `refresh` is the weekly report WITHOUT --skip-sync; the page itself never
touches the network; restore is refused while a job runs; and the tool registry still
does not offer run_sync (the guarded page is the only way).
"""
import json
import os
import tempfile
import unittest

from webui import sync as syncmod
from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from webui.live import LiveBoard
    from fantasy_sim.config import MY_TEAM
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_routes import build_tree
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

KEY = "CANARY-USER-SCOPE-KEY-0009"


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code, self.headers = status, headers or {}


def _fetch(status, headers=None, calls=None):
    def f(url, params=None, timeout=None):
        if calls is not None:
            calls.append((url, dict(params or {})))
        if isinstance(status, Exception):
            raise status
        return _Resp(status, headers)
    return f


class TestKeyAndProbe(unittest.TestCase):
    def test_user_scope_wins_over_the_process_environment(self):
        old = os.environ.get("ODDS_API_KEY")
        os.environ["ODDS_API_KEY"] = "CANARY-STALE-SHELL-KEY"
        try:
            self.assertEqual(syncmod.user_scope_key(read_user=lambda n: KEY), (KEY, "the Windows User scope"))
            self.assertEqual(syncmod.user_scope_key(read_user=lambda n: None), ("CANARY-STALE-SHELL-KEY", "this server's environment"))
            del os.environ["ODDS_API_KEY"]
            self.assertEqual(syncmod.user_scope_key(read_user=lambda n: None), (None, "nowhere"))
        finally:
            if old is None:
                os.environ.pop("ODDS_API_KEY", None)
            else:
                os.environ["ODDS_API_KEY"] = old

    def test_probe_verdicts_follow_h5_and_never_carry_the_key(self):
        calls = []
        ok = syncmod.probe_key(KEY, fetch=_fetch(200, {"x-requests-remaining": "412", "x-requests-used": "88"}, calls))
        self.assertEqual((ok["verdict"], ok["remaining"], ok["used"]), ("ok", "412", "88"))
        self.assertEqual(calls[0][1]["apiKey"], KEY)
        self.assertEqual(syncmod.probe_key(KEY, fetch=_fetch(401))["verdict"], "rejected")
        self.assertEqual(syncmod.probe_key(KEY, fetch=_fetch(403))["verdict"], "rejected")
        self.assertEqual(syncmod.probe_key(KEY, fetch=_fetch(503))["verdict"], "unreachable")
        self.assertEqual(syncmod.probe_key(KEY, fetch=_fetch(OSError("dns")))["verdict"], "unreachable")
        self.assertEqual(syncmod.probe_key("", fetch=_fetch(200))["verdict"], "absent")
        for verdict in (ok, syncmod.probe_key(KEY, fetch=_fetch(401)), syncmod.probe_key(KEY, fetch=_fetch(OSError("x")))):
            self.assertNotIn(KEY, json.dumps(verdict))

    def test_out_of_credits_is_not_a_rejected_key(self):
        """2026-09-30: the-odds-api answers an account out of credits with the same 401 as a bad
        key, and the page told the owner to fix a key that was fine. The body and the usage
        headers tell them apart (the real replies, captured that day)."""
        class Reply(_Resp):
            def __init__(self, status, body, headers=None):
                super().__init__(status, headers)
                self.body = body

            def json(self):
                return self.body

        def fetch(body, headers=None):
            return lambda url, params=None, timeout=None: Reply(401, body, headers)
        out = syncmod.probe_key(KEY, fetch=fetch({"message": "Usage quota has been reached.", "error_code": "OUT_OF_USAGE_CREDITS"},
                                                 {"x-requests-remaining": "0", "x-requests-used": "500"}))
        self.assertEqual((out["verdict"], out["remaining"], out["used"]), ("exhausted", "0", "500"))
        for word in ("rejected", "rotated", "revoked", "setx"):
            self.assertNotIn(word, out["detail"].lower())
        self.assertIn("credits", out["detail"].lower())
        self.assertNotIn(KEY, json.dumps(out))
        bad = syncmod.probe_key(KEY, fetch=fetch({"message": "API key is not valid.", "error_code": "INVALID_KEY"}))
        self.assertEqual(bad["verdict"], "rejected")

    def test_argv_for_each_mode(self):
        self.assertEqual(syncmod.argv_for("sync", python="PY"), ["PY", "-m", "scripts.run_sync"])
        self.assertEqual(syncmod.argv_for("refresh", python="PY"), ["PY", "-m", "scripts.weekly_report"])
        self.assertNotIn("--skip-sync", syncmod.argv_for("refresh", python="PY"))
        with self.assertRaises(KeyError):
            syncmod.argv_for("nope")


class TestBackups(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def _current(self, name):
        return os.path.join(self.td.name, "data", "current", name)

    def test_backup_copies_every_file_with_a_manifest_and_restore_brings_them_back(self):
        name = syncmod.backup(self.root, reason="test")
        bdir = os.path.join(syncmod.backups_dir(self.root), name)
        meta = json.load(open(os.path.join(bdir, "backup.json"), encoding="utf-8"))
        self.assertEqual(meta["reason"], "test")
        self.assertEqual(meta["week"], 3)
        self.assertEqual({f["name"] for f in meta["files"]}, {fn for fn in os.listdir(self._current("")) if os.path.isfile(self._current(fn))})
        self.assertTrue(os.path.isfile(os.path.join(bdir, "league_standings.json")))
        with open(self._current("league_standings.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")                                   # a bad sync
        self.assertEqual(syncmod.restore(self.root, name), len(meta["files"]))
        self.assertIn("Quantum Ferrets", open(self._current("league_standings.json"), encoding="utf-8").read())
        self.assertEqual(syncmod.list_backups(self.root)[0]["name"], name)

    def test_only_the_newest_ten_backups_are_kept_and_names_are_checked(self):
        names = [syncmod.backup(self.root) for _ in range(12)]
        kept = [b["name"] for b in syncmod.list_backups(self.root)]
        self.assertEqual(len(kept), syncmod.KEEP_BACKUPS)
        self.assertEqual(kept[0], names[-1])
        with self.assertRaises(ValueError):
            syncmod.restore(self.root, "../etc")
        with self.assertRaises(FileNotFoundError):
            syncmod.restore(self.root, "current_19990101T000000Z")

    def test_backups_live_under_data_local_which_is_never_served(self):
        name = syncmod.backup(self.root)
        self.assertTrue(os.path.realpath(syncmod.backups_dir(self.root)).startswith(os.path.realpath(os.path.join(self.td.name, "data", "local"))))
        self.assertFalse(self.root.exists(f"local/webui/backups/{name}/league_standings.json"))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestSyncPage(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        self.root = Root(self.td.name)
        self.runner = FakeRunner()
        self.probes = []

    def tearDown(self):
        self.td.cleanup()

    def client(self, status=200, key=KEY, runner=None):
        def probe(k):
            self.probes.append(k)
            return syncmod.probe_key(k, fetch=_fetch(status, {"x-requests-remaining": "400"}))
        app = create_app(self.root, runner=runner or self.runner, csrf_token="tok", key_probe=probe,
                         key_reader=lambda n: key, live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def test_the_page_renders_without_probing_and_names_the_key_source_not_the_key(self):
        body = self.client().get("/sync").get_data(as_text=True)
        self.assertIn("the Windows User scope", body)
        self.assertNotIn(KEY, body)
        self.assertEqual(self.probes, [])
        self.assertIn('name="mode" value="sync"', body)
        self.assertIn('name="mode" value="refresh"', body)
        self.assertIn("Backups", body)

    def test_a_rejected_key_launches_nothing_and_writes_nothing(self):
        before = self.root.tree_digest()
        r = self.client(status=401).post("/sync/launch", data={"_csrf": "tok", "mode": "sync"})
        self.assertEqual(r.status_code, 409)
        body = r.get_data(as_text=True)
        self.assertIn("REJECTED", body)
        self.assertIn("Not launched", body)
        self.assertNotIn(KEY, body)
        self.assertEqual(self.runner.launches, [])
        self.assertEqual(syncmod.list_backups(self.root), [])
        self.assertEqual(self.root.tree_digest(), before)
        for status in (503, OSError("down")):
            self.assertEqual(self.client(status=status).post("/sync/launch", data={"_csrf": "tok", "mode": "sync"}).status_code, 409)
        old = os.environ.pop("ODDS_API_KEY", None)           # no key anywhere: absent, refused
        try:
            self.assertEqual(self.client(key=None).post("/sync/launch", data={"_csrf": "tok", "mode": "sync"}).status_code, 409)
        finally:
            if old is not None:
                os.environ["ODDS_API_KEY"] = old
        self.assertEqual(self.runner.launches, [])

    def test_an_accepted_key_backs_up_then_launches_with_the_key_in_the_environment_only(self):
        c = self.client(status=200)
        r = c.post("/sync/launch", data={"_csrf": "tok", "mode": "sync"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.probes, [KEY])
        self.assertEqual(len(self.runner.launches), 1)
        argv, tool, label = self.runner.launches[0]
        self.assertEqual(argv[1:], ["-m", "scripts.run_sync"])
        self.assertEqual(tool, "run_sync")
        self.assertEqual(self.runner.envs[0], {"ODDS_API_KEY": KEY})
        backups = syncmod.list_backups(self.root)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["reason"], "before sync")
        jid = list(self.runner.metas)[-1]
        meta = self.runner.metas[jid]
        self.assertTrue(meta["sync"])
        self.assertEqual(meta["backup"], backups[0]["name"])
        self.assertEqual(meta["key_source"], "the Windows User scope")
        self.assertNotIn(KEY, json.dumps(meta))
        page = c.get(f"/jobs/{jid}").get_data(as_text=True)
        self.assertIn("backed up first", page)
        self.assertIn("Backups and restore", page)
        self.assertNotIn(KEY, page)

    def test_refresh_is_the_weekly_report_without_skip_sync(self):
        self.client(status=200).post("/sync/launch", data={"_csrf": "tok", "mode": "refresh"})
        argv, tool, label = self.runner.launches[0]
        self.assertEqual(argv[1:], ["-m", "scripts.weekly_report"])
        self.assertEqual(tool, "weekly_report")
        self.assertNotIn("--skip-sync", argv)

    def test_csrf_bad_mode_and_a_running_job_are_refused(self):
        self.assertEqual(self.client().post("/sync/launch", data={"mode": "sync"}).status_code, 403)
        self.assertEqual(self.client().post("/sync/launch", data={"_csrf": "tok", "mode": "wipe"}).status_code, 400)
        busy = FakeRunner(busy={"id": "20260926T000000Z_aaaaaa_slow", "tool": "run_simulation", "label": "slow", "started_at": "2026-09-26T00:00:00Z"})
        self.assertEqual(self.client(runner=busy).post("/sync/launch", data={"_csrf": "tok", "mode": "sync"}).status_code, 409)
        self.assertEqual(busy.launches, [])
        self.assertEqual(syncmod.list_backups(self.root), [], "no backup is taken for a refused launch")

    def test_restore_copies_a_backup_back_and_is_refused_while_a_job_runs(self):
        name = syncmod.backup(self.root, reason="test")
        with open(os.path.join(self.td.name, "data", "current", "league_standings.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        r = self.client().post("/sync/restore", data={"_csrf": "tok", "name": name})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Restored.", r.get_data(as_text=True))
        self.assertIn("Quantum Ferrets", open(os.path.join(self.td.name, "data", "current", "league_standings.json"), encoding="utf-8").read())
        self.assertEqual(self.client().post("/sync/restore", data={"_csrf": "tok", "name": "nope"}).status_code, 404)
        busy = FakeRunner(busy={"id": "20260926T000000Z_aaaaaa_slow", "tool": "run_simulation", "label": "slow", "started_at": "2026-09-26T00:00:00Z"})
        self.assertEqual(self.client(runner=busy).post("/sync/restore", data={"_csrf": "tok", "name": name}).status_code, 409)

    def test_the_tool_registry_still_does_not_offer_run_sync(self):
        from webui.tools import ENGINE, TOOLS
        self.assertNotIn("run_sync", ENGINE)
        self.assertNotIn("run_sync", TOOLS)
        self.assertEqual(self.client().get("/tools/run_sync").status_code, 404)
        self.assertIn('href="/sync"', self.client().get("/").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
class TestChanges(unittest.TestCase):
    """U2: what moved between the last backup and what is on disk now -- the answer to
    "what did I miss while I was away?" without reading a log."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        build_tree(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def cur(self, rel):
        return os.path.join(self.td.name, "data", "current", rel)

    def rewrite(self, rel, fn):
        with open(self.cur(rel), encoding="utf-8") as fh:
            d = json.load(fh)
        fn(d)
        with open(self.cur(rel), "w", encoding="utf-8") as fh:
            json.dump(d, fh)

    def test_nothing_to_compare_against(self):
        r = syncmod.changes(self.root)
        self.assertFalse(r["available"])
        self.assertTrue(r["note"])
        self.assertEqual(r["n"], 0)

    def test_an_unchanged_tree_reports_no_change(self):
        syncmod.backup(self.root, reason="test")
        r = syncmod.changes(self.root)
        self.assertTrue(r["available"])
        self.assertEqual((r["roster"], r["status"], r["projection"], r["standings"]), ([], [], [], []))
        self.assertEqual(r["n"], 0)

    def test_every_kind_of_move_is_named(self):
        from tests.test_webui_routes import TEAMS
        syncmod.backup(self.root, reason="test")

        def edit(d):
            d[TEAMS[2]].append(d[TEAMS[1]].pop(0))                       # traded away
            d[TEAMS[0]].append({"name": "Waiver Pickup", "pos": "RB", "team": "SEA",
                                "injury_status": None, "on_ir": False})  # added
            d[TEAMS[3]] = []                                             # dropped
            for p in d[TEAMS[4]]:
                p["injury_status"] = "Out"                               # status
        self.rewrite("live_rosters.json", edit)
        self.rewrite("player_baselines.json", lambda d: d.__setitem__("Player 5 O'Neil", dict(d["Player 5 O'Neil"], mean=8.0)))
        self.rewrite("league_standings.json", lambda d: d.__setitem__(TEAMS[0], dict(d[TEAMS[0]], h2h_wins=99, points_scored=999.0)))

        r = syncmod.changes(self.root)
        self.assertTrue(r["available"])
        by_name = {x["name"]: x for x in r["roster"]}
        self.assertEqual(by_name["Player 1 O'Neil"]["kind"], "moved")
        self.assertEqual((by_name["Player 1 O'Neil"]["was"], by_name["Player 1 O'Neil"]["now"]), (TEAMS[1], TEAMS[2]))
        self.assertEqual(by_name["Waiver Pickup"]["kind"], "added")
        self.assertIsNone(by_name["Waiver Pickup"]["was"])
        self.assertEqual(by_name["Player 3 O'Neil"]["kind"], "dropped")
        self.assertIsNone(by_name["Player 3 O'Neil"]["now"])
        self.assertEqual([(x["name"], x["was"], x["now"]) for x in r["status"]],
                         [("Player 4 O'Neil", None, "Out")])
        self.assertEqual([(x["name"], x["now"], x["delta"]) for x in r["projection"]],
                         [("Player 5 O'Neil", 8.0, -14.2)])
        self.assertEqual([(x["team"], x["wins"], x["points"]) for x in r["standings"]],
                         [(TEAMS[0], 99, 999.0)])
        self.assertEqual(r["n"], 6)

    def test_a_projection_that_barely_moved_is_not_news(self):
        syncmod.backup(self.root, reason="test")
        self.rewrite("player_baselines.json", lambda d: d.__setitem__("Player 5 O'Neil", dict(d["Player 5 O'Neil"], mean=22.3)))
        self.assertEqual(syncmod.changes(self.root)["projection"], [])

    @unittest.skipUnless(HAS_FLASK, "flask not installed")
    def test_the_sync_page_shows_it(self):
        from tests.test_webui_routes import TEAMS
        syncmod.backup(self.root, reason="test")
        self.rewrite("live_rosters.json", lambda d: d[TEAMS[2]].append(d[TEAMS[1]].pop(0)))
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok",
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        body = app.test_client().get("/sync").get_data(as_text=True)
        self.assertIn("What changed", body)
        self.assertIn("Player 1 O&#39;Neil", body)
        # and any backup can be made the comparison point, for "what did I miss while I was away"
        name = syncmod.list_backups(self.root)[0]["name"]
        self.assertIn(f'href="/sync?from={name}#changed"', body)
        picked = app.test_client().get(f"/sync?from={name}").get_data(as_text=True)
        self.assertIn("back to the newest", picked)
        self.assertEqual(app.test_client().get("/sync?from=nonsense").status_code, 200)

"""
tests.test_webui_jobs -- one engine subprocess at a time (docs/WEB_UI.md 2.5; W2).

Real subprocesses, but never the engine: every "tool" here is this interpreter running a
tiny -c program, so the tests exercise the runner's lock, its VOID semantics, its lock
file, its record parsing, and what it writes -- against a temp root. The process scan is
injected so no test depends on what else is running on the machine.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from webui.jobs import OK, RUNNING, VOID, JobRefused, JobRunner, R1_VOID
from webui.paths import Root

PY = sys.executable
NO_RIVALS = lambda exclude=(): []  # noqa: E731
CANARY = "CANARY-ODDS-KEY-42"


def tool(code):
    return [PY, "-c", code]


def wait_done(runner, job_id, timeout=30):
    for _ in range(int(timeout * 20)):
        m = runner.read(job_id)
        if m and m["state"] != RUNNING:
            return m
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} still running after {timeout}s")


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.td.name, "data", "decisions", "week_03"))
        self.root = Root(self.td.name)
        self.runner = JobRunner(self.root, scan=NO_RIVALS)

    def tearDown(self):
        self.td.cleanup()

    def test_a_clean_exit_is_ok_and_the_output_is_captured(self):
        jid = self.runner.launch(tool("print('hello from the tool')"), tool="fake", label="fake run")
        m = wait_done(self.runner, jid)
        self.assertEqual(m["state"], OK)
        self.assertEqual(m["rc"], 0)
        self.assertIn("hello from the tool", self.runner.log_text(jid))
        self.assertEqual(m["cwd"], self.root.root)
        self.assertFalse(os.path.exists(self.runner.lock_path), "lock file must be removed on exit")
        self.assertIsNone(self.runner.current())

    def test_a_nonzero_exit_is_void_with_the_r1_sentence(self):
        jid = self.runner.launch(tool("import sys; print('partial'); sys.exit(3)"), tool="fake")
        m = wait_done(self.runner, jid)
        self.assertEqual(m["state"], VOID)
        self.assertEqual(m["rc"], 3)
        self.assertIn(R1_VOID, m["note"])

    def test_a_second_launch_while_one_runs_is_refused_and_allowed_afterwards(self):
        jid = self.runner.launch(tool("import time; time.sleep(1.5)"), tool="slow")
        with self.assertRaises(JobRefused) as cm:
            self.runner.launch(tool("print(1)"), tool="fake")
        self.assertIn("busy", str(cm.exception))
        self.assertEqual(self.runner.current()["id"], jid)
        wait_done(self.runner, jid)
        jid2 = self.runner.launch(tool("print(2)"), tool="fake")
        self.assertEqual(wait_done(self.runner, jid2)["state"], OK)

    def test_another_engine_process_on_the_machine_refuses_the_launch(self):
        runner = JobRunner(self.root, scan=lambda exclude=(): [{"pid": 4242, "cmdline": "python -m scripts.run_simulation"}])
        with self.assertRaises(JobRefused) as cm:
            runner.launch(tool("print(1)"), tool="fake")
        self.assertIn("4242", str(cm.exception))
        self.assertIn("R1", str(cm.exception))
        self.assertIsNone(runner.current())
        # and the in-process lock was released by the refusal
        fresh = JobRunner(self.root, scan=NO_RIVALS)
        jid = fresh.launch(tool("print(1)"), tool="fake")
        self.assertEqual(wait_done(fresh, jid)["state"], OK)   # finished before tearDown removes its log

    def test_cwd_is_the_root_and_shell_is_never_used(self):
        seen = {}
        real = subprocess.Popen

        def spy(argv, **kw):
            seen["argv"], seen["kw"] = argv, kw
            return real(argv, **kw)
        runner = JobRunner(self.root, popen=spy, scan=NO_RIVALS)
        jid = runner.launch(tool("print(1)"), tool="fake")
        wait_done(runner, jid)
        self.assertIsInstance(seen["argv"], list)
        self.assertEqual(seen["kw"]["cwd"], self.root.root)
        self.assertIs(seen["kw"]["shell"], False)
        self.assertEqual(seen["kw"]["stderr"], subprocess.STDOUT)

    def test_environment_is_inherited_by_the_tool_but_never_written_by_the_runner(self):
        with patch.dict(os.environ, {"ODDS_API_KEY": CANARY}):
            jid = self.runner.launch(tool("import os; print('key seen' if os.environ.get('ODDS_API_KEY') else 'no key')"),
                                     tool="fake")
            m = wait_done(self.runner, jid)
        self.assertIn("key seen", self.runner.log_text(jid))
        self.assertNotIn(CANARY, json.dumps(m))
        with open(os.path.join(self.runner.jobs_dir, jid, "meta.json"), encoding="utf-8") as fh:
            self.assertNotIn(CANARY, fh.read())

    def test_the_announced_record_becomes_a_served_link(self):
        rec = os.path.join(self.td.name, "data", "decisions", "week_03", "lineup_x.json")
        with open(rec, "w", encoding="utf-8") as fh:
            fh.write("{}")
        jid = self.runner.launch(tool(r"print('  logged -> data\\decisions\\week_03\\lineup_x.json')"), tool="fake")
        m = wait_done(self.runner, jid)
        self.assertEqual(m["record"], "/file/decisions/week_03/lineup_x.json")

    def test_a_chain_links_its_digest_not_the_first_subtool_record(self):
        """The weekly report announces every sub-tool's record and its own digest last."""
        d = os.path.join(self.td.name, "data", "decisions", "week_03")
        for name in ("roster_grades_a_week3.json", "lineup_b_week3.json", "weekly_report_week3_c.md",
                     "weekly_report_week3_c.html"):
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write("x")
        code = ("print('  logged -> data/decisions/week_03/roster_grades_a_week3.json');"
                "print('  logged -> data/decisions/week_03/lineup_b_week3.json');"
                "print('[OK] digest -> data/decisions/week_03/weekly_report_week3_c.md');"
                "print('[OK] html   -> data/decisions/week_03/weekly_report_week3_c.html')")
        m = wait_done(self.runner, self.runner.launch(tool(code), tool="weekly_report"))
        self.assertEqual(m["record"], "/file/decisions/week_03/weekly_report_week3_c.html")
        code2 = ("print('  logged -> data/decisions/week_03/roster_grades_a_week3.json');"
                 "print('  logged -> data/decisions/week_03/lineup_b_week3.json')")
        m2 = wait_done(self.runner, self.runner.launch(tool(code2), tool="chain"))
        self.assertEqual(m2["record"], "/file/decisions/week_03/lineup_b_week3.json", "no html: the LAST record wins")

    def test_a_record_outside_data_is_not_linked(self):
        jid = self.runner.launch(tool("print('logged -> C:/Windows/system32/x.json')"), tool="fake")
        self.assertIsNone(wait_done(self.runner, jid)["record"])

    def test_cancel_makes_the_job_void(self):
        jid = self.runner.launch(tool("import time; time.sleep(30)"), tool="slow")
        self.assertTrue(self.runner.cancel(jid))
        m = wait_done(self.runner, jid)
        self.assertEqual(m["state"], VOID)
        self.assertNotEqual(m["rc"], 0)
        self.assertFalse(self.runner.cancel(jid), "nothing to cancel once finished")

    def test_stale_lock_and_dead_running_job_are_reconciled_on_start(self):
        os.makedirs(self.runner.base, exist_ok=True)
        with open(self.runner.lock_path, "w", encoding="utf-8") as fh:
            json.dump({"pid": 999999, "job": "old"}, fh)
        jdir = os.path.join(self.runner.jobs_dir, "20260101T000000Z_abc_fake")
        os.makedirs(jdir)
        with open(os.path.join(jdir, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "20260101T000000Z_abc_fake", "state": RUNNING, "pid": 999999,
                       "started_at": "2026-01-01T00:00:00Z", "tool": "fake", "label": "fake"}, fh)
        fresh = JobRunner(self.root, scan=NO_RIVALS, alive=lambda pid: False)
        self.assertFalse(os.path.exists(fresh.lock_path))
        m = fresh.read("20260101T000000Z_abc_fake")
        self.assertEqual(m["state"], VOID)
        self.assertIn("found dead", m["note"])

    def test_a_live_lock_from_another_instance_refuses_the_launch(self):
        os.makedirs(self.runner.base, exist_ok=True)
        with open(self.runner.lock_path, "w", encoding="utf-8") as fh:
            json.dump({"pid": 12345, "job": "elsewhere"}, fh)
        other = JobRunner(self.root, scan=NO_RIVALS, alive=lambda pid: True)
        with self.assertRaises(JobRefused) as cm:
            other.launch(tool("print(1)"), tool="fake")
        self.assertIn("12345", str(cm.exception))

    def test_job_ids_that_could_traverse_are_not_found(self):
        self.assertIsNone(self.runner.read("../../pyproject.toml"))
        self.assertIsNone(self.runner.read("..\\meta"))
        self.assertEqual(self.runner.tail("../x")[0], "")

    def test_an_unstartable_argv_is_refused_and_recorded_void(self):
        with self.assertRaises(JobRefused):
            self.runner.launch(["definitely-not-a-program-xyz"], tool="fake")
        jobs = self.runner.list()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["state"], VOID)
        # and the lock is free again
        jid = self.runner.launch(tool("print(1)"), tool="fake")
        self.assertEqual(wait_done(self.runner, jid)["state"], OK)

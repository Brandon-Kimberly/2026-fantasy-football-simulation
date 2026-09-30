"""
tests.test_webui_sandbox -- the sandbox root (docs/WEB_UI_ROADMAP.md UI-E6; docs/WEB_UI.md W5).

Copy data/, run there, throw it away. webui.sandbox.create copies the served directories
(current, weeks, decisions, logs, results) into a fresh temporary root marked as a sandbox --
never data/local (the secrets and the identity map) and never the image cache -- and discard
removes a sandbox and refuses anything not marked as one. The job runner takes a code root: a
job launched against a sandbox runs with its working directory there (every relative data/
write lands in the copy) and imports the real checkout's code. `py -3.10 -m webui --sandbox`
serves a fresh copy and discards it on exit, so a crawl or a what-if run never touches the real
tree.

Done when: a run against the sandbox leaves the real tree's digest unchanged. Found designing
this: `--root <copy>` already served a copy, but a job launched there could not import
anything -- the copy has no code.
"""
import os
import sys
import tempfile
import time
import unittest

from webui.paths import Root

try:
    import flask  # noqa: F401 -- availability probe: the fixture tree is the web tests' own
    from tests.test_webui_objects import plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        os.makedirs(os.path.join(self.td.name, "data", "local", "webui"), exist_ok=True)
        with open(os.path.join(self.td.name, "data", "local", "env.sh"), "w", encoding="utf-8") as fh:
            fh.write("export SECRET=1\n")
        os.makedirs(os.path.join(self.td.name, "data", "images", "teams"), exist_ok=True)
        with open(os.path.join(self.td.name, "data", "images", "teams", "gb.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n0")
        self.real = Root(self.td.name)
        self.made = []

    def tearDown(self):
        from webui import sandbox
        for sb in self.made:
            try:
                sandbox.discard(sb)
            except Exception:
                pass
        self.td.cleanup()


class TestCreateAndDiscard(Case):
    def test_the_copy_holds_the_data_and_nothing_secret(self):
        from webui import sandbox
        sb = sandbox.create(self.real)
        self.made.append(sb)
        self.assertNotEqual(os.path.normcase(sb.root), os.path.normcase(self.real.root))
        for d in ("current", "weeks", "decisions", "logs"):
            self.assertTrue(os.path.isdir(os.path.join(sb.data, d)), d)
        self.assertFalse(os.path.exists(os.path.join(sb.data, "local", "env.sh")), "data/local is never copied")
        self.assertFalse(os.path.exists(os.path.join(sb.data, "images")))
        self.assertTrue(sandbox.is_sandbox(sb))
        self.assertFalse(sandbox.is_sandbox(self.real))

    def test_discard_removes_a_sandbox_and_refuses_anything_else(self):
        from webui import sandbox
        sb = sandbox.create(self.real)
        sandbox.discard(sb)
        self.assertFalse(os.path.exists(sb.root))
        with self.assertRaises(ValueError):
            sandbox.discard(self.real)
        self.assertTrue(os.path.isdir(self.real.data), "the real tree is untouched")


class TestARunInTheSandbox(Case):
    def test_a_job_writes_the_copy_and_the_real_digest_does_not_move(self):
        from webui import sandbox
        from webui.jobs import JobRunner, OK
        before = self.real.tree_digest()
        sb = sandbox.create(self.real)
        self.made.append(sb)
        runner = JobRunner(sb, code_root=REPO, scan=lambda exclude=(): [])
        code = "import webui, os; open(os.path.join('data', 'current', 'probe.json'), 'w').write('{}')"
        job = runner.launch([sys.executable, "-c", code], "probe")
        for _ in range(200):
            if (runner.read(job) or {}).get("state") != "running":
                break
            time.sleep(0.05)
        meta = runner.read(job)
        self.assertEqual(meta["state"], OK, runner.log_text(job))
        self.assertTrue(os.path.isfile(os.path.join(sb.data, "current", "probe.json")), "the write landed in the copy")
        self.assertEqual(self.real.tree_digest(), before, "the real tree did not move")
        self.assertEqual(meta.get("sandbox"), sb.root)


class TestTheServerFlag(Case):
    def test_sandbox_serves_a_fresh_copy_and_cleans_it_up(self):
        from webui.__main__ import prepare_root
        root, code_root, cleanup = prepare_root(self.real.root, sandbox=True)
        try:
            self.assertNotEqual(os.path.normcase(root.root), os.path.normcase(self.real.root))
            self.assertEqual(os.path.normcase(code_root), os.path.normcase(self.real.root))
            self.assertTrue(os.path.isdir(os.path.join(root.data, "current")))
        finally:
            cleanup()
        self.assertFalse(os.path.exists(root.root))
        root2, code2, cleanup2 = prepare_root(self.real.root, sandbox=False)
        self.assertEqual(os.path.normcase(root2.root), os.path.normcase(self.real.root))
        cleanup2()
        self.assertTrue(os.path.isdir(self.real.data), "without --sandbox nothing is removed")


if __name__ == "__main__":
    unittest.main()

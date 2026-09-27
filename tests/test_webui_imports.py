"""
tests.test_webui_imports -- the web process never imports the engine (docs/WEB_UI.md 2.1).

Run in a SUBPROCESS on purpose: this suite's own interpreter has imported
fantasy_sim.simulation dozens of times before this module runs, so `sys.modules` in-process
cannot show what importing webui pulls in. Each test starts a fresh interpreter in a temp
directory holding a sentinel data/current/syndicate_warnings.log, imports, and reports
back (a) which fantasy_sim modules loaded and (b) whether the sentinel survived.

The characterisation twin imports fantasy_sim.simulation the same way and shows the
sentinel TRUNCATED -- F10's documented import-time FileHandler(mode='w'). It is what makes
the guard non-vacuous, and it is not a defect to fix.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = ("fantasy_sim.simulation", "fantasy_sim.decisions", "fantasy_sim.sync")
SENTINEL = b"sentinel: a real run's warnings, which an import must not clobber\n"

try:
    import flask  # noqa: F401 -- availability probe: the web tests skip cleanly without it
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def _run(script, workdir):
    env = dict(os.environ, PYTHONPATH=ROOT, MPLBACKEND="Agg")
    env.pop("SHOW_REAL_TEAM_NAMES", None)
    return subprocess.run([sys.executable, "-c", script], cwd=workdir, env=env,
                          capture_output=True, text=True, timeout=180)


def _plant(workdir):
    d = os.path.join(workdir, "data", "current")
    os.makedirs(d)
    p = os.path.join(d, "syndicate_warnings.log")
    with open(p, "wb") as f:
        f.write(SENTINEL)
    return p


REPORT = """
import json, sys
loaded = sorted(m for m in sys.modules if m.startswith('fantasy_sim'))
print('REPORT ' + json.dumps({'loaded': loaded}))
"""


class TestWebProcessImports(unittest.TestCase):
    @unittest.skipUnless(HAS_FLASK, "flask not installed")
    def test_importing_every_webui_module_loads_no_engine_and_leaves_the_warnings_log_alone(self):
        with tempfile.TemporaryDirectory() as wd:
            sentinel = _plant(wd)
            r = _run("import webui, webui.paths, webui.names, webui.live, webui.app, webui.__main__\n" + REPORT, wd)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            line = [ln for ln in r.stdout.splitlines() if ln.startswith("REPORT ")]
            self.assertTrue(line, r.stdout[-1000:])
            loaded = json.loads(line[-1][len("REPORT "):])["loaded"]
            bad = [m for m in loaded if m in FORBIDDEN or m.startswith("fantasy_sim.simulation")]
            self.assertEqual(bad, [], f"the web process imported the engine: {bad}")
            with open(sentinel, "rb") as f:
                self.assertEqual(f.read(), SENTINEL, "importing webui altered syndicate_warnings.log")

    def test_the_pure_modules_import_without_flask_at_all(self):
        with tempfile.TemporaryDirectory() as wd:
            _plant(wd)
            r = _run("import sys; sys.modules['flask'] = None\n"
                     "import webui, webui.paths, webui.names\nprint('OK')", wd)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("OK", r.stdout)

    def test_characterisation_importing_the_engine_truncates_the_warnings_log(self):
        """F10, pinned so the guard above is shown to test something real."""
        with tempfile.TemporaryDirectory() as wd:
            sentinel = _plant(wd)
            r = _run("import fantasy_sim.simulation\nprint('IMPORTED')", wd)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("IMPORTED", r.stdout)
            self.assertEqual(os.path.getsize(sentinel), 0,
                             "F10 changed: importing fantasy_sim.simulation no longer truncates the "
                             "mirror log -- update docs/WEB_UI.md 2.3 and this characterisation together")


class TestEntryPointPins(unittest.TestCase):
    """Textual pins on webui/__main__.py, in the style of tests.test_sample_report: the bind
    address is a literal, and no --host option exists (docs/WEB_UI.md 2.8)."""

    def setUp(self):
        with open(os.path.join(ROOT, "webui", "__main__.py"), encoding="utf-8") as f:
            self.src = f.read()

    def test_binds_the_loopback_literal(self):
        self.assertIn('host="127.0.0.1"', self.src)
        self.assertNotIn("0.0.0.0", self.src)

    def test_offers_no_host_option(self):
        self.assertNotIn('"--host"', self.src)
        self.assertNotIn("'--host'", self.src)

    def test_an_attempt_to_pass_host_is_refused_before_any_bind(self):
        r = subprocess.run([sys.executable, "-m", "webui", "--host", "0.0.0.0", "--port", "1"],
                           cwd=ROOT, capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, PYTHONPATH=ROOT, MPLBACKEND="Agg"))
        self.assertEqual(r.returncode, 2, r.stderr[-500:])
        self.assertIn("unrecognized arguments", r.stderr)

    def test_runs_with_the_reloader_off(self):
        # Werkzeug's reloader forks a twin process, which the W2 lock and the pre-launch
        # scan would see as a rival engine process.
        self.assertIn("use_reloader=False", self.src)

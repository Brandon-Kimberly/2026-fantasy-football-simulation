"""Workflow shell syntax: the gap that bit on kickoff day (2026-09-09).

The force-canonical baseline run did every meaningful thing right -- synced, gated
CANONICAL_OK, committed and pushed the canonical predictions row -- and then died on
its LAST, purely cosmetic step with a bash PARSE error (exit 2): an apostrophe inside a
`${VAR:-default}` expansion ("see the run's Artifacts section") opens a single-quoted
string that never closes. The readiness audit had already named workflow bash as the
one untested surface; it was proven only by live dispatch. This closes it: every bash
`run:` block in every workflow must parse.

Deliberately syntax-only (`bash -n`). It cannot catch logic errors, and it does not try
to -- but a workflow that cannot be PARSED fails 100% of the time, which is exactly the
class worth a mechanical guard.
"""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(ROOT, ".github", "workflows")

try:
    import yaml
except ImportError:                                    # pragma: no cover
    yaml = None


def _bash_blocks():
    """(file, step name, script) for every block GitHub will run under bash. A job on
    windows-latest defaults to pwsh, so only steps that ask for bash explicitly count
    there; an ubuntu job defaults to bash."""
    out = []
    for name in sorted(os.listdir(WORKFLOW_DIR)):
        if not name.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(WORKFLOW_DIR, name), encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        for job in (doc.get("jobs") or {}).values():
            windows = "windows" in str(job.get("runs-on", ""))
            for step in job.get("steps") or []:
                script = step.get("run")
                if not script:
                    continue
                shell = step.get("shell")
                if shell == "bash" or (shell is None and not windows):
                    out.append((name, step.get("name", "?"), script))
    return out


@unittest.skipIf(yaml is None, "pyyaml not installed; workflow syntax guard skipped")
class TestWorkflowBashParses(unittest.TestCase):
    def test_every_bash_run_block_parses(self):
        blocks = _bash_blocks()
        self.assertGreater(len(blocks), 10, "found suspiciously few bash blocks to check")
        failures = []
        for fname, step, script in blocks:
            # ${{ ... }} is GitHub template syntax, substituted before bash ever sees it.
            script = re.sub(r"\$\{\{[^}]*\}\}", "X", script)
            try:
                # bytes, not text: text mode would translate \n -> \r\n on Windows and
                # the stray CRs produce bogus parse errors of their own.
                proc = subprocess.run(["bash", "-n", "-"], input=script.encode("utf-8"),
                                      capture_output=True, timeout=30)
            except (FileNotFoundError, subprocess.TimeoutExpired) as ex:  # pragma: no cover
                self.skipTest(f"bash unavailable for the syntax guard ({ex})")
            if proc.returncode != 0:
                failures.append(f"{fname} -> {step}: {proc.stderr.decode(errors='replace').strip()}")
        self.assertEqual(failures, [], "workflow bash that will not parse:\n" + "\n".join(failures))


@unittest.skipIf(yaml is None, "pyyaml not installed; workflow syntax guard skipped")
class TestWorkflowBashExitStatus(unittest.TestCase):
    """The second way a cosmetic step kills a good run (2026-09-13).

    `[ "$MODE" = "force-canonical" ] && echo ...` PARSES fine, so the syntax guard above
    passes it. But a test that is false returns 1, and GitHub takes the block's final
    exit status as the STEP result -- so the job-summary step of the first automated
    canonical run that actually proceeded failed with exit 1 after sync, gate, canonical
    report and the committed predictions row had all succeeded. Same shape as the
    kickoff-day parse error: everything that mattered was already done, and the run still
    went red and alarmed.

    The rule is stricter than the bug needs, on purpose. Whether a given occurrence is
    "the last statement" changes the moment someone adds a line below it, so the guard
    does not try to decide which ones are currently safe: a statement-level test-and-run
    in workflow bash must neutralise its own exit status (`|| true`) or be written as an
    `if` block, which is the house style anyway.
    """

    # A test used as a statement: `[ x ] && cmd` / `[[ x ]] && cmd`, at the start of a
    # line (so `cmd1 && [ x ] && cmd2` -- where the status is already someone else's
    # concern -- is out of scope).
    STATEMENT_TEST = re.compile(r"^\s*\[\[?[^]]*\]\]?\s*&&")
    NEUTRALISED = re.compile(r"\|\|\s*(true|:)\s*$")

    def test_no_bare_statement_level_test_and_run(self):
        offenders = []
        for fname, step, script in _bash_blocks():
            for lineno, line in enumerate(script.splitlines(), 1):
                if self.STATEMENT_TEST.match(line) and not self.NEUTRALISED.search(line):
                    offenders.append(f"{fname} -> {step} (line {lineno}): {line.strip()[:90]}")
        self.assertEqual(
            offenders, [],
            "statement-level `[ ... ] && cmd` leaks its exit status into the step result; "
            "use an if block, or append `|| true`:\n" + "\n".join(offenders))


@unittest.skipIf(yaml is None, "pyyaml not installed; workflow syntax guard skipped")
class TestEveryAlarmCanClearItself(unittest.TestCase):
    """An alarm nobody can silence becomes an alarm nobody reads (2026-09-16).

    Four workflows open an `Automation failure: <name>` issue when they fail, and the
    body each one writes promises *"This issue auto-closes when a run succeeds."* Two of
    them could not keep that promise: `canonical-run` and `pages-sample` raised the alarm
    and had no clear-on-success step at all, so their issues sat open until closed by
    hand. Both were -- #8 on 2026-09-13 and #10 on 2026-09-16, each after the underlying
    failure had already been fixed and a later run had gone green.

    That is the same class as F41 and F44: a monitor whose false state is invisible to
    itself. The invariant is the one the issue body already states, so this test just
    holds the workflows to their own words."""

    ALARM = re.compile(r'title="Automation failure: ([a-z0-9-]+)"')

    def _by_file(self):
        out = {}
        for name in sorted(os.listdir(WORKFLOW_DIR)):
            if name.endswith((".yml", ".yaml")):
                with open(os.path.join(WORKFLOW_DIR, name), encoding="utf-8") as f:
                    out[name] = f.read()
        return out

    def test_a_workflow_that_raises_an_alarm_also_clears_it(self):
        missing = []
        for fname, text in self._by_file().items():
            if not self.ALARM.search(text):
                continue
            has_clear = "gh issue close" in text and "if: success()" in text
            if not has_clear:
                missing.append(fname)
        self.assertEqual(
            missing, [],
            "these workflows open an 'Automation failure' issue but never close it on a "
            "later success, while their issue body promises they will: " + ", ".join(missing))

    def test_the_promise_in_the_issue_body_is_the_one_being_tested(self):
        """Guards the test itself: if someone rewrites the body to stop promising
        auto-close, this test should be revisited rather than silently passing."""
        promises = [f for f, t in self._by_file().items()
                    if "auto-closes when a run succeeds" in t]
        self.assertGreaterEqual(len(promises), 2,
                                "the auto-close promise vanished from the alarm bodies; "
                                "re-read TestEveryAlarmCanClearItself before deleting it")


if __name__ == "__main__":
    unittest.main()

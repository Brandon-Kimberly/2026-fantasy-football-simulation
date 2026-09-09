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


if __name__ == "__main__":
    unittest.main()

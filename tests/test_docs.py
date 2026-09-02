"""Docs-match-reality guard. The README and CLAUDE.md went stale twice in one week (377
tests claimed vs 466 real; four shipped scripts undocumented), so staleness now fails
loudly instead of rotting silently:

- every 3+-digit "N tests" claim in README.md / CLAUDE.md must equal the discovered suite
  size (the 3-digit floor deliberately excludes the golden master's "15 tests" claim);
- every "N test modules" claim must equal the real count of tests/test_*.py files;
- every script under scripts/ must be mentioned in the README by its module path.

By design, adding tests or scripts without touching the docs fails this module. That is
the point.
"""
import glob
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _doc(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


class TestDocsMatchReality(unittest.TestCase):
    def test_stated_test_counts_match_the_discovered_suite(self):
        actual = unittest.TestLoader().discover(HERE, top_level_dir=ROOT).countTestCases()
        for doc in ("README.md", "CLAUDE.md"):
            stated = [int(x) for x in re.findall(r"\b(\d{3,})\s+tests\b", _doc(doc))]
            self.assertTrue(stated, f"{doc} states no suite-size claim at all")
            for n in stated:
                self.assertEqual(n, actual,
                                 f"{doc} claims {n} tests; the suite has {actual}. "
                                 f"Update the doc alongside the tests.")

    def test_stated_module_counts_match_the_test_tree(self):
        actual = len(glob.glob(os.path.join(HERE, "test_*.py")))
        for doc in ("README.md",):
            for n in (int(x) for x in re.findall(r"\b(\d+)\s+test modules\b", _doc(doc))):
                self.assertEqual(n, actual,
                                 f"{doc} claims {n} test modules; tests/ has {actual}.")

    def test_every_script_is_documented_in_the_readme(self):
        readme = _doc("README.md")
        missing = []
        for f in glob.glob(os.path.join(ROOT, "scripts", "*.py")):
            name = os.path.splitext(os.path.basename(f))[0]
            if name == "__init__":
                continue
            if f"scripts.{name}" not in readme:
                missing.append(name)
        self.assertEqual(missing, [],
                         "scripts shipped but not documented in README.md (mention each as "
                         "scripts.<name>): " + ", ".join(missing))


if __name__ == "__main__":
    unittest.main()

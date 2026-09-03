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

    def test_badges_match_reality(self):
        """The tests badge and the coverage badge are static Shields images; static means
        they CAN lie, so they are guarded like every other number: the tests badge must
        equal the discovered suite size, the coverage badge must equal the committed floor
        (coverage_floor.txt -- the single source of truth CI ratchets on)."""
        readme = _doc("README.md")
        m = re.search(r"badge/tests-(\d+)%20passing", readme)
        self.assertIsNotNone(m, "README has no tests badge")
        actual = unittest.TestLoader().discover(HERE, top_level_dir=ROOT).countTestCases()
        self.assertEqual(int(m.group(1)), actual,
                         f"tests badge says {m.group(1)}; the suite has {actual}")
        m = re.search(r"badge/coverage-([\d.]+)%25", readme)
        self.assertIsNotNone(m, "README has no coverage badge")
        floor = open(os.path.join(ROOT, "coverage_floor.txt"), encoding="utf-8").read().strip()
        self.assertEqual(m.group(1), floor,
                         f"coverage badge says {m.group(1)}; the committed floor is {floor}")

    def test_every_audit_plan_f_number_appears_in_the_summary(self):
        """F27: eighteen F-entries (F9-F26) accumulated in AUDIT_PLAN.md without ever
        reaching AUDIT_SUMMARY.md -- the document whose purpose is being the trustworthy
        overview. Guard the class: every F-heading in the plan must be mentioned in the
        summary."""
        plan_fs = set(re.findall(r"^### F(\d+)\b", _doc("docs/AUDIT_PLAN.md"), re.M))
        summary = _doc("AUDIT_SUMMARY.md")
        missing = sorted((int(n) for n in plan_fs
                          if not re.search(rf"\bF{n}\b", summary)))
        self.assertEqual(missing, [],
                         "AUDIT_PLAN F-entries absent from AUDIT_SUMMARY: "
                         + ", ".join(f"F{n}" for n in missing))

    def test_readme_headline_numbers_match_the_summary_totals(self):
        """F27's named repeatable mistake: 'checked a derived number against its stale
        origin' -- the README's bold audit line was verified against the summary's totals
        row while BOTH were stale together. This guard ties them so they can only move in
        the same commit; the summary totals row is the single source the README derives
        from."""
        m = re.search(r"\|\s*\*\*grand total\*\*\s*\|\s*\*\*~(\d+)[^|]*\|\s*\*\*(\d+) fixed",
                      _doc("AUDIT_SUMMARY.md"))
        self.assertIsNotNone(m, "AUDIT_SUMMARY has no parseable grand-total row")
        found, fixed = m.group(1), m.group(2)
        readme = _doc("README.md")
        line = re.search(r"\*\*What makes it different:.*?AUDIT_SUMMARY\.md", readme, re.S)
        self.assertIsNotNone(line, "README has no bold audit line")
        self.assertIn(f"~{found} findings", line.group(0),
                      f"README audit line does not carry the summary's ~{found} findings")
        self.assertIn(f"{fixed} fixed", line.group(0),
                      f"README audit line does not carry the summary's {fixed} fixed")

    def test_findings_cleared_in_the_plan_are_not_listed_open_in_the_summary(self):
        """The plan's F-headings carry status keywords (CLEARED/CLOSED/RESOLVED -- the
        convention CLAUDE.md's process rule codifies). Anything so marked must not sit in
        the summary's open-items table."""
        plan = _doc("docs/AUDIT_PLAN.md")
        closed = [m.group(1) for m in re.finditer(
            r"^### F(\d+)\b[^\n]*(?:CLEARED|CLOSED|RESOLVED)", plan, re.M)]
        summary = _doc("AUDIT_SUMMARY.md")
        m = re.search(r"## Open items.*?(?=\n## )", summary, re.S)
        self.assertIsNotNone(m, "AUDIT_SUMMARY has no open-items section")
        stale = [f"F{n}" for n in closed if re.search(rf"\|\s*F{n}\b", m.group(0))]
        self.assertEqual(stale, [],
                         "cleared/closed in AUDIT_PLAN but still open in the summary: "
                         + ", ".join(stale))

    def test_season_evaluation_is_tamper_evident(self):
        """SEASON_2026_EVALUATION.md's value is that it predates the results it will judge.
        Git is not immutable, so the lock is loudness: the file must hash to the recorded
        sidecar, any edit fails CI on an independent machine, and both files' histories
        show when anything changed."""
        import hashlib
        content = open(os.path.join(ROOT, "SEASON_2026_EVALUATION.md"), "rb").read()
        recorded = open(os.path.join(ROOT, "SEASON_2026_EVALUATION.sha256"),
                        encoding="utf-8").read().split()[0]
        self.assertEqual(hashlib.sha256(content).hexdigest(), recorded,
                         "SEASON_2026_EVALUATION.md no longer matches its recorded hash -- "
                         "the pre-commitment has been edited after the fact")

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

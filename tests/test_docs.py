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
        with open(os.path.join(ROOT, "coverage_floor.txt"), encoding="utf-8") as f:
            floor = f.read().strip()
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
        with open(os.path.join(ROOT, "SEASON_2026_EVALUATION.md"), "rb") as f:
            content = f.read()
        with open(os.path.join(ROOT, "SEASON_2026_EVALUATION.sha256"), encoding="utf-8") as f:
            recorded = f.read().split()[0]
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


class TestVersionMatchesTag(unittest.TestCase):
    def test_pyproject_version_matches_the_latest_git_tag(self):
        """Same pattern as the count and script-coverage guards: a stated number must
        match its source of truth. pyproject sat at 1.0.0 through three MAJOR releases
        before anyone noticed (2026-09-04); this pins it to the latest reachable tag.
        Skips (loudly, with the reason) where git or tags are unavailable -- CI's shallow
        checkout may not fetch tags -- because the enforcement point that matters is the
        local pre-commit hook, which runs where tags exist."""
        import re
        import subprocess
        m = re.search(r'^version = "([^"]+)"', _doc("pyproject.toml"), re.M)
        self.assertIsNotNone(m, "pyproject.toml has no version line")
        try:
            tag = subprocess.run(["git", "describe", "--tags", "--abbrev=0"],
                                 capture_output=True, text=True, timeout=10, cwd=ROOT)
        except Exception as ex:
            self.skipTest(f"git unavailable ({ex}); version-tag guard runs locally")
        if tag.returncode != 0 or not tag.stdout.strip():
            self.skipTest("no tags visible (shallow checkout?); version-tag guard runs locally")
        latest = tag.stdout.strip().lstrip("v")

        def sv(v):
            return tuple(int(x) for x in v.split("."))

        # Match-or-ahead, not exact match (2026-09-05): the bump commit necessarily
        # precedes its tag, so exact equality blocked every legitimate release sitting
        # at pre-commit. BEHIND is the drift disease this guard exists for (pyproject
        # sat at 1.0.0 through three MAJORs); AHEAD is a pending release, allowed.
        self.assertGreaterEqual(sv(m.group(1)), sv(latest),
                                f"pyproject version {m.group(1)} is BEHIND the latest tag "
                                f"v{latest} -- bump it in the tag's sitting (release policy)")
        m_cff = re.search(r"^version: (\S+)", _doc("CITATION.cff"), re.M)
        self.assertIsNotNone(m_cff, "CITATION.cff has no version line")
        self.assertGreaterEqual(sv(m_cff.group(1)), sv(latest),
                                f"CITATION.cff version {m_cff.group(1)} is BEHIND the latest "
                                f"tag v{latest} -- same sitting as the pyproject bump")
        self.assertEqual(m_cff.group(1), m.group(1),
                         "CITATION.cff and pyproject.toml disagree on the version")

    def test_citation_date_released_is_not_older_than_the_latest_tag(self):
        """H4. The version guard above pins the NUMBER and ignores the DATE, and the date
        drifted exactly the way the number used to: `CITATION.cff` read
        `date-released: 2026-09-05` at v7.0.0, two tags stale, while its `version` line was
        correct. A citation that names the right release on the wrong date is wrong in the
        one field a citation exists to carry.

        COVERAGE, NOT A CHARACTERISATION: the date is current today (the v7.0.0 sitting
        fixed it), so this passes on first run and no defect is being repaired. Said
        plainly rather than presented as a fix. Verified load-bearing by mutation --
        setting the date back to 2026-09-05 turns it red.

        BEHIND is the disease; AHEAD is allowed, for the same reason the version guard
        allows it. GitHub tags server-side, so a release cut today can point at a commit
        from yesterday, and the bump commit necessarily precedes its own tag.

        Same skip semantics as the version guard: the enforcement point is the local
        pre-commit hook, where tags exist.
        """
        import datetime as _dt
        import re
        import subprocess
        m = re.search(r'^date-released:\s*"?(\d{4}-\d{2}-\d{2})"?', _doc("CITATION.cff"), re.M)
        self.assertIsNotNone(m, "CITATION.cff has no date-released line")
        try:
            tag = subprocess.run(["git", "describe", "--tags", "--abbrev=0"],
                                 capture_output=True, text=True, timeout=10, cwd=ROOT)
        except Exception as ex:
            self.skipTest(f"git unavailable ({ex}); citation-date guard runs locally")
        if tag.returncode != 0 or not tag.stdout.strip():
            self.skipTest("no tags visible (shallow checkout?); citation-date guard runs locally")
        name = tag.stdout.strip()
        # The TAGGED COMMIT's date, not the tag object's: a lightweight tag has no date of
        # its own, and this works for both kinds.
        when = subprocess.run(["git", "log", "-1", "--format=%cs", name],
                              capture_output=True, text=True, timeout=10, cwd=ROOT)
        if when.returncode != 0 or not when.stdout.strip():
            self.skipTest(f"could not read the date of {name}; guard runs locally")
        tag_date = _dt.date.fromisoformat(when.stdout.strip())
        cff_date = _dt.date.fromisoformat(m.group(1))
        self.assertGreaterEqual(
            cff_date, tag_date,
            f"CITATION.cff date-released {cff_date} is BEHIND the latest tag {name} "
            f"({tag_date}) -- update it in the tag's sitting, alongside the version and "
            f"the CHANGELOG entry (release policy)")

    def test_changelog_lists_the_latest_git_tag(self):
        """Every release gets a CHANGELOG entry (owner's rule, 2026-09-04, made
        mechanical): the latest reachable tag must appear as a linked heading. Same skip
        semantics as the version guard -- the enforcement point is the local pre-commit
        hook, where tags exist. On failure: add the headline entry (release policy)."""
        import subprocess
        try:
            tag = subprocess.run(["git", "describe", "--tags", "--abbrev=0"],
                                 capture_output=True, text=True, timeout=10, cwd=ROOT)
        except Exception as ex:
            self.skipTest(f"git unavailable ({ex}); changelog-tag guard runs locally")
        if tag.returncode != 0 or not tag.stdout.strip():
            self.skipTest("no tags visible (shallow checkout?); changelog-tag guard runs locally")
        latest = tag.stdout.strip()
        self.assertIn(f"[{latest}]", _doc("CHANGELOG.md"),
                      f"CHANGELOG.md has no entry for the latest tag {latest} -- every "
                      "release gets a headline entry in the same sitting (release policy)")


class TestSampleWorkflowCoversRenderer(unittest.TestCase):
    def test_every_renderer_source_triggers_the_pages_deploy(self):
        """The sample is a BUILD PRODUCT since 2026-09-05 (deployed by
        .github/workflows/pages-sample.yml, never committed -- six 9MB blobs in ten
        days made the old committed-sample scheme untenable). The freshness property
        the old stamp guard enforced is now structural: a renderer change must trigger
        a redeploy, so the workflow's paths list has to cover every renderer source.
        This guard pins that the trigger cannot silently rot."""
        from scripts.make_sample_report import RENDERER_SOURCES
        wf = _doc(".github/workflows/pages-sample.yml")
        for rel in tuple(RENDERER_SOURCES) + ("scripts/make_sample_report.py",):
            self.assertIn(rel, wf,
                          f"pages-sample.yml does not trigger on {rel} -- a change "
                          "there would leave the deployed sample stale")


class TestFingerprintIsNewlineInsensitive(unittest.TestCase):
    def test_crlf_and_lf_copies_of_the_renderer_hash_identically(self):
        """A rebase or fresh checkout re-materializes the renderer sources through
        autocrlf, flipping their raw bytes CRLF<->LF with no content change -- which
        made the freshness guard fire a false alarm on an untouched renderer
        (2026-09-04, found after the F36 rebase). The fingerprint must hash logical
        content, not checkout-dependent bytes."""
        import tempfile
        from scripts.make_sample_report import RENDERER_SOURCES, renderer_fingerprint
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for repo, nl in ((a, b"\n"), (b, b"\r\n")):
                for rel in RENDERER_SOURCES:
                    path = os.path.join(repo, *rel.split("/"))
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(nl.join([b"line one", b"line two", b""]))
            self.assertEqual(renderer_fingerprint(a), renderer_fingerprint(b))



if __name__ == "__main__":
    unittest.main()


class TestNoStrayRunLogsAreTracked(unittest.TestCase):
    """H3. A `git add -A` after a piped run sweeps up whatever `> foo.log` left behind.
    This has happened three times: `w.log` (removed one commit later), then `collins.log`
    and `ev.log`, both committed unnoticed and both containing nothing but simulation
    chatter.

    The `.gitignore` rule is the fix; this is the guard that says so out loud, because the
    rule is easy to lose in a file whose first line is "All runtime output" followed by
    twenty deliberate exceptions.

    `data/logs/*.jsonl` are the real, deliberately-tracked records (projection log,
    decision log, bid ledger, and so on). They are `.jsonl`, not `.log`, and the root
    anchor keeps them out of scope either way.
    """

    def test_no_dot_log_file_is_tracked(self):
        import subprocess
        tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                                 cwd=ROOT).stdout.split()
        strays = [f for f in tracked if f.lower().endswith(".log")]
        self.assertEqual(strays, [],
                         f"stray run log(s) committed: {strays}. Delete them and check "
                         f".gitignore's root *.log rule.")

    def test_the_gitignore_rule_exists_and_is_root_anchored(self):
        """Root-anchored on purpose: an unanchored `*.log` would also swallow anything
        under data/logs/ if a future record is ever written with that extension."""
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("/*.log", body,
                      "the root *.log rule is what stops a piped run being committed")

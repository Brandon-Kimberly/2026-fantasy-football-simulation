"""scripts.localize_reports: the owner's private real-name conversion for downloaded
runner artifacts (F37 follow-up, 2026-09-06). Pure logic tested here; the live-fetch
mapping path is exercised by running the script, not the suite (no network in tests)."""
import os
import tempfile
import unittest
import zipfile

from scripts.localize_reports import (
    BANNER_MARKER, extract_zips, localize_text, refuse_unsafe_root,
)

MAP = {"Quantum Ferrets": "Real Team A", "Polar Yetis": "Real Team B"}


class TestLocalizeText(unittest.TestCase):
    def test_html_gets_every_name_replaced_and_a_banner_before_the_h1(self):
        html = "<body><h1>Weekly report -- Quantum Ferrets, week 3</h1>Polar Yetis lost.</body>"
        out = localize_text(html, MAP, kind="html")
        self.assertNotIn("Quantum Ferrets", out)
        self.assertIn("Real Team A", out)
        self.assertIn("Real Team B", out)
        self.assertIn(BANNER_MARKER, out)
        self.assertLess(out.index(BANNER_MARKER), out.index("<h1"),
                        "the private-copy banner must be visible at the top")

    def test_md_gets_a_top_banner(self):
        md = "# Weekly report -- Quantum Ferrets, week 3\n\nPolar Yetis lost.\n"
        out = localize_text(md, MAP, kind="md")
        self.assertTrue(out.splitlines()[0].startswith(">"))
        self.assertIn(BANNER_MARKER, out)
        self.assertNotIn("Quantum Ferrets", out)

    def test_idempotent_on_a_second_pass(self):
        html = "<body><h1>Quantum Ferrets</h1></body>"
        once = localize_text(html, MAP, kind="html")
        twice = localize_text(once, MAP, kind="html")
        self.assertEqual(once, twice, "re-running must be a no-op (banner not duplicated)")


class TestSafety(unittest.TestCase):
    def test_paths_outside_data_are_refused_hard(self):
        with self.assertRaises(SystemExit):
            refuse_unsafe_root("docs/results")
        with self.assertRaises(SystemExit):
            refuse_unsafe_root(".")
        refuse_unsafe_root(os.path.join("data", "results"))   # must not raise


class TestZipIntake(unittest.TestCase):
    def test_a_dropped_artifact_zip_is_extracted_once(self):
        with tempfile.TemporaryDirectory() as d:
            z = os.path.join(d, "weekly-report-123.zip")
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("week_01/report.html", "<h1>Quantum Ferrets</h1>")
            extract_zips(d)
            target = os.path.join(d, "weekly-report-123", "week_01", "report.html")
            self.assertTrue(os.path.exists(target))
            extract_zips(d)   # second call: already-extracted zips are skipped, no error


class TestFetch(unittest.TestCase):
    """--fetch (2026-09-06): the last manual seam in the weekly ritual -- browser
    download of runner artifacts -- replaced by gh. Pure planning/filing logic tested
    here; the gh calls sit behind an injectable seam and are exercised live."""

    def test_only_runs_without_an_existing_directory_are_planned(self):
        from scripts.localize_reports import missing_runs
        self.assertEqual(missing_runs(["111", "222", "333"],
                                      {"weekly-report-222", "unrelated"}),
                         ["111", "333"])

    def test_downloaded_week_dirs_are_filed_under_the_matching_results_week(self):
        import os, tempfile
        from scripts.localize_reports import file_artifact
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(src, "week_03", "archive"))
            with open(os.path.join(src, "week_03", "archive", "r.html"), "w") as f:
                f.write("<h1>x</h1>")
            file_artifact(src, root, "weekly-report-999")
            self.assertTrue(os.path.exists(os.path.join(
                root, "week_03", "weekly-report-999", "week_03", "archive", "r.html")))

    def test_an_artifact_without_week_dirs_files_under_unsorted(self):
        import os, tempfile
        from scripts.localize_reports import file_artifact
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as root:
            with open(os.path.join(src, "loose.md"), "w") as f:
                f.write("x")
            file_artifact(src, root, "weekly-report-7")
            self.assertTrue(os.path.exists(os.path.join(
                root, "unsorted", "weekly-report-7", "loose.md")))



if __name__ == "__main__":
    unittest.main()

"""H1: the real-name scanner, which existed only in a session transcript.

The standing rule is absolute -- no real team name and no real username in any tracked
file, ever. A LITERAL-match scan on 2026-09-22 reported the repo clean while four
real-identity strings sat in tracked files. A tokenising scan the next day found all four,
and it was an ad-hoc block in a chat window rather than a tool. H1 puts it in the repo.

The four shapes the literal scan could not see, reproduced below with FICTIONAL names
because this file is itself a tracked file:

  1. a username built from a team name          `walrus_fan_99`
  2. a variable named after a team               `quantum_rate = 0.3`
  3. one word of a team name merged into a fictional one   `NeonWalrusCats`
  4. a manager `style` string equal to the first word of a team name  `"style": "quantum"`

Every one of them is a SUBSTRING match against a tokenised name and none of them contains
the name itself, which is the whole argument for the tool.

Hermetic: no network, no environment, no real names. `fetch_names` is the only part that
talks to Sleeper and it is not exercised here -- the tokeniser and the scanner are pure
functions that take a name list, which is what makes this testable at all.
"""
import os
import tempfile
import unittest

from scripts.scan_real_names import identity_tokens, scan_paths

# Fictional, and deliberately shaped like the real ones: a two-word animal team name, a
# plural, and an underscored username.
NAMES = ["Quantum Ferrets", "Neon Walruses", "polar_yetis_owner"]


class TestTokenisation(unittest.TestCase):
    def test_each_word_of_a_team_name_is_its_own_token(self):
        t = identity_tokens(NAMES)
        self.assertIn("quantum", t)
        self.assertIn("ferrets", t)

    def test_plurals_contribute_their_stem(self):
        """`Walruses` in the league; `walrus_fan_99` in the file. Without the stem the
        username does not match anything."""
        self.assertIn("walrus", identity_tokens(NAMES))

    def test_the_separatorless_form_is_a_token(self):
        """A real name merged into a longer fictional string was one of the four misses."""
        self.assertIn("quantumferrets", identity_tokens(NAMES))

    def test_short_words_are_not_tokens(self):
        """Below five characters a token matches ordinary English constantly, and an
        unreadable report is a report nobody runs."""
        self.assertNotIn("neon", identity_tokens(NAMES))

    def test_the_minimum_length_is_adjustable(self):
        self.assertIn("neon", identity_tokens(NAMES, min_len=4))


class TestTheFourShapesTheLiteralScanMissed(unittest.TestCase):
    """The acceptance criterion. Each planted line is caught, and each is invisible to a
    literal search for the name itself -- which is asserted, not assumed."""

    PLANTS = {
        "username.py": 'OWNER = "walrus_fan_99"\n',
        "rates.py": "quantum_rate = 0.3\n",
        "merged.md": "The NeonWalrusCats finished third.\n",
        "profile.json": '{"style": "quantum"}\n',
    }

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for name, body in self.PLANTS.items():
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
                f.write(body)

    def _scan(self, tokens=None, paths=None):
        return scan_paths(tokens if tokens is not None else identity_tokens(NAMES),
                          paths if paths is not None else sorted(self.PLANTS),
                          root=self.dir)

    def test_every_planted_derivative_is_caught(self):
        found = {h[0] for h in self._scan()}
        self.assertEqual(found, set(self.PLANTS),
                         "each of the four 2026-09-22 shapes must be caught")

    def test_a_literal_scan_would_have_missed_all_four(self):
        """The reason this tool exists. If this ever fails, the planted lines have drifted
        into containing the name outright and the test above proves nothing."""
        for body in self.PLANTS.values():
            for name in NAMES:
                self.assertNotIn(name.lower(), body.lower())

    def test_a_hit_reports_the_token_that_matched(self):
        """A human has to be able to tell a leak from a coincidence, and the token is the
        only thing that lets them: NFL player names are domain data, not identities."""
        by_file = {h[0]: h[2] for h in self._scan()}
        self.assertEqual(by_file["rates.py"], "quantum")
        self.assertEqual(by_file["username.py"], "walrus")

    def test_a_clean_file_produces_no_hit(self):
        with open(os.path.join(self.dir, "clean.py"), "w", encoding="utf-8") as f:
            f.write("TEAMS = ['Rocket Pandas', 'Drifting Icebergs']\n")
        self.assertEqual(self._scan(paths=["clean.py"]), [])

    def test_the_allowlist_suppresses_an_adjudicated_token(self):
        """An adjudicated false positive is dropped by removing its token, the same way
        the CLI does it. The allowlist is local and gitignored on purpose -- a committed
        list of 'ordinary words to ignore' assembled from real names would BE a leak."""
        tokens = identity_tokens(NAMES) - {"quantum"}
        self.assertEqual({h[0] for h in self._scan(tokens=tokens)},
                         {"username.py", "merged.md"})

    def test_a_binary_file_is_skipped_rather_than_mojibaked(self):
        with open(os.path.join(self.dir, "blob.bin"), "wb") as f:
            f.write(b"\x00\x01quantum\x00")
        self.assertEqual(self._scan(paths=["blob.bin"]), [])

    def test_no_tokens_means_no_hits_rather_than_everything(self):
        """An empty token set must not be read as 'match anything'. The CLI additionally
        refuses outright when the name fetch comes back empty, because a scan against
        nothing reports CLEAN and means nothing."""
        self.assertEqual(self._scan(tokens=set()), [])

    def test_the_scanner_writes_nothing(self):
        before = sorted(os.listdir(self.dir))
        self._scan()
        self.assertEqual(sorted(os.listdir(self.dir)), before)


class TestTheAllowlistIsNotCommittable(unittest.TestCase):
    def test_gitignore_covers_the_allowlist(self):
        import fantasy_sim
        root = os.path.dirname(os.path.dirname(os.path.abspath(fantasy_sim.__file__)))
        with open(os.path.join(root, ".gitignore"), encoding="utf-8") as f:
            self.assertIn(".real_name_scan_allow", f.read(),
                          "the allowlist holds adjudicated real-name fragments and must "
                          "never be committable")


if __name__ == "__main__":
    unittest.main()

"""Secrets read from the environment must survive the ways people actually set them.

`config.LEAGUE_ID` carries `.strip()` and a comment recording why: *"a secret set via a
shell pipe carries a trailing newline (bit the first Pages run, 2026-09-05)"*. That lesson
was applied to one variable and not the others. `ODDS_API_KEY` in particular is read raw,
and a key with a trailing newline fails with a 401 that the sync reports as
`VEGAS FALLBACK: odds API request failed` -- indistinguishable from the API being down,
which is F52's class of confusion applied to a credential.

Found 2026-09-23, while the owner was rotating that key.

These run in SUBPROCESSES. Reloading config in-process to vary the environment would
rebind SIM_CONFIG to a new object while simulation.py still holds the old one, which
tests/test_invariants.py exists to police.
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every config value read straight from the environment, and the variable behind it.
ENV_BACKED = [
    ("LEAGUE_ID", "SLEEPER_LEAGUE_ID"),
    ("ESPN_LEAGUE_ID", "ESPN_LEAGUE_ID"),
    ("ODDS_API_KEY", "ODDS_API_KEY"),
    ("ESPN_S2", "ESPN_S2"),
    ("ESPN_SWID", "ESPN_SWID"),
]


def _config_value(attr, env):
    out = subprocess.run(
        [sys.executable, "-c",
         f"from fantasy_sim import config; print(repr(config.{attr}))"],
        capture_output=True, text=True, cwd=ROOT, env=env)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


class TestEnvironmentSecretsAreStripped(unittest.TestCase):
    """A value set through a shell pipe, a CI secret, or a copy-paste that caught a
    newline must not silently become a different credential."""

    def test_every_env_backed_value_strips_surrounding_whitespace(self):
        bad = []
        for attr, var in ENV_BACKED:
            env = dict(os.environ)
            env[var] = "  abc123\n"
            got = _config_value(attr, env)
            if got != repr("abc123"):
                bad.append(f"{attr} (from {var}) -> {got}")
        self.assertEqual(
            bad, [],
            "a secret with surrounding whitespace must be stripped, exactly as "
            "config.LEAGUE_ID already is: " + "; ".join(bad))

    def test_an_unset_variable_stays_empty_rather_than_becoming_whitespace(self):
        for attr, var in ENV_BACKED:
            env = {k: v for k, v in os.environ.items() if k != var}
            self.assertEqual(_config_value(attr, env), repr(""),
                             f"{attr} must be empty when {var} is unset")


class TestSecretsAreNotCommitted(unittest.TestCase):
    """The odds key is the one credential in this project. It must never reach the repo --
    in a tracked file or in history."""

    def test_the_odds_key_is_not_in_any_tracked_file(self):
        key = os.getenv("ODDS_API_KEY", "").strip()
        if not key:
            self.skipTest("ODDS_API_KEY not set in this environment")
        out = subprocess.run(["git", "grep", "-I", "-l", key],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(out.stdout.strip(), "",
                         "the odds API key appears in a tracked file")

    def test_no_env_file_is_tracked(self):
        """data/local/ holds the real values and is gitignored. A stray .env elsewhere
        would not be."""
        out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=ROOT)
        tracked = out.stdout.split()
        leaked = [f for f in tracked
                  if os.path.basename(f) in (".env", "env.sh", "secrets.json")
                  or f.startswith("data/local/")]
        self.assertEqual(leaked, [], f"credential-bearing files are tracked: {leaked}")


if __name__ == "__main__":
    unittest.main()

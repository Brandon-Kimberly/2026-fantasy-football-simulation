"""B20: the league renewal chain is broken at 2025, and the missing id was hardcoded.

The 2025 league's `previous_league_id` is `None`, so every tool that walks the chain
(`luck_ledger --all`, `season_retrospective`) silently stops one season short. The 2024
id existed only as a literal.

WORSE THAN B20 SAID, and this is the part that matters. B20 describes the id as living
"in a scratchpad script and a `--league-id` flag". It is in **four tracked files** --
`scripts/luck_ledger.py`, `docs/LUCK_LEDGER.md`, `docs/AUDIT_PLAN.md` and
`docs/SCOPED_BACKLOG.md` (B20's own text) -- which makes it a standing violation of F37,
the finding that made league identifiers environment-only precisely so they stay out of
the repository. `AUDIT_PLAN.md` also carries two DRAFT ids, and `/draft/{id}/picks` is
public and exposes rosters, so those identify the league just as well.

Written before the fix and confirmed failing (rule 1). The repo-wide guard is the one
test here that is a guard rather than a characterisation -- it would have caught this
years earlier, and its job now is to make sure it cannot come back.
"""
import logging
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Sleeper league/draft/transaction ids are 18-19 digits starting with 1.
SLEEPER_ID = re.compile(r'(?<![\d.])1\d{17,18}(?![\d.])')

# Ids that may appear in the repo, each with a reason. TRANSACTION ids are not league
# identifiers: they are opaque without the league id, and data/logs/decision_log.jsonl --
# committed, and the irreplaceable season record -- is full of them already. Scrubbing two
# code comments while the log carries a thousand would be theatre. League and DRAFT ids
# are a different matter and belong in the environment (F37).
ALLOWED_IDS = {
    "1401228656092672000",   # a transaction id, cited in decisions.py and test_decisions.py
}


def _tracked():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=ROOT)
    return [f for f in out.stdout.split()
            if not f.startswith(("data/local", "data/logs"))]


class TestNoLeagueIdentifiersInTheRepo(unittest.TestCase):
    """GUARD (F37). Not a characterisation of a logic defect -- a standing check that the
    repository carries no Sleeper identifier. It is red right now because four files do."""

    def test_no_sleeper_shaped_ids_outside_the_allowlist(self):
        offenders = {}
        for f in _tracked():
            try:
                with open(os.path.join(ROOT, f), encoding="utf-8", errors="ignore") as fh:
                    s = fh.read()
            except OSError:
                continue
            found = {x for x in SLEEPER_ID.findall(s)} - ALLOWED_IDS
            if found:
                offenders[f] = len(found)
        self.assertEqual(
            offenders, {},
            "F37: league identifiers are environment-only and must never be committed. "
            f"Files carrying one: {sorted(offenders)}")


class TestKnownLeagueIds(unittest.TestCase):
    def test_the_map_exists(self):
        from fantasy_sim import config
        self.assertTrue(hasattr(config, "KNOWN_LEAGUE_IDS"),
                        "B20: the renewal chain needs a season -> league id map")
        self.assertIsInstance(config.KNOWN_LEAGUE_IDS, dict)

    def test_it_holds_no_literals_and_reads_the_environment(self):
        """F37. Every value comes from an env var; with none set (the hermetic suite) the
        map is simply empty rather than exposing a default."""
        import inspect
        from fantasy_sim import config
        src = inspect.getsource(config)
        m = re.search(r"KNOWN_LEAGUE_IDS\s*=\s*(.{0,600})", src, re.S)
        self.assertIsNotNone(m)
        self.assertIsNone(SLEEPER_ID.search(m.group(1)),
                          "B20/F37: a league id literal in config.py")
        self.assertIn("SLEEPER_LEAGUE_ID_2024", src,
                      "B20 scope: 2024 comes from SLEEPER_LEAGUE_ID_2024")

    def test_unset_environment_yields_no_entries_rather_than_blanks(self):
        """A SUBPROCESS, with the league vars stripped. Reloading config in-process would
        rebind SIM_CONFIG to a new object while simulation.py still holds the old one --
        the hazard tests/test_invariants.py exists to police."""
        import sys
        env = {k: v for k, v in os.environ.items() if not k.startswith("SLEEPER_LEAGUE_ID")}
        out = subprocess.run(
            [sys.executable, "-c",
             "from fantasy_sim import config; "
             "print(sorted(config.KNOWN_LEAGUE_IDS.items()))"],
            capture_output=True, text=True, cwd=ROOT, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]",
                         "with no league env set the map must be EMPTY, never blank values")

    def test_a_set_environment_populates_the_map(self):
        import sys
        env = dict(os.environ, SLEEPER_LEAGUE_ID_2024="L24", SLEEPER_LEAGUE_ID_2025="L25",
                   SLEEPER_LEAGUE_ID="L26")
        out = subprocess.run(
            [sys.executable, "-c",
             "from fantasy_sim import config; "
             "print(sorted(config.KNOWN_LEAGUE_IDS.items()))"],
            capture_output=True, text=True, cwd=ROOT, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(),
                         "[('2024', 'L24'), ('2025', 'L25'), ('2026', 'L26')]")


class TestResolveTheChain(unittest.TestCase):
    """The walker is fetch-injected so this stays hermetic (F48): no network, no env."""

    CHAIN = {
        "L2026": {"season": "2026", "previous_league_id": "L2025"},
        "L2025": {"season": "2025", "previous_league_id": None},   # the break
        "L2024": {"season": "2024", "previous_league_id": None},
    }

    def _fetch(self, lid):
        return dict(self.CHAIN.get(lid) or {})

    def test_it_walks_the_chain(self):
        from fantasy_sim.league_chain import resolve_chain
        got = resolve_chain("L2026", fetch=self._fetch, known={})
        self.assertEqual([s for s, _ in got], ["2026", "2025"])

    def test_the_map_supplies_the_season_the_broken_chain_cannot_reach(self):
        from fantasy_sim.league_chain import resolve_chain
        got = resolve_chain("L2026", fetch=self._fetch, known={"2024": "L2024"})
        self.assertEqual([s for s, _ in got], ["2026", "2025", "2024"],
                         "B20: 2024 is orphaned and must come from the map")
        self.assertEqual(dict(got)["2024"], "L2024")

    def test_a_disagreement_between_chain_and_map_warns(self):
        """B20 scope: chain-walkers 'consult it as a fallback and *warn* when the chain
        and the map disagree' -- a stale env var must be loud, not silently preferred."""
        from fantasy_sim.league_chain import resolve_chain
        with self.assertLogs(level=logging.WARNING) as cm:
            got = resolve_chain("L2026", fetch=self._fetch, known={"2025": "SOMETHING-ELSE"})
        self.assertTrue(any("2025" in m for m in cm.output), cm.output)
        self.assertEqual(dict(got)["2025"], "L2025",
                         "the live chain wins; the map is a fallback, not an override")

    def test_agreement_is_silent(self):
        from fantasy_sim.league_chain import resolve_chain
        logging.disable(logging.NOTSET)
        with self.assertRaises(AssertionError):
            with self.assertLogs(level=logging.WARNING):
                resolve_chain("L2026", fetch=self._fetch, known={"2025": "L2025"})

    def test_a_cycle_terminates(self):
        from fantasy_sim.league_chain import resolve_chain
        loop = {"A": {"season": "2026", "previous_league_id": "B"},
                "B": {"season": "2025", "previous_league_id": "A"}}
        got = resolve_chain("A", fetch=lambda i: dict(loop.get(i) or {}), known={})
        self.assertEqual(len(got), 2)

    def test_no_current_league_id_is_refused_not_guessed(self):
        from fantasy_sim.league_chain import resolve_chain
        with self.assertRaises(ValueError):
            resolve_chain("", fetch=self._fetch, known={})


if __name__ == "__main__":
    unittest.main()

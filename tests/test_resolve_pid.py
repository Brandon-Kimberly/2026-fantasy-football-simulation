"""B17: nothing protects a script that builds {name: pid} from the raw player cache.

`sync.resolve_player_keys` gets collisions right for the baselines, and
`decisions.resolve_player` gets them right for name-keyed pools. Neither helps an ad-hoc
script that does `by_name.setdefault(name, pid)` straight off the 12,228-player cache --
which is what every scratchpad script did in week 3, and one of them scored the CB
DeVonta Smith's 0.0 as the WR's.

WHAT B17 GOT WRONG, and it is worth recording. B17 says of `decisions.resolve_player`:
"check whether it raises on ambiguity. If it does not, make it." It does, and changing it
would be a mistake. Its POOL is already disambiguated by `resolve_player_keys`:

  - exactly one colliding pid rostered -> he keeps the plain name, so an exact-match
    query correctly returns the rostered player;
  - none rostered -> BOTH are keyed "Name (pid)", so a plain-name query matches two
    substrings and the resolver refuses with the candidate list.

Verified in TestResolvePlayerWasNeverTheBug below. The gap is the RAW-cache path, which
bypasses that keying entirely, so the fix is a new helper rather than a change to that one.

THE COLLISIONS ARE NOT THE THREE B17 NAMES. On the live cache there are 220 colliding
names, SEVEN of which involve a player rostered in this league -- including Josh Allen
(QB BUF vs a guard) and DJ Moore (WR BUF vs a CB). The fixtures below mirror the real
shapes; the suite stays hermetic (F48) and never reads data/current.

Written before the helper existed and confirmed failing (rule 1).
"""
import logging
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fixture DBs shaped like the real Sleeper cache, mirroring live collisions.
DB = {
    # collision: one WR, one CB -- the one that actually burned us
    "7525":  {"first_name": "DeVonta", "last_name": "Smith", "position": "WR", "team": "PHI"},
    "13977": {"first_name": "DeVonta", "last_name": "Smith", "position": "CB", "team": "CAR"},
    # collision: QB vs G
    "4984":  {"first_name": "Josh", "last_name": "Allen", "position": "QB", "team": "BUF"},
    "2212":  {"first_name": "Josh", "last_name": "Allen", "position": "G", "team": None},
    # collision where NEITHER is rostered
    "9001":  {"first_name": "Ghost", "last_name": "Twin", "position": "WR", "team": "NO"},
    "9002":  {"first_name": "Ghost", "last_name": "Twin", "position": "LB", "team": "SF"},
    # no collision
    "4866":  {"first_name": "Jahmyr", "last_name": "Gibbs", "position": "RB", "team": "DET"},
}
ROSTERED = {"7525", "4984", "4866"}          # the WR, the QB, and Gibbs


class TestTheHelperExists(unittest.TestCase):
    def test_resolve_pid_is_importable(self):
        from fantasy_sim import player_ids
        self.assertTrue(hasattr(player_ids, "resolve_pid"),
                        "B17: an ad-hoc script needs one place to turn a name into a pid "
                        "that refuses to guess")

    def test_the_module_is_cheap_to_import(self):
        """An ad-hoc script must not pay for the engine to resolve a name. decisions.py
        imports FantasySimulationEngine at module scope, which is why this does not live
        beside resolve_player.

        Checked in a SUBPROCESS on purpose: clearing fantasy_sim out of sys.modules in
        this process would re-import config and hand the rest of the suite a different
        SIM_CONFIG object, which tests/test_invariants.py exists to police."""
        import subprocess
        import sys
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; import fantasy_sim.player_ids; "
             "print('simulation' in ''.join(sys.modules))"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "False",
                         "B17: resolving a name must not import the engine")


class TestUnambiguousNames(unittest.TestCase):
    def test_a_unique_name_resolves(self):
        from fantasy_sim.player_ids import resolve_pid
        self.assertEqual(resolve_pid("Jahmyr Gibbs", DB, rostered_pids=ROSTERED), "4866")

    def test_an_unknown_name_is_refused(self):
        from fantasy_sim.player_ids import resolve_pid, AmbiguousPlayer
        with self.assertRaises((KeyError, AmbiguousPlayer)):
            resolve_pid("Nobody At All", DB, rostered_pids=ROSTERED)


class TestCollisions(unittest.TestCase):
    """Mirrors sync.resolve_player_keys' rule exactly -- one rostered wins, none is
    ambiguous, two is fatal."""

    def test_exactly_one_rostered_resolves_to_him_and_warns(self):
        from fantasy_sim.player_ids import resolve_pid
        with self.assertLogs(level=logging.WARNING) as cm:
            pid = resolve_pid("DeVonta Smith", DB, rostered_pids=ROSTERED)
        self.assertEqual(pid, "7525", "the rostered WR, not the CB")
        self.assertTrue(any("COLLISION" in m for m in cm.output), cm.output)

    def test_the_other_real_collision_resolves_too(self):
        from fantasy_sim.player_ids import resolve_pid
        with self.assertLogs(level=logging.WARNING):
            self.assertEqual(resolve_pid("Josh Allen", DB, rostered_pids=ROSTERED), "4984")

    def test_none_rostered_is_refused_not_guessed(self):
        from fantasy_sim.player_ids import resolve_pid, AmbiguousPlayer
        with self.assertRaises(AmbiguousPlayer) as ctx:
            resolve_pid("Ghost Twin", DB, rostered_pids=ROSTERED)
        msg = str(ctx.exception)
        self.assertIn("9001", msg)
        self.assertIn("9002", msg)

    def test_two_rostered_is_refused(self):
        """sync raises ValueError here because name-keyed data cannot represent it."""
        from fantasy_sim.player_ids import resolve_pid, AmbiguousPlayer
        with self.assertRaises(AmbiguousPlayer):
            resolve_pid("DeVonta Smith", DB, rostered_pids={"7525", "13977"})

    def test_no_rostered_set_at_all_is_refused_rather_than_defaulted(self):
        """A scratchpad script usually has no roster context. It must get an error, not
        whichever pid happened to iterate first -- the setdefault bug, exactly."""
        from fantasy_sim.player_ids import resolve_pid, AmbiguousPlayer
        with self.assertRaises(AmbiguousPlayer):
            resolve_pid("DeVonta Smith", DB)


class TestExplicitDisambiguation(unittest.TestCase):
    def test_position_disambiguates(self):
        from fantasy_sim.player_ids import resolve_pid
        self.assertEqual(resolve_pid("DeVonta Smith", DB, pos="WR"), "7525")
        self.assertEqual(resolve_pid("DeVonta Smith", DB, pos="CB"), "13977")

    def test_team_disambiguates(self):
        from fantasy_sim.player_ids import resolve_pid
        self.assertEqual(resolve_pid("DeVonta Smith", DB, team="PHI"), "7525")

    def test_a_filter_that_matches_nothing_is_refused(self):
        from fantasy_sim.player_ids import resolve_pid, AmbiguousPlayer
        with self.assertRaises((KeyError, AmbiguousPlayer)):
            resolve_pid("DeVonta Smith", DB, pos="QB")


class TestItAgreesWithSync(unittest.TestCase):
    """ANTI-DRIFT. sync.resolve_player_keys is deliberately NOT refactored to call this --
    it is pinned byte-exactly by golden_sync and a shared-helper refactor would risk the
    baselines for no behavioural gain. Two implementations of one rule must therefore be
    shown to agree, or they will drift."""

    def test_both_pick_the_same_pid_for_a_one_rostered_collision(self):
        from fantasy_sim.player_ids import resolve_pid
        from fantasy_sim.sync import resolve_player_keys
        logging.disable(logging.WARNING)
        try:
            keys = resolve_player_keys(list(DB), DB, rostered_pids=ROSTERED)
            mine = resolve_pid("DeVonta Smith", DB, rostered_pids=ROSTERED)
        finally:
            logging.disable(logging.NOTSET)
        plain = [p for p, k in keys.items() if k == "DeVonta Smith"]
        self.assertEqual(plain, [mine],
                         "resolve_pid and sync.resolve_player_keys disagree about which "
                         "colliding pid keeps the plain name")

    def test_both_refuse_when_two_colliding_players_are_rostered(self):
        from fantasy_sim.player_ids import resolve_pid, AmbiguousPlayer
        from fantasy_sim.sync import resolve_player_keys
        both = {"7525", "13977"}
        with self.assertRaises(ValueError):
            resolve_player_keys(list(DB), DB, rostered_pids=both)
        with self.assertRaises(AmbiguousPlayer):
            resolve_pid("DeVonta Smith", DB, rostered_pids=both)


class TestResolvePlayerWasNeverTheBug(unittest.TestCase):
    """GUARD, not a characterisation. B17 suspected decisions.resolve_player of guessing on
    ambiguity. It does not, because its pool is already keyed by resolve_player_keys. This
    pins that reasoning so nobody 'fixes' the forgiving resolver later and breaks
    `gibbs` finding Jahmyr Gibbs at 9:40 on a Sunday."""

    def test_an_exact_query_returns_the_rostered_player(self):
        from fantasy_sim.decisions import resolve_player
        self.assertEqual(resolve_player("DeVonta Smith", ["DeVonta Smith", "Jahmyr Gibbs"]),
                         "DeVonta Smith")

    def test_a_plain_query_against_two_pid_keyed_entries_is_refused(self):
        from fantasy_sim.decisions import resolve_player
        with self.assertRaises(SystemExit):
            resolve_player("DeVonta Smith",
                           ["DeVonta Smith (13977)", "DeVonta Smith (7525)"])


if __name__ == "__main__":
    unittest.main()

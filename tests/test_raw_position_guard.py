"""T4: raw NFL positions still reach slot-position matching, and nothing stops it.

Phase 3 finding 3 fixed this inside the engine (`config.normalize_position`: DE/DT/NT -> DL,
CB/S/FS/SS -> DB, OLB/ILB/MLB -> LB, FB -> RB). Two ad-hoc queries on 2026-09-23 wrote
`pos == 'DL'` anyway and silently excluded every DE and DT -- missing two DL-eligible free
agents, one of them the best at the position. The sweep T4 asked for found the tools clean
on that exact pattern, but it found this instead:

**`scripts.season_retrospective._positions` and the identical block in
`scripts.run_points_backtest` fall back to the RAW `position` when a cached player carries
no `fantasy_positions`.** 325 of 12,228 cached entries are in that state. The raw string
then goes to `FantasySimulationEngine._solve_optimal_assignment`, which matches
`slot_pos in pos_opts` literally -- so a defensive end is not eligible at DL, and his points
are dropped from the "realized optimal lineup" that is the points-backtest's own target and
season_retrospective's lineup-efficiency denominator. Measured:

    _positions({"1": {"position": "DE"}})                 -> {'1': ['DE']}
    real_optimal_points(QB+DL slots, QB 20.0, DE 14.0)    -> 20.0, the DL slot left EMPTY

**It is LATENT, not live, and commit 1's message overstated that.** Measured against the
real cache afterwards: all 325 entries missing `fantasy_positions` are either unclassified
(240 with `position: null`) or offensive linemen (85 G/C/T), so no player currently reaches
the fallback with a mappable defensive position. 0 of the 228 player ids in the 2025 bundle
change eligibility at all, and 2025 was non-IDP besides. The measured impact on both
seasons is **zero**; what closes here is the path, not an active error. Said plainly
because a finding that inflates its own severity is worth less than one that does not.

The path is worth closing anyway: it exists precisely for players Sleeper has not yet
classified, which is a state a newly-signed player passes through, and the failure is
silent and flattering -- understating the realized optimal makes lineup efficiency look
BETTER than it was, so nobody would catch it from the output.

**The obvious fix is wrong and a test pins that.** Mapping every raw position through
`normalize_position` would give an offensive lineman `'FLEX'` -- that function's return for
anything it does not recognise is its UNKNOWN sentinel, not an eligibility claim -- and
`_solve_optimal_assignment` would then happily start a left tackle at FLEX. It would also
destroy team defenses: `normalize_position('DEF')` is `'FLEX'` too.

**Sleeper's own `fantasy_positions` is already slot-shaped** and must keep winning when it
is present: DE -> ['DL'], CB -> ['DB'], FB -> ['RB'], OLB -> ['LB'], and it carries real
dual eligibility (114 cached linebackers list ['DL','LB']) that the fallback cannot know.

Written before the change. Three of the six below are red against current behaviour; the
other three are green BY DESIGN -- they are regression guards on the fix, not
characterisations, and are marked as such so no one reads them as evidence of a defect.
"""
import ast
import os
import unittest


def _pos_of(db):
    from scripts.season_retrospective import _positions
    return _positions(db)


class TestTheRawFallbackLosesEligibility(unittest.TestCase):
    """Red. The defect itself."""

    def test_a_defensive_end_with_no_fantasy_positions_is_eligible_at_DL(self):
        self.assertEqual(_pos_of({"1": {"position": "DE"}}), {"1": ["DL"]})

    def test_a_cornerback_with_no_fantasy_positions_is_eligible_at_DB(self):
        self.assertEqual(_pos_of({"1": {"position": "CB"}}), {"1": ["DB"]})

    def test_the_optimal_lineup_actually_counts_him(self):
        """End to end, on the number that carries the defect: the points-backtest's own
        optimal target. A 14.0 defensive end must fill the DL slot, not vanish."""
        from scripts.run_points_backtest import real_optimal_points
        bundle = {"roster_positions": ["QB", "DL", "BN"],
                  "settings": {"playoff_week_start": 15},
                  "roster_map": {"1": "A"},
                  "matchups": {"3": [{"roster_id": 1,
                                      "players_points": {"q1": 20.0, "d1": 14.0}}]}}
        positions = _pos_of({"q1": {"position": "QB"}, "d1": {"position": "DE"}})
        out = real_optimal_points(bundle, positions)
        self.assertAlmostEqual(out["A"][3], 34.0,
                               msg="QB 20 + DE 14 at the DL slot; 20.0 means the DL slot "
                                   "was left empty because 'DE' != 'DL'")


class TestTheObviousFixWouldBeWorse(unittest.TestCase):
    """GREEN BY DESIGN -- regression guards on the fix, not characterisations of a defect.
    Each pins a way that a blanket `normalize_position` over the raw position would break
    something that works today."""

    def test_an_offensive_lineman_gets_no_eligibility_not_FLEX(self):
        got = _pos_of({"1": {"position": "OL"}}).get("1", [])
        self.assertNotIn("FLEX", got,
                         "normalize_position returns 'FLEX' for anything it does not "
                         "recognise; that is UNKNOWN, not FLEX eligibility, and "
                         "_solve_optimal_assignment would start a tackle at FLEX")

    def test_a_team_defense_keeps_its_DEF_eligibility(self):
        """normalize_position('DEF') is also 'FLEX'. 2025 had a DEF slot."""
        self.assertEqual(_pos_of({"1": {"position": "DEF"}}), {"1": ["DEF"]})

    def test_sleepers_own_dual_eligibility_wins_when_present(self):
        """114 cached linebackers list ['DL','LB']. The raw `position` cannot know that, so
        the field must keep priority over the fallback."""
        self.assertEqual(
            _pos_of({"1": {"position": "DE", "fantasy_positions": ["DL", "LB"]}}),
            {"1": ["DL", "LB"]})


class TestNoToolComparesARawPositionToASlotLiteral(unittest.TestCase):
    """The guard T4 asked for: an AST sweep of every module under `fantasy_sim/` and
    `scripts/` that fails on a comparison between a position-shaped expression and a
    position literal whose meaning depends on normalisation.

    **Only the alias-sensitive literals are flagged.** `QB`, `WR`, `TE` and `K` are their
    own normal form -- `normalize_position` is the identity on them -- so comparing a raw
    string against `'QB'` cannot silently drop anybody and flagging it would be noise the
    next reader learns to whitelist. The flagged set is the four canonical positions that
    HAVE aliases (DL, LB, DB, and RB, which swallows FB) plus every alias itself.

    **A name assigned from `normalize_position(...)` in the same function is clean.** That
    is the correct, extremely common idiom (`pos = normalize_position(...)` then
    `if pos == 'DL'`), and a guard that reddened on it would be deleted within a week. The
    tracking is per-function and order-insensitive, which is deliberately permissive: this
    is a tripwire for the raw-comparison habit, not a dataflow analysis.

    Tests are excluded. A fixture that writes `{"pos": "LB"}` and then asserts on it is
    authoring both sides in normal form, which is exactly what a fixture should do.
    """

    ALIASED = frozenset({
        "DL", "LB", "DB", "RB",                                  # have aliases
        "DE", "DT", "NT", "CB", "S", "FS", "SS", "OLB", "ILB", "MLB", "FB",   # are aliases
    })
    POS_NAMES = frozenset({"pos", "position", "p_pos", "pos1", "pos2", "raw_pos"})
    POS_KEYS = frozenset({"pos", "position", "fantasy_positions"})
    CLEANERS = frozenset({"normalize_position", "fantasy_slot_positions"})

    # Functions whose position value is normalised somewhere the AST cannot see: a
    # parameter every caller normalises, or a dict built by another function. Each entry
    # was checked by hand against every call site and carries the reason. Kept SHORT --
    # a long accepted list is a guard that has been argued out of existence.
    ACCEPTED = {
        # p_pos is a parameter; all five call sites pass normalize_position output
        # (simulation.py:1390 and :1497, decisions.py:78, :855, :1091).
        "fantasy_sim/simulation.py::_script_multiplier": "parameter, normalised by callers",
        # player_data[*]['pos'] is written as normalize_position(...) at line 73 of the
        # same module, so every read of it is already in normal form.
        "fantasy_sim/backtest_player.py::analyze_correlations": "dict built normalised",
        # `pos` here is a page key iterated from the canonical slot list, not player data.
        "fantasy_sim/positional_tiers.py::_build_tier_table_html": "slot key, not a player",
    }

    @classmethod
    def _callee(cls, node):
        if not isinstance(node, ast.Call):
            return None
        f = node.func
        return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)

    @classmethod
    def _is_pos_expr(cls, node):
        if isinstance(node, ast.Name):
            low = node.id.lower()
            return low in cls.POS_NAMES or (low.endswith("_pos") and "slot" not in low)
        if isinstance(node, ast.Attribute):
            return node.attr in cls.POS_KEYS
        if isinstance(node, ast.Subscript):
            s = node.slice
            return isinstance(s, ast.Constant) and s.value in cls.POS_KEYS
        if isinstance(node, ast.Call) and cls._callee(node) == "get" and node.args:
            a = node.args[0]
            return isinstance(a, ast.Constant) and a.value in cls.POS_KEYS
        return False

    @classmethod
    def _literals(cls, node):
        """The position strings a comparator offers, for `== 'DL'` and `in ('DL','LB')`."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return {e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        return set()

    @classmethod
    def _clean_names(cls, scope):
        """Names bound from normalize_position(...) anywhere in this function body."""
        out = set()
        for n in cls._own_nodes(scope):
            if isinstance(n, ast.Assign) and cls._callee(n.value) in cls.CLEANERS:
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        out.add(t.id)
            elif isinstance(n, ast.AnnAssign) and cls._callee(n.value) in cls.CLEANERS:
                if isinstance(n.target, ast.Name):
                    out.add(n.target.id)
        return out

    @staticmethod
    def _own_nodes(scope):
        """Every node in this scope's body, NOT descending into nested functions -- each
        function gets its own `clean` set, and without this the module scope would also
        report every offence found inside its own functions."""
        out, stack = [], list(ast.iter_child_nodes(scope))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            out.append(n)
            stack.extend(ast.iter_child_nodes(n))
        return out

    @classmethod
    def _offences(cls, tree, path):
        hits = []
        scopes = [tree] + [n for n in ast.walk(tree)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for scope in scopes:
            name = getattr(scope, "name", None)
            if name in cls.CLEANERS:
                continue                      # the mapping itself
            if f"{path}::{name}" in cls.ACCEPTED:
                continue
            clean = cls._clean_names(scope)
            for n in cls._own_nodes(scope):
                if not isinstance(n, ast.Compare) or len(n.comparators) != 1:
                    continue
                left = n.left
                if isinstance(left, ast.Name) and left.id in clean:
                    continue
                if cls._callee(left) in cls.CLEANERS:
                    continue
                if not cls._is_pos_expr(left):
                    continue
                bad = cls._literals(n.comparators[0]) & cls.ALIASED
                if bad:
                    hits.append(f"{path}:{n.lineno}: compares a position against "
                                f"{sorted(bad)} without normalize_position")
        return hits

    @staticmethod
    def _sources():
        import fantasy_sim
        root = os.path.dirname(os.path.dirname(os.path.abspath(fantasy_sim.__file__)))
        for sub in ("fantasy_sim", "scripts"):
            for dirpath, dirnames, filenames in os.walk(os.path.join(root, sub)):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                for fn in sorted(filenames):
                    if fn.endswith(".py"):
                        full = os.path.join(dirpath, fn)
                        yield os.path.relpath(full, root).replace(os.sep, "/"), full

    def test_the_library_and_the_tools_are_clean(self):
        hits = []
        for rel, full in self._sources():
            # utf-8-sig: scripts/probes carries a BOM, and a BOM is not a syntax error
            # in a file Python actually runs -- only in ast.parse of its raw text.
            with open(full, encoding="utf-8-sig") as f:
                hits += self._offences(ast.parse(f.read(), filename=rel), rel)
        self.assertEqual(hits, [], "raw-position comparison(s):\n  " + "\n  ".join(hits))

    def test_the_guard_actually_catches_the_pattern(self):
        """Proof by planting, because a green sweep on a clean tree proves nothing about
        the sweep. Each snippet is a shape seen in the wild."""
        for src in ("if p['pos'] == 'DL': pass",
                    "if entry.get('position') in ('DE', 'DT'): pass",
                    "def f(e):\n    pos = e.get('pos')\n    return pos == 'DB'",
                    "xs = [n for n in names if data[n]['pos'] == 'RB']"):
            with self.subTest(src=src):
                self.assertTrue(self._offences(ast.parse(src), "<planted>"),
                                "the guard missed a raw-position comparison")

    def test_the_guard_does_not_redden_the_normalised_idiom(self):
        """The one false positive that would get this test deleted."""
        clean = ("def f(e):\n"
                 "    pos = normalize_position(e.get('pos', 'FLEX'))\n"
                 "    return pos == 'DL' or pos in ('LB', 'DB')")
        self.assertEqual(self._offences(ast.parse(clean), "<clean>"), [])

    def test_an_alias_free_literal_is_not_flagged(self):
        """QB/WR/TE/K are their own normal form. Flagging them is noise, and noise is how
        a guard teaches itself to be ignored."""
        self.assertEqual(self._offences(ast.parse("if p['pos'] == 'QB': pass"), "<qb>"), [])


if __name__ == "__main__":
    unittest.main()

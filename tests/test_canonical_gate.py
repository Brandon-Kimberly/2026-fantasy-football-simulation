"""F36 tier 2: the canonical gate and its remediation catalog (scripts.canonical_gate).

The gate is the DEGRADED-judgment mitigation made mechanical: explicit allowlists decide
what may quote a canonical prediction unattended, and anything unrecognized refuses --
conservatively -- rather than guessing. The remediation blocks are the owner's
requirement that every failure says exactly what to do, verbatim.
"""
import unittest

from scripts.canonical_gate import (
    ABORT, CANONICAL_OK, REPORT_ONLY, canonical_gate, classify_degraded_entry,
    remediation_markdown,
)

# The six permanent residents of a HEALTHY sync (2026-09-03 manifest, verbatim shapes).
BENIGN = [
    "WARNING | NAME COLLISION: 'Justin Jefferson' is pid 13524 (LB, CLE), pid 6794 (WR, MIN). pid 6794 is rostered and keeps the plain name; the rest are stored as 'Name (pid)'.",
    "WARNING | BASELINES: rostered player 'Josh Jacobs' (RB, GB) has a zero/empty Sleeper projection (injury_status=NA, on_ir=False); CARRIED the previous sync's baseline mean 12.98 as his healthy-week expectation. He enters the engine through F4's absence handling if his status warrants it, not at full strength.",
    "WARNING | BASELINES: 32 entries have positions with no calibrated constants and use the anonymous defaults (k=1.5, rate=0.18): {'DEF': 32}",
    "WARNING | DEPTH WATCHDOG: GB backup RBs -- depth chart says 'Chris Brooks' (depth 2, mean 6.0) but baseline means say 'Josh Jacobs' (depth 4, mean 13.0). Vacated-volume weighting follows the MEANS (F24: measured correct on 2025 events); judge this case by hand if that backfield's lead goes down.",
]
TYSON = ("WARNING | BASELINES: rostered player 'Jordyn Tyson' (WR, NO) has a zero/empty "
         "Sleeper projection and is NOT in baselines. The engine will abort on him unless "
         "SIM_CONFIG['KNOWN_MISSING_ASSETS'] carries an entry (team must match: NO).")
ESPN_DOWN = "WARNING | ESPN BLEND: fetch failed (ConnectionError); all players fall back to Sleeper-only this sync."


class TestClassification(unittest.TestCase):
    def test_the_permanent_residents_are_benign(self):
        for e in BENIGN:
            self.assertEqual(classify_degraded_entry(e), "benign", e[:60])

    def test_the_blocking_classes_carry_their_keys(self):
        self.assertEqual(classify_degraded_entry(TYSON), "blocking:missing_baseline")
        self.assertEqual(classify_degraded_entry(ESPN_DOWN), "blocking:espn")

    def test_a_whitelist_covered_missing_player_is_benign(self):
        covered = ("WARNING | BASELINES: rostered player 'Jordyn Tyson' (WR, NO) has a "
                   "zero/empty Sleeper projection; covered by KNOWN_MISSING_ASSETS -- the "
                   "engine imputes the whitelisted baseline.")
        self.assertEqual(classify_degraded_entry(covered), "benign")

    def test_anything_unrecognized_blocks_conservatively(self):
        v = classify_degraded_entry("WARNING | SOME NEW FAILURE CLASS: never seen before")
        self.assertEqual(v, "blocking:unrecognized")


class TestGateVerdicts(unittest.TestCase):
    def test_stale_aborts_regardless_of_everything_else(self):
        g = canonical_gate("STALE", [], "odds_api")
        self.assertEqual(g["verdict"], ABORT)

    def test_a_healthy_degraded_list_is_canonical_ok(self):
        g = canonical_gate("DEGRADED", BENIGN, "odds_api")
        self.assertEqual(g["verdict"], CANONICAL_OK)
        self.assertEqual(g["blocking"], [])

    def test_the_week1_verified_table_counts_as_real_vegas(self):
        g = canonical_gate("DEGRADED", BENIGN, "week1_verified_table")
        self.assertEqual(g["verdict"], CANONICAL_OK)

    def test_a_vegas_fallback_forces_report_only(self):
        for src in ("fallback_no_api_key", "fallback_api_error", "fallback_empty_payload"):
            g = canonical_gate("OK", [], src)
            self.assertEqual(g["verdict"], REPORT_ONLY, src)
            self.assertIn("odds", [b["key"] for b in g["blocking"]])

    def test_the_tyson_class_forces_report_only_with_its_key(self):
        g = canonical_gate("DEGRADED", BENIGN + [TYSON], "odds_api")
        self.assertEqual(g["verdict"], REPORT_ONLY)
        self.assertEqual([b["key"] for b in g["blocking"]], ["missing_baseline"])


class TestRemediation(unittest.TestCase):
    """Every blocking key must map to a block that says: what happened, the verbatim
    command, whether to commit/push, and how to verify. Written for a reader who has
    forgotten everything about this project (owner's requirement)."""

    WINDOW = {"name": "run2_sunday", "deadline": "2026-09-13T17:00:00Z", "hours_left": 4.5}

    def _md(self, status, degraded, vegas):
        return remediation_markdown(canonical_gate(status, degraded, vegas), self.WINDOW)

    def test_every_blocking_key_has_a_remediation_block(self):
        from scripts.canonical_gate import REMEDIATIONS
        for b in (canonical_gate("OK", [TYSON, ESPN_DOWN,
                                        "WARNING | ??? unknown"], "fallback_api_error")["blocking"]):
            self.assertIn(b["key"], REMEDIATIONS, b["key"])

    def test_the_odds_block_carries_the_exact_commands_and_the_deadline(self):
        md = self._md("OK", [], "fallback_api_error")
        for needle in ("setx ODDS_API_KEY", "NEW terminal", "the-odds-api.com",
                       "gh secret set ODDS_API_KEY",
                       "py -3.10 -m scripts.weekly_report --canonical",
                       "py -3.10 -m scripts.check_freshness",
                       "2026-09-13T17:00:00Z", "run2_sunday"):
            self.assertIn(needle, md, needle)

    def test_the_missing_baseline_block_matches_the_real_config_shape_field_for_field(self):
        md = self._md("OK", [TYSON], "odds_api")
        for needle in ("KNOWN_MISSING_ASSETS", "SIM_CONFIG", "fantasy_sim/config.py",
                       '"mean"', '"std_aleatoric"', '"std_epistemic"', '"pos"',
                       '"team"', '"bye"', "judgment call", "git push"):
            self.assertIn(needle, md, needle)

    def test_abort_produces_the_stale_block_with_the_wait_or_force_split(self):
        md = self._md("STALE", [], "odds_api")
        self.assertIn("scripts.run_sync", md)
        self.assertIn("week", md.lower())


class TestProjectionPoolFloor(unittest.TestCase):
    """The unrostered-pool gap (owner, 2026-09-04): a partial projection fetch that
    silently drops FREE-AGENT players thins the pool, shifts replacement levels, and
    moves every VORP number downstream -- with no warning firing, because every existing
    detector watches rostered players. The floor is one-sided (thinning is the failure
    mode; the pool legitimately GREW and shrank ~8% through roster cutdowns) and derived
    from observed history, not picked: see PROJECTION_POOL_FLOOR's comment."""

    def test_a_thinned_pool_blocks_with_its_key(self):
        g = canonical_gate("OK", [], "odds_api", baselines_count=400)
        self.assertEqual(g["verdict"], REPORT_ONLY)
        self.assertIn("thin_projections", [b["key"] for b in g["blocking"]])

    def test_the_observed_history_and_normal_churn_never_trip_it(self):
        for n in (888, 964, 800):   # both recorded populations and a -10% churn case
            g = canonical_gate("OK", [], "odds_api", baselines_count=n)
            self.assertEqual(g["verdict"], CANONICAL_OK, n)

    def test_unknown_count_skips_the_check_rather_than_guessing(self):
        g = canonical_gate("OK", [], "odds_api", baselines_count=None)
        self.assertEqual(g["verdict"], CANONICAL_OK)

    def test_the_thin_pool_block_names_the_consequence_and_the_check(self):
        from scripts.canonical_gate import REMEDIATIONS
        md = REMEDIATIONS["thin_projections"]
        for needle in ("replacement", "VORP", "scripts.run_sync", "player_baselines.json"):
            self.assertIn(needle, md, needle)


class TestEspnBlockWording(unittest.TestCase):
    def test_the_espn_block_says_credentials_are_not_normally_needed(self):
        """The league is PUBLIC and the blend works with no cookies (verified live:
        115/152 espn means without credentials). The remediation must lead with that,
        not send the reader hunting for cookies they never had (owner, 2026-09-04)."""
        from scripts.canonical_gate import REMEDIATIONS
        md = REMEDIATIONS["espn"]
        self.assertIn("public", md.lower())
        self.assertIn("no credentials", md.lower())
        self.assertLess(md.lower().index("public"), md.lower().index("espn_s2"),
                        "the no-credentials-needed statement must come BEFORE the "
                        "only-if-private cookie instructions")



if __name__ == "__main__":
    unittest.main()

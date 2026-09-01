"""F15 at-draft analysis (AUDIT_PLAN.md): for each pick, the drafted player's baseline
mean / VORP / tier versus the best available at that pick -- available meaning not yet
drafted, at a position the roster could still fill under the position limits. Written before
fantasy_sim.draft_review existed. The crafted 2-round draft is engineered so each verdict
label appears once: value (pick 1, near-tie), steal (pick 2, clearly best on the board),
reach (pick 3, clearly better left available), unresolved (pick 4, not in the pool)."""
import unittest

from fantasy_sim.draft_review import derive_position_caps, render_draft_html, review_draft


def _pick(no, rnd, team, pid, name, pos):
    return {"pick_no": no, "round": rnd, "draft_slot": no, "roster_id": 1 if team == "Alpha" else 2,
            "team": team, "picked_by": "u1" if team == "Alpha" else "u2",
            "player_id": pid, "is_keeper": False, "name": name, "pos": pos, "nfl_team": "SEA"}


BASELINES = {
    # QB replacement 10: QB_top vorp 16, QB_mid vorp 8. RB replacement 5: RB_top 15,
    # RB_mid 7, RB_low 3. std_epistemic 1.0 everywhere -> combined SE sqrt(2) ~ 1.41,
    # the steal/reach threshold at TIER_Z = 1.0.
    "QB_top": {"mean": 26.0, "std_epistemic": 1.0, "pos": "QB", "team": "SEA", "player_id": "1"},
    "QB_mid": {"mean": 18.0, "std_epistemic": 1.0, "pos": "QB", "team": "DET", "player_id": "2"},
    "RB_top": {"mean": 20.0, "std_epistemic": 1.0, "pos": "RB", "team": "KC", "player_id": "3"},
    "RB_mid": {"mean": 12.0, "std_epistemic": 1.0, "pos": "RB", "team": "SF", "player_id": "4"},
    "RB_low": {"mean": 8.0, "std_epistemic": 1.0, "pos": "RB", "team": "NYJ", "player_id": "5"},
}
REPLACEMENTS = {"QB": 10.0, "RB": 5.0, "FLEX": 5.0}

DRAFT = {
    "draft_id": "D_TEST", "season": "2026", "league_id": "L1", "status": "complete",
    "start_time": 1_787_356_800_000,   # 2026-08-22T00:00Z, before any "now"
    "settings": {"rounds": 2, "teams": 2},
    "picks": [
        _pick(1, 1, "Alpha", "1", "QB_top", "QB"),      # vorp 16 vs best alt RB_top 15: value
        _pick(2, 1, "Beta", "3", "RB_top", "RB"),       # vorp 15 vs best alt QB_mid 8: steal
        _pick(3, 2, "Beta", "5", "RB_low", "RB"),       # vorp 3 vs best alt QB_mid 8: reach
        _pick(4, 2, "Alpha", "999", "Mystery Man", "RB"),  # not in the pool: unresolved
    ],
}


class TestDerivePositionCaps(unittest.TestCase):
    def test_caps_are_the_observed_per_team_maxima(self):
        self.assertEqual(derive_position_caps(DRAFT), {"QB": 1, "RB": 2})


class TestReviewDraft(unittest.TestCase):
    def setUp(self):
        self.r = review_draft(DRAFT, BASELINES, REPLACEMENTS)

    def test_each_pick_carries_drafted_value_best_alternative_and_a_label(self):
        p1, p2, p3, p4 = self.r["picks"]
        self.assertEqual(p1["name"], "QB_top")
        self.assertAlmostEqual(p1["vorp"], 16.0)
        self.assertEqual(p1["tier"], 1)
        self.assertEqual(p1["best_alt"]["name"], "RB_top")
        self.assertAlmostEqual(p1["vorp_gap"], 1.0)
        self.assertEqual(p1["label"], "value", "a 1.0 gap is inside sqrt(2) combined SE")
        self.assertEqual(p2["label"], "steal")
        self.assertAlmostEqual(p2["vorp_gap"], 7.0, msg="RB_top 15 over the best left, QB_mid 8")
        self.assertEqual(p3["label"], "reach")
        self.assertAlmostEqual(p3["vorp_gap"], -5.0)
        self.assertEqual(p3["best_alt"]["name"], "QB_mid")
        self.assertEqual(p4["label"], "unresolved")
        self.assertIsNone(p4["vorp"])

    def test_position_caps_gate_the_available_board(self):
        # Pick 4 is Alpha's: Alpha already holds QB_top and the observed QB cap is 1, so
        # QB_mid (vorp 8, the best player left) must NOT be on Alpha's board -- RB_mid is.
        p4 = self.r["picks"][3]
        self.assertEqual(p4["best_alt"]["name"], "RB_mid")

    def test_taken_players_leave_the_board(self):
        p3 = self.r["picks"][2]
        self.assertNotIn(p3["best_alt"]["name"], ("QB_top", "RB_top"),
                         "picks 1 and 2 are gone by pick 3")

    def test_pick1_sanity_check_reproduces_the_top_of_the_board(self):
        s = self.r["sanity"]
        self.assertEqual(s["pick1"], "QB_top")
        self.assertEqual(s["pick1_board_rank_by_vorp"], 1)
        self.assertEqual(s["board_size"], 5)

    def test_manager_and_round_rollups_count_the_labels(self):
        managers = {m["team"]: m for m in self.r["managers"]}
        self.assertEqual(managers["Beta"]["steals"], 1)
        self.assertEqual(managers["Beta"]["reaches"], 1)
        self.assertEqual(managers["Alpha"]["values"], 1)
        self.assertEqual(managers["Alpha"]["unresolved"], 1)
        self.assertAlmostEqual(managers["Beta"]["mean_gap"], 1.0, msg="(+7 - 5) / 2")
        rounds = {r["round"]: r for r in self.r["rounds"]}
        self.assertEqual(rounds[1]["steals"], 1)
        self.assertEqual(rounds[2]["reaches"], 1)

    def test_the_proxy_caveat_is_in_the_result_not_just_a_docstring(self):
        note = self.r["proxy_note"]
        self.assertIn("proxy", note.lower())
        self.assertIn("2026-08-22", note, "the draft's real date is named")
        self.assertIn("days", note.lower(), "the lag to today's baselines is stated")
        self.assertEqual(self.r["unresolved"], ["Mystery Man"])


class TestMedianRelativeGaps(unittest.TestCase):
    """The chart's reference problem: vorp_gap is bounded above by 0 by construction (nobody
    can beat the best available), so absolute bars all read negative. The fix is a league
    reference in the RESULT: league_median_gap (median per-pick gap across the draft) and a
    per-manager rel_gap = mean_gap - league_median_gap, so better-than-median is genuinely
    positive. Written before the fields existed."""

    def test_league_median_and_manager_relative_gaps(self):
        r = review_draft(DRAFT, BASELINES, REPLACEMENTS)
        # scored gaps in the crafted draft: +1.0, +7.0, -5.0 -> median +1.0
        self.assertAlmostEqual(r["league_median_gap"], 1.0)
        managers = {m["team"]: m for m in r["managers"]}
        self.assertAlmostEqual(managers["Alpha"]["rel_gap"], 0.0)   # mean +1.0 - median +1.0
        self.assertAlmostEqual(managers["Beta"]["rel_gap"], 0.0)    # mean (+7-5)/2 - median


class TestRenderDraftHtml(unittest.TestCase):
    """The HTML report, weekly-report pattern: sortable tables via the existing renderer,
    proxy banner FIRST, roll-ups, steals/reaches call-outs, the full pick table. Written
    before render_draft_html existed."""

    def setUp(self):
        self.r = review_draft(DRAFT, BASELINES, REPLACEMENTS)
        self.html = render_draft_html(self.r)

    def test_proxy_banner_is_prominent_before_any_table(self):
        self.assertIn('class="banner"', self.html)
        self.assertLess(self.html.index("AT-DRAFT VALUE IS A PROXY"),
                        self.html.index("<table"),
                        "the caveat comes before the first table, not below it")

    def test_tables_are_sortable_via_the_existing_renderer(self):
        self.assertIn('data-key=', self.html)
        self.assertIn('data-sort=', self.html)
        self.assertIn("<script>", self.html, "the shared sorting JS is on the page")

    def test_every_pick_is_on_the_page_with_its_verdict_and_best_alternative(self):
        for name in ("QB_top", "RB_top", "RB_low", "Mystery Man"):
            self.assertIn(name, self.html)
        self.assertIn("steal", self.html)
        self.assertIn("reach", self.html)
        self.assertIn("unresolved", self.html)

    def test_rollups_callouts_and_the_median_reference_are_present(self):
        self.assertIn("Per manager", self.html)
        self.assertIn("Per round", self.html)
        self.assertIn("Biggest steals", self.html)
        self.assertIn("Biggest reaches", self.html)
        self.assertIn("median", self.html.lower(),
                      "the league-median reference is explained on the page")
        self.assertIn("0.0", self.html, "the unachievable absolute ceiling is stated")


if __name__ == "__main__":
    unittest.main()

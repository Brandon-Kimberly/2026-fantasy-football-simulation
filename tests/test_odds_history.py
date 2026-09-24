"""R1: the report never showed how the championship odds got where they are.

Champ% for one roster went **24.9 -> 35.4 -> 38.2 in a single day** (a QB claim, a kicker
claim, a trade). `predictions_2026.jsonl` recorded every one of those states; nothing
anywhere showed the trajectory, so the only way to know a move was worth ten points of
championship probability was to remember the previous number by hand.

**THE TRAP THE ITEM NAMES IS THE WHOLE DESIGN CONSTRAINT.** The log holds scheduled runs
AND ad-hoc ones -- 9 of 23 rows are canonical today. An ad-hoc evaluation is a what-if,
often run three times in an hour while deciding something, and it is NOT a prediction
(F56/B5's provenance split). Splicing those into a time series would invent movement the
model's published view never made. A row with no `canonical` key at all is likewise not
canonical: the earliest rows predate the flag and one is explicitly `backfilled`.

**ANNOTATIONS ARE EVIDENCE, NOT EXPLANATION**, and a test pins that the output says so. A
list of transactions next to a +10.5 delta invites the inference that those transactions
caused it, when odds also move because rival rosters changed, because Vegas lines moved,
and because the blend saw another week of real scores.

New capability, so no red characterisation exists and none is claimed; these pass on the
first run and were verified by mutation (recorded in the commit message).
"""
import unittest

from fantasy_sim.odds_history import (canonical_rows, describe_moves, moves_in_window,
                                      odds_history, render_lines)

ME = "Quantum Ferrets"


def _pred(at, week, champ, playoff=90.0, wins=15.0, canonical=True):
    row = {"logged_at": at, "week": week, "record_type": "week_predictions",
           "season_outcomes": [
               {"Team": ME, "Champ_Pct": champ, "Playoff_Pct": playoff,
                "Expected_Wins": wins, "Playoff_SE": 0.2},
               {"Team": "Neon Walruses", "Champ_Pct": 10.0, "Playoff_Pct": 40.0,
                "Expected_Wins": 11.0, "Playoff_SE": 0.4}]}
    if canonical is not None:
        row["canonical"] = canonical
    return row


def _tx(created, type_="waiver", teams=(ME,), adds=("Somebody",), bid=None):
    return {"created": created, "type": type_, "teams": list(teams),
            "adds": [{"name": a} for a in adds], "drops": [], "faab_bid": bid}


class TestOnlyCanonicalRowsCount(unittest.TestCase):
    def test_ad_hoc_rows_are_excluded(self):
        rows = [_pred("2026-09-22T10:00:00Z", 3, 24.9),
                _pred("2026-09-22T11:00:00Z", 3, 99.0, canonical=False),
                _pred("2026-09-23T10:00:00Z", 3, 35.4)]
        self.assertEqual([r["champ_pct"] for r in odds_history(rows, ME)["rows"]],
                         [24.9, 35.4], "a what-if is not a published prediction")

    def test_a_row_with_no_canonical_flag_is_not_canonical(self):
        """The earliest rows predate the flag and one is backfilled. Absent is not True."""
        rows = [_pred("2026-09-01T10:00:00Z", 1, 50.0, canonical=None),
                _pred("2026-09-22T10:00:00Z", 3, 24.9)]
        self.assertEqual(len(canonical_rows(rows)), 1)

    def test_rows_are_ordered_by_time_not_file_order(self):
        rows = [_pred("2026-09-23T10:00:00Z", 3, 35.4),
                _pred("2026-09-22T10:00:00Z", 3, 24.9)]
        self.assertEqual([r["champ_pct"] for r in odds_history(rows, ME)["rows"]],
                         [24.9, 35.4])

    def test_it_reports_how_many_of_the_log_it_used(self):
        rows = [_pred("2026-09-22T10:00:00Z", 3, 24.9),
                _pred("2026-09-22T11:00:00Z", 3, 99.0, canonical=False)]
        h = odds_history(rows, ME)
        self.assertEqual((h["n_canonical"], h["n_total"]), (1, 2))


class TestTheDeltas(unittest.TestCase):
    ROWS = [_pred("2026-09-23T08:00:00Z", 3, 24.9, 90.0, 15.0),
            _pred("2026-09-23T14:00:00Z", 3, 35.4, 93.0, 16.0),
            _pred("2026-09-23T20:00:00Z", 3, 38.2, 95.6, 16.5)]

    def test_the_first_row_has_no_delta_rather_than_a_zero(self):
        """Zero would read as 'nothing changed', which is a claim about a comparison that
        does not exist."""
        first = odds_history(self.ROWS, ME)["rows"][0]
        self.assertEqual((first["d_champ"], first["d_playoff"], first["d_wins"]),
                         (None, None, None))

    def test_each_delta_is_against_the_PREVIOUS_CANONICAL_row(self):
        rows = list(self.ROWS)
        rows.insert(1, _pred("2026-09-23T09:00:00Z", 3, 80.0, canonical=False))
        got = odds_history(rows, ME)["rows"]
        self.assertAlmostEqual(got[1]["d_champ"], 10.5, places=2,
                               msg="35.4 - 24.9, ignoring the ad-hoc 80.0 between them")

    def test_a_team_absent_from_a_row_is_skipped_not_zeroed(self):
        rows = [_pred("2026-09-22T10:00:00Z", 3, 24.9),
                {"logged_at": "2026-09-23T10:00:00Z", "week": 3, "canonical": True,
                 "season_outcomes": [{"Team": "Neon Walruses", "Champ_Pct": 5.0}]}]
        self.assertEqual(len(odds_history(rows, ME)["rows"]), 1)


class TestTheAnnotations(unittest.TestCase):
    def test_only_moves_inside_the_window_are_attached(self):
        rows = [_pred("2026-09-23T08:00:00Z", 3, 24.9),
                _pred("2026-09-23T20:00:00Z", 3, 38.2)]
        dec = [_tx("2026-09-23T07:00:00Z", adds=("Too Early",)),
               _tx("2026-09-23T12:00:00Z", adds=("In Window",)),
               _tx("2026-09-24T01:00:00Z", adds=("Too Late",))]
        moves = odds_history(rows, ME, dec)["rows"][1]["moves"]
        self.assertEqual([m["adds"][0] for m in moves], ["In Window"])

    def test_the_window_is_half_open_so_a_move_is_never_counted_twice(self):
        """A transaction landing exactly on a run's timestamp belongs to the window that
        ENDS there. Counting it in both would double-attribute it."""
        exact = _tx("2026-09-23T14:00:00Z", adds=("Exact",))
        rows = [_pred("2026-09-23T08:00:00Z", 3, 24.9),
                _pred("2026-09-23T14:00:00Z", 3, 35.4),
                _pred("2026-09-23T20:00:00Z", 3, 38.2)]
        got = odds_history(rows, ME, [exact])["rows"]
        self.assertEqual([m["adds"][0] for m in got[1]["moves"]], ["Exact"])
        self.assertEqual(got[2]["moves"], [])

    def test_my_moves_are_marked_and_sorted_first(self):
        rows = [_pred("2026-09-23T08:00:00Z", 3, 24.9),
                _pred("2026-09-23T20:00:00Z", 3, 38.2)]
        dec = [_tx("2026-09-23T10:00:00Z", teams=("Neon Walruses",), adds=("Theirs",)),
               _tx("2026-09-23T11:00:00Z", teams=(ME,), adds=("Mine",))]
        line = describe_moves(odds_history(rows, ME, dec)["rows"][1]["moves"])
        self.assertTrue(line.startswith("me:"), line)
        self.assertIn("Mine", line)
        self.assertIn("Theirs", line)

    def test_a_rivals_move_is_kept_because_it_moves_my_odds_too(self):
        rows = [_pred("2026-09-23T08:00:00Z", 3, 24.9),
                _pred("2026-09-23T20:00:00Z", 3, 38.2)]
        dec = [_tx("2026-09-23T10:00:00Z", type_="trade", teams=("Neon Walruses",),
                   adds=("Theirs",))]
        self.assertEqual(len(odds_history(rows, ME, dec)["rows"][1]["moves"]), 1)

    def test_a_drop_only_move_names_who_left(self):
        """Seen on the real log: a drop-only transaction rendered as a bare "-". A dash is
        a shrug; "dropped X" is information."""
        m = {"type": "free_agent", "mine": True, "teams": [ME], "adds": [],
             "drops": ["Gone Guy"], "faab_bid": None, "created": "2026-09-23T10:00:00Z"}
        self.assertIn("dropped Gone Guy", describe_moves([m]))

    def test_no_decision_log_means_no_moves_not_a_crash(self):
        rows = [_pred("2026-09-23T08:00:00Z", 3, 24.9),
                _pred("2026-09-23T20:00:00Z", 3, 38.2)]
        self.assertEqual(odds_history(rows, ME)["rows"][1]["moves"], [])

    def test_an_unparseable_timestamp_is_skipped_rather_than_raising(self):
        self.assertEqual(moves_in_window([_tx("not a date")], None,
                                         "2026-09-23T20:00:00Z"), [])


class TestItRefusesToImplyCausation(unittest.TestCase):
    def test_the_output_says_the_moves_are_not_the_cause(self):
        h = odds_history([_pred("2026-09-23T08:00:00Z", 3, 24.9)], ME)
        self.assertIn("not what caused", h["causation_note"])
        self.assertIn("rival rosters", h["causation_note"])

    def test_the_output_says_ad_hoc_rows_were_excluded(self):
        h = odds_history([_pred("2026-09-23T08:00:00Z", 3, 24.9)], ME)
        self.assertIn("ad-hoc", h["canonical_note"])


class TestRendering(unittest.TestCase):
    def test_the_trajectory_and_both_caveats_reach_the_text(self):
        rows = [_pred("2026-09-23T08:00:00Z", 3, 24.9),
                _pred("2026-09-23T20:00:00Z", 3, 38.2)]
        text = "\n".join(render_lines(odds_history(rows, ME, [_tx("2026-09-23T10:00:00Z")])))
        for marker in ("HOW THE ODDS HAVE MOVED", "+13.3", "net since", "ad-hoc",
                       "not what caused"):
            self.assertIn(marker, text, marker)

    def test_an_empty_history_says_so_rather_than_printing_a_header_alone(self):
        self.assertIn("no canonical prediction rows",
                      "\n".join(render_lines(odds_history([], ME))))

    def test_the_overlay_is_the_only_way_a_display_name_changes(self):
        text = "\n".join(render_lines(odds_history([_pred("2026-09-23T08:00:00Z", 3, 24.9)], ME),
                                      name_of={ME: "Local Alias"}))
        self.assertIn("Local Alias", text)
        self.assertNotIn(ME, text)


class TestItReachesTheWeeklyReport(unittest.TestCase):
    """R1 asks for a section IN the report, not only a standalone tool."""

    HIST = None

    @classmethod
    def setUpClass(cls):
        rows = [_pred("2026-09-22T18:42:00Z", 3, 28.7, 84.0, 17.85),
                _pred("2026-09-23T20:00:00Z", 3, 38.2, 95.6, 20.02)]
        cls.HIST = odds_history(rows, ME, [_tx("2026-09-23T10:00:00Z", bid=29,
                                               adds=("Patrick Mahomes",))])

    def _report(self, with_odds):
        from tests.test_weekly_report import _fixture_results
        res = _fixture_results()
        if with_odds:
            res["odds_history"] = self.HIST
        return {"status": "OK", "failed_step": None, "error": None, "results": res,
                "started_at": "t0", "finished_at": "t1"}

    def test_the_markdown_section_carries_the_trajectory_and_both_caveats(self):
        from fantasy_sim.weekly_report import render_digest
        md = render_digest(self._report(True), ME, 3)
        for marker in ("How the odds have moved", "+9.5", "Net since", "ad-hoc",
                       "not what caused", "Patrick Mahomes"):
            self.assertIn(marker, md, marker)

    def test_the_html_section_and_its_toc_link_are_present(self):
        from fantasy_sim.weekly_report import render_html
        html = render_html(self._report(True), ME, 3)
        self.assertIn('id="odds"', html)
        self.assertIn(">Odds history<", html)

    def test_a_report_without_the_section_renders_exactly_as_before(self):
        from fantasy_sim.weekly_report import render_digest, render_html
        self.assertNotIn("How the odds have moved", render_digest(self._report(False), ME, 3))
        self.assertNotIn('id="odds"', render_html(self._report(False), ME, 3))

    def test_the_section_is_pure_ASCII(self):
        """The digest gets opened on a Windows console where the default codepage is
        cp1252. A Greek delta in a column header raises UnicodeEncodeError there, which is
        how the first version of this section was caught."""
        from fantasy_sim.weekly_report import render_digest
        md = render_digest(self._report(True), ME, 3)
        i = md.index("## How the odds have moved")
        section = md[i:i + 2000]
        self.assertEqual([c for c in section if ord(c) > 127], [], "non-ASCII in the section")

    def test_the_step_is_in_the_default_chain_after_the_predictions_log(self):
        """Order matters: the predictions log writes THIS run's canonical row, and the
        history renders the series that row belongs to."""
        from fantasy_sim.weekly_report import build_steps
        names = [n for n, _ in build_steps(ME)[0]]
        self.assertIn("odds_history", names)
        self.assertLess(names.index("predictions_log"), names.index("odds_history"))


if __name__ == "__main__":
    unittest.main()

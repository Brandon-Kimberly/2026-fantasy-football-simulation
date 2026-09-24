"""T6: bye-week exposure and the roster-crunch forecast, both of which were done by hand.

Two questions drove real decisions and neither had a tool:

  "Which weeks am I short at a position?"   -> one hole all season, a week-7 DL
  "When the IR'd QB returns I am at 20 active and must cut someone -- who?"

The first is why every RB-for-WR offer got declined: two of the RBs leave three bye holes
and the RB wire is barren. That reasoning lived in a chat window.

NEW CAPABILITY, NOT A DEFECT FIX, so there is no red characterisation to separate and none
is claimed. The backlog's acceptance criterion is what the fixture is built to reproduce:
a week-7 DL hole and exactly ONE droppable bench piece. Every assertion was confirmed
load-bearing by mutating `fantasy_sim/roster_calendar.py` (the commit message lists which).

THE FIXTURE, and why every number in it is deliberate. Thirteen starters, six bench, one on
IR -- 19 active against a limit of 19, so the IR man's return forces a cut. Byes are placed
so that five of the six bench pieces cover exactly one starter's bye each and the sixth
covers none, which is what makes "exactly one droppable piece" a real assertion rather than
an artefact:

    wk 5   RB1 out  -> RB4 covers (top bench piece eligible at the freed FLEX)
    wk 6   WR1 out, AND RB4 also on bye -> WR4 covers
    wk 7   DL1 out  -> NOBODY: the only DL on the roster. THE HOLE.
    wk 8   LB1 out  -> LB2 covers (the only other LB)
    wk 9   K1  out  -> K2 covers
    wk 10  QB1 out  -> QB2 covers
    TE3 covers nothing in any week -> the one droppable piece

RB4's own week-6 bye is load-bearing in the fixture, not decoration: without it RB4 (the
highest-mean bench piece) would cover week 6 as well and WR4 would never appear, and the
test would be asserting something the roster does not actually do.
"""
import logging
import unittest
from unittest.mock import patch

from fantasy_sim.roster_calendar import (bye_map, calendar, covering_bench, crunch,
                                         remaining_weeks, render_lines)
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)

TEAMS = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis"]
ME = TEAMS[0]
WEEK = 3

# (name, pos, mean, bye, injury_status, on_ir)
STARTERS = [("QB1", "QB", 22.0, 10, None, False), ("RB1", "RB", 17.0, 5, None, False),
            ("RB2", "RB", 16.0, 0, None, False), ("WR1", "WR", 18.0, 6, None, False),
            ("WR2", "WR", 17.0, 0, None, False), ("TE1", "TE", 14.0, 0, None, False),
            ("K1", "K", 9.5, 9, None, False), ("DL1", "DL", 10.0, 7, None, False),
            ("LB1", "LB", 10.5, 8, None, False), ("DB1", "DB", 10.2, 0, None, False),
            ("WR3", "WR", 13.0, 0, "Questionable", False),   # F51: NOT a hole
            ("RB3", "RB", 16.0, 0, None, False), ("TE2", "TE", 15.0, 0, None, False)]
BENCH = [("QB2", "QB", 12.0, 0, None, False), ("RB4", "RB", 9.0, 6, None, False),
         ("WR4", "WR", 8.0, 0, None, False), ("TE3", "TE", 7.0, 0, None, False),
         ("LB2", "LB", 6.0, 0, None, False), ("K2", "K", 5.0, 0, None, False)]
IR = [("QB_IR", "QB", 20.0, 0, "IR", True)]
FILLER = [("QB", 15.0), ("RB", 13.0), ("RB", 12.0), ("WR", 12.0), ("WR", 11.0),
          ("TE", 9.0), ("K", 7.0), ("DL", 8.0), ("LB", 8.0), ("DB", 8.0),
          ("RB", 7.0), ("WR", 7.0), ("WR", 6.0)]


def _fs():
    base, rosters, pid = {}, {}, 7000
    entries = []
    for nm, pos, mu, bye, status, on_ir in STARTERS + BENCH + IR:
        pid += 1
        e = {"mean": mu, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
             "team": "DET", "bye": bye, "player_id": str(pid)}
        if status:
            e["injury_status"] = status
        if on_ir:
            e["on_ir"] = True
        base[nm] = e
        entries.append({"name": nm, "pos": pos, "team": "DET"})
    rosters[ME] = entries
    for t in TEAMS[1:]:
        entries = []
        for i, (pos, mu) in enumerate(FILLER):
            pid += 1
            nm = f"{t[:2]}_{pos}_{i}"
            base[nm] = {"mean": mu, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
                        "team": "DET", "bye": 0, "player_id": str(pid)}
            entries.append({"name": nm, "pos": pos, "team": "DET"})
        rosters[t] = entries
    return {
        LEAGUE_STATE_FILE: {"current_week": WEEK},
        LEAGUE_STANDINGS_FILE: {t: {"remaining_faab": 100} for t in TEAMS},
        VEGAS_FILE: {"_meta": {"week": WEEK, "source": "odds_api", "fetched_at": "x"},
                     "DET": {"total": 22.0, "spread": 0.0, "opponent": "CHI"},
                     "CHI": {"total": 22.0, "spread": 0.0, "opponent": "DET"}},
        LIVE_ROSTERS_FILE: rosters,
        BASELINES_FILE: base,
        TEAM_RATINGS_FILE: {"DET": {"off_rating": 22}, "CHI": {"off_rating": 22}},
        DEFENSIVE_RATINGS_FILE: {"DET": {"points_allowed_estimate": 21.5, "games_sampled": 0},
                                 "CHI": {"points_allowed_estimate": 21.5, "games_sampled": 0}},
        DEFENSIVE_TIERS_FILE: {"TOP_DEFENSE": [], "BOTTOM_DEFENSE": []},
        LEAGUE_SCHEDULE_FILE: [[[TEAMS[0], TEAMS[1]], [TEAMS[2], TEAMS[3]]]] * 14,
        NFL_SCHEDULE_FILE: {str(w): {"DET": "CHI", "CHI": "DET"} for w in range(1, 19)},
        WEEKLY_ACTUALS_FILE: {},
    }


def _engine():
    fs = _fs()
    pre = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.ERROR)
    with patch("os.path.exists", side_effect=lambda p: p in fs), \
         patch("fantasy_sim.simulation.load_json", side_effect=lambda p: fs[p]):
        e = FantasySimulationEngine()
    logging.getLogger().setLevel(pre)
    return e


class TestTheFixtureIsWhatItClaims(unittest.TestCase):
    """Without this, a green run below proves nothing about the real shape."""

    def test_nineteen_active_against_a_limit_of_nineteen(self):
        from fantasy_sim.roster_calendar import ACTIVE_ROSTER_LIMIT
        e = _engine()
        active = [n for n in e.rosters[ME] if not e.baselines[n].get("on_ir")]
        self.assertEqual(len(active), 19)
        self.assertEqual(ACTIVE_ROSTER_LIMIT, 19)

    def test_there_is_exactly_one_DL_on_the_roster(self):
        e = _engine()
        self.assertEqual([n for n in e.rosters[ME] if e.baselines[n]["pos"] == "DL"], ["DL1"])


class TestTheCalendar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = _engine()
        cls.cal = calendar(cls.e, ME)

    def test_the_remaining_weeks_start_at_the_current_week(self):
        self.assertEqual(remaining_weeks(self.e), list(range(WEEK, 15)))

    def test_byes_are_mapped_per_week(self):
        m, m_ir = bye_map(self.e, ME, range(3, 15))
        self.assertEqual(m[7], ["DL1"])
        self.assertEqual(sorted(m[6]), ["RB4", "WR1"])
        self.assertEqual(m[4], [])
        self.assertEqual(m_ir, {w: [] for w in range(3, 15)},
                         "nobody on IR has a bye inside the window in this fixture")

    def test_the_week_7_DL_hole_is_found(self):
        """The backlog's acceptance criterion, first half."""
        self.assertEqual(self.cal["holes"], {7: ["DL"]},
                         "one unfillable slot all season: the only DL is on bye in week 7")

    def test_each_bye_week_names_the_bench_piece_that_covers_it(self):
        covers = {r["week"]: [c["name"] for c in r["covers"]] for r in self.cal["rows"]}
        self.assertEqual(covers[5], ["RB4"])
        self.assertEqual(covers[6], ["WR4"])
        self.assertEqual(covers[7], [], "nobody can cover a DL the roster does not have")
        self.assertEqual(covers[8], ["LB2"])
        self.assertEqual(covers[9], ["K2"])
        self.assertEqual(covers[10], ["QB2"])

    def test_a_week_with_no_bye_has_no_cover_and_no_hole(self):
        row = next(r for r in self.cal["rows"] if r["week"] == 4)
        self.assertEqual((row["on_bye"], row["covers"], row["unfilled"]), ([], [], []))

    def test_a_QUESTIONABLE_starter_is_not_a_hole(self):
        """F51, and the backlog names it as the trap. Questionable is in no absence set:
        the Sleeper projection the baseline derives from already reflects expected usage,
        so treating him as out would invent a hole and send the owner to spend FAAB on it.
        WR3 is Questionable in every week of this fixture."""
        for row in self.cal["rows"]:
            self.assertNotIn("WR", row["unfilled"], f"week {row['week']}")
        self.assertNotIn("WR3", [c["name"] for r in self.cal["rows"] for c in r["covers"]])

    def test_the_IR_player_is_never_counted_as_startable(self):
        for row in self.cal["rows"]:
            self.assertNotIn("QB_IR", [c["name"] for c in row["covers"]], f"wk {row['week']}")
        self.assertTrue(all(r["n_startable"] <= 13 for r in self.cal["rows"]))

    def test_an_IR_player_on_bye_is_listed_separately_not_as_a_bye_hit(self):
        """Found on the first live run: the IR'd man appeared in a week's bye list, which
        reads as "three of my players are out that week" when two of them are and the
        third has been out all along. He is still worth naming -- his bye matters the
        moment he is activated -- so he is listed apart rather than dropped."""
        e = _engine()
        e.baselines["QB_IR"]["bye"] = 4
        cal = calendar(e, ME)
        row = next(r for r in cal["rows"] if r["week"] == 4)
        self.assertEqual(row["on_bye"], [], "he is already out; this is not a new absence")
        self.assertEqual(row["on_bye_ir"], ["QB_IR"])


class TestTheCrunch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = _engine()
        cls.cr = crunch(cls.e, ME)

    def test_the_return_pushes_the_roster_over_the_limit(self):
        self.assertEqual(self.cr["active_now"], 19)
        r = next(x for x in self.cr["returns"] if x["name"] == "QB_IR")
        self.assertEqual((r["active_on_return"], r["over_limit"], r["must_drop"]),
                         (20, True, 1))

    def test_exactly_one_bench_piece_is_droppable(self):
        """The backlog's acceptance criterion, second half. Five of the six bench pieces
        cover a bye; the sixth covers none."""
        self.assertEqual([d["name"] for d in self.cr["droppable"]], ["TE3"])

    def test_the_other_five_are_named_as_load_bearing_with_their_weeks(self):
        lb = {d["name"]: d["covers_weeks"] for d in self.cr["load_bearing"]}
        self.assertEqual(lb, {"RB4": [5], "WR4": [6], "LB2": [8], "K2": [9], "QB2": [10]})

    def test_covering_bench_is_the_same_answer_reached_directly(self):
        cov = covering_bench(self.e, ME)
        self.assertEqual(sorted(cov), ["K2", "LB2", "QB2", "RB4", "WR4"])
        self.assertNotIn("TE3", cov)

    def test_a_return_that_lands_exactly_on_the_limit_is_not_over(self):
        """The boundary, pinned because the first version of this file did NOT catch it:
        mutating `> limit` to `>= limit` left every test green, since at 19 active the
        return reaches 20 and both comparisons read True. Found by mutation testing, which
        is the whole reason for doing it on a suite that passed first time."""
        e = _engine()
        e.rosters[ME] = [n for n in e.rosters[ME] if n != "TE3"]   # 18 active + 1 on IR
        cr = crunch(e, ME)
        r = cr["returns"][0]
        self.assertEqual((cr["active_now"], r["active_on_return"]), (18, 19))
        self.assertFalse(r["over_limit"], "19 of 19 is full, not over")
        self.assertEqual(r["must_drop"], 0)

    def test_a_roster_with_nobody_on_IR_reports_no_returns(self):
        e = _engine()
        e.baselines["QB_IR"]["on_ir"] = False
        cr = crunch(e, ME)
        self.assertEqual(cr["returns"], [])
        self.assertEqual(cr["active_now"], 20)

    def test_the_note_says_the_return_week_is_unknown(self):
        """Sleeper publishes a designation, not a date. Inventing a week would be the kind
        of confident wrong number this repo keeps finding."""
        self.assertIn("not a return date", self.cr["note"])


class TestRendering(unittest.TestCase):
    def test_both_sections_reach_the_text(self):
        e = _engine()
        text = "\n".join(render_lines(calendar(e, ME), crunch(e, ME)))
        for marker in ("ROSTER CALENDAR", "HOLE: week 7", "ROSTER CRUNCH",
                       "when QB_IR returns", "load-bearing bench", "droppable bench"):
            self.assertIn(marker, text, marker)

    def test_the_overlay_is_the_only_way_a_display_name_changes(self):
        e = _engine()
        text = "\n".join(render_lines(calendar(e, ME), crunch(e, ME),
                                      name_of={ME: "Local Alias"}))
        self.assertIn("Local Alias", text)
        self.assertNotIn(ME, text)

    def test_no_player_name_is_truncated(self):
        """The first live run printed "Chris Olave, Eddy Pineiro, F" -- a fixed-width
        column cutting a name in half. A planning tool that hides who is on bye is worse
        than no column at all, so names wrap onto continuation lines instead.

        Driven with a synthetic row rather than the engine fixture: the fixture's names are
        three characters long and cannot truncate, so the first version of this test passed
        against the broken renderer and proved nothing. Recorded because that is the same
        mistake the T5 fixture made with its units."""
        long_names = ["Bartholomew Fitzwilliam", "Alexandrina Vasquez-Obi",
                      "Maximilian Featherstone", "Konstantina Papadopoulos"]
        cal = {"team": ME, "weeks": [4],
               "rows": [{"week": 4, "on_bye": long_names, "on_bye_ir": [],
                         "bye_starters": long_names, "unfilled": [], "covers": [],
                         "n_startable": 13}],
               "holes": {}}
        cr = {"team": ME, "limit": 19, "active_now": 19, "ir": [], "returns": [],
              "droppable": [], "load_bearing": [], "note": "n/a"}
        text = "\n".join(render_lines(cal, cr))
        for nm in long_names:
            self.assertIn(nm, text, f"{nm} was cut from the bye column")

    def test_a_roster_with_no_hole_says_so_rather_than_printing_nothing(self):
        e = _engine()
        e.baselines["DL1"]["bye"] = 0
        text = "\n".join(render_lines(calendar(e, ME), crunch(e, ME)))
        self.assertIn("No unfillable slot", text)

    def test_droppable_is_labelled_as_not_a_value_ranking(self):
        """The live run named a 10.9-mean linebacker as the one droppable piece. He is
        droppable because a second LB already covers the only LB bye, not because he is
        the worst player -- and a reader who takes the list as a value ranking will cut
        the wrong man."""
        e = _engine()
        self.assertIn("NOT a value ranking",
                      "\n".join(render_lines(calendar(e, ME), crunch(e, ME))))


class TestItReachesTheWeeklyReport(unittest.TestCase):
    """T6 asks for this as a section in the report, not only as a tool."""

    @classmethod
    def setUpClass(cls):
        e = _engine()
        cls.rc = {"calendar": calendar(e, ME), "crunch": crunch(e, ME)}

    def _report(self, with_calendar):
        from tests.test_weekly_report import _fixture_results
        res = _fixture_results()
        if with_calendar:
            res["roster_calendar"] = self.rc
        return {"status": "OK", "failed_step": None, "error": None, "results": res,
                "started_at": "t0", "finished_at": "t1"}

    def test_the_markdown_section_carries_the_hole_and_the_crunch(self):
        from fantasy_sim.weekly_report import render_digest
        md = render_digest(self._report(True), ME, WEEK)
        for marker in ("Roster calendar", "**Hole, week 7:**", "**When QB_IR returns:**",
                       "**Load-bearing bench:**", "**Droppable bench:**"):
            self.assertIn(marker, md, marker)

    def test_the_html_section_carries_them_too(self):
        from fantasy_sim.weekly_report import render_html
        html = render_html(self._report(True), ME, WEEK)
        for marker in ('id="calendar"', "Hole, week 7", "When QB_IR returns"):
            self.assertIn(marker, html, marker)

    def test_a_report_without_the_section_renders_exactly_as_before(self):
        from fantasy_sim.weekly_report import render_digest, render_html
        self.assertNotIn("Roster calendar", render_digest(self._report(False), ME, WEEK))
        self.assertNotIn('id="calendar"', render_html(self._report(False), ME, WEEK))

    def test_quiet_weeks_are_dropped_from_the_table(self):
        """A planning table is read for its exceptions; twelve rows of dashes hide the
        three that matter. Weeks 3, 4, 11-14 have nothing to say in this fixture."""
        from fantasy_sim.weekly_report import _calendar_rows
        weeks = [r[0] for r in _calendar_rows(self.rc["calendar"])]
        self.assertEqual(weeks, ["5", "6", "7", "8", "9", "10"])

    def test_the_step_is_in_the_default_chain(self):
        from fantasy_sim.weekly_report import build_steps
        steps, _ = build_steps(ME)
        self.assertIn("roster_calendar", [n for n, _ in steps])


if __name__ == "__main__":
    unittest.main()

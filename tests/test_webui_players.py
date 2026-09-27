"""
tests.test_webui_players -- picking and resolving player names, progress, and the quiet job page.

Pure half: the player index (suggestions ranked prefix > word-prefix > substring > fuzzy,
restricted to a roster or to free agents), name resolution (exact wins; one close match is
accepted and reported; several or none is a form error listing the candidates), the job
label without its numeric knobs, and the progress stages read off the engine's markers.
Route half (skips without Flask): /api/players, a misspelled name launching the corrected
argv with the correction recorded on the job, /jobs/<id>.json, and a job page that polls
in place instead of reloading.
"""
import json
import os
import tempfile
import unittest

from fantasy_sim.config import MY_TEAM
from webui import render
from webui.paths import Root
from webui.players import Ambiguous, NoMatch, PlayerIndex
from webui.tools import FormError, get, label_for, resolve_form

try:
    import flask  # noqa: F401 -- availability probe
    from webui.app import create_app
    from tests.test_webui_launch import FakeRunner
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

POOL = [
    {"name": "DeVonta Smith", "pos": "WR", "nfl": "PHI", "owner": MY_TEAM},
    {"name": "Xavier Worthy", "pos": "WR", "nfl": "KC", "owner": MY_TEAM},
    {"name": "Jordan Love", "pos": "QB", "nfl": "GB", "owner": MY_TEAM},
    {"name": "C.J. Stroud", "pos": "QB", "nfl": "HOU", "owner": None},
    {"name": "Justin Herbert", "pos": "QB", "nfl": "LAC", "owner": None},
    {"name": "Justin Jefferson", "pos": "WR", "nfl": "MIN", "owner": "Neon Walruses"},
    {"name": "Justin Jefferson (13524)", "pos": "LB", "nfl": "CLE", "owner": None},
    {"name": "Josh Allen", "pos": "QB", "nfl": "BUF", "owner": "Turbo Llamas"},
    {"name": "Devonte Wyatt", "pos": "DL", "nfl": "GB", "owner": None},
    {"name": "Genesis Smith", "pos": "DB", "nfl": "ARI", "owner": None},
    {"name": "Tremon Smith", "pos": "DB", "nfl": "NE", "owner": None},
]

ENGINE_LOG = """[PRE-FLIGHT SUCCESS] 1249 Projections Validated.

[>>>] EXECUTING 10 INDEPENDENT BATCHES (10,000 TOTAL RUNS)...
[SUCCESS] Markov simulation resolved across all batches. Rendering visual telemetry...
"""


class TestIndex(unittest.TestCase):
    def setUp(self):
        self.idx = PlayerIndex(POOL)

    def test_search_ranks_prefix_then_word_prefix_then_substring(self):
        names = [p["name"] for p in self.idx.search("ju")]
        self.assertEqual(names[:3], ["Justin Herbert", "Justin Jefferson", "Justin Jefferson (13524)"])
        self.assertEqual([p["name"] for p in self.idx.search("wort")], ["Xavier Worthy"])
        self.assertEqual([p["name"] for p in self.idx.search("love")], ["Jordan Love"])

    def test_search_is_restricted_by_roster_or_to_free_agents(self):
        self.assertEqual([p["name"] for p in self.idx.search("j", owner="mine", mine=MY_TEAM)], ["Jordan Love"])
        free = [p["name"] for p in self.idx.search("j", owner="free")]
        self.assertEqual(free[:2], ["Justin Herbert", "Justin Jefferson (13524)"])   # prefix first ...
        self.assertEqual(free[2:], ["C.J. Stroud"])                                   # ... substring last
        self.assertNotIn("Josh Allen", free)
        self.assertEqual([p["name"] for p in self.idx.search("", owner="Turbo Llamas")], ["Josh Allen"])

    def test_fuzzy_catches_a_misspelling(self):
        self.assertIn("DeVonta Smith", [p["name"] for p in self.idx.search("devonte smtih")])

    def test_resolve_exact_is_case_insensitive_and_exact(self):
        self.assertEqual(self.idx.resolve("devonta smith"), ("DeVonta Smith", True))

    def test_resolve_accepts_one_close_match_and_says_so(self):
        self.assertEqual(self.idx.resolve("Devonte Smith"), ("DeVonta Smith", False))
        self.assertEqual(self.idx.resolve("Worthy"), ("Xavier Worthy", False))

    def test_a_clear_winner_beats_a_crowd_of_lookalikes(self):
        """Found live: 'devonte smith' against the full pool was refused as ambiguous with
        Devonte Wyatt, Genesis Smith, Tremon Smith listed -- none of them close to the whole
        typed string. The one obvious match is accepted; a bare surname is still ambiguous."""
        self.assertEqual(self.idx.resolve("devonte smith"), ("DeVonta Smith", False))
        self.assertEqual(self.idx.resolve("devonta smtih"), ("DeVonta Smith", False))
        with self.assertRaises(Ambiguous) as cm:
            self.idx.resolve("smith")
        self.assertEqual(set(cm.exception.options) & {"DeVonta Smith", "Genesis Smith", "Tremon Smith"},
                         {"DeVonta Smith", "Genesis Smith", "Tremon Smith"})

    def test_resolve_refuses_ambiguity_with_the_candidates(self):
        with self.assertRaises(Ambiguous) as cm:
            self.idx.resolve("Justin")
        self.assertIn("Justin Herbert", cm.exception.options)
        self.assertIn("Justin Jefferson", cm.exception.options)

    def test_resolve_respects_the_roster_restriction(self):
        with self.assertRaises(NoMatch):
            self.idx.resolve("Josh Allen", owner="mine", mine=MY_TEAM)
        with self.assertRaises(NoMatch):
            self.idx.resolve("DeVonta Smith", owner="free")
        with self.assertRaises(NoMatch):
            self.idx.resolve("")

    def test_from_root_joins_baselines_to_rosters_and_keeps_rostered_players_the_baselines_lack(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "data", "current"))
            with open(os.path.join(td, "data", "current", "player_baselines.json"), "w", encoding="utf-8") as fh:
                json.dump({"A Player": {"pos": "RB", "team": "SEA"}, "B Player": {"pos": "WR", "team": "GB"}}, fh)
            with open(os.path.join(td, "data", "current", "live_rosters.json"), "w", encoding="utf-8") as fh:
                json.dump({MY_TEAM: [{"name": "A Player", "pos": "RB"}, {"name": "Missing Man", "pos": "WR", "team": "NO"}]}, fh)
            idx = PlayerIndex.for_root(Root(td))
            self.assertEqual(idx.describe("A Player")["owner"], MY_TEAM)
            self.assertIsNone(idx.describe("B Player")["owner"])
            self.assertEqual(idx.describe("Missing Man")["pos"], "WR")
            self.assertIs(PlayerIndex.for_root(Root(td)), idx, "cached until the files change")


class TestFormResolution(unittest.TestCase):
    def setUp(self):
        self.idx = PlayerIndex(POOL)

    def test_a_close_name_is_corrected_and_noted(self):
        form, notes = resolve_form(get("compare_players"), {"a": "Devonte Smith", "b": "xavier worthy"}, self.idx, MY_TEAM)
        self.assertEqual((form["a"], form["b"]), ("DeVonta Smith", "Xavier Worthy"))
        self.assertEqual(notes, ["Devonte Smith → DeVonta Smith"])

    def test_lists_resolve_per_name_against_the_right_roster(self):
        form, notes = resolve_form(get("evaluate_move"), {"team": MY_TEAM, "add": "stroud, herbert", "drop": "love"}, self.idx, MY_TEAM)
        self.assertEqual(form["add"], "C.J. Stroud, Justin Herbert")
        self.assertEqual(form["drop"], "Jordan Love")
        self.assertEqual(len(notes), 3)
        with self.assertRaises(FormError) as cm:
            resolve_form(get("evaluate_move"), {"team": MY_TEAM, "drop": "Josh Allen"}, self.idx, MY_TEAM)
        self.assertIn("is on Turbo Llamas, not your roster", str(cm.exception))

    def test_team_players_follow_the_selected_team(self):
        form, _n = resolve_form(get("evaluate_trade"), {"team_a": MY_TEAM, "a_gives": "worthy", "team_b": "Turbo Llamas", "b_gives": "allen"}, self.idx, MY_TEAM)
        self.assertEqual((form["a_gives"], form["b_gives"]), ("Xavier Worthy", "Josh Allen"))
        with self.assertRaises(FormError) as cm:
            resolve_form(get("evaluate_trade"), {"team_a": MY_TEAM, "a_gives": "allen", "team_b": "Turbo Llamas"}, self.idx, MY_TEAM)
        self.assertIn(f"on {MY_TEAM}", str(cm.exception))

    def test_ambiguity_lists_the_candidates(self):
        with self.assertRaises(FormError) as cm:
            resolve_form(get("compare_players"), {"a": "Justin", "b": "Jordan Love"}, self.idx, MY_TEAM)
        self.assertIn("could be", str(cm.exception))
        self.assertIn("Justin Herbert", str(cm.exception))

    def test_no_index_means_no_resolution(self):
        form, notes = resolve_form(get("compare_players"), {"a": "whoever", "b": "x"}, None, MY_TEAM)
        self.assertEqual((form["a"], notes), ("whoever", []))

    def test_labels_name_the_players_and_never_the_knobs(self):
        self.assertEqual(label_for(get("compare_players"), {"a": "DeVonta Smith", "b": "Xavier Worthy", "sims": "2000", "week": "5"}),
                         "Compare players · DeVonta Smith vs Xavier Worthy")
        self.assertEqual(label_for(get("optimize_lineup"), {"team": MY_TEAM, "sims": "1000"}), f"Optimize lineup · {MY_TEAM}")
        self.assertEqual(label_for(get("run_simulation"), {}), "Run simulation")


class TestProgress(unittest.TestCase):
    def test_engine_stages_are_read_off_the_markers(self):
        p = render.progress(ENGINE_LOG, "run_simulation")
        self.assertEqual(p["stages"], ["validating projections", "simulating", "rendering charts", "exports written"])
        self.assertEqual((p["reached"], p["stage"]), (3, "rendering charts"))
        self.assertEqual(p["last"], "", "the engine's own lines are chatter, not the tool talking")
        self.assertEqual(render.progress("", "run_simulation")["stage"], "starting")

    def test_report_stages_extend_to_the_sub_tools_and_the_digest(self):
        log = ENGINE_LOG + "[EXPORT COMPLETE] x\n  logged -> data/decisions/week_03/archive/roster_grades_a.json\n  logged -> data/decisions/week_03/archive/lineup_b.json\n"
        p = render.progress(log, "weekly_report")
        self.assertEqual(p["stage"], "lineup")
        self.assertIn("digest written", p["stages"])
        self.assertTrue(p["last"].startswith("logged ->"))

    def test_a_plain_tool_is_computing_after_preflight(self):
        self.assertEqual(render.progress("[PRE-FLIGHT SUCCESS] 55 Projections Validated.\nSOME OUTPUT\n", "roster_calendar")["stage"], "computing")
        self.assertEqual(render.progress("x\n  logged -> data/x.json", "roster_calendar")["stage"], "record written")

    def test_logging_mirror_lines_are_chatter_but_indented_quotes_are_content(self):
        blocks = render.console_blocks("WARNING | ROSTER HOLES: Neon Walruses cannot fill DL\nHEADING\n  degraded: WARNING | odds fell back\n")
        self.assertEqual([b["kind"] for b in blocks], ["chatter", "heading", "para"])


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestRoutes(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.td.name, "data", "current"))
        with open(os.path.join(self.td.name, "data", "current", "player_baselines.json"), "w", encoding="utf-8") as fh:
            json.dump({p["name"]: {"pos": p["pos"], "team": p["nfl"]} for p in POOL}, fh)
        rosters = {}
        for p in POOL:
            if p["owner"]:
                rosters.setdefault(p["owner"], []).append({"name": p["name"], "pos": p["pos"]})
        with open(os.path.join(self.td.name, "data", "current", "live_rosters.json"), "w", encoding="utf-8") as fh:
            json.dump(rosters, fh)
        self.root = Root(self.td.name)
        self.runner = FakeRunner()
        app = create_app(self.root, runner=self.runner, csrf_token="tok")
        app.testing = True
        self.c = app.test_client()

    def tearDown(self):
        self.td.cleanup()

    def test_api_players_suggests_from_the_right_pool(self):
        mine = self.c.get(f"/api/players?q=j&owner=mine").get_json()["players"]
        self.assertEqual([p["name"] for p in mine], ["Jordan Love"])
        free = self.c.get("/api/players?q=&owner=free").get_json()["players"]
        self.assertTrue(all(p["owner"] is None for p in free))
        self.assertIn("C.J. Stroud", [p["name"] for p in free])

    def test_a_misspelled_name_launches_the_corrected_argv_and_records_the_correction(self):
        r = self.c.post("/tools/compare_players", data={"_csrf": "tok", "a": "devonte smith", "b": "worthy", "sims": "2000"})
        self.assertEqual(r.status_code, 302, r.data[:300])
        argv, _tool, label = self.runner.launches[0]
        self.assertEqual(argv[-2:], ["DeVonta Smith", "Xavier Worthy"])
        self.assertEqual(label, "Compare players · DeVonta Smith vs Xavier Worthy")
        jid = list(self.runner.metas)[-1]
        self.assertEqual(self.runner.metas[jid]["resolved"], ["devonte smith → DeVonta Smith", "worthy → Xavier Worthy"])
        page = self.c.get(f"/jobs/{jid}").get_data(as_text=True)
        self.assertIn("Players matched", page)
        self.assertIn("DeVonta Smith vs Xavier Worthy", page)
        self.assertNotIn("2000", page.split("<h1")[1].split("</h1>")[0], "the title carries names, not knobs")

    def test_an_unknown_name_is_a_form_error_not_a_launch(self):
        r = self.c.post("/tools/compare_players", data={"_csrf": "tok", "a": "Nobody Atall", "b": "Jordan Love"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("no player named", r.get_data(as_text=True))
        self.assertEqual(self.runner.launches, [])

    def test_the_running_job_page_polls_in_place_and_never_meta_refreshes(self):
        self.c.post("/tools/roster_calendar", data={"_csrf": "tok", "team": MY_TEAM})
        jid = list(self.runner.metas)[-1]
        page = self.c.get(f"/jobs/{jid}").get_data(as_text=True)
        self.assertNotIn("http-equiv", page)
        self.assertIn(f"/jobs/{jid}.json", page)
        self.assertIn('id="stages"', page)
        js = self.c.get(f"/jobs/{jid}.json").get_json()
        self.assertEqual(js["state"], "RUNNING")
        self.assertIn("progress", js)
        self.assertEqual(js["progress"]["stage"], "starting")
        self.assertIsInstance(js["tail"], list)
        self.assertEqual(self.c.get("/jobs/nope.json").status_code, 404)

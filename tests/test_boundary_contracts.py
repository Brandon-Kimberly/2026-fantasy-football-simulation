"""B26: one test per boundary that pins the REQUEST, not the reply the test supplied.

F50 and F52 had the same shape -- correct, passing tests that patched the function whose
*input* was wrong, then asserted arithmetic on the input the test itself handed over.
Neither could catch a bad request, because the request never ran.

**THE GOAL IS NOT TO REMOVE MOCKS.** Hermetic tests are a design requirement (`CLAUDE.md`,
environment section: the suite needs none of the league identifiers, and F48 says keep it
that way). These patch `requests.get` -- the transport, the lowest thing there is -- and
let the REAL function build the URL and parse the reply. That is the level F52's
`test_free_agents_is_asked_for_the_week_being_synced` works at, and it is the pattern B26
asks to spread.

**THESE ARE COVERAGE, NOT REGRESSION TESTS, and saying so matters (rule 1).** No defect is
being fixed here: each of the three boundaries below is believed correct today and these
pass on first run. A test written after the code proves nothing on its own -- so each one
was verified load-bearing by MUTATION, breaking the contract in the source and confirming
the test goes red. What that mutation was is recorded against each class.

The sweep found five boundaries with neither a request-pin nor an empty-return test:
`generate_league_schedule`, `fetch_league_wide_player_scores`, `ingest_transactions`,
`ingest_drafts`, and `live_matchup._fetch_json`. B26 says fix the top three. The three
chosen are the ones whose silent failure is already known to be expensive here, and the
remaining two are recorded in the finding rather than quietly dropped.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class TestLeagueWideScoresAsksForTheRightWeek(unittest.TestCase):
    """F54's feed: the one that took the Bayesian blend from 157 players to 919.

    It is the pool every waiver claim is drawn from, so a request for the wrong season or
    week degrades every free-agent comparison back to a preseason prior -- silently,
    because the function returns {} on failure by design so a dead feed cannot break a
    sync. That design is right and is exactly why the REQUEST needs pinning instead.

    Mutation check: changing the URL's `{int(week)}` to `{int(week) - 1}` turns the first
    test red.
    """

    def _run(self, year=2026, week=3, payload=None):
        seen = []

        def fake_fetch(url):
            seen.append(url)
            return payload if payload is not None else []

        from fantasy_sim.sync import fetch_league_wide_player_scores
        out = fetch_league_wide_player_scores(year, week, {"pts_ppr": 1.0}, fetch=fake_fetch)
        return seen, out

    def test_it_asks_for_the_season_and_week_it_was_given(self):
        seen, _ = self._run(year=2026, week=3)
        self.assertTrue(seen, "no request was made at all")
        for url in seen:
            self.assertIn("/stats/nfl/2026/3", url,
                          "a wrong week here silently reverts the blend to preseason priors")

    def test_it_asks_for_the_regular_season_only(self):
        seen, _ = self._run()
        for url in seen:
            self.assertIn("season_type=regular", url)

    def test_it_asks_for_every_position_including_idp(self):
        """This is an IDP league. Dropping DL/LB/DB would leave exactly the positions
        whose priors are least trustworthy (B1) unrefined."""
        seen, _ = self._run()
        asked = {u.split("position[]=")[1] for u in seen if "position[]=" in u}
        for pos in ("QB", "RB", "WR", "TE", "K", "DL", "LB", "DB"):
            self.assertIn(pos, asked)

    def test_an_empty_feed_returns_an_empty_dict_not_an_exception(self):
        _seen, out = self._run(payload=[])
        self.assertEqual(out, {})

    def test_a_raising_feed_still_returns_a_dict(self):
        """Degrade to matchup-only, never break the sync."""
        from fantasy_sim.sync import fetch_league_wide_player_scores

        def boom(_url):
            raise ConnectionError("down")

        self.assertEqual(
            fetch_league_wide_player_scores(2026, 3, {"pts_ppr": 1.0}, fetch=boom), {})


class TestTransactionsAskForEveryWeekAndKeepSleepersLeg(unittest.TestCase):
    """The decision log, which is what the bid ledger reconciles against.

    Two contracts, and F65 is what happens when the second is assumed rather than pinned:
    the recorded `week` is Sleeper's `leg`, NOT the loop counter -- so a claim submitted
    before a week rolled over is stamped with the earlier week. The bid ledger matched on
    (player_id, week) and silently resolved nothing for it. The behaviour is correct; it
    was the unwritten assumption that cost a day.

    Mutation check: changing `tx.get("leg", wk)` to `wk` turns the leg test red.
    """

    TX = [{"transaction_id": "t1", "type": "waiver", "status": "complete", "leg": 2,
           "created": 1_700_000_000_000, "roster_ids": [1], "settings": {"waiver_bid": 29},
           "adds": {"4046": 1}, "drops": None},
          {"transaction_id": "t2", "type": "waiver", "status": "pending", "leg": 2,
           "created": 1_700_000_000_000, "roster_ids": [1], "adds": {"9999": 1}}]

    def _run(self, current_week=3, txs=None, tmp=None):
        seen = []

        def fake_get(url, timeout=None):
            seen.append(url)
            return _Resp(self.TX if txs is None else txs)

        from fantasy_sim import sync
        with patch.object(sync, "requests") as rq:
            rq.get.side_effect = fake_get
            n = sync.ingest_transactions(
                {1: "Quantum Ferrets"}, current_week,
                {"Some Guy": {"player_id": "4046", "mean": 20.0}},
                {"4046": {"first_name": "Some", "last_name": "Guy"}},
                my_team="Quantum Ferrets", path=tmp)
        # Nothing appended means the file is never created, which is itself correct --
        # an empty run must not leave an empty artifact behind.
        rows = []
        if tmp and os.path.exists(tmp):
            with open(tmp, encoding="utf-8") as fh:
                rows = [json.loads(x) for x in fh if x.strip()]
        return seen, n, rows

    def test_it_asks_for_every_week_up_to_the_current_one(self):
        with tempfile.TemporaryDirectory() as d:
            seen, _n, _rows = self._run(current_week=3, tmp=os.path.join(d, "log.jsonl"))
        self.assertEqual(len([u for u in seen if "/transactions/" in u]), 3,
                         "weeks 1..3; a narrower sweep drops earlier claims for good")
        for wk in (1, 2, 3):
            self.assertTrue(any(u.endswith(f"/transactions/{wk}") for u in seen))

    def test_the_recorded_week_is_sleepers_leg_not_the_loop_counter(self):
        """F65's root cause, now written down."""
        with tempfile.TemporaryDirectory() as d:
            _seen, _n, rows = self._run(current_week=3, tmp=os.path.join(d, "log.jsonl"))
        self.assertTrue(rows)
        self.assertEqual(rows[0]["week"], 2,
                         "the leg at SUBMISSION, which is why a bid ledger keyed on "
                         "current_week could not match it (F65)")

    def test_only_completed_transactions_are_recorded(self):
        """B14's whole premise: a lost claim never becomes a transaction, so this log is
        a record of WINS ONLY and cannot be asked what was lost."""
        with tempfile.TemporaryDirectory() as d:
            _seen, _n, rows = self._run(current_week=1, tmp=os.path.join(d, "log.jsonl"))
        self.assertEqual([r["transaction_id"] for r in rows], ["t1"])

    def test_an_empty_payload_appends_nothing_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            _seen, n, rows = self._run(current_week=2, txs=[],
                                       tmp=os.path.join(d, "log.jsonl"))
        self.assertEqual((n, rows), (0, []))


class TestLiveMatchupTransportIsLoudOnFailure(unittest.TestCase):
    """`live_matchup._fetch_json` had NO test of any kind.

    It is the transport under `game_clocks`, and B3's locks are computed from those
    clocks. `decisions.locked_nfl_teams` deliberately reads a missing clock as UNLOCKED --
    "an absent clock is ignorance, not a kickoff" -- which is the right call ONLY because
    a failed fetch raises here rather than returning an empty scoreboard. If this ever
    swallowed its errors, every game would read pregame, locks would switch off league
    wide, and the optimizer would go back to proposing lineups that cannot be set.

    Mutation check: wrapping the body in `try/except: return {}` turns the raise test red.
    """

    def test_it_raises_on_an_http_error_rather_than_returning_empty(self):
        from scripts.live_matchup import _fetch_json
        with patch("scripts.live_matchup.requests.get", return_value=_Resp({}, status=503)):
            with self.assertRaises(Exception):
                _fetch_json("http://x/y")

    def test_it_returns_the_parsed_payload_on_success(self):
        from scripts.live_matchup import _fetch_json
        with patch("scripts.live_matchup.requests.get",
                   return_value=_Resp({"week": 3}, status=200)):
            self.assertEqual(_fetch_json("http://x/y"), {"week": 3})

    def test_a_missing_clock_still_reads_as_unlocked(self):
        """The other half of the contract, and the reason the raise above matters: an
        absent scoreboard entry must never freeze a player out of his owner's lineup."""
        from fantasy_sim.decisions import locked_nfl_teams
        self.assertEqual(locked_nfl_teams({}), frozenset())
        self.assertEqual(locked_nfl_teams({"KC": (1.0, "pregame")}), frozenset())
        self.assertEqual(locked_nfl_teams({"KC": (0.5, "unknown")}), frozenset())
        self.assertEqual(locked_nfl_teams({"KC": (0.5, "Q3 05:22")}), frozenset({"KC"}))


class TestLeagueScheduleAsksForEveryWeekInOrder(unittest.TestCase):
    """H2, closing F66's fourth gap. `generate_league_schedule` had an empty-return test
    but nothing pinning the REQUEST.

    The property that matters is positional. The engine indexes this list as
    `league_schedule[week - 1]`, so it must hold exactly one entry per week, built from a
    request for THAT week. A failed week used to be `continue`d, which shifted every later
    week one index earlier and silently mis-assigned opponents for the rest of the season
    (AUDIT_PHASE_3_FINDINGS.md finding 2b).

    COVERAGE, not a regression test: the boundary is believed correct and this passes on
    first run. Verified load-bearing by mutation -- pinning the URL to week 1 regardless of
    the loop variable turns it red.

    **`save_json` IS PATCHED, AND THAT IS NOT OPTIONAL.** This function WRITES
    `data/current/league_schedule.json` as a side effect and returns the list of FAILED
    weeks, not the schedule. The first version of this test patched only the transport, so
    running the suite replaced the real league schedule with a two-team fixture -- exactly
    the F11 class of defect (a test silently truncating real data, found only by accident).
    The schedule is captured from the write instead.
    """

    @staticmethod
    def _run(fake_get, weeks=5):
        """Returns (schedule_written, urls_asked, failed_weeks). Writes nothing."""
        from fantasy_sim import sync
        asked, written = [], []

        def spy(url, timeout=None):
            asked.append(url)
            return fake_get(url)

        with patch.object(sync.requests, "get", side_effect=spy),              patch.object(sync, "save_json", side_effect=lambda p, o: written.append(o)):
            failed = sync.generate_league_schedule({1: "A", 2: "B"}, regular_season_weeks=weeks)
        return (written[-1] if written else None), asked, failed

    @staticmethod
    def _ok(_url):
        return _Resp([{"roster_id": 1, "matchup_id": 1}, {"roster_id": 2, "matchup_id": 1}])

    def test_it_asks_for_each_week_once_and_in_order(self):
        schedule, asked, failed = self._run(self._ok)
        self.assertEqual(len(asked), 5, "one request per week, no more and no fewer")
        self.assertEqual([u.rsplit("/", 1)[-1] for u in asked], ["1", "2", "3", "4", "5"],
                         "the week in the URL must follow the loop, or every later week "
                         "is built from the wrong week's matchups")
        for u in asked:
            self.assertIn("/matchups/", u)
        self.assertEqual(failed, [])
        self.assertEqual(len(schedule), 5, "exactly one entry per week: the engine indexes "
                                           "this list positionally")

    def test_a_failed_week_still_occupies_its_index(self):
        """The finding itself (AUDIT_PHASE_3_FINDINGS 2b), pinned at the transport level
        rather than assumed."""
        def flaky(url):
            return _Resp(None, status=500) if url.endswith("/3") else self._ok(url)

        schedule, _asked, failed = self._run(flaky)
        self.assertEqual(failed, [3], "the failure is reported, not swallowed")
        self.assertEqual(len(schedule), 5)
        self.assertEqual(schedule[2], [], "week 3 failed; it must be EMPTY, not absent")
        self.assertTrue(schedule[3], "week 4 must still be at index 3, not shifted up")


class TestDraftIngestionAsksForThePicksAndRefusesAnEmptyDraft(unittest.TestCase):
    """H2, closing F66's fifth gap. `ingest_drafts` had neither a request-pin nor an
    empty-return test -- the only boundary in the sweep with no test of any kind besides
    `live_matchup._fetch_json`, which F66 fixed.

    Two properties. It must ask `/draft/{draft_id}/picks` for the draft it found, and an
    empty picks payload must write NOTHING -- a draft file is immutable once written
    (F15), so a zero-pick file written on a transient empty reply would be permanent and
    would poison `draft_review` for that season forever.

    COVERAGE, not a regression test; passes on first run. Verified by mutation: asking for
    `/draft/{league_id}/picks` instead of the draft id, and removing the `if not picks`
    guard, each turn it red.
    """

    LEAGUE = {"season": "2026", "previous_league_id": None}
    DRAFT = {"draft_id": "DRAFT_9", "season": "2026", "status": "complete",
             "start_time": 1, "settings": {"rounds": 1}}
    PICKS = [{"pick_no": 1, "round": 1, "draft_slot": 1, "roster_id": 1, "picked_by": "u1",
              "player_id": "111", "is_keeper": False,
              "metadata": {"first_name": "Alpha", "last_name": "One", "position": "RB",
                           "team": "DET"}}]

    def _run(self, picks):
        from fantasy_sim import sync
        asked = []

        def fake_get(url, timeout=None):
            asked.append(url)
            if url.endswith("/drafts"):
                return _Resp([self.DRAFT])
            if "/picks" in url:
                return _Resp(picks)
            return _Resp(self.LEAGUE)

        d = tempfile.mkdtemp()
        with patch.object(sync.requests, "get", side_effect=fake_get):
            n = sync.ingest_drafts({1: "A"}, league_id="L1",
                                   path_fn=lambda s: os.path.join(d, f"draft_{s}.json"))
        return n, asked, d

    def test_it_asks_for_the_picks_of_the_draft_it_found(self):
        n, asked, d = self._run(self.PICKS)
        self.assertEqual(n, 1)
        self.assertIn(f"/draft/{self.DRAFT['draft_id']}/picks", " ".join(asked),
                      "the picks must be fetched by DRAFT id; a league id here would "
                      "silently return nothing and the season would never be recorded")
        with open(os.path.join(d, "draft_2026.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["picks"][0]["name"], "Alpha One")

    def test_an_empty_picks_payload_writes_nothing(self):
        """A draft file is immutable once written (F15). A zero-pick file created from a
        transient empty reply would be permanent and would poison draft_review forever."""
        n, _asked, d = self._run([])
        self.assertEqual(n, 0)
        self.assertFalse(os.path.exists(os.path.join(d, "draft_2026.json")))

    def test_a_null_picks_payload_is_treated_the_same(self):
        n, _asked, d = self._run(None)
        self.assertEqual(n, 0)
        self.assertFalse(os.path.exists(os.path.join(d, "draft_2026.json")))


if __name__ == "__main__":
    unittest.main()

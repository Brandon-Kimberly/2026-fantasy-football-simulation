"""T3: the trade tools propose players already committed to a pending trade.

With a McConkey-for-Coker trade pending on 2026-09-23, `find_trades --require-mutual`
ranked "give Nick Bolton, get Jalen Coker" FIRST -- a player already promised to somebody
else. Sleeper's `/league/{id}/transactions/{week}` returns those with `status: "pending"`,
and `sync.ingest_transactions` deliberately keeps only `complete` (B14's premise: the
decision log is a record of what HAPPENED). So nothing downstream has ever seen them.

**PENDING IS NOT CERTAIN, and that shapes the whole design.** A trade can be vetoed or
withdrawn and the players come straight back. So:

  * the exclusion is ADVISORY and every tool that applies it says so and says how many;
  * `--include-pending` turns it off, because a rejected offer must not leave the finder
    permanently blind to a player;
  * **the engine never sees any of this.** Applying a pending trade to rosters as if it
    were complete would put unowned players in lineups, in the paired simulation and in
    the weekly projections. The exclusion lives in the candidate lists of three screens
    and nowhere else, and a test pins that `engine.rosters` is untouched.

**Matching is by player_id.** The raw cache carries 220 colliding names, seven of them
involving a player rostered in this league (B17), and a name-keyed exclusion would remove
the wrong man.

**Absence is unknown, not "nothing pending".** A missing or unreadable file excludes
nobody and says nothing, the same rule the bid ledger follows for an unresolved claim --
silently excluding everyone on a read error would be far worse than proposing one stale
trade.

Written before the change.
"""
import json
import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    LEAGUE_STATE_FILE, LEAGUE_STANDINGS_FILE, VEGAS_FILE, LIVE_ROSTERS_FILE, BASELINES_FILE,
    TEAM_RATINGS_FILE, DEFENSIVE_RATINGS_FILE, DEFENSIVE_TIERS_FILE, LEAGUE_SCHEDULE_FILE,
    NFL_SCHEDULE_FILE, WEEKLY_ACTUALS_FILE,
)

TEAMS = ["Quantum Ferrets", "Neon Walruses", "Rocket Pandas", "Polar Yetis"]
ME, OTHER = TEAMS[0], TEAMS[1]
WEEK = 3
# 13 starters + 5 bench. The bench is not decoration: `_construct_trade_offers` returns
# nothing unless the RICH side has at least two bench players, so a 14-man fixture produces
# an empty `buy` list and every exclusion test below would pass vacuously.
SLOTS = [("QB", 18.0), ("RB", 17.0), ("RB", 15.0), ("WR", 16.0), ("WR", 13.0),
         ("TE", 12.0), ("K", 9.0), ("DL", 9.0), ("LB", 9.0), ("DB", 9.0),
         ("RB", 11.0), ("WR", 10.5), ("WR", 10.0),
         ("WR", 8.0), ("RB", 7.0), ("TE", 6.5), ("DL", 7.5), ("LB", 7.0)]
# ME is thin at skill and strong on defence; the rivals are the reverse, with two buried
# skill studs. That asymmetry is what gives the finder something to propose: an offer needs
# BOTH a hole of mine to fill and a chip of mine that upgrades one of THEIR starters.
MINE_SKILL, MINE_DEF = 0.5, 1.9
THEIR_SKILL, THEIR_DEF = 1.0, 0.5
BURIED = {"Ne_WR_13": 22.0, "Ne_RB_14": 21.0}


def _fs():
    base, rosters, pid = {}, {}, 9000
    for t in TEAMS:
        entries = []
        for i, (pos, mu) in enumerate(SLOTS):
            pid += 1
            nm = f"{t[:2]}_{pos}_{i}"
            base[nm] = {"mean": mu, "std_aleatoric": 3.0, "std_epistemic": 1.5, "pos": pos,
                        "team": "DET", "bye": 0, "player_id": str(pid)}
            entries.append({"name": nm, "pos": pos, "team": "DET"})
        rosters[t] = entries
    for nm, e in base.items():
        skill = e["pos"] in ("WR", "RB", "TE")
        if nm.startswith(ME[:2] + "_"):
            e["mean"] = round(e["mean"] * (MINE_SKILL if skill else MINE_DEF), 2)
        else:
            e["mean"] = round(e["mean"] * (THEIR_SKILL if skill else THEIR_DEF), 2)
    for nm, mu in BURIED.items():
        base[nm]["mean"] = mu
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


def _pending_doc(engine, mine, theirs):
    """The file sync writes: both sides, by pid, with the team each player is leaving."""
    b = engine.baselines
    return {"_meta": {"week": WEEK, "fetched_at": "2026-09-23T12:00:00Z", "n": 1},
            "trades": [{"transaction_id": "tx1", "week": WEEK, "status": "pending",
                        "teams": [ME, OTHER],
                        "players": [{"player_id": b[mine]["player_id"], "name": mine,
                                     "from_team": ME, "to_team": OTHER},
                                    {"player_id": b[theirs]["player_id"], "name": theirs,
                                     "from_team": OTHER, "to_team": ME}]}]}


class TestReadingTheFile(unittest.TestCase):
    def setUp(self):
        self.e = _engine()
        self.mine, self.theirs = self.e.rosters[ME][0], self.e.rosters[OTHER][0]
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "pending_trades.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(_pending_doc(self.e, self.mine, self.theirs), fh)

    def test_it_returns_the_committed_names_resolved_through_player_id(self):
        """B17: 220 colliding names in the raw cache. A name-keyed exclusion removes the
        wrong man, so the file carries pids and the engine resolves them."""
        from fantasy_sim.pending import committed_players
        self.assertEqual(committed_players(self.e, path=self.path),
                         {self.mine, self.theirs})

    def test_a_missing_file_excludes_nobody(self):
        """Absence is unknown, not 'nothing pending'. Excluding everyone on a read error
        would be far worse than proposing one stale trade."""
        from fantasy_sim.pending import committed_players
        self.assertEqual(committed_players(self.e, path=os.path.join(self.dir, "nope.json")),
                         set())

    def test_unreadable_junk_excludes_nobody_rather_than_raising(self):
        from fantasy_sim.pending import committed_players
        bad = os.path.join(self.dir, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(committed_players(self.e, path=bad), set())

    def test_a_pid_nobody_in_this_league_owns_is_skipped_not_invented(self):
        from fantasy_sim.pending import committed_players
        doc = _pending_doc(self.e, self.mine, self.theirs)
        doc["trades"][0]["players"].append(
            {"player_id": "999999", "name": "Somebody Else", "from_team": "?", "to_team": "?"})
        p = os.path.join(self.dir, "extra.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        self.assertEqual(committed_players(self.e, path=p), {self.mine, self.theirs})


class TestTheScreensExcludeThem(unittest.TestCase):
    """The defect: a player already promised to somebody else ranked first."""

    # The two men in the pending deal, chosen because the UNFILTERED finder really does
    # propose both on this fixture (the control test below asserts exactly that): one of
    # my defensive chips going out, one of their buried receivers coming back.
    MINE, THEIRS = "Qu_LB_17", "Ne_WR_11"

    def setUp(self):
        self.e = _engine()
        self.mine, self.theirs = self.MINE, self.THEIRS
        self.excluded = {self.mine, self.theirs}

    @staticmethod
    def _named(r):
        out = set()
        for row in r["buy"]:
            out |= {row["target"]} | set(row["i_give"]) | set(row["i_get"])
        for row in r["sell"]:
            out |= set(row["they_want"]) | set(row["they_give"])
        return out

    def test_find_trade_targets_drops_both_sides_of_the_pending_deal(self):
        from fantasy_sim.decisions import find_trade_targets
        r = find_trade_targets(self.e, ME, week=WEEK, exclude=self.excluded)
        overlap = self._named(r) & self.excluded
        self.assertFalse(overlap, f"pending players proposed: {overlap}")
        self.assertEqual(set(r["excluded_pending"]), self.excluded)

    def test_exhaustive_swaps_drops_them_too(self):
        from fantasy_sim.swaps import exhaustive_swaps
        rows = exhaustive_swaps(self.e, ME, week=WEEK, exclude=self.excluded)
        for row in rows:
            self.assertFalse(set(row["i_give"]) & self.excluded, row)
            self.assertFalse(set(row["i_get"]) & self.excluded, row)

    def test_leverage_does_not_offer_a_committed_player_as_my_surplus(self):
        from fantasy_sim.leverage import leverage
        rows = leverage(self.e, ME, WEEK, exclude=self.excluded)
        for rival in rows:
            for hole in rival["holes"]:
                self.assertNotIn(hole.get("i_could_send"), self.excluded)

    def test_with_no_exclusion_the_same_players_ARE_proposed(self):
        """THE CONTROL. Without it every test above could pass because the fixture never
        proposes those players anyway, and the file would prove nothing. The first version
        of this fixture did exactly that: 14 men per roster leaves the rich side one bench
        player, `_construct_trade_offers` needs two, and `buy` came back empty."""
        from fantasy_sim.decisions import find_trade_targets
        named = self._named(find_trade_targets(self.e, ME, week=WEEK))
        self.assertIn(self.theirs, named, "the fixture must propose the incoming player")
        self.assertIn(self.mine, named, "...and the outgoing one")
        self.assertEqual(find_trade_targets(self.e, ME, week=WEEK)["excluded_pending"], [])


class TestTheSyncWriter(unittest.TestCase):
    """`sync.write_pending_trades`. Written after the reader, and the ordering is stated
    rather than glossed: these passed on the first run and are coverage of new plumbing,
    verified by mutation (see the commit message)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "pending_trades.json")
        self.roster_map = {1: ME, 2: OTHER}
        self.players_db = {"p1": {"first_name": "Alpha", "last_name": "One"},
                           "p2": {"first_name": "Beta", "last_name": "Two"}}

    @staticmethod
    def _tx(status="pending", type_="trade"):
        return {"transaction_id": "tx1", "type": type_, "status": status, "leg": WEEK,
                "roster_ids": [1, 2],
                "adds": {"p1": 2, "p2": 1}, "drops": {"p1": 1, "p2": 2}}

    def _run(self, payloads):
        from fantasy_sim import sync

        class R:
            status_code = 200

            def __init__(self, p):
                self._p = p

            def json(self):
                return self._p

        it = iter(payloads)
        with patch.object(sync.requests, "get", side_effect=lambda *a, **k: R(next(it, []))):
            return sync.write_pending_trades(self.roster_map, WEEK, self.players_db,
                                             path=self.path)

    def _doc(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_pending_trade_is_written_with_both_sides_by_pid(self):
        self.assertEqual(self._run([[self._tx()], []]), 1)
        players = self._doc()["trades"][0]["players"]
        self.assertEqual({p["player_id"] for p in players}, {"p1", "p2"})
        by_pid = {p["player_id"]: p for p in players}
        self.assertEqual((by_pid["p1"]["from_team"], by_pid["p1"]["to_team"]), (ME, OTHER))
        self.assertEqual(by_pid["p2"]["name"], "Beta Two", "a name for a human reader")

    def test_a_COMPLETE_trade_is_not_pending(self):
        self.assertEqual(self._run([[self._tx(status="complete")], []]), 0)
        self.assertEqual(self._doc()["trades"], [])

    def test_a_pending_WAIVER_is_not_a_trade(self):
        self.assertEqual(self._run([[self._tx(type_="waiver")], []]), 0)

    def test_the_file_is_REWRITTEN_not_appended(self):
        """A pending trade that completes or is vetoed stops being pending. An append-only
        record would keep excluding its players forever."""
        self._run([[self._tx()], []])
        self.assertEqual(self._run([[], []]), 0)
        self.assertEqual(self._doc()["trades"], [])

    def test_a_fetch_failure_leaves_the_existing_file_alone(self):
        """An empty document reads as "no pending trades", which is a CLAIM. Absence must
        read as unknown -- the rule the bid ledger already follows."""
        from fantasy_sim import sync
        self._run([[self._tx()], []])
        with patch.object(sync.requests, "get", side_effect=RuntimeError("down")):
            self.assertEqual(sync.write_pending_trades(self.roster_map, WEEK,
                                                       self.players_db, path=self.path), 0)
        self.assertEqual(len(self._doc()["trades"]), 1, "the known pending trade survives")


class TestTheAdvisoryNote(unittest.TestCase):
    def test_it_says_advisory_and_names_the_escape_hatch(self):
        from fantasy_sim.pending import note
        txt = note({"Alpha One", "Beta Two"})
        self.assertIn("2 player(s) excluded", txt)
        self.assertIn("ADVISORY", txt)
        self.assertIn("--include-pending", txt)
        self.assertIn("vetoed", txt)

    def test_an_empty_exclusion_says_nothing(self):
        from fantasy_sim.pending import note
        self.assertEqual(note(set()), "")


class TestTheEngineIsNeverTouched(unittest.TestCase):
    def test_excluding_players_does_not_change_any_roster(self):
        """Pending is not complete. Applying it to the engine would put unowned players in
        lineups, in the paired simulation and in the weekly projections."""
        from fantasy_sim.decisions import find_trade_targets
        e = _engine()
        before = {t: list(r) for t, r in e.rosters.items()}
        find_trade_targets(e, ME, week=WEEK, exclude={e.rosters[ME][0], e.rosters[OTHER][0]})
        self.assertEqual({t: list(r) for t, r in e.rosters.items()}, before)


if __name__ == "__main__":
    unittest.main()

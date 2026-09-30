"""The odds credit budget (owner request 2026-09-30): make running out of odds credits impossible.

The account has 500 credits a month and ran out on 2026-09-30, the season's first month and a
short one. Measured that day: every sync cost 3 credits -- 1 for the key check against the
paid odds endpoint, 2 for the fetch (spreads + totals, one region) -- and about 112 syncs ran
on the owner's machine alone, most of them development, not decisions. A full in-season month
at that rate runs out around the 29th.

Being careful is not a guarantee, so the budget is enforced in code:
  * the key check reads the free endpoint (/v4/sports: `x-requests-last: 0`, and it still
    answers 200 with the balance when the balance is 0 -- both observed that day);
  * a sync that is not an official run reuses this week's real lines when they are fresh
    (config.ODDS_REUSE_HOURS) instead of paying again;
  * a sync that is not an official run never spends below config.ODDS_CREDIT_RESERVE, which is
    held for the official runs; with the balance unknown it does not spend at all;
  * official runs (scripts.run_sync --official from canonical-run, weekly_report --canonical)
    always fetch fresh lines, and may spend the reserve.
Held or reused, the real lines already on disk for this week are kept (C3/F67), never
flattened.

Written before the code.
"""
import datetime as _real_dt
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import fantasy_sim.sync as sync

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = _real_dt.datetime(2026, 10, 8, 12, 0, 0)


class _Now(_real_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 8, 12, 0, 0)


def lines(week=6, hours_ago=2.0, source="the_odds_api", stale=False):
    meta = {"week": week, "source": source,
            "fetched_at": (NOW - _real_dt.timedelta(hours=hours_ago)).isoformat(timespec="seconds")}
    if stale:
        meta["stale_since"] = NOW.isoformat(timespec="seconds")
    return {"DET": {"total": 25.75, "spread": -3.5, "opponent": "GB"}, "_meta": meta}


def cfg():
    from fantasy_sim import config
    return config.ODDS_CREDIT_RESERVE, config.ODDS_FETCH_COST, config.ODDS_REUSE_HOURS


class TestTheConstants(unittest.TestCase):
    def test_they_exist_and_each_cites_its_source(self):
        with open(os.path.join(REPO, "fantasy_sim", "config.py"), encoding="utf-8") as fh:
            src = fh.read()
        for name in ("ODDS_CREDIT_RESERVE", "ODDS_FETCH_COST", "ODDS_REUSE_HOURS"):
            i = src.index(name + " =")
            self.assertIn("#", src[max(0, i - 900):i], f"{name} carries a sourcing comment (rule 5)")
        reserve, cost, hours = cfg()
        self.assertEqual(cost, 2, "spreads + totals, one region")
        self.assertGreater(reserve, cost)
        self.assertGreater(hours, 0)


class TestTheDecision(unittest.TestCase):
    """sync.odds_fetch_decision(week, existing, official, sharp, remaining, now) -> (action, why),
    action in fetch / reuse / hold."""

    def decide(self, existing=None, official=False, sharp=False, remaining=400, week=6):
        return sync.odds_fetch_decision(week, existing, official=official, sharp=sharp,
                                        remaining=remaining, now=NOW)[0]

    def test_fresh_lines_for_this_week_are_reused(self):
        self.assertEqual(self.decide(lines(hours_ago=2)), "reuse")

    def test_what_is_never_reused(self):
        _r, _c, hours = cfg()
        self.assertEqual(self.decide(lines(hours_ago=hours + 0.5)), "fetch", "too old")
        self.assertEqual(self.decide(lines(week=5)), "fetch", "last week's lines")
        self.assertEqual(self.decide(lines(stale=True)), "fetch", "already kept from a failed fetch")
        self.assertEqual(self.decide(lines(source="fallback_api_error")), "fetch", "not real lines")
        self.assertEqual(self.decide(lines(hours_ago=-3)), "fetch", "stamped in the future")
        self.assertEqual(self.decide(None), "fetch", "nothing on disk")

    def test_an_official_run_or_a_sharp_poll_always_fetches_fresh(self):
        self.assertEqual(self.decide(lines(hours_ago=1), official=True), "fetch")
        self.assertEqual(self.decide(lines(hours_ago=1), sharp=True), "fetch")

    def test_nothing_but_an_official_run_spends_the_reserve(self):
        reserve, cost, _h = cfg()
        self.assertEqual(self.decide(remaining=reserve + cost), "fetch")
        self.assertEqual(self.decide(remaining=reserve + cost - 1), "hold")
        self.assertEqual(self.decide(remaining=reserve + cost - 1, sharp=True), "hold")
        self.assertEqual(self.decide(remaining=reserve + cost - 1, official=True), "fetch")

    def test_nobody_spends_what_is_not_there(self):
        _r, cost, _h = cfg()
        self.assertEqual(self.decide(remaining=cost - 1, official=True), "hold")
        self.assertEqual(self.decide(remaining=0, official=True), "hold")

    def test_an_unknown_balance_spends_only_for_an_official_run(self):
        self.assertEqual(self.decide(remaining=None), "hold")
        self.assertEqual(self.decide(remaining=None, official=True), "fetch")

    def test_the_hold_says_why(self):
        reserve, _c, _h = cfg()
        _a, why = sync.odds_fetch_decision(6, None, official=False, sharp=False, remaining=reserve, now=NOW)
        self.assertIn(str(reserve), why)
        self.assertIn("official", why)

    def test_a_month_of_syncs_never_reaches_zero(self):
        """The guarantee, as a property: however many non-official syncs run, at any hours,
        the balance never drops below the reserve, and the official runs still have theirs."""
        reserve, cost, _h = cfg()
        import random
        rng = random.Random(20260930)
        balance, on_disk = 500, None
        t = _real_dt.datetime(2026, 10, 1)
        for _ in range(3000):
            t += _real_dt.timedelta(minutes=rng.randint(1, 30))
            official = rng.random() < 0.004
            action, _w = sync.odds_fetch_decision(6, on_disk, official=official, sharp=rng.random() < 0.1,
                                                  remaining=balance, now=t)
            if action == "fetch":
                balance -= cost
                on_disk = {"_meta": {"week": 6, "source": "the_odds_api", "fetched_at": t.isoformat()}}
            if not official:
                self.assertGreaterEqual(balance, reserve)
        self.assertGreaterEqual(balance, 0)
        for _ in range(reserve // cost):     # the reserve covers this many official runs
            action, _w = sync.odds_fetch_decision(6, on_disk, official=True, sharp=False, remaining=balance, now=t)
            self.assertEqual(action, "fetch")
            balance -= cost
        self.assertGreaterEqual(balance, 0)


def _reply(status=200, body=None, headers=None):
    m = MagicMock()
    m.status_code = status
    m.headers = headers or {}
    m.json.return_value = body if body is not None else []
    m.raise_for_status.return_value = None
    return m


class TestTheFreeCheck(unittest.TestCase):
    def test_the_key_check_reads_the_free_endpoint(self):
        from webui import sync as websync
        seen = []

        def get(url, params=None, timeout=None):
            seen.append(url)
            return _reply(200, headers={"x-requests-remaining": "300", "x-requests-used": "200"})
        self.assertEqual(sync.verify_odds_key("k" * 32, fetch=get)[0], "ok")
        self.assertEqual(websync.probe_key("k" * 32, fetch=get)["verdict"], "ok")
        for url in seen:
            self.assertTrue(url.rstrip("/").endswith("/v4/sports"), f"{url} is a paid endpoint")

    def test_the_free_endpoint_at_zero_is_out_of_credits(self):
        """Observed 2026-09-30: at a zero balance /v4/sports still answers 200."""
        from webui import sync as websync
        get = lambda url, params=None, timeout=None: _reply(200, headers={"x-requests-remaining": "0", "x-requests-used": "500"})  # noqa: E731
        self.assertEqual(sync.verify_odds_key("k" * 32, fetch=get)[0], "exhausted")
        self.assertEqual(websync.probe_key("k" * 32, fetch=get)["verdict"], "exhausted")

    def test_the_balance_reader(self):
        ok = lambda url, params=None, timeout=None: _reply(200, headers={"x-requests-remaining": "321"})  # noqa: E731
        self.assertEqual(sync.odds_credit_balance("k" * 32, fetch=ok), 321)
        none = lambda url, params=None, timeout=None: _reply(200)  # noqa: E731
        self.assertIsNone(sync.odds_credit_balance("k" * 32, fetch=none))

        def boom(*a, **k):
            raise OSError("down")
        self.assertIsNone(sync.odds_credit_balance("k" * 32, fetch=boom))
        self.assertIsNone(sync.odds_credit_balance("", fetch=ok))


class TestTheFetchHonoursTheBudget(unittest.TestCase):
    GAME = {"home_team": "Detroit Lions", "away_team": "Green Bay Packers",
            "commence_time": "2026-10-11T17:00:00Z",
            "bookmakers": [{"key": "draftkings", "markets": [
                {"key": "totals", "outcomes": [{"name": "Over", "point": 48.0}]},
                {"key": "spreads", "outcomes": [{"name": "Detroit Lions", "point": -3.5},
                                                {"name": "Green Bay Packers", "point": 3.5}]}]}]}

    def run_fetch(self, on_disk, official, remaining):
        paid, saved = [], {}

        def get(url, params=None, timeout=None, **kw):
            if url.rstrip("/").endswith("/v4/sports"):
                return _reply(200, headers={"x-requests-remaining": str(remaining)} if remaining is not None else {})
            if "the-odds-api" in url:
                paid.append(url)
                return _reply(200, [self.GAME], {"x-requests-last": "2", "x-requests-remaining": str((remaining or 0) - 2)})
            raise OSError("weather is not under test")

        with tempfile.TemporaryDirectory() as tmp:
            vf = os.path.join(tmp, "vegas_totals.json")
            if on_disk is not None:
                with open(vf, "w", encoding="utf-8") as fh:
                    json.dump(on_disk, fh)
            with patch.object(sync, "datetime", _Now), \
                    patch.object(sync, "ODDS_API_KEY", "k" * 32), \
                    patch.object(sync, "VEGAS_FILE", vf), \
                    patch.object(sync.requests, "get", side_effect=get), \
                    patch.object(sync, "save_json", side_effect=lambda p, d, *a, **k: saved.__setitem__(os.path.basename(p), d)):
                out = sync.fetch_vegas_implied_totals(6, week_schedule={"DET": "GB", "GB": "DET"},
                                                      budget={"official": official})
        return out, paid, saved

    def test_fresh_lines_are_reused_and_nothing_is_paid(self):
        out, paid, _s = self.run_fetch(lines(hours_ago=1), official=False, remaining=400)
        self.assertEqual(paid, [])
        self.assertEqual(out["DET"]["total"], 25.75)

    def test_below_the_reserve_nothing_is_paid_and_the_real_lines_are_kept(self):
        reserve, _c, _h = cfg()
        out, paid, saved = self.run_fetch(lines(hours_ago=20), official=False, remaining=reserve)
        self.assertEqual(paid, [])
        self.assertEqual(out["DET"]["total"], 25.75, "this week's real lines, not the flat 21.5")
        self.assertIn("credit", saved["vegas_totals.json"]["_meta"]["stale_reason"])

    def test_an_official_run_pays_once_even_below_the_reserve(self):
        reserve, _c, _h = cfg()
        _o, paid, saved = self.run_fetch(lines(hours_ago=1), official=True, remaining=reserve)
        self.assertEqual(len(paid), 1)
        self.assertEqual(saved["vegas_totals.json"]["_meta"]["source"], "the_odds_api")


class TestEveryCallerSaysWhetherItIsOfficial(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_the_sync_always_passes_a_budget(self):
        src = self.read("fantasy_sim", "sync.py")
        calls = [i for i in range(len(src)) if src.startswith("fetch_vegas_implied_totals(", i)
                 and not src[max(0, i - 4):i].endswith("def ")]
        self.assertTrue(calls)
        for i in calls:
            self.assertIn("budget=", src[i:src.index(")", src.index("week_schedule", i)) + 40],
                          "the sync's own call must carry the budget -- without it nothing is held back")

    def test_run_sync_has_an_official_flag(self):
        import scripts.run_sync as rs
        with patch.object(rs, "verify_odds_key", return_value=("ok", "")), \
                patch.object(rs, "sync_all") as sa:
            rs.main(["--official"])
            self.assertTrue(sa.call_args.kwargs.get("official"))
            rs.main([])
            self.assertFalse(sa.call_args.kwargs.get("official"))

    def test_the_official_workflow_and_the_canonical_report_say_so(self):
        self.assertTrue("python -m scripts.run_sync --official" in self.read(".github", "workflows", "canonical-run.yml"),
                        "canonical-run's sync is the official one")
        self.assertTrue("sync_all(official=canonical)" in self.read("fantasy_sim", "weekly_report.py"),
                        "weekly_report --canonical syncs as an official run")


if __name__ == "__main__":
    unittest.main()

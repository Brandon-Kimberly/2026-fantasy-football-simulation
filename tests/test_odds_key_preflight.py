"""H5: a dead odds key is a 401 that reads exactly like the API being down.

On 2026-09-23 the Bash tool held a **pre-rotation** `ODDS_API_KEY` for an entire session,
across two `setx` rotations and a terminal reset — the process environment and the Windows
User scope held different 32-character values, and only the User-scope one returned 200.
Every sync in that session 401'd and reported "VEGAS FALLBACK: odds API request failed",
which is the same message the sync prints when the-odds-api is genuinely down.

F67 (C3) made that non-destructive: real same-week lines are now kept rather than
flattened. This item makes it **deliberate** rather than merely survivable — the sync
should say the key was REJECTED, say what to do about it on this machine, and stop before
writing anything unless the operator asks for the fallback on purpose.

**ONLY A 401 STOPS.** A 5xx, a timeout, or an unreachable host is a transient, and the
scheduled runner must not be blocked by one — it warns and proceeds under F67's
keep-the-good-file rule. An ABSENT key is the documented no-key state (`config.ODDS_API_KEY`
is optional by design) and likewise warns and proceeds; refusing there would break the
preseason path and every hermetic CI run.

Written before `verify_odds_key` existed and confirmed failing (rule 1).
"""
import unittest


class _Resp:
    def __init__(self, status):
        self.status_code = status

    def json(self):
        return []


def _fetch(status):
    def go(_url, params=None, timeout=None):
        return _Resp(status)
    return go


class TestTheVerdicts(unittest.TestCase):
    def test_a_200_is_ok(self):
        from fantasy_sim.sync import verify_odds_key
        v, _d = verify_odds_key("k" * 32, fetch=_fetch(200))
        self.assertEqual(v, "ok")

    def test_a_401_is_rejected(self):
        from fantasy_sim.sync import verify_odds_key
        v, _d = verify_odds_key("k" * 32, fetch=_fetch(401))
        self.assertEqual(v, "rejected")

    def test_a_403_is_also_rejected(self):
        """the-odds-api answers an out-of-quota key with 401; a 403 is the same class of
        'this key will not work', and treating it as transient would loop forever."""
        from fantasy_sim.sync import verify_odds_key
        v, _d = verify_odds_key("k" * 32, fetch=_fetch(403))
        self.assertEqual(v, "rejected")

    def test_a_500_is_unreachable_not_rejected(self):
        """The runner must not be stopped by the API having a bad afternoon."""
        from fantasy_sim.sync import verify_odds_key
        v, _d = verify_odds_key("k" * 32, fetch=_fetch(500))
        self.assertEqual(v, "unreachable")

    def test_a_raising_fetch_is_unreachable(self):
        from fantasy_sim.sync import verify_odds_key

        def boom(*_a, **_k):
            raise ConnectionError("down")
        v, _d = verify_odds_key("k" * 32, fetch=boom)
        self.assertEqual(v, "unreachable")

    def test_an_empty_key_is_absent_and_is_not_an_error(self):
        """config.ODDS_API_KEY is optional by design; the preseason path and hermetic CI
        both run without it."""
        from fantasy_sim.sync import verify_odds_key
        for key in ("", None, "   "):
            v, _d = verify_odds_key(key, fetch=_fetch(200))
            self.assertEqual(v, "absent", f"{key!r} should read as absent")

    def test_it_never_returns_the_key_in_the_detail(self):
        """The detail is printed and may be pasted into a chat or an issue."""
        from fantasy_sim.sync import verify_odds_key
        secret = "abc123def456abc123def456abc123de"
        for status in (200, 401, 500):
            _v, detail = verify_odds_key(secret, fetch=_fetch(status))
            self.assertNotIn(secret, str(detail))


class TestTheOperatorGuidance(unittest.TestCase):
    """The message has to name the actual remedy on this machine, because the failure is
    indistinguishable from the API being down and the wrong diagnosis wastes a session."""

    def test_a_rejection_names_the_user_scope_lookup(self):
        from fantasy_sim.sync import verify_odds_key
        _v, detail = verify_odds_key("k" * 32, fetch=_fetch(401))
        self.assertIn("User", detail)
        self.assertIn("GetEnvironmentVariable", detail)

    def test_a_rejection_says_the_shell_may_be_stale(self):
        from fantasy_sim.sync import verify_odds_key
        _v, detail = verify_odds_key("k" * 32, fetch=_fetch(401))
        self.assertIn("stale", detail.lower())

    def test_an_unreachable_message_does_not_blame_the_key(self):
        from fantasy_sim.sync import verify_odds_key
        _v, detail = verify_odds_key("k" * 32, fetch=_fetch(500))
        self.assertNotIn("rejected", detail.lower())


class TestTheGate(unittest.TestCase):
    """`should_stop_for_odds_key` is the decision `run_sync` makes. Pure, so the CLI stays
    a thin wrapper and the policy is testable."""

    def test_rejected_stops(self):
        from fantasy_sim.sync import should_stop_for_odds_key
        self.assertTrue(should_stop_for_odds_key("rejected", allow_fallback=False))

    def test_rejected_does_not_stop_when_the_operator_opts_in(self):
        from fantasy_sim.sync import should_stop_for_odds_key
        self.assertFalse(should_stop_for_odds_key("rejected", allow_fallback=True))

    def test_nothing_else_stops(self):
        from fantasy_sim.sync import should_stop_for_odds_key
        for verdict in ("ok", "unreachable", "absent"):
            self.assertFalse(should_stop_for_odds_key(verdict, allow_fallback=False),
                             f"{verdict} must not block a scheduled run")


if __name__ == "__main__":
    unittest.main()

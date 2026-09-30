"""
tests.test_webui_chat -- a read-only chat about the league, on the owner's Claude subscription
(owner request 2026-09-30).

The owner wants to ask the questions they ask here -- waivers, lineups, trades -- from inside
the web UI, with two hard rules: nothing it does may change the code, the data or the
simulation, and it may cost nothing beyond the Claude subscription they already have.

So the chat runs Claude Code headless (`claude -p`, signed in with the subscription -- no API
key), and read-only is structural, not a request:
  * every built-in tool is off (`--tools ""`): no shell, no file edits, no web;
  * the only tools are this project's own MCP server (webui.chat_tools), and nothing else
    (`--strict-mcp-config`, `--setting-sources ""`, `--permission-mode dontAsk`);
  * those tools read and search a COPY of the league data outside the repository, and run the
    Tools page's allowlisted analysis tools in that copy -- never sync, never the engine runs,
    never a tool that appends to a tracked log;
  * one analysis run at a time, and none while the site or anything else runs one (R1);
  * real names stay on this machine: what the owner types is mapped to the pseudonyms before
    it leaves, and the answer is mapped back on the page.
Written before the modules exist.
"""
import json
import os
import tempfile
import threading
import time
import unittest

try:
    import flask  # noqa: F401 -- availability probe
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.live import LiveBoard
    from webui.names import Overlay
    from webui.paths import Root
    from webui.settings import Settings
    from tests.test_webui_launch import FakeRunner
    from tests.test_webui_modes import DEV_TERMS, visible_text
    from tests.test_webui_objects import QF, plant
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTheCommandLine(unittest.TestCase):
    def argv(self, **kw):
        from webui.chat import build_argv
        base = dict(claude="claude", prompt="hello", mcp_config="C:/x/mcp.json", system_prompt="brief", model="fast")
        base.update(kw)
        return build_argv(**base)

    def test_no_built_in_tool_and_only_our_server(self):
        a = self.argv()
        self.assertEqual(a[:3], ["claude", "-p", "hello"])
        self.assertEqual(a[a.index("--tools") + 1], "", "every built-in tool off: no shell, no edits, no web")
        self.assertIn("--strict-mcp-config", a)
        self.assertEqual(a[a.index("--mcp-config") + 1], "C:/x/mcp.json")
        self.assertEqual(a[a.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(a[a.index("--setting-sources") + 1], "", "no user or project settings: no hooks, no allow rules")
        self.assertEqual(a[a.index("--allowedTools") + 1], "mcp__syndicate")
        self.assertEqual(a[a.index("--system-prompt") + 1], "brief")
        self.assertEqual(a[a.index("--output-format") + 1], "stream-json")
        for bad in ("--dangerously-skip-permissions", "bypassPermissions", "acceptEdits", "--add-dir", "--bare"):
            self.assertNotIn(bad, a)

    def test_the_model_and_the_conversation(self):
        a = self.argv(model="deep", session_id="abc-123")
        self.assertEqual(a[a.index("--model") + 1], "opus")
        self.assertEqual(a[a.index("--resume") + 1], "abc-123")
        self.assertEqual(self.argv(model="fast")[self.argv(model="fast").index("--model") + 1], "sonnet")
        self.assertNotIn("--resume", self.argv())

    def test_no_api_key_reaches_it(self):
        from webui.chat import child_env
        env = child_env({"ANTHROPIC_API_KEY": "sk-x", "PATH": "p", "SLEEPER_LEAGUE_ID": "1"})
        self.assertNotIn("ANTHROPIC_API_KEY", env, "the subscription, never a billed key")
        self.assertEqual(env["PATH"], "p")
        self.assertGreaterEqual(int(env["MCP_TOOL_TIMEOUT"]), 20 * 60 * 1000, "a paired simulation takes minutes")


class TestTheStream(unittest.TestCase):
    LINES = [
        {"type": "system", "subtype": "init", "session_id": "s-1", "tools": []},
        {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Claim "}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Allen."}}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__syndicate__run_tool",
                                                      "input": {"name": "waiver_targets"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Claim Allen.", "session_id": "s-1"},
    ]

    def test_the_events(self):
        from webui.chat import parse_line
        ev = [e for line in self.LINES for e in parse_line(json.dumps(line))]
        kinds = [e["kind"] for e in ev]
        self.assertEqual(kinds, ["session", "text", "text", "tool", "tool_done", "done"])
        self.assertEqual(ev[0]["session_id"], "s-1")
        self.assertEqual(ev[3]["name"], "run_tool")
        self.assertEqual(ev[3]["input"], {"name": "waiver_targets"})
        self.assertEqual(ev[-1]["text"], "Claim Allen.")
        self.assertEqual(parse_line("not json"), [])


class TestNamesStayHome(unittest.TestCase):
    def test_both_ways(self):
        from webui.chat import pseudonymize
        mapping = {"Quantum Ferrets": "Real Team One", "Cosmic Badgers": "Made Up Two"}
        self.assertEqual(pseudonymize("Should I trade with made up two or Real Team One?", mapping),
                         "Should I trade with Cosmic Badgers or Quantum Ferrets?")
        self.assertEqual(pseudonymize("nothing to map", {}), "nothing to map")


def _workspace():
    td = tempfile.mkdtemp()
    for d in ("current", "logs", "decisions", "weeks"):
        os.makedirs(os.path.join(td, "data", d), exist_ok=True)
    with open(os.path.join(td, "data", "current", "league_state.json"), "w", encoding="utf-8") as fh:
        json.dump({"current_week": 4}, fh)
    with open(os.path.join(td, "data", "current", "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("alpha\nBraelon Allen is the RB1\ngamma\n")
    return td


class TestTheToolServer(unittest.TestCase):
    def setUp(self):
        self.ws = _workspace()
        self.runs = []

        def runner(argv, cwd, env, timeout):
            self.runs.append((argv, cwd, env))
            return 0, "Quantum Ferrets -- week 4\n  1 Braelon Allen RB\n"
        from webui.chat_tools import ToolServer
        self.srv = ToolServer(self.ws, REPO, runner=runner, scan=lambda: [], real_lock=None)

    def rpc(self, method, params=None, id=1):
        return self.srv.handle({"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}})

    def call(self, name, **args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        text = "".join(c.get("text", "") for c in r["result"]["content"])
        return r["result"].get("isError", False), text

    def test_the_handshake_and_the_tool_list(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertIsNone(self.srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = {t["name"] for t in self.rpc("tools/list")["result"]["tools"]}
        self.assertEqual(names, {"league_snapshot", "list_data", "read_data", "search_data", "list_tools", "run_tool"})
        self.assertEqual(self.rpc("no/such")["error"]["code"], -32601)

    def test_reading_stays_inside_the_copy(self):
        err, text = self.call("read_data", path="current/notes.txt")
        self.assertFalse(err)
        self.assertIn("Braelon Allen", text)
        for bad in ("../secret.txt", "/etc/passwd", "C:/Windows/win.ini", "local/webui/settings.json", "current/../../x"):
            with self.subTest(path=bad):
                err, text = self.call("read_data", path=bad)
                self.assertTrue(err, bad)
        err, text = self.call("list_data", path="current")
        self.assertIn("league_state.json", text)
        err, text = self.call("search_data", pattern="RB1")
        self.assertIn("current/notes.txt", text)

    def test_only_the_allowlisted_analysis_runs(self):
        for bad in ("run_simulation", "weekly_report", "run_sync", "no_such_tool", "../x"):
            with self.subTest(tool=bad):
                err, _t = self.call("run_tool", name=bad)
                self.assertTrue(err, bad)
        self.assertEqual(self.runs, [])
        err, text = self.call("run_tool", name="waiver_targets", options={"team": QF if HAS_FLASK else "Quantum Ferrets", "top": "5", "canonical": "1"})
        self.assertFalse(err, text)
        self.assertIn("Braelon Allen", text)
        argv, cwd, env = self.runs[0]
        self.assertEqual(argv[1:3], ["-m", "scripts.waiver_targets"])
        self.assertIn("--top", argv)
        self.assertNotIn("--canonical", argv, "a chat run is never the week's official run")
        self.assertEqual(os.path.normcase(cwd), os.path.normcase(self.ws), "it runs in the copy, so every write lands there")
        self.assertIn(os.path.normcase(REPO), os.path.normcase(env["PYTHONPATH"]))

    def test_one_run_at_a_time(self):
        from webui.chat_tools import ToolServer
        busy = ToolServer(self.ws, REPO, runner=lambda *a, **k: (0, ""), scan=lambda: [{"pid": 9, "cmdline": "py -m scripts.run_simulation"}], real_lock=None)
        r = busy.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_tool", "arguments": {"name": "waiver_targets"}}})
        self.assertTrue(r["result"]["isError"])
        self.assertIn("one at a time", "".join(c["text"] for c in r["result"]["content"]))


class _FakeProc:
    def __init__(self, lines, hold=None):
        self._lines, self._hold, self.pid, self.returncode = lines, hold, 4242, None

    @property
    def stdout(self):
        def gen():
            for ln in self._lines:
                if self._hold is not None:
                    self._hold.wait(5)
                yield (json.dumps(ln) + "\n").encode("utf-8")
        return gen()

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestTheService(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)
        self.base = tempfile.mkdtemp()
        self.calls = []
        self.hold = None

        def popen(argv, **kw):
            self.calls.append((argv, kw))
            sid = "s-%d" % len(self.calls)
            return _FakeProc([{"type": "system", "subtype": "init", "session_id": sid},
                              {"type": "result", "subtype": "success", "is_error": False, "result": "Start **Gibbs**.", "session_id": sid}],
                             hold=self.hold)
        from webui.chat import ChatService
        self.svc = ChatService(self.root, Overlay({QF: "Real Team One"}), claude="claude", popen=popen, base=self.base)

    def tearDown(self):
        self.td.cleanup()

    def wait_done(self, cid):
        for _ in range(200):
            if not self.svc.state(cid)["running"]:
                return
            time.sleep(0.02)
        self.fail("turn never finished")

    def test_a_turn_and_the_next(self):
        cid = self.svc.new()
        self.svc.send(cid, "Should Real Team One start Gibbs?", "fast")
        self.wait_done(cid)
        conv = self.svc.store.get(cid)
        self.assertEqual([m["role"] for m in conv["messages"]], ["user", "assistant"])
        self.assertEqual(conv["session_id"], "s-1")
        argv, kw = self.calls[0]
        self.assertIn(QF, argv[2], "the pseudonym goes out")
        self.assertNotIn("Real Team One", argv[2])
        self.assertFalse(os.path.normcase(os.path.abspath(kw["cwd"])).startswith(os.path.normcase(REPO)),
                         "outside the repository: no project instructions, no path into the code")
        self.svc.send(cid, "and next week?", "fast")
        self.wait_done(cid)
        argv2, _kw = self.calls[1]
        self.assertEqual(argv2[argv2.index("--resume") + 1], "s-1")
        st = self.svc.state(cid)
        self.assertIn("<strong>Gibbs</strong>", st["messages"][-1]["html"])

    def test_the_copy_has_no_private_folder(self):
        ws = self.svc.workspace()
        self.assertTrue(os.path.isdir(os.path.join(ws, "data", "current")))
        self.assertFalse(os.path.exists(os.path.join(ws, "data", "local")))
        self.assertFalse(os.path.normcase(os.path.abspath(ws)).startswith(os.path.normcase(os.path.abspath(self.root.root))))
        cfg = json.load(open(self.svc.mcp_config(), encoding="utf-8"))
        server = cfg["mcpServers"]["syndicate"]
        self.assertEqual(server["args"][:2], ["-m", "webui.chat_tools"])
        self.assertIn(ws, server["args"])

    def test_one_turn_at_a_time(self):
        from webui.chat import Busy
        self.hold = threading.Event()
        cid = self.svc.new()
        self.svc.send(cid, "first", "fast")
        with self.assertRaises(Busy):
            self.svc.send(cid, "second", "fast")
        self.hold.set()
        self.wait_done(cid)


@unittest.skipUnless(HAS_FLASK, "flask not installed")
class TestThePage(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        plant(self.td.name)
        self.root = Root(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def client(self, mode):
        st = Settings(self.root)
        st.set_mode(mode)
        app = create_app(self.root, runner=FakeRunner(), csrf_token="tok", settings=st,
                         live=LiveBoard(self.root, MY_TEAM, league_id=None, fetch=None))
        app.testing = True
        return app.test_client()

    def test_both_views(self):
        for mode in ("dev", "simple"):
            with self.subTest(mode=mode):
                c = self.client(mode)
                body = c.get("/chat").get_data(as_text=True)
                self.assertIn('href="/chat"', body, "Chat is in the navigation")
                self.assertIn('id="chat-input"', body)
                if mode == "simple":
                    self.assertEqual([t for t in DEV_TERMS if t in visible_text(body)], [])

    def test_posts_need_the_token(self):
        c = self.client("dev")
        self.assertEqual(c.post("/chat/new").status_code, 403)
        self.assertEqual(c.post("/chat/new", data={"_csrf": "tok"}).status_code in (200, 302), True)


class TestMarkdown(unittest.TestCase):
    def test_safe_and_useful(self):
        from webui.md import render
        html = render("**Claim** Braelon Allen\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n`code` <script>x</script> [x](javascript:alert(1)) [y](https://sleeper.com)")
        self.assertIn("<strong>Claim</strong>", html)
        self.assertIn("<li>one</li>", html)
        self.assertIn("<table", html)
        self.assertIn("<code>code</code>", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn('href="javascript', html)
        self.assertIn('href="https://sleeper.com"', html)


if __name__ == "__main__":
    unittest.main()

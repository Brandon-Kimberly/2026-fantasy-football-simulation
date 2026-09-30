"""webui.chat -- a read-only chat about the league, on the owner's Claude subscription
(owner request 2026-09-30).

Each message runs Claude Code headless (`claude -p`), signed in with the subscription the
owner already pays for -- no API key ever reaches it, so nothing is billed beyond the plan. The
next message resumes the same Claude session, so a conversation carries on.

Read-only is structural (tests.test_webui_chat):
  * every built-in tool is off (`--tools ""`): no shell, no file edits, no web;
  * the only tools are webui.chat_tools, an MCP server of this project's own, and nothing else
    (`--strict-mcp-config`), with no user or project settings loaded (no hooks, no allow
    rules) and `--permission-mode dontAsk`, which denies anything not allowed;
  * those tools read a COPY of the league data outside the repository and run the Tools page's
    allowlisted analysis tools in that copy, one at a time;
  * Claude Code starts in a folder outside the repository, so no project instructions load.
Real names stay on this machine: what the owner types is mapped to the pseudonyms before it
leaves, and the page maps the answer back.

Conversations are kept under data/local/webui/chat/ (private, never committed).
"""
import datetime as _dt
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading

from webui import md

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIEFING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_briefing.md")
MODELS = {"fast": "sonnet", "deep": "opus"}
TOOL_PREFIX = "mcp__syndicate__"
CID_RE = re.compile(r"^[A-Za-z0-9_\-]{6,80}$")


class Busy(RuntimeError):
    """A turn is already running."""


def build_argv(claude, prompt, mcp_config, system_prompt, model, session_id=None):
    """The one command line a chat turn runs. Pinned by tests: every built-in tool off, only our
    MCP server, no settings, deny-by-default permissions."""
    argv = [claude, "-p", prompt,
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--tools", "",
            "--strict-mcp-config", "--mcp-config", mcp_config,
            "--allowedTools", "mcp__syndicate",
            "--permission-mode", "dontAsk",
            "--setting-sources", "",
            "--system-prompt", system_prompt,
            "--model", MODELS.get(model, MODELS["deep"])]
    if session_id:
        argv += ["--resume", session_id]
    return argv


def child_env(base):
    """The environment for Claude Code: the server's, less any API key (the subscription is
    the only way it may run), with room for a paired simulation inside one tool call."""
    env = {k: v for k, v in dict(base).items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["MCP_TOOL_TIMEOUT"] = str(26 * 60 * 1000)
    env["MAX_MCP_OUTPUT_TOKENS"] = "25000"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def parse_line(line):
    """The events in one line of Claude Code's stream-json output."""
    try:
        e = json.loads(line)
    except (ValueError, TypeError):
        return []
    if not isinstance(e, dict):
        return []
    t = e.get("type")
    if t == "system" and e.get("subtype") == "init":
        return [{"kind": "session", "session_id": e.get("session_id")}]
    if t == "stream_event":
        ev = e.get("event") or {}
        if ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
            return [{"kind": "text", "delta": ev["delta"].get("text", "")}]
        if ev.get("type") == "content_block_start" and (ev.get("content_block") or {}).get("type") == "text":
            return [{"kind": "block"}]
        return []
    if t == "assistant":
        out = []
        for c in (e.get("message") or {}).get("content") or []:
            if c.get("type") == "tool_use":
                name = str(c.get("name") or "")
                out.append({"kind": "tool", "id": c.get("id"), "input": c.get("input") or {},
                            "name": name[len(TOOL_PREFIX):] if name.startswith(TOOL_PREFIX) else name})
        return out
    if t == "user":
        return [{"kind": "tool_done", "id": c.get("tool_use_id"), "error": bool(c.get("is_error"))}
                for c in (e.get("message") or {}).get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_result"]
    if t == "result":
        return [{"kind": "done", "text": e.get("result") or "", "session_id": e.get("session_id"),
                 "error": bool(e.get("is_error")) or e.get("subtype") not in (None, "success")}]
    return []


def pseudonymize(text, mapping):
    """`text` with every real team name (any case) replaced by its pseudonym. `mapping` is the
    overlay's {pseudonym: real name}."""
    s = text or ""
    for fict, real in sorted((mapping or {}).items(), key=lambda kv: -len(kv[1])):
        if real:
            s = re.sub(r"(?<!\w)" + re.escape(real) + r"(?!\w)", fict, s, flags=re.I)
    return s


def tool_label(name, inp):
    """What a tool call is doing, in words."""
    if name == "run_tool":
        from webui.tools import humanize
        return "Running " + humanize(str((inp or {}).get("name") or "a tool")).lower()
    return {"league_snapshot": "Reading the league", "list_data": "Looking through the data",
            "read_data": "Reading " + str((inp or {}).get("path") or "a file"),
            "search_data": "Searching the data", "list_tools": "Checking the tools"}.get(name, name)


class ChatStore:
    def __init__(self, root):
        self.dir = os.path.join(root.local, "webui", "chat")

    def _p(self, cid):
        if not CID_RE.match(cid or ""):
            raise KeyError(cid)
        return os.path.join(self.dir, cid + ".json")

    def create(self):
        now = _dt.datetime.now(_dt.timezone.utc)
        cid = now.strftime("%Y%m%dT%H%M%S") + "_" + secrets.token_hex(3)
        conv = {"id": cid, "title": "New chat", "created": now.isoformat(timespec="seconds"), "session_id": None, "messages": []}
        self.save(conv)
        return cid

    def get(self, cid):
        try:
            with open(self._p(cid), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def save(self, conv):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._p(conv["id"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(conv, fh, indent=1)
        os.replace(tmp, self._p(conv["id"]))

    def delete(self, cid):
        try:
            os.remove(self._p(cid))
        except (OSError, KeyError):
            pass

    def list(self):
        out = []
        try:
            names = os.listdir(self.dir)
        except OSError:
            return out
        for n in names:
            if n.endswith(".json"):
                c = self.get(n[:-5])
                if c:
                    out.append({"id": c["id"], "title": c.get("title") or "New chat", "created": c.get("created"),
                                "n": len(c.get("messages") or [])})
        out.sort(key=lambda c: c["id"], reverse=True)
        return out


class ChatService:
    def __init__(self, root, overlay=None, claude="auto", popen=subprocess.Popen, base=None):
        self.root = root
        self.overlay = overlay
        self.claude = shutil.which("claude") if claude == "auto" else claude
        self.popen = popen
        self.base = base or os.path.join(tempfile.gettempdir(), "syndicate-chat")
        self.store = ChatStore(root)
        self._busy = threading.Lock()
        self._turn = None               # {cid, text, tools, proc, started}
        self._ws = None
        self._ws_made = None

    @property
    def available(self):
        return bool(self.claude)

    # ------------------------------------------------------------ the data copy
    def _manifest_time(self):
        return self.root.mtime("current/sync_manifest.json") or self.root.mtime("current/league_standings.json") or 0

    def workspace(self):
        """The chat's copy of the league data, outside the repository; made again after a sync."""
        from webui import sandbox
        if self._ws and os.path.isdir(self._ws) and (self._ws_made or 0) >= self._manifest_time():
            return self._ws
        os.makedirs(self.base, exist_ok=True)
        old = self._ws
        made = _dt.datetime.now().timestamp()
        self._ws = sandbox.create(self.root, base=self.base).root
        self._ws_made = made
        if old:
            try:
                sandbox.discard(old)
            except (OSError, ValueError):
                pass
        return self._ws

    def mcp_config(self):
        ws = self.workspace()
        path = os.path.join(self.base, "mcp.json")
        cfg = {"mcpServers": {"syndicate": {"type": "stdio", "command": sys.executable,
                                            "args": ["-m", "webui.chat_tools", "--workspace", ws],
                                            "env": {"PYTHONPATH": REPO, "PYTHONIOENCODING": "utf-8"}}}}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=1)
        return path

    # ------------------------------------------------------------ conversations
    def new(self):
        return self.store.create()

    def briefing(self):
        try:
            with open(BRIEFING, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return "You answer questions about a fantasy football league, read-only."

    def _context(self):
        now = _dt.datetime.now().strftime("%A %b %d %Y, %I:%M %p").replace(" 0", " ")
        state = self.root.read_json("current/league_state.json", {}) or {}
        man = self.root.read_json("current/sync_manifest.json", {}) or {}
        return (f"[Now: {now} local time. NFL week {state.get('current_week', '?')}. "
                f"League data last refreshed {man.get('finished_at', 'unknown')} (UTC).]")

    def send(self, cid, text, model="deep"):
        conv = self.store.get(cid)
        if conv is None:
            raise KeyError(cid)
        text = (text or "").strip()
        if not text:
            raise ValueError("empty message")
        if not self.available:
            raise RuntimeError("Claude Code is not installed on this computer")
        if not self._busy.acquire(blocking=False):
            raise Busy("the chat is already answering -- one question at a time")
        try:
            conv["messages"].append({"role": "user", "text": text, "at": _dt.datetime.now().isoformat(timespec="seconds")})
            if conv.get("title") in (None, "", "New chat"):
                conv["title"] = text[:70] + ("…" if len(text) > 70 else "")
            self.store.save(conv)
            mapping = getattr(self.overlay, "mapping", {}) or {}
            prompt = self._context() + "\n\n" + pseudonymize(text, mapping)
            argv = build_argv(self.claude, prompt, self.mcp_config(), self.briefing(), model, conv.get("session_id"))
            proc = self.popen(argv, cwd=self.base, env=child_env(os.environ), stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            self._turn = {"cid": cid, "text": "", "tools": [], "proc": proc, "model": model,
                          "started": _dt.datetime.now().timestamp()}
            threading.Thread(target=self._pump, args=(cid, proc, model), daemon=True).start()
        except BaseException:
            self._turn = None
            self._busy.release()
            raise

    def _pump(self, cid, proc, model):
        turn = self._turn
        final, session, err = None, None, False
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                for ev in parse_line(line):
                    k = ev["kind"]
                    if k == "session":
                        session = ev.get("session_id") or session
                    elif k == "block":
                        if turn["text"] and not turn["text"].endswith("\n\n"):
                            turn["text"] += "\n\n"
                    elif k == "text":
                        turn["text"] += ev["delta"]
                    elif k == "tool":
                        turn["tools"].append({"id": ev.get("id"), "name": ev["name"], "input": ev.get("input") or {},
                                              "label": tool_label(ev["name"], ev.get("input")), "done": False,
                                              "started": _dt.datetime.now().timestamp()})
                    elif k == "tool_done":
                        for t in turn["tools"]:
                            if t["id"] == ev.get("id"):
                                t["done"], t["error"] = True, ev.get("error")
                    elif k == "done":
                        final, err = ev.get("text") or "", ev.get("error")
                        session = ev.get("session_id") or session
            proc.wait()
        except Exception as ex:                                      # noqa: BLE001 -- the message says so
            final, err = f"{type(ex).__name__}: {ex}", True
        finally:
            conv = self.store.get(cid) or {"id": cid, "messages": []}
            text = turn["text"].strip() or (final or "")
            if final is None and not text:
                text, err = "The chat stopped before it answered.", True
            conv["messages"].append({"role": "assistant", "text": text, "error": bool(err), "model": model,
                                     "tools": [{"name": t["name"], "label": t["label"], "input": t["input"]} for t in turn["tools"]],
                                     "at": _dt.datetime.now().isoformat(timespec="seconds")})
            if session and not err:
                conv["session_id"] = session
            self.store.save(conv)
            self._turn = None
            self._busy.release()

    def stop(self, cid):
        t = self._turn
        if t and t["cid"] == cid:
            try:
                t["proc"].kill()
            except OSError:
                pass

    def delete(self, cid):
        if self._turn and self._turn["cid"] == cid:
            raise Busy("that chat is still answering")
        self.store.delete(cid)

    # ------------------------------------------------------------------ the page
    def _real(self, s):
        return self.overlay.text(s) if self.overlay is not None else (s or "")

    def state(self, cid):
        conv = self.store.get(cid) or {"id": cid, "messages": []}
        msgs = []
        for m in conv.get("messages") or []:
            text = self._real(m.get("text"))
            msgs.append({"role": m["role"], "text": text, "error": m.get("error", False),
                         "html": md.render(text) if m["role"] == "assistant" else None,
                         "tools": m.get("tools") or [], "model": m.get("model")})
        t = self._turn
        live = None
        if t and t["cid"] == cid:
            now = _dt.datetime.now().timestamp()
            live = {"text": self._real(t["text"]), "seconds": int(now - t["started"]),
                    "tools": [{"label": x["label"], "done": x["done"], "seconds": int(now - x["started"]),
                               "input": x["input"], "name": x["name"]} for x in t["tools"]]}
        return {"id": cid, "title": self._real(conv.get("title")), "messages": msgs,
                "running": bool(t and t["cid"] == cid), "live": live, "busy_elsewhere": bool(t and t["cid"] != cid)}

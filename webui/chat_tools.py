"""webui.chat_tools -- the chat's only tools: an MCP server over stdio (owner request 2026-09-30).

Claude Code runs headless for the web UI's chat with every built-in tool switched off, so this
server is everything the chat can do. It is read-only by construction:

  league_snapshot  standings, records, points, budgets, my roster with projections and injury
                   status, and the week's matchup -- the context most questions need
  list_data        the files in the chat's copy of the league data
  read_data        a file (or a page of it) from that copy
  search_data      a regex search across that copy
  list_tools       the analysis tools it may run, with their options
  run_tool         one of them, in the copy

The copy (webui.sandbox) sits outside the repository and holds no data/local, and every tool
runs with the copy as its working directory, so the only files any of this writes -- a tool's
own answer record -- land in the copy. The tools are the Tools page's allowlist (webui.tools
TOOLS): never the engine runs, never sync, never a tool that appends to a tracked log, never
--canonical. One run at a time, and none while any other analysis process is on the machine
or the site's own runner holds its lock (R1).

Run as `py -3.10 -m webui.chat_tools --workspace <copy>`; the checkout is found from this
file, never passed on the command line (a path naming the project would read as an engine
process to webui.jobs' scan).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading

PROTOCOL = "2025-06-18"
MAX_OUT = 40000            # characters of text returned to the model in one result
READ_LINES = 400
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT_EXT = (".json", ".jsonl", ".txt", ".md", ".csv", ".log", ".html")
RUN_TIMEOUT = 25 * 60


class ToolError(Exception):
    pass


def default_runner(argv, cwd, env, timeout):
    p = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", errors="replace")


def default_scan():
    from webui.jobs import scan_engine_processes
    return scan_engine_processes(exclude={os.getpid()})


def _clip(text, limit=MAX_OUT):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit // 4] + f"\n\n[... {len(text) - limit} characters cut ...]\n\n" + text[-(limit * 3 // 4):]


class ToolServer:
    def __init__(self, workspace, repo=REPO, runner=None, scan=None, real_lock="auto"):
        self.workspace = os.path.abspath(workspace)
        self.data = os.path.join(self.workspace, "data")
        self.repo = os.path.abspath(repo)
        self.runner = runner or default_runner
        self.scan = scan or default_scan
        # the site's own runner lock in the real checkout: a live pid there means a Tools-page run
        self.real_lock = os.path.join(self.repo, "data", "local", "webui", "engine.lock") if real_lock == "auto" else real_lock
        self._run_lock = threading.Lock()

    # ------------------------------------------------------------------ protocol
    def handle(self, msg):
        """One JSON-RPC message in, its response out (None for a notification)."""
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None                                          # notifications need no answer
        try:
            if method == "initialize":
                return self._ok(mid, {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL,
                                      "capabilities": {"tools": {}},
                                      "serverInfo": {"name": "syndicate", "version": "1"}})
            if method == "ping":
                return self._ok(mid, {})
            if method == "tools/list":
                return self._ok(mid, {"tools": self.specs()})
            if method == "tools/call":
                p = msg.get("params") or {}
                try:
                    text = self.call(p.get("name"), p.get("arguments") or {})
                    return self._ok(mid, {"content": [{"type": "text", "text": text}], "isError": False})
                except ToolError as ex:
                    return self._ok(mid, {"content": [{"type": "text", "text": str(ex)}], "isError": True})
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
        except Exception as ex:                                  # noqa: BLE001 -- the answer, never a crash
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(ex).__name__}: {ex}"}}

    @staticmethod
    def _ok(mid, result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def specs(self):
        def obj(props, required=()):
            return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}
        return [
            {"name": "league_snapshot", "description": "Standings, records, points for and against, playoff odds, every team's budget, "
             "my roster with this week's projections and injury status, and this week's matchup. Start here.",
             "inputSchema": obj({})},
            {"name": "list_data", "description": "List the files and folders in the league data (current/, weeks/, decisions/, logs/, results/).",
             "inputSchema": obj({"path": {"type": "string", "description": "a folder inside the data, e.g. 'current'; blank for the top"}})},
            {"name": "read_data", "description": "Read a file from the league data, a page of lines at a time. JSON is pretty-printed first.",
             "inputSchema": obj({"path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1},
                                 "max_lines": {"type": "integer", "minimum": 1, "maximum": 2000}}, ("path",))},
            {"name": "search_data", "description": "Search the league data's text files for a regular expression; returns file:line: text.",
             "inputSchema": obj({"pattern": {"type": "string"}, "path": {"type": "string", "description": "a folder or file to search; blank for all"},
                                 "max_hits": {"type": "integer", "minimum": 1, "maximum": 400}}, ("pattern",))},
            {"name": "list_tools", "description": "The analysis tools run_tool can run, with the question each answers and its options.",
             "inputSchema": obj({})},
            {"name": "run_tool", "description": "Run one analysis tool (see list_tools) on a copy of the league data and return its output. "
             "Read-only: it changes nothing real. One run at a time; simulation-based tools take a few minutes.",
             "inputSchema": obj({"name": {"type": "string"}, "options": {"type": "object", "description": "option name -> value, as list_tools names them"}},
                                ("name",))},
        ]

    # --------------------------------------------------------------------- tools
    def call(self, name, args):
        if name == "league_snapshot":
            return self.league_snapshot()
        if name == "list_data":
            return self.list_data(args.get("path") or "")
        if name == "read_data":
            return self.read_data(args.get("path") or "", int(args.get("start_line") or 1), int(args.get("max_lines") or READ_LINES))
        if name == "search_data":
            return self.search_data(args.get("pattern") or "", args.get("path") or "", int(args.get("max_hits") or 100))
        if name == "list_tools":
            return self.list_tools()
        if name == "run_tool":
            return self.run_tool(args.get("name") or "", args.get("options") or {})
        raise ToolError(f"no tool named {name!r}")

    def _path(self, rel):
        """An absolute path inside the copy's data, or ToolError. No absolute paths, no drive
        letters, no '..', and never a private folder."""
        rel = (rel or "").replace("\\", "/").strip()
        if re.match(r"^([A-Za-z]:|/|~)", rel):
            raise ToolError("paths are relative to the league data, e.g. 'current/league_standings.json'")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise ToolError("'..' is not allowed: paths stay inside the league data")
        if parts and parts[0].lower() in ("local", "images"):
            raise ToolError(f"{parts[0]}/ is not part of the league data")
        full = os.path.abspath(os.path.join(self.data, *parts))
        if os.path.normcase(os.path.commonpath([full, self.data])) != os.path.normcase(self.data):
            raise ToolError("outside the league data")
        return full

    def list_data(self, rel):
        full = self._path(rel)
        if not os.path.isdir(full):
            raise ToolError(f"not a folder: {rel or '.'}")
        rows = []
        for name in sorted(os.listdir(full)):
            p = os.path.join(full, name)
            if os.path.isdir(p):
                if name.lower() in ("local", "images") and not rel:
                    continue
                rows.append(f"{name}/")
            elif not name.lower().endswith((".png", ".jpg", ".svg")):
                rows.append(f"{name}  ({os.path.getsize(p):,} bytes)")
        return "\n".join(rows) or "(empty)"

    def read_data(self, rel, start, count):
        full = self._path(rel)
        if not os.path.isfile(full):
            raise ToolError(f"no file: {rel}")
        if not full.lower().endswith(TEXT_EXT):
            raise ToolError("only text files can be read (json, jsonl, txt, md, csv, log, html)")
        with open(full, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if full.lower().endswith(".json"):
            try:
                text = json.dumps(json.loads(text), indent=1, ensure_ascii=False)
            except ValueError:
                pass
        lines = text.split("\n")
        start = max(1, start)
        count = max(1, min(count, 2000))
        page = lines[start - 1:start - 1 + count]
        tail = f"\n[lines {start}-{start + len(page) - 1} of {len(lines)}]" if len(lines) > len(page) else ""
        return _clip("\n".join(page)) + tail

    def search_data(self, pattern, rel, max_hits):
        try:
            rx = re.compile(pattern, re.I)
        except re.error as ex:
            raise ToolError(f"not a valid regular expression: {ex}") from None
        full = self._path(rel)
        files = [full] if os.path.isfile(full) else [os.path.join(d, f) for d, _s, fs in os.walk(full) for f in fs]
        hits = []
        for f in sorted(files):
            if not f.lower().endswith(TEXT_EXT) or os.sep + "local" + os.sep in f:
                continue
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    for n, line in enumerate(fh, 1):
                        if rx.search(line):
                            hits.append(f"{os.path.relpath(f, self.data).replace(os.sep, '/')}:{n}: {line.strip()[:300]}")
                            if len(hits) >= max_hits:
                                return "\n".join(hits) + f"\n[stopped at {max_hits} matches]"
            except OSError:
                continue
        return "\n".join(hits) or "no matches"

    def league_snapshot(self):
        from webui.paths import Root
        root = Root(self.workspace)
        out = {}
        state = root.read_json("current/league_state.json", {}) or {}
        out["current_week"] = state.get("current_week")
        manifest = root.read_json("current/sync_manifest.json", {}) or {}
        out["data_as_of"] = manifest.get("finished_at")
        try:
            from fantasy_sim.config import MY_TEAM
            out["my_team"] = MY_TEAM
        except Exception:                                        # noqa: BLE001
            MY_TEAM = None
        try:
            from webui.standings import table
            out["standings"] = [{k: r.get(k) for k in ("rank", "team", "wins", "points_for", "points_against", "streak",
                                                       "playoff", "champ", "faab")}
                                | {"h2h": (r.get("h2h") or {}).get("text"), "median": (r.get("median") or {}).get("text")}
                                for r in table(root)]
        except Exception as ex:                                  # noqa: BLE001
            out["standings_error"] = str(ex)
        base = root.read_json("current/player_baselines.json", {}) or {}
        rosters = root.read_json("current/live_rosters.json", {}) or {}
        mine = []
        for p in rosters.get(MY_TEAM) or []:
            b = base.get(p.get("name")) or {}
            mine.append({"name": p.get("name"), "pos": b.get("pos") or p.get("pos"), "nfl": b.get("team"),
                         "projection_this_week": b.get("mean"), "injury": b.get("injury_status") or p.get("injury_status"),
                         "bye": b.get("bye")})
        out["my_roster"] = mine
        sched = root.read_json("current/league_schedule.json", []) or []
        wk = out.get("current_week")
        if isinstance(sched, list) and wk and 0 < int(wk) <= len(sched):
            out["this_week_matchups"] = sched[int(wk) - 1]
        out["note"] = ("Team names are the league's pseudonyms; the owner sees real names on their screen. "
                       "Past weeks' scores in banked_scores.json are the league's own.")
        return json.dumps(out, indent=1, default=str)

    def list_tools(self):
        from webui.tools import TOOLS, simple_fields  # noqa: F401 -- the site's allowlist
        rows = []
        for name, t in TOOLS.items():
            opts = [f"{f.name} ({f.kind}{', default ' + str(f.default) if f.default not in (None, '') else ''})"
                    for f in t.fields if f.name != "canonical"]
            rows.append(f"{name}: {t.question}" + (f" {t.detail}" if t.detail else "") + (f"\n  options: {'; '.join(opts)}" if opts else ""))
        return "\n".join(rows)

    def run_tool(self, name, options):
        from webui.tools import TOOLS, FormError, resolve_form
        tool = TOOLS.get(name)
        if tool is None:
            raise ToolError(f"{name!r} is not one of the tools the chat may run -- see list_tools")
        if not isinstance(options, dict):
            raise ToolError("options must be an object of option name -> value")
        known = {f.name for f in tool.fields}
        unknown = sorted(set(options) - known - {"canonical"})
        if unknown:
            raise ToolError(f"{name} has no option {', '.join(unknown)}; its options are {', '.join(sorted(known - {'canonical'}))}")
        form = {k: ("" if v is None else str(v)) for k, v in options.items() if k != "canonical"}
        for f in tool.fields:
            if f.name not in form and f.default not in (None, "") and f.kind != "flag":
                form[f.name] = str(f.default)
        try:
            from webui.paths import Root
            from webui.players import PlayerIndex
            from fantasy_sim.config import MY_TEAM
            form, notes = resolve_form(tool, form, PlayerIndex.from_root(Root(self.workspace)), MY_TEAM)
            form.pop("canonical", None)
            argv = tool.argv(form, python=sys.executable)
        except FormError as ex:
            raise ToolError(str(ex)) from None
        if "--canonical" in argv:
            raise ToolError("a chat run is never the week's official run")
        if not self._run_lock.acquire(blocking=False):
            raise ToolError("another analysis is already running for this chat -- one at a time")
        try:
            rivals = self.scan()
            if rivals:
                raise ToolError("another analysis is running on this machine (one at a time, so the simulation "
                                f"stays reliable): {rivals[0].get('cmdline', '')[:120]} -- try again when it finishes")
            if self.real_lock and os.path.exists(self.real_lock):
                try:
                    with open(self.real_lock, encoding="utf-8") as fh:
                        lock = json.load(fh)
                    from webui.jobs import pid_alive
                    if pid_alive(lock.get("pid")):
                        raise ToolError("the site is running a tool right now -- one at a time; try again when it finishes")
                except (OSError, ValueError):
                    pass
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(p for p in (self.repo, env.get("PYTHONPATH")) if p)
            env["PYTHONIOENCODING"] = "utf-8"
            env.pop("SHOW_REAL_TEAM_NAMES", None)                   # pseudonyms only, in anything it prints
            try:
                rc, text = self.runner(argv, self.workspace, env, RUN_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise ToolError(f"{name} ran past {RUN_TIMEOUT // 60} minutes and was stopped") from None
        finally:
            self._run_lock.release()
        head = f"$ {name} {' '.join(argv[3:])}\n" + (f"(names corrected: {', '.join(notes)})\n" if notes else "")
        if rc != 0:
            return head + f"[exit code {rc} -- the run failed; its output follows]\n" + _clip(text)
        return head + _clip(text)


def serve(server, stdin=None, stdout=None):
    """Newline-delimited JSON-RPC on stdio until stdin closes."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        resp = server.handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", required=True)
    a = ap.parse_args(argv)
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    serve(ToolServer(a.workspace))


if __name__ == "__main__":
    main()

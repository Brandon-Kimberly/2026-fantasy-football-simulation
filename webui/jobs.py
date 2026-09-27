"""webui.jobs -- one engine subprocess at a time (docs/WEB_UI.md 2.4, 2.5; phase W2).

Every tool the UI launches is a subprocess of the exact CLI the owner types, with
cwd=root, an argv LIST (never a shell string), and stdout+stderr to a job log under
data/local/webui/jobs/<id>/. That directory is under data/local: blanket-gitignored, never
served by the path chokepoint, and never copied anywhere.

R1 is a real hardware fault under parallel load, so the single-flight rule is enforced
three ways, and the simplest safe rule is used -- EVERY launch takes the same lock, heavy
or not, because classifying tools by sampling depth would be a second place the R1
assumption lives:

  1. in-process: a threading.Lock acquired non-blocking around Popen; a second launch
     while it is held is refused (HTTP 409 upstream);
  2. cross-process: data/local/webui/engine.lock, written after Popen with {pid, argv,
     started_at, job}; a lock whose pid is alive refuses a launch (another server
     instance); a lock whose pid is dead is stale and removed;
  3. pre-launch scan: any OTHER python process whose command line looks like this
     project's engine, a tool, or the test suite refuses the launch and is listed. A
     heuristic over command lines, and labelled as one.

VOID semantics: a job that exits non-zero, is cancelled, or is found dead on a server
restart is VOID, never "finished with errors" -- the R1 sentence is attached verbatim.
"""
import datetime as _dt
import json
import os
import re
import secrets
import subprocess
import sys
import threading

RUNNING, OK, VOID = "RUNNING", "OK", "VOID"
R1_VOID = "a crashed run is void -- re-run it alone (AUDIT_PLAN.md R1)"
ENGINE_PATTERNS = ("scripts.", "fantasy_sim", "unittest", "tests.golden_sync", "tests.test_golden_master")
RECORD_RE = re.compile(r"(?:logged|report|chart|written|recorded|digest|html)\s*->\s*(\S+)")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{3,80}$")


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class JobRefused(RuntimeError):
    """A launch the runner will not make. Rendered as HTTP 409 upstream, with the reason."""


# --------------------------------------------------------------------- process facts
def pid_alive(pid):
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except ImportError:
        pass
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"], capture_output=True,
                             text=True, timeout=15).stdout
        return str(int(pid)) in out
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def scan_engine_processes(exclude=()):
    """[{pid, cmdline}] for OTHER python processes whose command line matches
    ENGINE_PATTERNS. psutil when available, else a PowerShell CIM query on Windows."""
    exclude = {int(p) for p in exclude}
    found = []
    try:
        import psutil
        for p in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
            info = p.info
            if info["pid"] in exclude or not (info.get("name") or "").lower().startswith("py"):
                continue
            cmd = " ".join(info.get("cmdline") or [])
            if any(pat in cmd for pat in ENGINE_PATTERNS):
                found.append({"pid": info["pid"], "cmdline": cmd[:200]})
        return found
    except ImportError:
        pass
    if sys.platform != "win32":
        return found
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'py%'\" | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        rows = json.loads(out) if out else []
        rows = rows if isinstance(rows, list) else [rows]
        for r in rows:
            pid, cmd = r.get("ProcessId"), r.get("CommandLine") or ""
            if pid in exclude:
                continue
            if any(pat in cmd for pat in ENGINE_PATTERNS):
                found.append({"pid": pid, "cmdline": cmd[:200]})
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return found


# ------------------------------------------------------------------------- runner
class JobRunner:
    def __init__(self, root, popen=subprocess.Popen, scan=scan_engine_processes, alive=pid_alive):
        self.root = root
        self.base = os.path.join(root.local, "webui")
        self.jobs_dir = os.path.join(self.base, "jobs")
        self.lock_path = os.path.join(self.base, "engine.lock")
        self._popen, self._scan, self._alive = popen, scan, alive
        self._lock = threading.Lock()
        self._current = None
        self._proc = None
        self.reconcile()

    # ----------------------------------------------------------------- storage
    def _dir(self, job_id):
        if not JOB_ID_RE.match(str(job_id)):
            raise FileNotFoundError(job_id)
        return os.path.join(self.jobs_dir, job_id)

    def _write(self, job_id, meta):
        with open(os.path.join(self._dir(job_id), "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=1)

    def read(self, job_id):
        try:
            with open(os.path.join(self._dir(job_id), "meta.json"), encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, ValueError):
            return None

    def list(self):
        if not os.path.isdir(self.jobs_dir):
            return []
        out = []
        for name in os.listdir(self.jobs_dir):
            m = self.read(name)
            if m:
                out.append(m)
        return sorted(out, key=lambda m: m.get("started_at") or "", reverse=True)

    def current(self):
        return self.read(self._current) if self._current else None

    def typical_seconds(self, tool):
        """Median wall time of past OK runs of `tool`, or None -- the only estimate the job
        page makes, and it says 'typically', never 'remaining'."""
        secs = []
        for m in self.list():
            if m.get("tool") != tool or m.get("state") != OK:
                continue
            try:
                a = _dt.datetime.strptime(m["started_at"], "%Y-%m-%dT%H:%M:%SZ")
                b = _dt.datetime.strptime(m["finished_at"], "%Y-%m-%dT%H:%M:%SZ")
            except (KeyError, TypeError, ValueError):
                continue
            secs.append((b - a).total_seconds())
        if not secs:
            return None
        secs.sort()
        return secs[len(secs) // 2]

    def tail(self, job_id, chars=6000):
        try:
            with open(os.path.join(self._dir(job_id), "stdout.log"), "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - chars))
                return fh.read().decode("utf-8", errors="replace"), size
        except FileNotFoundError:        # unknown id, an id that could traverse, or no log yet
            return "", 0

    def log_text(self, job_id):
        try:
            with open(os.path.join(self._dir(job_id), "stdout.log"), encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""

    # -------------------------------------------------------------------- lock
    def _read_lock(self):
        try:
            with open(self.lock_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, ValueError):
            return None

    def _remove_lock(self):
        try:
            os.remove(self.lock_path)
        except FileNotFoundError:
            pass

    def reconcile(self):
        """Server start: a lock whose pid is dead is stale; a RUNNING job whose pid is dead
        is VOID (it was killed, crashed, or the server died under it)."""
        lock = self._read_lock()
        if lock and not self._alive(lock.get("pid")):
            self._remove_lock()
        for m in self.list():
            if m.get("state") == RUNNING and not self._alive(m.get("pid")):
                m.update(state=VOID, finished_at=m.get("finished_at") or _iso(_now()),
                         note=f"found dead on server start; {R1_VOID}")
                self._write(m["id"], m)

    # ------------------------------------------------------------------ launch
    def launch(self, argv, tool, label=None, extra=None):
        """Start `argv` (a list; argv[0] the interpreter) as the one running job. Returns
        the job id. Raises JobRefused when a job is running, an engine.lock is held by a
        live pid, or another engine process is on the machine. `extra`: additional meta
        fields (e.g. the player-name corrections the form made) -- never the environment."""
        if not isinstance(argv, (list, tuple)) or not argv:
            raise JobRefused("argv must be a non-empty list")
        if not self._lock.acquire(blocking=False):
            raise JobRefused(f"busy: job {self._current} is still running")
        try:
            rivals = self._scan(exclude={os.getpid()})
            if rivals:
                names = "; ".join(f"pid {r['pid']}: {r['cmdline']}" for r in rivals[:5])
                raise JobRefused(f"another engine process is running (R1: one at a time) -- {names}")
            lock = self._read_lock()
            if lock and self._alive(lock.get("pid")):
                raise JobRefused(f"engine.lock is held by live pid {lock.get('pid')} (job {lock.get('job')}) -- "
                                 "another server instance?")
            self._remove_lock()
            now = _now()
            job_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(3)}_{tool}"
            jdir = self._dir(job_id)
            os.makedirs(jdir, exist_ok=True)
            meta = {"id": job_id, "tool": tool, "label": label or tool, "state": RUNNING,
                    "started_at": _iso(now), "finished_at": None, "rc": None, "pid": None,
                    "python": argv[0], "args": list(argv[1:]), "cwd": self.root.root,
                    "record": None, "note": None, **{k: v for k, v in (extra or {}).items() if k not in ("id", "state", "pid")}}
            logfh = open(os.path.join(jdir, "stdout.log"), "ab")
            try:
                proc = self._popen(list(argv), cwd=self.root.root, env=os.environ.copy(),
                                   stdout=logfh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   shell=False)
            except Exception as ex:
                logfh.close()
                meta.update(state=VOID, finished_at=_iso(_now()), note=f"could not start: {ex}")
                self._write(job_id, meta)
                raise JobRefused(f"could not start {tool}: {ex}") from ex
            meta["pid"] = proc.pid
            self._write(job_id, meta)
            with open(self.lock_path, "w", encoding="utf-8") as fh:
                json.dump({"pid": proc.pid, "job": job_id, "started_at": meta["started_at"],
                           "args": meta["args"]}, fh)
            self._current, self._proc = job_id, proc
            threading.Thread(target=self._watch, args=(job_id, proc, logfh), daemon=True).start()
            return job_id
        except BaseException:
            self._lock.release()
            raise

    def _watch(self, job_id, proc, logfh):
        try:
            rc = proc.wait()
        finally:
            try:
                logfh.close()
            except OSError:
                pass
        meta = self.read(job_id) or {"id": job_id}
        meta.update(finished_at=_iso(_now()), rc=rc, state=OK if rc == 0 else VOID,
                    note=None if rc == 0 else f"exit code {rc}; {R1_VOID}",
                    record=self._find_record(job_id))
        self._write(job_id, meta)
        self._remove_lock()
        self._current, self._proc = None, None
        self._lock.release()

    def cancel(self, job_id):
        """Terminate the running job. Its watcher records VOID (rc != 0)."""
        if self._current != job_id or self._proc is None:
            return False
        try:
            self._proc.terminate()
        except OSError:
            return False
        return True

    def _find_record(self, job_id):
        """The record the tool announced ('logged -> data/...'), as a served link. A chain
        such as the weekly report announces every sub-tool's record and then its own digest
        last, so the HTML digest wins when present and otherwise the LAST served path does."""
        from webui.paths import PathRefused, normalize
        served = []
        for m in RECORD_RE.finditer(self.log_text(job_id)):
            rel = normalize(m.group(1))
            try:
                self.root.resolve_file(rel)
                served.append(rel)
            except (PathRefused, FileNotFoundError):
                continue
        if not served:
            return None
        html = [r for r in served if r.lower().endswith(".html")]
        return self.root.link(html[-1] if html else served[-1])

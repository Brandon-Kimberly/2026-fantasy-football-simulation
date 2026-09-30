"""webui.paths -- one absolute root, one chokepoint (docs/WEB_UI.md section 2.2).

`storage.DATA_DIR = "data"` is CWD-relative and the web process never chdir()s, so every
read joins a relative path onto the root bound at construction -- through `resolve_file`
or `resolve_dir`, which refuse anything outside `<root>/data`, anything under
`data/local/` (env.sh, the identity map), any traversal, any absolute path, and any file
extension that is not one the UI serves. Pure stdlib: no fantasy_sim, no Flask, so the
module imports (and its tests run) without either.

Relative paths are relative to `data/`, forward-slashed, and accept the `data/` prefix and
backslashes that storage's helpers produce on Windows, so `link(storage_path)` turns any
helper output into a URL.
"""
import hashlib
import json
import os
import re
import time

TOP_DIRS = ("current", "weeks", "decisions", "logs", "results")
EXTENSIONS = (".json", ".jsonl", ".png", ".html", ".md", ".txt", ".log")
WEEK_DIR_RE = re.compile(r"^week_(\d{2})$")
STAMP_RE = re.compile(r"(\d{8}T\d{6}Z)")
# Longest first, so `roster_grades_...` reads as roster_grades, not roster.
KNOWN_TOOLS = ("weekly_report", "roster_grades", "trade_targets", "roster_calendar",
               "matchup_watch", "gameday", "lineup", "matchup", "waivers", "compare",
               "move", "trade", "draft_review", "season_retrospective")


# UI-P6/E7: the image cache the sync fills. Served only through Root.image_file, which accepts
# a digits-only Sleeper id as .jpg or a lower-case team code as .png -- never a path.
IMAGE_KINDS = {"players": (re.compile(r"^\d{1,12}$"), ".jpg"), "teams": (re.compile(r"^[a-z]{2,3}$"), ".png")}


class PathRefused(ValueError):
    """A path the chokepoint will not serve. Rendered as HTTP 400, never as a file."""


def normalize(rel):
    """Forward slashes, `data/` prefix stripped, no leading/trailing separators."""
    rel = str(rel or "").replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    if rel == "data" or rel.startswith("data/"):
        rel = rel[4:].lstrip("/")
    return rel.strip("/")


def _parts(rel):
    rel = normalize(rel)
    if not rel:
        raise PathRefused("empty path")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise PathRefused("absolute path")
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise PathRefused("traversal")
    if parts[0] not in TOP_DIRS:
        raise PathRefused(f"not under a served directory: {parts[0]}")
    return parts


def _inside(parent, child):
    parent, child = os.path.normcase(parent), os.path.normcase(child)
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:      # different drives on Windows
        return False


class Root:
    def __init__(self, root):
        self.root = os.path.realpath(str(root))
        self.data = os.path.join(self.root, "data")
        self.local = os.path.join(self.data, "local")
        self._images = {}

    # ------------------------------------------------------------------ images (UI-P6)
    IMAGE_LIST_TTL_S = 5     # audit 2026-09-29: a file written in the same Windows clock tick as a listing
    #                          leaves the folder's mtime unchanged, so the listing also expires after this

    def _image_names(self, kind):
        """The cached file names of one kind, re-listed when the folder changes or the listing is
        IMAGE_LIST_TTL_S old."""
        d = os.path.join(self.data, "images", kind)
        try:
            m = os.stat(d).st_mtime_ns
        except OSError:
            return frozenset()
        now = time.monotonic()
        hit = self._images.get(kind)
        if not hit or hit[0] != m or now - hit[1] > self.IMAGE_LIST_TTL_S:
            hit = (m, now, frozenset(os.listdir(d)))
            self._images[kind] = hit
        return hit[2]

    def image_file(self, kind, name):
        """The full path of a cached image, or None: the kind is known, the name is a valid
        id or team code with its kind's extension, and the file is there."""
        if kind not in IMAGE_KINDS or not isinstance(name, str):
            return None
        pattern, ext = IMAGE_KINDS[kind]
        stem, dot, got = name.rpartition(".")
        if not dot or "." + got != ext or not pattern.match(stem) or name not in self._image_names(kind):
            return None
        return os.path.join(self.data, "images", kind, name)

    def image(self, kind, key):
        """The local URL of a cached headshot ("players", a Sleeper id) or logo ("teams", a
        team code, any case), or None."""
        if not key or kind not in IMAGE_KINDS:
            return None
        key = str(key).lower() if kind == "teams" else str(key)
        name = key + IMAGE_KINDS[kind][1]
        return f"/img/{kind}/{name}" if self.image_file(kind, name) else None

    # ------------------------------------------------------------------ chokepoint
    def _full(self, rel):
        parts = _parts(rel)
        full = os.path.realpath(os.path.join(self.data, *parts))
        if not _inside(self.data, full):
            raise PathRefused("outside data/")
        if _inside(self.local, full):
            raise PathRefused("data/local is never served")
        return full, "/".join(parts)

    def resolve_file(self, rel):
        """Absolute path of a served file, or PathRefused / FileNotFoundError."""
        full, rel = self._full(rel)
        if os.path.splitext(rel)[1].lower() not in EXTENSIONS:
            raise PathRefused(f"extension not served: {rel}")
        if not os.path.isfile(full):
            raise FileNotFoundError(rel)
        return full

    def resolve_dir(self, rel):
        full, _rel = self._full(rel)
        if not os.path.isdir(full):
            raise FileNotFoundError(rel)
        return full

    def exists(self, rel):
        try:
            self.resolve_file(rel)
            return True
        except (PathRefused, FileNotFoundError):
            return False

    @staticmethod
    def link(rel):
        return "/file/" + normalize(rel)

    # ------------------------------------------------------------------ readers
    def read_json(self, rel, default=None):
        try:
            with open(self.resolve_file(rel), encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return default

    def read_text(self, rel):
        with open(self.resolve_file(rel), encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def read_bytes(self, rel):
        with open(self.resolve_file(rel), "rb") as fh:
            return fh.read()

    def tail_jsonl(self, rel, n=200):
        """The last n rows of a JSONL file, newest first; malformed lines are kept as text."""
        rows = []
        with open(self.resolve_file(rel), encoding="utf-8", errors="replace") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        for ln in lines[-int(n):]:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                rows.append({"_unparsed": ln})
        rows.reverse()
        return rows, len(lines)

    def mtime(self, rel):
        try:
            return os.path.getmtime(self.resolve_file(rel))
        except (PathRefused, FileNotFoundError):
            return None

    # ------------------------------------------------------------------ listings
    def _week_dirs(self, top):
        base = os.path.join(self.data, top)
        if not os.path.isdir(base):
            return []
        out = []
        for name in os.listdir(base):
            m = WEEK_DIR_RE.match(name)
            if m and os.path.isdir(os.path.join(base, name)):
                out.append(int(m.group(1)))
        return sorted(out)

    def weeks(self):
        return self._week_dirs("weeks")

    def decision_weeks(self):
        return self._week_dirs("decisions")

    def _files(self, rel_dir):
        """Served files directly inside rel_dir, as entries; subdirectories separately."""
        try:
            full = self.resolve_dir(rel_dir)
        except FileNotFoundError:
            return [], []
        files, dirs = [], []
        for name in sorted(os.listdir(full)):
            p = os.path.join(full, name)
            if os.path.isdir(p):
                if name != "local":
                    dirs.append(name)
            elif os.path.splitext(name)[1].lower() in EXTENSIONS:
                files.append(self.entry(rel_dir + "/" + name))
        return files, dirs

    def entry(self, rel):
        rel = normalize(rel)
        name = rel.rsplit("/", 1)[-1]
        stamp = STAMP_RE.search(name)
        tool = None
        for t in KNOWN_TOOLS:
            if name == t or name.startswith(t + "_"):
                tool = t
                break
        if tool is None:
            tool = name.split("_", 1)[0].split(".", 1)[0]
        try:
            size = os.path.getsize(self.resolve_file(rel))
        except (PathRefused, FileNotFoundError):
            size = None
        return {"rel": rel, "name": name, "link": self.link(rel), "tool": tool,
                "stamp": stamp.group(1) if stamp else None,
                "ext": os.path.splitext(name)[1].lower().lstrip("."), "size": size}

    def week_files(self, week):
        rel = f"weeks/week_{int(week):02d}"
        files, dirs = self._files(rel)
        subs = {d: self._files(rel + "/" + d)[0] for d in dirs}
        return {"rel": rel, "files": files, "subdirs": subs}

    def decisions(self, week):
        rel = f"decisions/week_{int(week):02d}"
        canonical, _dirs = self._files(rel)
        archive, _dirs2 = self._files(rel + "/archive")
        key = lambda e: (e["stamp"] or "", e["name"])  # noqa: E731
        return {"rel": rel, "canonical": sorted(canonical, key=key, reverse=True),
                "archive": sorted(archive, key=key, reverse=True)}

    def adhoc(self):
        files, _ = self._files("decisions/adhoc")
        return sorted(files, key=lambda e: (e["stamp"] or "", e["name"]), reverse=True)

    def season(self):
        files, _ = self._files("decisions/season")
        return sorted(files, key=lambda e: e["name"])

    def logs(self):
        files, _ = self._files("logs")
        return files

    def current(self):
        files, _ = self._files("current")
        return files

    def results(self):
        """data/results/week_NN/<run>/... -- a downloaded artifact nests its files one or two
        directories deeper (week_NN/ inside the run), so each run is walked, not listed."""
        out = []
        for wk in self._week_dirs("results"):
            rel = f"results/week_{wk:02d}"
            files, dirs = self._files(rel)
            runs = []
            for d in dirs:
                found = []
                stack = [rel + "/" + d]
                while stack:
                    cur = stack.pop()
                    sub_files, sub_dirs = self._files(cur)
                    found.extend(sub_files)
                    stack.extend(cur + "/" + s for s in sub_dirs if cur.count("/") < 5)
                runs.append({"name": d, "files": sorted(found, key=lambda e: (e["ext"] != "html", e["name"]))})
            out.append({"week": wk, "files": files, "runs": runs})
        return out

    # ------------------------------------------------------------------ integrity
    def tree_digest(self):
        """sha256 over every file's path and bytes under data/ -- the 'nothing changed'
        assertion the route tests make before and after walking every page."""
        h = hashlib.sha256()
        for dirpath, dirnames, filenames in os.walk(self.data):
            dirnames.sort()
            for name in sorted(filenames):
                p = os.path.join(dirpath, name)
                h.update(os.path.relpath(p, self.data).replace("\\", "/").encode("utf-8"))
                with open(p, "rb") as fh:
                    h.update(fh.read())
        return h.hexdigest()

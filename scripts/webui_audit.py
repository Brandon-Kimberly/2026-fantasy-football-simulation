"""scripts.webui_audit -- crawl and photograph the web UI so an audit is measured, not eyeballed.

  py -3.10 -m scripts.webui_audit --base http://127.0.0.1:8766 --out <dir> [--modes dev,simple]
                                  [--screens] [--settled] [--overflow 400] [--edge "C:/.../msedge.exe"] [--max-pages 400]

Point it at a server started with --no-real-names over a sandbox copy (docs/WEB_UI.md W5/W9):
the report and the screenshots are files, and real names never go into a file (H1). For
each view it toggles the server's mode (POST /mode with the page's CSRF token), walks every
internal link from /, and records per page: status, time, weight, title, h1 count,
duplicate ids, images without alt, inputs without a label, tables wider than 12 columns,
text leaks (None / nan / undefined / unrendered braces), broken links, and -- with
--screens and a Chromium (Edge) binary -- a desktop, a phone-width and a dark-mode
screenshot plus the console messages the page logged. Read-only against the server: the
only POSTs are the two mode toggles, and the mode is put back where it was.

Writes <out>/report.md, <out>/pages.json and <out>/shots/*.png. Nothing under the
repository changes.
"""
import argparse
import collections
import datetime as _dt
import html.parser
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LEAKS = ("None", " nan", "NaN", "undefined", "[object Object]", "{{", "}}", "&amp;amp;", "()", "—%")
SKIP = ("/cancel", "/mode", "/sync/launch", "/sync/restore", "/api/", "/jobs/", ".png")
SHOT_PAGES = ("/", "/league", "/forecasts", "/forecasts/week-3", "/decisions", "/records", "/records/week-3",
              "/tools", "/tools/compare_players", "/tools/weekly_report", "/jobs", "/logs", "/logs/decision-log",
              "/system", "/sync", "/results")
EDGE_DEFAULT = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


class Page(html.parser.HTMLParser):
    """What one page is made of, for the checks above."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = collections.Counter()
        self.links, self.imgs_no_alt, self.inputs, self.labels_for = [], 0, [], set()
        self.h1, self.title, self.tables, self._in_title, self._in_tr, self._cols, self.max_cols = 0, "", 0, False, False, 0, 0
        self.text = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids[a["id"]] += 1
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "img" and not a.get("alt", None) and a.get("alt") != "":
            self.imgs_no_alt += 1
        if tag in ("input", "select", "textarea") and a.get("type") != "hidden":
            self.inputs.append((a.get("id"), a.get("name"), a.get("aria-label")))
        if tag == "label" and a.get("for"):
            self.labels_for.add(a["for"])
        if tag == "h1":
            self.h1 += 1
        if tag == "title":
            self._in_title = True
        if tag == "table":
            self.tables += 1
        if tag == "tr":
            self._in_tr, self._cols = True, 0
        if tag in ("td", "th") and self._in_tr:
            self._cols += 1
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "tr":
            self.max_cols = max(self.max_cols, self._cols)
            self._in_tr = False
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.text.append(data)


def fetch(base, path, method="GET", data=None):
    req = urllib.request.Request(base + path, method=method, data=data,
                                 headers={"Host": urllib.parse.urlparse(base).netloc})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
            return r.status, body, int((time.monotonic() - t0) * 1000), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read(), int((time.monotonic() - t0) * 1000), ex.headers.get("Content-Type", "")


def csrf_token(base):
    _s, body, _ms, _ct = fetch(base, "/")
    m = re.search(r'name="_csrf" value="([^"]+)"', body.decode("utf-8", "replace"))
    return m.group(1) if m else None


def current_mode(base):
    _s, body, _ms, _ct = fetch(base, "/")
    return "dev" if "Switch to the simple view" in body.decode("utf-8", "replace") else "simple"


def set_mode(base, token, mode):
    data = urllib.parse.urlencode({"_csrf": token, "mode": mode, "back": "/"}).encode()
    status, _b, _ms, _ct = fetch(base, "/mode", method="POST", data=data)
    return status in (302, 200)


def audit_page(base, path):
    status, body, ms, ctype = fetch(base, path)
    rec = {"path": path, "status": status, "ms": ms, "kb": round(len(body) / 1024, 1), "type": ctype.split(";")[0]}
    if "text/html" not in ctype:
        return rec, []
    text = body.decode("utf-8", "replace")
    p = Page()
    p.feed(text)
    visible = " ".join(p.text)
    rec.update({
        "title": p.title.strip(), "h1": p.h1, "tables": p.tables, "max_cols": p.max_cols,
        "dup_ids": sorted(k for k, v in p.ids.items() if v > 1),
        "imgs_no_alt": p.imgs_no_alt,
        "unlabeled_inputs": [n or i for i, n, aria in p.inputs if not aria and (not i or i not in p.labels_for)],
        "leaks": sorted({t for t in LEAKS if t in visible}),
        "words": len(visible.split()),
        "links": len(p.links),
    })
    internal = []
    for h in p.links:
        h = h.split("#", 1)[0]
        if h.startswith("/") and not h.startswith("//") and not any(s in h for s in SKIP):
            internal.append(h)
    return rec, internal


def crawl(base, max_pages):
    seen, queue, out, link_from = set(), collections.deque(["/"]), [], {}
    while queue and len(seen) < max_pages:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        rec, internal = audit_page(base, path)
        out.append(rec)
        for h in internal:
            link_from.setdefault(h, path)
            if h not in seen:
                queue.append(h)
    for rec in out:
        if rec["status"] >= 400:
            rec["linked_from"] = link_from.get(rec["path"])
    return out


def winpath(p):
    """A path Edge can open on Windows. A Git-Bash style '/c/Users/...' handed to a native
    process is not converted by MSYS when it sits inside a Python string, and Edge then
    fails to create its profile directory with a modal dialog that outlives the timeout."""
    p = str(p or "")
    m = re.match(r"^/([A-Za-z])/(.*)$", p)
    if m and sys.platform.startswith("win"):
        p = f"{m.group(1).upper()}:/{m.group(2)}"
    return os.path.abspath(p)


def screenshot(edge, base, path, out_png, width, height, dark=False, profile=None, settled=False):
    out_png, profile = winpath(out_png), winpath(profile or os.path.join(os.path.dirname(out_png), "edge-profile"))
    args = [edge, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile}", "--hide-scrollbars", f"--window-size={width},{height}",
            "--virtual-time-budget=6000", "--enable-logging=stderr", "--v=0", f"--screenshot={out_png}"]
    if dark:
        args.append("--force-dark-mode")
    if settled:                          # capture the resting page: our CSS honours reduced motion
        args.append("--force-prefers-reduced-motion")
    args.append(base + path)
    r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    console = [ln.strip() for ln in (r.stderr or "").splitlines() if "CONSOLE" in ln]
    return os.path.exists(out_png), console


def overflow_probe(edge, base, path, width, profile):
    """Load the page at `width` with the ?audit=1 hook and read back what reaches past the
    viewport: (document scrollWidth, viewport width, 'tag#id.class:px | ...')."""
    sep = "&" if "?" in path else "?"
    profile = winpath(profile)
    args = [edge, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile}", "--hide-scrollbars", f"--window-size={width},1400", "--virtual-time-budget=6000",
            "--force-prefers-reduced-motion", "--dump-dom", base + path + sep + "audit=1"]
    r = subprocess.run(args, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
    dom = r.stdout or ""
    m = re.search(r'data-audit-scroll="(\d+)" data-audit-width="(\d+)" data-audit-over="([^"]*)"', dom)
    if not m:
        return None, None, "no probe result (is the server's ?audit=1 hook present?)"
    return int(m.group(1)), int(m.group(2)), m.group(3)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8766")
    ap.add_argument("--out", required=True)
    ap.add_argument("--modes", default="dev,simple")
    ap.add_argument("--max-pages", type=int, default=400)
    ap.add_argument("--screens", action="store_true")
    ap.add_argument("--edge", default=EDGE_DEFAULT)
    ap.add_argument("--settled", action="store_true", help="screenshot with prefers-reduced-motion forced: the page at rest, no mid-animation captures")
    ap.add_argument("--overflow", type=int, default=0, metavar="WIDTH", help="probe every screenshot page at WIDTH px (>= 520: the headless minimum) for elements past the viewport (needs the ?audit=1 hook)")
    args = ap.parse_args(argv)
    args.out = winpath(args.out)
    os.makedirs(args.out, exist_ok=True)
    token = csrf_token(args.base)
    if not token:
        print("no CSRF token on /: is the server up?", file=sys.stderr)
        return 2
    start_mode = current_mode(args.base)
    results, shots = {}, {}
    try:
        for mode in args.modes.split(","):
            if not set_mode(args.base, token, mode):
                print(f"could not set mode {mode}", file=sys.stderr)
                return 2
            pages = crawl(args.base, args.max_pages)
            results[mode] = pages
            print(f"[{mode}] {len(pages)} pages crawled", flush=True)
            if args.screens and os.path.isfile(args.edge):
                sdir = os.path.join(args.out, "shots")
                os.makedirs(sdir, exist_ok=True)
                profile = os.path.join(args.out, "edge-profile")
                for path in SHOT_PAGES:
                    if mode == "simple" and any(path.startswith(p) for p in ("/records", "/jobs", "/logs", "/system", "/sync", "/results")):
                        continue
                    name = "root" if path == "/" else re.sub(r"[^a-z0-9]+", "-", path.strip("/").lower()).strip("-")
                    # headless Chromium will not lay out narrower than ~504 px, so "phone" is 520: a large phone, and
                    # what every <=560/600 px breakpoint is judged against
                    for kind, w, h, dark in (("desktop", 1280, 2200, False), ("phone", 520, 2600, False), ("dark", 1280, 2200, True)):
                        png = os.path.join(sdir, f"{mode}-{name}-{kind}.png")
                        ok, console = screenshot(args.edge, args.base, path, png, w, h, dark, profile, args.settled)
                        shots[f"{mode} {path} {kind}"] = {"png": png if ok else None, "console": console}
                    print(f"  shot {path}", flush=True)
            if args.overflow and os.path.isfile(args.edge):
                profile = os.path.join(args.out, "edge-profile")
                for path in SHOT_PAGES:
                    if mode == "simple" and any(path.startswith(p) for p in ("/records", "/jobs", "/logs", "/system", "/sync", "/results")):
                        continue
                    sw, vw, over = overflow_probe(args.edge, args.base, path, args.overflow, profile)
                    shots[f"{mode} {path} overflow@{args.overflow}"] = {"scroll": sw, "viewport": vw, "over": over, "png": None, "console": []}
                    print(f"  overflow {path}: scroll {sw} / viewport {vw}" + (f" -- {over}" if over else ""), flush=True)
    finally:
        set_mode(args.base, token, start_mode)
    with open(os.path.join(args.out, "pages.json"), "w", encoding="utf-8") as fh:
        json.dump({"results": results, "shots": shots}, fh, indent=1)
    lines = [f"# Web UI audit -- {_dt.datetime.now(_dt.timezone.utc):%Y-%m-%d %H:%MZ}", ""]
    for mode, pages in results.items():
        bad = [p for p in pages if p["status"] >= 400]
        slow = sorted([p for p in pages if p.get("ms", 0) > 800], key=lambda p: -p["ms"])
        heavy = sorted([p for p in pages if p.get("kb", 0) > 150], key=lambda p: -p["kb"])
        lines += [f"## {mode} view: {len(pages)} pages, {len(bad)} failing, {len(slow)} slower than 800 ms, {len(heavy)} heavier than 150 KB", ""]
        for p in bad:
            lines.append(f"- FAIL {p['status']} `{p['path']}` (linked from `{p.get('linked_from')}`)")
        for p in slow[:15]:
            lines.append(f"- SLOW {p['ms']} ms `{p['path']}`")
        for p in heavy[:15]:
            lines.append(f"- HEAVY {p['kb']} KB `{p['path']}`")
        for p in pages:
            issues = []
            if p.get("h1", 1) != 1:
                issues.append(f"h1×{p.get('h1')}")
            if p.get("dup_ids"):
                issues.append("dup ids " + ",".join(p["dup_ids"][:6]))
            if p.get("imgs_no_alt"):
                issues.append(f"{p['imgs_no_alt']} img without alt")
            if p.get("unlabeled_inputs"):
                issues.append("unlabeled inputs " + ",".join(str(x) for x in p["unlabeled_inputs"][:6]))
            if p.get("leaks"):
                issues.append("leaks " + ",".join(p["leaks"]))
            if p.get("max_cols", 0) > 12:
                issues.append(f"table {p['max_cols']} cols")
            if issues:
                lines.append(f"- ISSUE `{p['path']}`: " + "; ".join(issues))
        lines.append("")
    probes = {k: v for k, v in shots.items() if "overflow@" in k}
    if probes:
        lines += ["## Overflow probe (elements reaching past the viewport)", ""]
        for key, v in probes.items():
            bad = v["scroll"] is None or (v["viewport"] and v["scroll"] > v["viewport"])
            lines.append(f"- {'OVERFLOW' if bad else 'ok'} {key}: scroll {v['scroll']} / viewport {v['viewport']}" + (f" -- {v['over']}" if v["over"] else ""))
        lines.append("")
    if shots:
        lines += ["## Console messages from the screenshot runs", ""]
        for key, s in shots.items():
            for c in s["console"]:
                if "error" in c.lower() or "warn" in c.lower() or "uncaught" in c.lower():
                    lines.append(f"- {key}: {c[:240]}")
        lines.append("")
    with open(os.path.join(args.out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

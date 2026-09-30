"""
scripts.readme_shots -- retake the README's screenshots of the web UI, and record what they show.

    py -3.10 -m scripts.readme_shots

The README shows the site as it is, and tests.test_docs holds it to that: the manifest this
writes (docs/webui_shots.json) carries a fingerprint of the page templates and the renderer
(`ui_hash`), and a commit that changes those without retaking the shots fails the docs guard
(owner request 2026-09-30).

How the shots are taken, so nothing identifying is ever in them:
  * a throwaway copy of this checkout's data (webui.sandbox), with the image cache removed --
    no player photos, no team logos; the pages fall back to their lettered marks;
  * the site served from that copy with --no-real-names (pseudonyms only) in the simple view;
  * an automated browser, so the page is shot at rest (the polish layer stands down);
  * light and dark, each written as WebP beside a README <picture>.
Needs Playwright and Edge or Chrome (as the browser tests do). Reads live data; writes only
docs/webui_*.webp and docs/webui_shots.json.
"""
import argparse
import datetime as _dt
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join("docs", "webui_shots.json")
WIDTH = 1400

# (name, path, what to shoot: None = the viewport, else a CSS selector, height, width). League is
# shot wide: its standings scroll sideways below about 1800 px, which reads as cut off in a README.
SHOTS = (
    ("home", "/", None, 1100, WIDTH),
    ("league", "/league", None, 1000, 1900),
    ("matchups", "/matchups", None, 1000, WIDTH),
    ("odds_race", "/forecasts", 'section:has(h2:has-text("Playoff-odds race"))', 900, WIDTH),
)


def ui_hash(root=ROOT):
    """A fingerprint of what the pages look like: every template, and the renderer that draws
    their charts and tables. Line endings do not count."""
    h = hashlib.sha256()
    tdir = os.path.join(root, "webui", "templates")
    files = sorted(os.path.join(tdir, f) for f in os.listdir(tdir) if f.endswith(".html"))
    files.append(os.path.join(root, "webui", "render.py"))
    for f in files:
        with open(f, "rb") as fh:
            h.update(os.path.relpath(f, root).replace(os.sep, "/").encode("utf-8") + b"\0")
            h.update(fh.read().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait(url, seconds=60):
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except OSError:
            time.sleep(0.5)
    return False


def main(argv=None):
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    from PIL import Image
    from playwright.sync_api import sync_playwright
    from webui import sandbox
    from webui.paths import Root

    box = sandbox.create(Root(ROOT))
    shutil.rmtree(os.path.join(box.data, "images"), ignore_errors=True)       # no photos, no logos
    port = _free_port()
    env = dict(os.environ, SHOW_REAL_TEAM_NAMES="0")
    server = subprocess.Popen([sys.executable, "-m", "webui", "--root", box.root, "--port", str(port),
                               "--no-real-names", "--mode", "simple"], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shots = []
    try:
        base = f"http://127.0.0.1:{port}"
        if not _wait(base + "/"):
            raise SystemExit("the site did not start")
        with sync_playwright() as pw:
            browser = None
            for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
                try:
                    browser = pw.chromium.launch(headless=True, **kw)
                    break
                except Exception:                                    # noqa: BLE001 -- try the next
                    continue
            if browser is None:
                raise SystemExit("no browser Playwright can launch (Edge, Chrome, or its own)")
            for name, path, sel, height, width in SHOTS:
                files = []
                for scheme in ("light", "dark"):
                    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme,
                                              device_scale_factor=1)
                    page = ctx.new_page()
                    page.goto(base + path, wait_until="load")
                    page.wait_for_timeout(600)
                    png = page.locator(sel).first.screenshot() if sel else page.screenshot()
                    ctx.close()
                    out = os.path.join("docs", f"webui_{name}_{scheme}.webp")
                    Image.open(io.BytesIO(png)).convert("RGB").save(os.path.join(ROOT, out), "WEBP", quality=82, method=6)
                    files.append(out.replace(os.sep, "/"))
                    print(f"  {out}")
                shots.append({"name": name, "path": path, "width": width, "files": files})
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        try:
            sandbox.discard(box)
        except (OSError, ValueError):
            pass
    doc = {"ui_hash": ui_hash(ROOT), "taken_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "names": "pseudonyms", "images": "none", "view": "simple", "width": WIDTH, "shots": shots}
    with open(os.path.join(ROOT, MANIFEST), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    print(f"wrote {MANIFEST}: {len(shots)} shots, fingerprint {doc['ui_hash'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

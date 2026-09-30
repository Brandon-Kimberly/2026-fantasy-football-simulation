"""webui.static_site -- the public copy of the website: a static snapshot for GitHub Pages
(owner request 2026-09-30).

The site is rendered in its simple view from the owner's team's point of view, with pseudonyms
only and no images, into plain HTML files, and the Pages workflow publishes them after each
week's official run (scripts.build_public_site). Nothing on a public page needs a server: the
app's `static_site` mode leaves out live scores, alerts, Tools, Chat, the view and theme forms
and the playoff picker, and the export here:

  * crawls from Home through every public link (`is_public`): the league's pages, never a
    developer page, a tool, the chat, an API or an image;
  * writes each page where Pages can serve it (`static_path`): /base/league/index.html, and a
    query string as /base/players/q/owner-all/index.html;
  * rewrites every internal link to that address, and turns a link to anything not public into
    plain text;
  * renders from its own copy of the data with the image cache left out, so no player photo or
    team logo is ever referenced.

`leak_check` is the publishing gate: every real team name, username and league id, taken from
Sleeper at build time (`forbidden_identities`), must be absent from every file -- whole words,
any case -- or nothing is published. It names the file, never the identity.
"""
import html as _html
import os
import re
import shutil
import tempfile
from urllib.parse import urlsplit

# Paths that need a server, or are the owner's alone: never on the public site.
PRIVATE = ("/tools", "/chat", "/api", "/img", "/file", "/jobs", "/sync", "/system", "/status", "/health", "/logs",
           "/records", "/results", "/mode", "/theme", "/gameday", "/trade", "/accuracy", "/playoffs/result",
           "/manifest.webmanifest")
# A whole attribute name only: `\b` alone also matched after the hyphen of data-src / data-href,
# and rewrote a script's data address as a page link (found building the browser playoff machine).
ATTR_RE = re.compile(r'(?<![\w-])(href|src|action)="([^"]*)"')
ANCHOR_RE = re.compile(r'<a\b([^>]*)(?<![\w-])href="([^"]*)"([^>]*)>(.*?)</a>', re.S)
TEXT_EXT = (".html", ".txt", ".json", ".js", ".css", ".xml")


class LeakFound(RuntimeError):
    """A real identity is in the built site. Nothing may be published."""


def _slug(q):
    return re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")


def is_public(href):
    """True for a site path that belongs on the public copy."""
    if not isinstance(href, str) or not href.startswith("/") or href.startswith("//"):
        return False
    path = urlsplit(href).path.rstrip("/") or "/"
    return not any(path == p or path.startswith(p + "/") for p in PRIVATE)


def static_path(url, base):
    """Where the page for site path `url` lives on the public copy."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    out = base.rstrip("/") + path + "/"
    if parts.query:
        out += "q/" + _slug(parts.query) + "/"
    if parts.fragment:
        out += "#" + parts.fragment
    return out


def _page_key(url):
    """The page a link names, without its fragment: what is crawled and written once."""
    parts = urlsplit(url)
    return (parts.path.rstrip("/") or "/") + ("?" + parts.query if parts.query else "")


def _copy_without_images(real, into):
    """The served data, less the image cache and the private folder."""
    from webui.paths import TOP_DIRS
    data = os.path.join(into, "data")
    os.makedirs(data)
    for d in TOP_DIRS:
        src = os.path.join(real.data, d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(data, d),
                            ignore=shutil.ignore_patterns("*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.webp"))
    return into


SCRIPT_RE = re.compile(r"(<script\b.*?</script>)", re.S | re.I)
SCRIPT_SRC_RE = re.compile(r'(<script\b[^>]*?\bsrc=")(/assets/)', re.I)


def _rewrite(html, base, found, page="/"):
    """The markup outside <script> blocks rewritten (_rewrite_markup); a script's links go
    through window.siteUrl at run time, and its code is never touched. `page` is the site path
    of the page being written, which a query-only link ('?pos=RB') is relative to."""
    html = SCRIPT_SRC_RE.sub(lambda m: m.group(1) + base + m.group(2), html)    # the shared scripts (UI-E2)
    parts = SCRIPT_RE.split(html)
    return "".join(p if i % 2 else _rewrite_markup(p, base, found, page) for i, p in enumerate(parts))


def _rewrite_markup(html, base, found, page="/"):
    """Every internal link pointed at its static address; a link to anything private becomes
    plain text; each public page it names is added to `found`."""
    here = urlsplit(page).path or "/"

    def absolute(url):
        url = _html.unescape(url)                     # '&amp;' in markup is '&' in the address
        return here + url if url.startswith("?") else url

    def anchor(m):
        url, inner = absolute(m.group(2)), m.group(4)
        if url.startswith("/") and not url.startswith("//") and not is_public(url):
            return f'<span class="off">{inner}</span>'
        return m.group(0)
    html = ANCHOR_RE.sub(anchor, html)

    def attr(m):
        name, url = m.group(1), absolute(m.group(2))
        if not url.startswith("/") or url.startswith("//"):
            return m.group(0)
        if url.startswith("/assets/"):                # a shared stylesheet: a file, written once below
            return f'{name}="{base}{url}"'
        if not is_public(url):
            return f'{name}="#"'
        found.add(_page_key(url))
        return f'{name}="{static_path(url, base)}"'
    return ATTR_RE.sub(attr, html)


def export(real_root, out_dir, base, start=("/",), max_pages=6000, origin=None):
    """Render the public site from `real_root`'s data into `out_dir`. Returns a report."""
    from fantasy_sim.config import MY_TEAM
    from webui.app import create_app
    from webui.chat import ChatService
    from webui.jobs import JobRunner
    from webui.live import LiveBoard
    from webui.names import Overlay
    from webui.paths import Root
    from webui.settings import Settings

    work = tempfile.mkdtemp(prefix="syn-public-")
    try:
        root = Root(_copy_without_images(real_root, work))
        settings = Settings(root)
        settings.set_mode("simple")
        settings.set_theme("system")
        overlay = Overlay()                                     # pseudonyms, always
        app = create_app(root, overlay=overlay, csrf_token="static", settings=settings,
                         runner=JobRunner(root), live=LiveBoard(root, MY_TEAM, league_id=None, fetch=None),
                         chat=ChatService(root, overlay, claude=None), static_site=base.rstrip("/"),
                         site_origin=origin)                  # each page's link preview names its own address
        app.testing = True
        client = app.test_client()
        os.makedirs(out_dir, exist_ok=True)
        queue, seen, written, skipped = [_page_key(s) for s in start], set(), [], []
        while queue and len(written) < max_pages:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            r = client.get(url)
            if r.status_code != 200 or r.mimetype != "text/html":
                skipped.append((url, r.status_code))
                continue
            found = set()
            html = _rewrite(r.get_data(as_text=True), base.rstrip("/"), found, url)
            rel = static_path(url, "").strip("/")
            dest = os.path.join(out_dir, *[p for p in rel.split("/") if p], "index.html")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(html)
            written.append(url)
            queue.extend(sorted(u for u in found if u not in seen))
        r = client.get("/no-such-page-on-the-public-site")
        with open(os.path.join(out_dir, "404.html"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_rewrite(r.get_data(as_text=True), base.rstrip("/"), set()))
        with open(os.path.join(out_dir, ".nojekyll"), "w") as fh:
            fh.write("")
        r = client.get("/playoffs/outcomes.json")                          # the browser machine's seasons, once
        if r.status_code == 200 and os.path.isdir(os.path.join(out_dir, "playoffs")):
            with open(os.path.join(out_dir, "playoffs", "outcomes.json"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(r.get_data(as_text=True))
        os.makedirs(os.path.join(out_dir, "assets"), exist_ok=True)       # UI-E2: once for the whole site
        for hashed, (body, _mime) in app.config["ASSETS"].files.items():
            with open(os.path.join(out_dir, "assets", hashed), "wb") as fh:
                fh.write(body)
        return {"pages": len(written), "skipped": skipped, "truncated": bool(queue)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def forbidden_identities(league_ids, fetch):
    """Every string that would identify the real league: its ids and names, and every
    manager's username and team name. `fetch(path)` reads Sleeper's API ('/league/<id>/users')."""
    out = set()
    for lid in [x for x in league_ids if x]:
        out.add(str(lid))
        try:
            league = fetch(f"/league/{lid}") or {}
            if league.get("name"):
                out.add(str(league["name"]))
        except Exception:                                        # noqa: BLE001 -- the users matter more
            pass
        for u in fetch(f"/league/{lid}/users") or []:
            for v in (u.get("display_name"), u.get("username"), (u.get("metadata") or {}).get("team_name")):
                if v:
                    out.add(str(v))
    names = {s for s in out if len(s.strip()) >= 3}               # a name too short to test is no word to forbid
    return sorted(names | {str(x) for x in league_ids if x})     # an id always counts


def leak_check(out_dir, forbidden):
    """Raise LeakFound when any forbidden string is in any built file, as a whole word in any
    case. The message names the files and counts, never the identities."""
    pats = [re.compile(r"(?<![A-Za-z0-9])" + re.escape(f.strip()) + r"(?![A-Za-z0-9])", re.I) for f in forbidden if f and f.strip()]
    hits = []
    for d, _s, fs in os.walk(out_dir):
        for f in fs:
            if not f.lower().endswith(TEXT_EXT):
                continue
            p = os.path.join(d, f)
            with open(p, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            n = sum(len(rx.findall(text)) for rx in pats)
            if n:
                hits.append(f"{os.path.relpath(p, out_dir).replace(os.sep, '/')} ({n})")
    if hits:
        raise LeakFound(f"{len(hits)} file(s) carry a real identity -- nothing published: " + ", ".join(sorted(hits)[:20]))
    return len(pats)

"""
webui.assets -- the shared stylesheet and scripts, served once and cached (UI-E2).

UI-E2 was deferred while the UI was only a local server, which does not feel the weight. The
public site (webui.static_site) does: its first build was 455 MB, and every page carried the
same ~63 KB stylesheet and ~30 KB script inline. They now live in webui/assets/ and are served
at /assets/<name>.<content hash>.<ext> with a year's immutable caching: a changed file gets a
new name, so a browser never holds a stale copy, and a name that is not the current hash is a
404. Only what depends on the page stays inline in base.html (the palette, the site address,
the density and theme preferences, the audit probe).

table.js is not a file here: it is fantasy_sim.positional_tiers._TABLE_JS, the sorter the
reports share, served from that constant so the two never drift.

Line endings are normalised before hashing, so a Windows checkout (CRLF) and the runner's
(LF) serve the same bytes under the same name.
"""
import hashlib
import os

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
TYPES = {".css": "text/css", ".js": "text/javascript"}
CACHE = "public, max-age=31536000, immutable"


def hashed_name(name, body):
    """'site.css' + its bytes -> 'site.<first 10 hex of sha256>.css'."""
    stem, ext = os.path.splitext(name)
    return f"{stem}.{hashlib.sha256(body).hexdigest()[:10]}{ext}"


class Assets:
    """{name: bytes} -> the hashed names, and the bytes behind each."""

    def __init__(self, sources):
        self.names, self.files = {}, {}
        for name, body in sorted(sources.items()):
            h = hashed_name(name, body)
            self.names[name] = h
            self.files[h] = (body, TYPES[os.path.splitext(name)[1]])

    def url(self, name):
        return "/assets/" + self.names[name]

    def get(self, hashed):
        """(bytes, mimetype) for a current hashed name, else None."""
        return self.files.get(hashed)


def load(table_js):
    sources = {}
    for f in sorted(os.listdir(DIR)):
        if os.path.splitext(f)[1] in TYPES:
            with open(os.path.join(DIR, f), "rb") as fh:
                sources[f] = fh.read().replace(b"\r\n", b"\n")
    sources["table.js"] = table_js.replace("\r\n", "\n").encode("utf-8")
    return Assets(sources)

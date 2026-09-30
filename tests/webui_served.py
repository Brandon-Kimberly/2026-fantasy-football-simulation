"""
tests.webui_served -- the page as a browser receives it (UI-E2, 2026-09-30).

The shared stylesheet and scripts moved out of base.html into webui/assets/, served by content
hash. The tests that read CSS rules or script text used to find them inline, in the rendered
page or in base.html; these helpers put the served files back where the page links them, so
those tests keep reading exactly what a browser gets -- their assertions unchanged.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_HTML = os.path.join(HERE, "..", "webui", "templates", "base.html")
LINK_RE = re.compile(r'<link rel="stylesheet" href="[^"]*/assets/([^"]+)">')
SRC_RE = re.compile(r'<script src="[^"]*/assets/([^"]+)"></script>')
_REG = []


def registry():
    if not _REG:
        from fantasy_sim.positional_tiers import _TABLE_JS
        from webui import assets
        _REG.append(assets.load(_TABLE_JS))
    return _REG[0]


def _body(hashed):
    hit = registry().get(hashed)
    assert hit is not None, f"{hashed} is not a current asset"
    return hit[0].decode("utf-8")


def inline_assets(html):
    """A rendered page with each /assets/ stylesheet and script inlined in its place."""
    html = LINK_RE.sub(lambda m: "<style>\n" + _body(m.group(1)) + "</style>", html)
    return SRC_RE.sub(lambda m: "<script>\n" + _body(m.group(1)) + "</script>", html)


def base_source():
    """base.html with the shared files spliced in where it links them: the one-file source the
    layout was before UI-E2."""
    reg = registry()
    with open(BASE_HTML, encoding="utf-8") as fh:
        src = fh.read()
    for name in ("site.css", "table.js", "site.js"):
        body = reg.get(reg.names[name])[0].decode("utf-8")
        if name.endswith(".css"):
            src = src.replace("<link rel=\"stylesheet\" href=\"{{ asset('site.css') }}\">", "<style>\n" + body + "</style>")
        else:
            src = src.replace("<script src=\"{{ asset('" + name + "') }}\"></script>", "<script>\n" + body + "</script>")
    return src

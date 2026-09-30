"""webui.md -- a small, safe markdown renderer for the chat's answers.

Everything is escaped first; only a known set of constructs becomes markup: headings, bold,
italics, inline code and code blocks, bullet and numbered lists, tables, block quotes,
horizontal rules and links (http, https or a site path only -- never javascript: or data:).
The chat's text comes from a model, so nothing it writes is ever passed through as HTML.
"""
import html
import re

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
_CODE = re.compile(r"`([^`]+)`")


def _safe_href(url):
    u = html.unescape(url).strip()
    return u if re.match(r"^(https?://|/(?!/))", u, re.I) else None


def _inline(text):
    """Escaped text with inline markup applied. Code spans are held aside so nothing inside
    them is formatted."""
    s = html.escape(text, quote=False)
    held = []

    def hold(m):
        held.append("<code>" + m.group(1) + "</code>")
        return f"\x00{len(held) - 1}\x00"
    s = _CODE.sub(hold, s)

    def link(m):
        href = _safe_href(m.group(2))
        if not href:
            return m.group(1)
        return f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener">{m.group(1)}</a>'
    s = _LINK.sub(link, s)
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    s = _ITAL.sub(r"<em>\1</em>", s)
    return re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], s)


def _cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_rule_row(line):
    return bool(re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", line))


def render(text):
    """HTML for `text`."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out, i, para = [], 0, []

    def flush():
        if para:
            out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            out.append("<pre><code>" + html.escape("\n".join(block), quote=False) + "</code></pre>")
            i += 1
            continue
        if not stripped:
            flush()
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            flush()
            level = min(6, len(m.group(1)) + 2)             # a chat answer's "#" is a section, not the page title
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if "|" in stripped and i + 1 < len(lines) and _is_rule_row(lines[i + 1]):
            flush()
            head = _cells(stripped)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            t = ['<div class="scroller"><table class="mdt"><thead><tr>']
            t += [f"<th>{_inline(c)}</th>" for c in head]
            t.append("</tr></thead><tbody>")
            for r in rows:
                t.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+", stripped):
            flush()
            ordered = bool(re.match(r"^\d+[.)]\s+", stripped))
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines):
                s = lines[i].strip()
                mm = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", s)
                if mm and bool(re.match(r"^\d", mm.group(1))) == ordered:
                    items.append(mm.group(2))
                elif s and items and lines[i].startswith(("  ", "\t")):
                    items[-1] += " " + s                        # a wrapped item continues
                else:
                    break
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if stripped.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote>" + "<br>".join(_inline(q) for q in quote) + "</blockquote>")
            continue
        para.append(stripped)
        i += 1
    flush()
    return "\n".join(out)

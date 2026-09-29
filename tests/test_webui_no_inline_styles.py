"""
tests.test_webui_no_inline_styles -- inline styles into the design system (docs/WEB_UI_ROADMAP.md
UI-E11).

A static `style="..."` attribute sits outside the tokens and the themes: it cannot follow the
compact density (UI-V5), a theme, or a later change to the spacing scale. Every one in the
templates becomes a class. What stays is the allow-list the roadmap names, a style whose VALUE
is computed -- a Jinja expression (a team's hue, a bar's width) or a script building the
attribute from data -- because no class can hold a number the page only knows at render.
Written before the migration: 112 static attributes were counted.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "..", "webui", "templates")
STYLE = re.compile(r'style="([^"]*)"')


def dynamic(value):
    """A value computed at render (Jinja) or by a script concatenating data into it."""
    return "{{" in value or "{%" in value or "' +" in value or "+ '" in value


def static_styles():
    out = []
    for name in sorted(os.listdir(TEMPLATES)):
        if name.endswith(".html"):
            with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
                for m in STYLE.finditer(fh.read()):
                    if not dynamic(m.group(1)):
                        out.append(f"{name}: {m.group(0)}")
    return out


class TestNoStaticInlineStyles(unittest.TestCase):
    def test_every_static_style_is_a_class(self):
        found = static_styles()
        self.assertEqual(found, [], f"{len(found)} static inline styles; first: {found[:5]}")

    def test_the_allow_list_is_only_computed_values(self):
        """The dynamic ones stay, and each really is computed -- a guard on `dynamic` itself."""
        for value in ("width:{{ w }}%", "background:hsl({{ team|hue }} 55% 42%)", "width:' + x + '%"):
            self.assertTrue(dynamic(value), value)
        for value in ("margin-top:8px", "width:7.2em", "grid-template-columns:1fr"):
            self.assertFalse(dynamic(value), value)


if __name__ == "__main__":
    unittest.main()

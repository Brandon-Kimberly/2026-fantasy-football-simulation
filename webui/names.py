"""webui.names -- the real-name overlay, in memory only (docs/WEB_UI.md section 2.6).

Everything on disk, in URLs, in form values, in job argv and in the access log carries the
repository's fictional team names. Real names exist as one dict fetched at startup through
the same F37/F48 machinery the weekly digest uses (fantasy_sim.weekly_report.real_name_overlay:
empty unless SHOW_REAL_TEAM_NAMES is set, never on a runner, fetched live, never written)
and are substituted into RESPONSE BODIES only -- `text` for a value a template renders,
`html` for a digest file read from disk (fantasy_sim.weekly_report.localize_names, which is
idempotent: a file that already carries the private marker is left alone).

The fantasy_sim imports are inside the methods that need them so this module, like
webui.paths, imports without matplotlib and is testable with a hand-built mapping.
"""


class Overlay:
    def __init__(self, mapping=None):
        self.mapping = {str(k): str(v) for k, v in (mapping or {}).items() if k and v}
        # Longest fictional name first, so a name that is a prefix of another cannot be
        # substituted inside it.
        self._order = sorted(self.mapping, key=len, reverse=True)

    @classmethod
    def from_environment(cls):
        """The digest's own overlay: {} when the flag is unset (the library default, so the
        suite never reaches the network), when GITHUB_ACTIONS is set, or when the fetch
        fails -- in every one of those cases the UI renders pseudonyms and says so."""
        from fantasy_sim.weekly_report import real_name_overlay
        return cls(real_name_overlay())

    @property
    def enabled(self):
        return bool(self.mapping)

    def text(self, value):
        """A template value with every fictional name replaced. Autoescaping happens after
        this filter runs, so a real name is escaped like any other string."""
        s = "" if value is None else str(value)
        for fict in self._order:
            s = s.replace(fict, self.mapping[fict])
        return s

    def html(self, html):
        """A whole HTML document read from disk, localized the way the digest is."""
        if not self.enabled:
            return html
        from fantasy_sim.weekly_report import localize_names
        return localize_names(html, self.mapping, kind="html")

    @staticmethod
    def marker():
        from fantasy_sim.weekly_report import PRIVATE_MARKER
        return PRIVATE_MARKER

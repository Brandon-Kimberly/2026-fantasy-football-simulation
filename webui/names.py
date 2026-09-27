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


AVATAR_THUMB = "https://sleepercdn.com/avatars/thumbs/{}"


def _avatars_from_sleeper():
    """{fictional team: avatar URL} for the owner's eyes, fetched the way the name overlay
    is (users + rosters joined through TEAM_NAME_MAP) and held in memory only. A league
    team avatar (users[].metadata.avatar, a full URL) wins over the account avatar
    (users[].avatar, an id on Sleeper's CDN). {} when real names are off, on a runner, or
    when the fetch fails -- the mark falls back to initials."""
    from fantasy_sim.weekly_report import real_names_enabled
    if not real_names_enabled():
        return {}
    try:
        import requests
        from fantasy_sim.config import BASE_URL, LEAGUE_ID, TEAM_NAME_MAP
        users = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/users", timeout=10).json() or []
        rosters = requests.get(f"{BASE_URL}/league/{LEAGUE_ID}/rosters", timeout=10).json() or []
        by_user = {}
        for u in users:
            url = (u.get("metadata") or {}).get("avatar") or (AVATAR_THUMB.format(u["avatar"]) if u.get("avatar") else None)
            if url:
                by_user[u.get("user_id")] = url
        out = {}
        for r in rosters:
            fict = TEAM_NAME_MAP.get(str(r.get("roster_id")))
            if fict and by_user.get(r.get("owner_id")):
                out[fict] = by_user[r.get("owner_id")]
        return out
    except Exception:
        return {}


class Overlay:
    def __init__(self, mapping=None, avatars=None):
        self.mapping = {str(k): str(v) for k, v in (mapping or {}).items() if k and v}
        # Longest fictional name first, so a name that is a prefix of another cannot be
        # substituted inside it.
        self._order = sorted(self.mapping, key=len, reverse=True)
        # {fictional team: avatar URL}: rendered as <img src> only, never written, and only
        # meaningful alongside real names (an avatar identifies the account the way a
        # name does).
        self.avatars = {str(k): str(v) for k, v in (avatars or {}).items() if k and v}

    @classmethod
    def from_environment(cls):
        """The digest's own overlay: {} when the flag is unset (the library default, so the
        suite never reaches the network), when GITHUB_ACTIONS is set, or when the fetch
        fails -- in every one of those cases the UI renders pseudonyms and says so."""
        from fantasy_sim.weekly_report import real_name_overlay
        mapping = real_name_overlay()
        return cls(mapping, _avatars_from_sleeper() if mapping else None)

    @property
    def enabled(self):
        return bool(self.mapping)

    def avatar(self, team):
        """The team's avatar URL, or None (the mark then shows initials)."""
        return self.avatars.get(str(team)) if self.enabled else None

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

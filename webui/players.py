"""webui.players -- who can be named in a form, and what a typed name resolves to.

The pool is data/current/player_baselines.json (every projected player, ~1,250) joined to
live_rosters.json for ownership, plus any rostered player the baselines lack (the
KNOWN_MISSING_ASSETS case). Two operations, both pure over that pool:

  search(q, owner)   ranked suggestions for a partial name -- prefix, then word-prefix,
                     then substring, then fuzzy -- optionally restricted to one roster
                     ("mine"/a team name) or to free agents ("free");
  resolve(name, owner)  the one player a typed name means: exact (case-insensitive) wins;
                     otherwise a single close match is accepted and reported back so the
                     job says what it launched with; several close matches, or none, is a
                     form error that lists the candidates rather than a launch with a
                     name the tool would reject.

Cached per root by the two files' mtimes; a sync invalidates it.
"""
import difflib

FREE, ALL = "free", "all"


class Ambiguous(ValueError):
    def __init__(self, name, options):
        super().__init__(name)
        self.name, self.options = name, options


class NoMatch(ValueError):
    """No such player in the requested pool. `elsewhere` names the roster an exact match
    sits on when the name is real but outside the pool ("he is on X, not a free agent")."""
    def __init__(self, name, elsewhere=None):
        super().__init__(name)
        self.name, self.elsewhere = name, elsewhere


class PlayerIndex:
    def __init__(self, players):
        self.players = sorted(players, key=lambda p: p["name"].casefold())
        self._by_fold = {}
        for p in self.players:
            self._by_fold.setdefault(p["name"].casefold(), p)

    # ---------------------------------------------------------------- construction
    @classmethod
    def from_root(cls, root):
        base = root.read_json("current/player_baselines.json", {}) or {}
        rosters = root.read_json("current/live_rosters.json", {}) or {}
        owner, extra = {}, {}
        for team, entries in rosters.items():
            for e in entries or []:
                if e.get("name"):
                    owner[e["name"]] = team
                    extra[e["name"]] = e
        players = []
        for name, d in base.items():
            if not isinstance(d, dict):
                continue
            players.append({"name": name, "pos": d.get("pos"), "nfl": d.get("team"), "owner": owner.get(name)})
        for name, e in extra.items():
            if name not in base:
                players.append({"name": name, "pos": e.get("pos"), "nfl": e.get("team"), "owner": owner.get(name)})
        return cls(players)

    _cache = {}

    @classmethod
    def for_root(cls, root):
        key = root.root
        stamp = tuple(root.mtime(p) for p in ("current/player_baselines.json", "current/live_rosters.json"))
        hit = cls._cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
        idx = cls.from_root(root)
        cls._cache[key] = (stamp, idx)
        return idx

    # ---------------------------------------------------------------- filtering
    def pool(self, owner=None, mine=None):
        """owner: None/'all' -> everyone; 'free' -> unowned; 'mine' -> mine's roster;
        a team name -> that roster."""
        if owner in (None, ALL, ""):
            return self.players
        if owner == FREE:
            return [p for p in self.players if not p.get("owner")]
        team = mine if owner == "mine" else owner
        return [p for p in self.players if p.get("owner") == team]

    def search(self, q, owner=None, mine=None, limit=12):
        q = (q or "").strip().casefold()
        pool = self.pool(owner, mine)
        if not q:
            return pool[:limit]
        ranked = []
        for p in pool:
            n = p["name"].casefold()
            if n.startswith(q):
                ranked.append((0, -_ratio(q, n), n, p))
            elif any(w.startswith(q) for w in n.replace("'", "").replace(".", "").split()):
                ranked.append((1, -_ratio(q, n), n, p))
            elif q in n:
                ranked.append((2, -_ratio(q, n), n, p))
        if len(ranked) < limit:
            seen = {id(p) for _r, _s, _n, p in ranked}
            names = [p["name"] for p in pool if id(p) not in seen]
            for m in difflib.get_close_matches(q, [n.casefold() for n in names], n=limit, cutoff=0.6):
                for p in pool:
                    if p["name"].casefold() == m and id(p) not in seen:
                        ranked.append((3, -_ratio(q, m), m, p))
                        seen.add(id(p))
        ranked.sort(key=lambda t: (t[0], t[1], t[2]))
        return [p for _r, _s, _n, p in ranked[:limit]]

    def resolve(self, name, owner=None, mine=None):
        """(canonical name, exact) -- or Ambiguous / NoMatch.

        Exact (case-insensitive) wins. Otherwise the candidates are scored against the WHOLE
        typed string, and a clear winner is accepted: 'devonte smith' is DeVonta Smith even
        with a dozen other Smiths in the pool, because nothing else comes close. A single
        candidate, or a single candidate that starts with what was typed, is accepted too.
        Anything else is Ambiguous, with the candidates best-first."""
        raw = (name or "").strip()
        if not raw:
            raise NoMatch(raw)
        pool = self.pool(owner, mine)
        fold = raw.casefold()
        for p in pool:
            if p["name"].casefold() == fold:
                return p["name"], True
        known = self._by_fold.get(fold)
        if known is not None:                      # a real player, just not in this pool
            raise NoMatch(raw, elsewhere=known.get("owner") or "the free agents")
        close = self.search(raw, owner, mine, limit=8)
        if not close:
            raise NoMatch(raw)
        scored = sorted(((_ratio(fold, p["name"].casefold()), p["name"]) for p in close), reverse=True)
        best, runner_up = scored[0], (scored[1] if len(scored) > 1 else (0.0, None))
        if best[0] >= 0.8 and best[0] - runner_up[0] >= 0.08:
            return best[1], False
        starts = [p for p in close if p["name"].casefold().startswith(fold)]
        if len(close) == 1 or len(starts) == 1:
            return (close if len(close) == 1 else starts)[0]["name"], False
        raise Ambiguous(raw, [n for _s, n in scored])

    def describe(self, name):
        p = self._by_fold.get((name or "").casefold())
        return p or {"name": name, "pos": None, "nfl": None, "owner": None}


def _ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def csv_split(text):
    return [s.strip() for s in str(text or "").split(",") if s.strip()]

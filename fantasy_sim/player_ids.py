"""
fantasy_sim.player_ids

Turning a player NAME into a Sleeper player_id, without ever guessing.

B17. The pipeline already handles name collisions in two places and both are correct:
`sync.resolve_player_keys` disambiguates the baselines at sync time, and
`decisions.resolve_player` resolves against pools that keying has already made
unambiguous. What neither covers is the ad-hoc path -- a script that builds
`{name: pid}` straight off the raw player cache, where `setdefault` silently keeps
whichever pid iterated first. In week 3 of 2026 one such script scored the CB DeVonta
Smith's 0.0 as the WR's.

The live cache carries **220 colliding names**, seven of them involving a player rostered
in this league (DeVonta Smith WR/CB, Josh Allen QB/G, DJ Moore WR/CB, Justin Jefferson
WR/LB, Lamar Jackson QB/CB, Kenneth Walker RB/WR, Antonio Williams WR/RB). A name is not
an identifier here; the pid is.

DELIBERATELY DEPENDENCY-FREE. `decisions.py` imports `FantasySimulationEngine` at module
scope, so resolving a name from there costs a full engine import. A scratchpad script
should be able to `from fantasy_sim.player_ids import resolve_pid` and pay for a stdlib
import and nothing else. A test pins that, in a subprocess.

THE RULE IS sync.resolve_player_keys' RULE, restated:
  - name maps to one pid                       -> that pid
  - collision, exactly one pid rostered        -> that pid, with a WARNING
  - collision, none or several rostered        -> AmbiguousPlayer, listing the candidates

`sync.resolve_player_keys` is NOT refactored to call this. It is pinned byte-exactly by
`tests/golden_sync.py`, and a shared-helper refactor would put the baselines at risk for
no behavioural gain (rule 4: do not refactor what is not covered by intent). Two
implementations of one rule will drift unless something says otherwise, so
`tests/test_resolve_pid.py::TestItAgreesWithSync` asserts they agree.
"""
import logging


class AmbiguousPlayer(LookupError):
    """A name that does not identify exactly one player. Carries the candidates so the
    caller can pick, rather than making the caller re-derive them."""

    def __init__(self, message, candidates=()):
        super().__init__(message)
        self.candidates = list(candidates)


def player_name(record):
    """Sleeper's first+last, the same spelling every name-keyed structure uses.

    Mirrors sync._player_name. Duplicated rather than imported: importing sync would drag
    in requests and numpy and defeat this module's whole reason for existing.
    """
    return f"{record.get('first_name', '')} {record.get('last_name', '')}".strip()


def candidates_for(name, players_db, pos=None, team=None):
    """Every pid in `players_db` whose name matches, optionally filtered.

    Matching is exact-then-case-insensitive on the full name. No substring or last-name
    fallback: `decisions.resolve_player` is the forgiving front door for a human typing at
    the command line, and this is the one scripts use, where a near-miss should be an
    error rather than a guess.
    """
    want = (name or "").strip()
    wl = want.lower()
    out = []
    for pid, rec in (players_db or {}).items():
        if not isinstance(rec, dict):
            continue
        n = player_name(rec)
        if n != want and n.lower() != wl:
            continue
        if pos is not None and (rec.get("position") or "") != pos:
            continue
        if team is not None and (rec.get("team") or "") != team:
            continue
        out.append(str(pid))
    return sorted(out)


def describe(pids, players_db):
    """"pid 7525 (WR, PHI)" per candidate -- the same shape sync's collision warning uses,
    so the two read alike in a log."""
    return ", ".join(
        "pid %s (%s, %s)" % (p, (players_db.get(p) or {}).get("position"),
                             (players_db.get(p) or {}).get("team"))
        for p in pids)


def resolve_pid(name, players_db, rostered_pids=None, pos=None, team=None):
    """The pid for `name`, or `AmbiguousPlayer` if the name does not pin one down.

    `rostered_pids` supplies the league context that breaks the common collision: when
    exactly one colliding player is on a roster, he is the one meant, and a WARNING says
    so. Without that context -- the usual case in a scratchpad script -- a colliding name
    is simply refused, which is the entire point: the alternative is `setdefault` quietly
    picking whichever pid came first.

    `pos` / `team` disambiguate explicitly and are checked BEFORE the roster rule, so a
    caller who knows what they want never depends on roster state.

    Raises KeyError when nothing matches at all -- a typo and an ambiguity are different
    problems and deserve different exceptions.
    """
    hits = candidates_for(name, players_db, pos=pos, team=team)
    if not hits:
        qualifier = "".join(filter(None, [f" pos={pos!r}" if pos else "",
                                          f" team={team!r}" if team else ""]))
        raise KeyError(f"{name!r}{qualifier}: no such player in the player database")
    if len(hits) == 1:
        return hits[0]

    records = describe(hits, players_db)
    rostered = [p for p in hits if p in set(rostered_pids or ())]
    if len(rostered) == 1:
        logging.warning(
            "NAME COLLISION: %r is %s. pid %s is rostered and is the one meant; pass "
            "pos= or team= to select another.", name, records, rostered[0])
        return rostered[0]

    detail = (f"{len(rostered)} of them are on league rosters" if rostered
              else "none of them are on a league roster")
    raise AmbiguousPlayer(
        f"{name!r} does not identify one player: {records}, and {detail}. "
        f"Pass pos= or team= to disambiguate, or key by player_id directly.",
        candidates=hits)

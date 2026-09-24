"""How the odds have moved, and what moved them (R1).

Champ% for one roster went **24.9 -> 35.4 -> 38.2 in a single day** on 2026-09-23 — a QB
claim, a kicker claim and a trade. `data/logs/predictions_2026.jsonl` recorded every one of
those states and nothing anywhere showed the trajectory, so the only way to see that a move
was worth ten points of championship probability was to remember the number from before it.

CANONICAL ROWS ONLY, and this is the trap the item names. The predictions log holds both
scheduled runs and ad-hoc ones — 9 of 23 rows are canonical today. **An ad-hoc evaluation
is not a prediction** (F56 / B5's provenance split): it is a what-if, often run three times
in an hour while deciding something, and splicing those into a time series would invent
movement that never happened to the model's actual published view. A row with no
`canonical` flag at all is likewise not canonical — the earliest rows predate the flag and
one is explicitly `backfilled`.

The deltas are between CONSECUTIVE canonical rows, so each one answers "what changed since
the last time this model published a view", which is the question a reader of a trajectory
is actually asking.

ANNOTATIONS ARE EVIDENCE, NOT EXPLANATION. Each window carries the transactions that landed
inside it, from the decision log. They are what HAPPENED in that window, not a claim about
causation: odds also move because other rosters moved, because Vegas lines changed, and
because the Bayesian blend saw another week of real scores. The note says so, because a
list of moves next to a +10.5 delta invites exactly the wrong inference.
"""
from datetime import datetime

CANONICAL_ONLY_NOTE = (
    "Canonical rows only: scheduled runs, not ad-hoc what-ifs (F56/B5). An ad-hoc "
    "evaluation is not a prediction, and splicing them in would invent movement.")

CAUSATION_NOTE = (
    "The moves listed are what LANDED in each window, not what caused the change. Odds "
    "also move because rival rosters changed, because Vegas lines moved, and because the "
    "blend saw another week of real scores.")


def _parse(stamp):
    try:
        return datetime.strptime(str(stamp).replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None


def canonical_rows(rows):
    """Scheduled prediction rows, oldest first. Anything not explicitly canonical is out."""
    out = [r for r in (rows or []) if r.get("canonical") is True and r.get("logged_at")]
    return sorted(out, key=lambda r: str(r.get("logged_at")))


def _outcome(row, team):
    for o in row.get("season_outcomes") or []:
        if o.get("Team") == team:
            return o
    return None


def moves_in_window(decisions, start, end, team=None):
    """Transactions with `created` in (start, end]. `start` None means everything before."""
    lo, hi = _parse(start), _parse(end)
    out = []
    for d in decisions or []:
        when = _parse(d.get("created"))
        if when is None or hi is None or when > hi:
            continue
        if lo is not None and when <= lo:
            continue
        teams = d.get("teams") or []
        out.append({
            "type": d.get("type"),
            "mine": bool(team) and team in teams,
            "teams": teams,
            "adds": [a.get("name") for a in (d.get("adds") or [])],
            "drops": [x.get("name") for x in (d.get("drops") or [])],
            "faab_bid": d.get("faab_bid"),
            "created": d.get("created"),
        })
    return out


def odds_history(prediction_rows, team, decisions=None):
    """The trajectory of one roster's odds across canonical runs, with deltas and moves."""
    rows = canonical_rows(prediction_rows)
    out, prev = [], None
    for r in rows:
        o = _outcome(r, team)
        if o is None:
            continue
        rec = {
            "at": r.get("logged_at"), "week": r.get("week"),
            "champ_pct": float(o.get("Champ_Pct", 0.0)),
            "playoff_pct": float(o.get("Playoff_Pct", 0.0)),
            "expected_wins": float(o.get("Expected_Wins", 0.0)),
            "playoff_se": float(o.get("Playoff_SE", 0.0) or 0.0),
        }
        if prev is None:
            rec.update(d_champ=None, d_playoff=None, d_wins=None, moves=[])
        else:
            rec.update(
                d_champ=round(rec["champ_pct"] - prev["champ_pct"], 2),
                d_playoff=round(rec["playoff_pct"] - prev["playoff_pct"], 2),
                d_wins=round(rec["expected_wins"] - prev["expected_wins"], 3),
                moves=moves_in_window(decisions, prev["at"], rec["at"], team))
        out.append(rec)
        prev = rec
    return {"team": team, "rows": out, "n_canonical": len(rows),
            "n_total": len(prediction_rows or []),
            "canonical_note": CANONICAL_ONLY_NOTE, "causation_note": CAUSATION_NOTE}


def describe_moves(moves, limit=3):
    """A one-line summary of a window's transactions, mine first."""
    if not moves:
        return ""
    ordered = sorted(moves, key=lambda m: (not m["mine"], m.get("created") or ""))
    parts = []
    for m in ordered[:limit]:
        who = "me" if m["mine"] else (m["teams"][0] if m["teams"] else "?")
        adds = ", ".join(a for a in m["adds"] if a)
        drops = ", ".join(x for x in m["drops"] if x)
        # A drop-only row is a real move and used to render as a bare "-". Name what left
        # instead: "dropped X" is information; a dash is a shrug.
        what = adds or (f"dropped {drops}" if drops else "-")
        bid = f" ${m['faab_bid']}" if m.get("faab_bid") is not None else ""
        parts.append(f"{who}: {m['type']}{bid} {what}")
    if len(ordered) > limit:
        parts.append(f"+{len(ordered) - limit} more")
    return "; ".join(parts)


def render_lines(hist, name_of=None):
    def T(x):
        return (name_of or {}).get(x, x) if isinstance(name_of, dict) else x

    out = [f"HOW THE ODDS HAVE MOVED -- {T(hist['team'])} "
           f"({hist['n_canonical']} canonical run(s) of {hist['n_total']} logged)"]
    if not hist["rows"]:
        out.append("  no canonical prediction rows for this team yet.")
        return out
    out.append(f"  {'when':18s} {'wk':>2s} {'champ%':>7s} {'d':>7s} {'playoff%':>9s} {'d':>6s} "
               f"{'expW':>6s} {'d':>7s}  what landed since the previous run")
    for r in hist["rows"]:
        d = lambda v, w, p=1: (f"{v:+{w}.{p}f}" if v is not None else "-".rjust(w))  # noqa: E731
        out.append(f"  {str(r['at'])[:16]:18s} {r['week']:2d} {r['champ_pct']:7.1f} "
                   f"{d(r['d_champ'], 7)} {r['playoff_pct']:9.1f} {d(r['d_playoff'], 6)} "
                   f"{r['expected_wins']:6.2f} {d(r['d_wins'], 7, 2)}  "
                   f"{describe_moves(r['moves'])}")
    first, last = hist["rows"][0], hist["rows"][-1]
    out.append(f"  net since {str(first['at'])[:10]}: champ "
               f"{last['champ_pct'] - first['champ_pct']:+.1f}, playoff "
               f"{last['playoff_pct'] - first['playoff_pct']:+.1f}, expected wins "
               f"{last['expected_wins'] - first['expected_wins']:+.2f}")
    out.append("  " + hist["canonical_note"])
    out.append("  " + hist["causation_note"])
    return out

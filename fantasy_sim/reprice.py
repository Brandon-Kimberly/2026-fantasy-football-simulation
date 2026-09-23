"""
fantasy_sim.reprice

What a proposed scoring change would do, before the league votes on it.

B22. F49's repricing -- every IDP under `idp_sack` 2.0 and `idp_qb_hit` 0.5 -- was a
one-off scratchpad script. It found the owner's starting DL was the league's biggest
loser at -19.5% and that he should refuse any trade bringing him a pass rusher. That
analysis was not reproducible by anyone, and this league votes on scoring more than once
a season.

`sync.generate_player_baselines` reads scoring live from the league object, so the moment
a change is REAL this tool becomes a no-op. Its entire value is the window before.

**THE ARITHMETIC IS SYNC'S OWN.** That function scores a stat line as
`sum(stats[k] * mult for k, mult in scoring.items())`, divided by games played for a
season-long projection. Reimplementing it differently here would price a change the model
would never actually apply, so `score_line` is the same expression and
`tests/test_reprice.py` asserts the equivalence.

Pure: dicts in, dicts out. The fetching lives in `scripts/reprice`.
"""


def score_line(stats, scoring):
    """One stat line under one set of settings -- sync's own expression."""
    return sum(float((stats or {}).get(k, 0.0) or 0.0) * float(m)
               for k, m in (scoring or {}).items())


def apply_override(scoring, override):
    """`scoring` with `override` applied. PARTIAL: everything unmentioned survives.

    A full-replacement reading would silently zero the forty-odd categories the caller
    did not name and report a catastrophe for every player in the league.

    An unknown key RAISES. `{"idp_sacks": 2.0}` -- plural, not a real setting -- would
    otherwise change nothing and print a clean "no impact", which is the most dangerous
    output a tool built to warn can produce. Setting a category to 0.0 is fine and means
    what it says: removing a category is a real proposal.
    """
    base = dict(scoring or {})
    unknown = sorted(k for k in (override or {}) if k not in base)
    if unknown:
        raise KeyError(
            f"not a scoring category in this league: {unknown}. A misspelled key would "
            f"change nothing and report 'no impact'. Known keys include: "
            f"{sorted(base)[:8]}...")
    base.update({k: float(v) for k, v in (override or {}).items()})
    return base


def _name(rec):
    return f"{(rec or {}).get('first_name', '')} {(rec or {}).get('last_name', '')}".strip()


def reprice_players(projections, players_db, scoring, override, per_game=False):
    """Every projected player under the current settings and the proposed ones.

    `projections` is Sleeper's payload: {pid: {"stats": {...}}} or {pid: {...}}.
    `per_game=True` divides by `gp`, matching sync's season-projection path.

    A player who scores zero under the CURRENT settings reports `pct: None` rather than
    dividing by zero -- an infinite percentage change on a man projected for nothing is
    noise that would dominate any ranking.
    """
    new_scoring = apply_override(scoring, override)
    rows = []
    for pid, payload in (projections or {}).items():
        stats = (payload or {}).get("stats", payload) or {}
        gp = float(stats.get("gp", 1.0) or 1.0) if per_game else 1.0
        if gp <= 0:
            gp = 16.0
        before = score_line(stats, scoring) / gp
        after = score_line(stats, new_scoring) / gp
        rec = (players_db or {}).get(str(pid)) or {}
        rows.append({
            "player_id": str(pid), "name": _name(rec) or str(pid),
            "pos": rec.get("position"),
            "before": before, "after": after, "delta": after - before,
            "pct": ((after - before) / before * 100.0) if before else None,
        })
    rows.sort(key=lambda r: r["delta"])          # worst hit first
    return rows


def team_impact(rows, rosters):
    """Per-team total movement, rostered players only, worst hit first."""
    by_name = {r["name"]: r for r in rows or []}
    out = []
    for team, names in (rosters or {}).items():
        hits = [by_name[n] for n in names if n in by_name and abs(by_name[n]["delta"]) > 1e-9]
        out.append({
            "team": team,
            "delta": float(sum(h["delta"] for h in hits)),
            "n_affected": len(hits),
            "worst": hits[0]["name"] if hits else None,
        })
    out.sort(key=lambda t: t["delta"])
    return out

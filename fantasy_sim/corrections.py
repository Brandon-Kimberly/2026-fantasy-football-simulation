"""
fantasy_sim.corrections

What changed after the fact: the first recorded score against the current one.

B19. `weekly_actuals.json` is regenerated on every sync, so a stat correction overwrites
the number it corrected and leaves no trace. `sync.append_first_recorded_scores` freezes
what was ORIGINALLY reported; this compares the two and answers the question that could
not previously be asked -- how often do corrections happen, and how big are they?

It matters beyond curiosity. Every quoted-vs-realized comparison in January is measured
against the REALIZED side, and that side is mutable. Without a frozen copy, a calibration
computed in January need not match the same calibration computed in December, and neither
would be identifiably wrong.

Pure: rows in, dicts out.
"""


def diff_corrections(first_rows, weekly_actuals):
    """Every first-recorded score whose current value differs.

    A score that has VANISHED entirely is reported with `now: None` rather than skipped --
    a disappearing score is the largest correction there is, and silently dropping it
    would hide the worst case.

    Ranked by magnitude, because the question is "did a correction flip a result", and a
    0.1 rounding change and a 7-point reversal are not the same event.
    """
    current = {}
    for wk_key, payload in (weekly_actuals or {}).items():
        try:
            wk = int(str(wk_key).split("_")[-1])
        except ValueError:
            continue
        for name, pts in ((payload or {}).get("player_scores") or {}).items():
            current[(wk, name)] = float(pts or 0.0)

    corrections = []
    for r in first_rows or []:
        key = (r.get("week"), r.get("name"))
        was = float(r.get("points") or 0.0)
        if key not in current:
            corrections.append({"week": key[0], "name": key[1],
                                "player_id": r.get("player_id"),
                                "first": was, "now": None, "delta": -was,
                                "recorded_at": r.get("recorded_at")})
            continue
        now = current[key]
        if abs(now - was) > 1e-9:
            corrections.append({"week": key[0], "name": key[1],
                                "player_id": r.get("player_id"),
                                "first": was, "now": now, "delta": now - was,
                                "recorded_at": r.get("recorded_at")})

    corrections.sort(key=lambda c: -abs(c["delta"]))
    n = len(first_rows or [])
    return {
        "n_recorded": n,
        "n_corrected": len(corrections),
        "rate": (len(corrections) / n) if n else 0.0,
        "largest": (abs(corrections[0]["delta"]) if corrections else 0.0),
        "total_abs": float(sum(abs(c["delta"]) for c in corrections)),
        "corrections": corrections,
        "note": ("`first` is what Sleeper reported on the first sync after the week "
                 "completed; `now` is what it reports today. A null `now` means the score "
                 "has disappeared from weekly_actuals entirely."),
    }


def team_impact(corrections, weekly_actuals, rosters):
    """How much each team's weekly total moved, and whether any result could have flipped.

    Only a comparison against the week's own median or the head-to-head margin can say a
    result FLIPPED; this reports the movement and leaves that judgement to the caller
    rather than asserting a flip it cannot verify from these inputs alone.
    """
    by_team = {}
    owner = {}
    for team, names in (rosters or {}).items():
        for n in names:
            owner[n] = team
    for c in corrections or []:
        t = owner.get(c["name"])
        if t is None:
            continue
        slot = by_team.setdefault((c["week"], t), {"week": c["week"], "team": t,
                                                   "delta": 0.0, "players": []})
        slot["delta"] += c["delta"]
        slot["players"].append(c["name"])
    out = sorted(by_team.values(), key=lambda x: -abs(x["delta"]))
    return {"teams": out,
            "note": ("movement only. Whether a RESULT flipped depends on that week's "
                     "median and head-to-head margin, which are not inputs here.")}

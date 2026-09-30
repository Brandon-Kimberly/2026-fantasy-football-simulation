"""
webui.results -- a week's results as the LEAGUE counts them (owner decision 2026-09-28; F83).

weekly_actuals.json is written from Sleeper's /matchups, which recomputes a completed week
against the league's CURRENT scoring. For weeks played under earlier scoring (weeks 1-2 of
2026, docs/EVALUATION_BOUNDARIES.md boundary 1) that re-scored box score can name a
different winner than the league's own record. data/logs/as_played_results_2026.json --
written by scripts.as_played_record, verified against the league's banked wins for every
team -- holds the as-played results; this lays them over the actuals. Points stay the box
scores (the record claims results only); `rescored` marks a row whose box score alone would
have given a different result, so a page can say the score shown is the re-scored one.
"""
AS_PLAYED = "logs/as_played_results_2026.json"


def as_played(root):
    """{week: {team: {h2h_win, median_win}}} from the as-played record; {} without one."""
    doc = root.read_json(AS_PLAYED, {}) or {}
    out = {}
    for key, v in doc.items():
        if str(key).startswith("week_") and isinstance(v, dict):
            try:
                out[int(str(key)[5:])] = v
            except ValueError:
                continue
    return out


def banked_curve(root, team, traj, before=None, results=None):
    """A forecast's expected-cumulative-wins curve with its COMPLETED weeks taken from the
    league's record (h2h + median, as counted). The export counts Sleeper's re-scored box
    scores, so a re-scored week read as a win the league counted as a loss (2, 3, 3 where the
    league banked 2, 2, 3 -- owner report 2026-09-29). `before`: only weeks before it (an
    export for week n banks weeks 1..n-1); the weeks to come stay the forecast's."""
    out = list(traj or [])
    banked = 0.0
    for played, teams in sorted((results if results is not None else week_results(root)).items()):
        mine = teams.get(team) or {}
        if mine.get("h2h_win") is None or not 0 < played <= len(out) or (before is not None and played >= before):
            continue
        banked += float(mine["h2h_win"]) + float(mine.get("median_win") or 0.0)
        out[played - 1] = banked
    return out


def rescaled_weeks(root):
    """The weeks whose box scores Sleeper now re-scores under later settings -- the weeks the
    as-played record covers (its _meta.weeks, else its week keys). Their POINTS are on the new
    scale even where the result stands, so a page showing them says so."""
    doc = root.read_json(AS_PLAYED, {}) or {}
    weeks = (doc.get("_meta") or {}).get("weeks")
    if weeks:
        return {int(w) for w in weeks}
    return set(as_played(root))


def week_results(root):
    """{week: {team: row}}: each weekly_actuals row, with h2h_win and median_win taken from the
    as-played record where it covers the week, plus `as_played` (the record decided it) and
    `rescored` (the box score's own result differed)."""
    actuals = root.read_json("current/weekly_actuals.json", {}) or {}
    record = as_played(root)
    out = {}
    for key, wk in actuals.items():
        if not str(key).startswith("week_") or not isinstance(wk, dict):
            continue
        try:
            n = int(str(key)[5:])
        except ValueError:
            continue
        rows = {}
        for team, r in (wk.get("team_results") or {}).items():
            row = dict(r or {})
            rec = (record.get(n) or {}).get(team)
            row["as_played"], row["rescored"] = bool(rec), False
            for field in ("h2h_win", "median_win"):
                if rec and rec.get(field) is not None:
                    if row.get(field) is None or float(row[field]) != float(rec[field]):
                        row["rescored"] = True
                    row[field] = rec[field]
            rows[team] = row
        out[n] = rows
    return out

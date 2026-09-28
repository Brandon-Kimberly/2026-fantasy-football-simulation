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

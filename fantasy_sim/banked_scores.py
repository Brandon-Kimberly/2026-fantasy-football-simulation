"""
fantasy_sim.banked_scores -- each completed week's score as the league BANKED it.

Sleeper's /matchups does not store a finished week. It recomputes each player's current stat
line against the league's CURRENT scoring settings, every time it is asked. So when the league
changes a scoring setting mid-season, the API rewrites every earlier week onto the new scale,
while the league's standings -- and Sleeper's own app -- keep the week as it was banked. The
two can name different winners (2026: an IDP cut landed before week 2 closed; the API serves
week 1 on the new scale, the league banked it on the old).

What this reproduces, for each completed week and team:

    each starter's CURRENT stat line (so official stat corrections are in), scored under the
    settings in force when the week was banked, summed -- unless the commissioner overrode the
    score (`custom_points`), which wins

Which settings were in force when is not recorded by Sleeper, so it is found: the league's
settings over time come from a log the sync appends to whenever they change
(data/logs/scoring_settings.jsonl), and of every way to assign those settings to the weeks in
order, the one that reproduces every team's banked points (`fpts`) to the cent is the answer.
Measured on the live league on 2026-09-29: of the eight ways to assign old/new to weeks 1-3,
exactly one reproduced all 8 teams to the cent. Points against and each team's win-loss string
are then checked too. Anything short of that is reported unverified, with why, and is never
shown as the league's.

Pure: no network, no engine import. fantasy_sim.sync fetches and writes; webui reads the file.
"""
import hashlib
import itertools
import json
import os

CENT = 0.005


def player_points(stats, settings):
    """One stat line scored under `settings`, to the cent (Sleeper rounds each player)."""
    total = 0.0
    for k, v in (stats or {}).items():
        w = (settings or {}).get(k)
        if w is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        total += float(v) * float(w)
    return round(total, 2)


def week_table(matchups, stats, settings, team_map):
    """{team: {points, override, api_points, matchup_id, starters {pid: points}}} for one week."""
    out = {}
    for m in matchups or []:
        team = team_map.get(str(m.get("roster_id")))
        if not team:
            continue
        starters = {str(p): player_points((stats or {}).get(str(p)), settings)
                    for p in (m.get("starters") or []) if p and str(p) != "0"}
        custom = m.get("custom_points")
        out[team] = {"points": round(float(custom), 2) if custom is not None else round(sum(starters.values()), 2),
                     "override": custom is not None, "api_points": m.get("points"),
                     "matchup_id": m.get("matchup_id"), "starters": starters}
    return out


def _results(table, median):
    """{team: (h2h_win, median_win)}: the matchup partner by matchup_id (a tie is half each), and
    the top half of the week's scores beating the median (None when the league plays none)."""
    pts = sorted(r["points"] for r in table.values())
    n = len(pts)
    cut = ((pts[n // 2 - 1] + pts[n // 2]) / 2 if n % 2 == 0 else pts[n // 2]) if n else 0.0
    out = {}
    for team, r in table.items():
        opp = next((t for t, o in table.items() if t != team and r["matchup_id"] is not None
                    and o["matchup_id"] == r["matchup_id"]), None)
        mine, theirs = r["points"], (table[opp]["points"] if opp else None)
        h = None if theirs is None else (1.0 if mine > theirs else (0.0 if mine < theirs else 0.5))
        med = None
        if median:
            med = 1 if mine > cut else (0 if mine < cut else 0.5)
        out[team] = (h, med, opp)
    return out


def _letter(v):
    return "W" if v == 1 else ("L" if v == 0 else "T")


def resolve(weeks, eras, team_map, points_for, points_against=None, records=None, median=True):
    """The league's banked week scores, found and checked.

    weeks          {week: {"matchups": [...], "stats": {pid: stat line}}}
    eras           [{"observed_at", "settings"}], oldest first -- the settings the league has had
    points_for     {team: banked fpts}; points_against {team: banked fpts_against}, optional
    records        {team: Sleeper's record string, e.g. "WWLLLW"}, optional: its length says how
                   many games are banked, so a finished week the league has not processed yet is
                   left out rather than breaking the check
    median         the league plays the median game (two results a week)

    Returns {verified, why, eras {week: observed_at}, weeks {week: {team: row}}, checks}.
    """
    per_week = 2 if median else 1
    wanted = sorted(int(w) for w in weeks)
    if records:
        lengths = {len(s or "") for s in records.values()}
        if len(lengths) == 1:
            banked = lengths.pop() // per_week
            wanted = [w for w in wanted if w <= banked]
    distinct, seen = [], set()
    for e in eras or []:
        key = json.dumps(e.get("settings") or {}, sort_keys=True)
        if key not in seen:
            seen.add(key)
            distinct.append(e)
    out = {"verified": False, "why": None, "eras": {}, "weeks": {}, "checks": {"points_for": False,
           "points_against": None, "records": None}}
    if not distinct:
        out["why"] = "no scoring settings recorded for this season"
        return out
    if not wanted:
        out["why"] = "no completed week the league has banked"
        return out
    tables = {(w, i): week_table(weeks[w]["matchups"], weeks[w]["stats"], e["settings"], team_map)
              for w in wanted for i, e in enumerate(distinct)}
    teams = sorted(points_for)
    matching, closest = [], None
    # settings only move forward in time: week by week the era index never decreases
    for assign in itertools.combinations_with_replacement(range(len(distinct)), len(wanted)):
        totals = {t: 0.0 for t in teams}
        for w, i in zip(wanted, assign):
            for t, r in tables[(w, i)].items():
                if t in totals:
                    totals[t] += r["points"]
        off = {t: round(totals[t] - float(points_for[t]), 2) for t in teams}
        miss = {t: d for t, d in off.items() if abs(d) >= CENT}
        if not miss:
            matching.append(assign)
        elif closest is None or len(miss) < len(closest):
            closest = miss
    if not matching:
        out["why"] = ("no assignment of the recorded scoring settings to weeks " + ", ".join(map(str, wanted))
                      + " reproduces the league's points for every team; closest is off by "
                      + ", ".join(f"{t} {d:+.2f}" for t, d in sorted((closest or {}).items())))
        return out
    chosen = {tuple(json.dumps({t: tables[(w, i)][t]["points"] for t in teams}, sort_keys=True)
                    for w, i in zip(wanted, a)): a for a in matching}
    if len(chosen) > 1:
        out["why"] = f"{len(matching)} different assignments of the scoring settings reproduce the league's points"
        return out
    assign = matching[0]
    out["checks"]["points_for"] = True
    against = {t: 0.0 for t in teams}
    seq = {t: "" for t in teams}
    for w, i in zip(wanted, assign):
        table = tables[(w, i)]
        res = _results(table, median)
        out["eras"][w] = distinct[i].get("observed_at")
        rows = {}
        for t, r in table.items():
            h, med, opp = res[t]
            rows[t] = {"points": r["points"], "h2h_win": h, "median_win": med, "override": r["override"],
                       "api_points": r["api_points"], "starters": r["starters"]}
            if t in against and opp is not None:
                against[t] += table[opp]["points"]
            if t in seq:
                seq[t] += (_letter(h) if h is not None else "") + (_letter(med) if med is not None else "")
        out["weeks"][w] = rows
    problems = []
    if points_against:
        bad = {t: round(against[t] - float(points_against[t]), 2) for t in teams
               if t in points_against and abs(against[t] - float(points_against[t])) >= CENT}
        out["checks"]["points_against"] = not bad
        if bad:
            problems.append("points against off by " + ", ".join(f"{t} {d:+.2f}" for t, d in sorted(bad.items())))
    if records:
        bad = sorted(t for t in teams if t in records and records[t] != seq[t])
        out["checks"]["records"] = not bad
        if bad:
            problems.append("win-loss record differs for " + ", ".join(f"{t} ({seq[t]} against the league's {records[t]})" for t in bad))
    if problems:
        out["why"] = "; ".join(problems)
        return out
    out["verified"] = True
    return out


# ---- the settings log ---------------------------------------------------------------------

def _digest(settings):
    return hashlib.sha256(json.dumps(settings or {}, sort_keys=True).encode("utf-8")).hexdigest()


def eras(path, season):
    """The season's distinct scoring settings, oldest first: [{observed_at, week, settings}].
    Rows are read in time order and a repeat of the settings in force is absorbed, so a
    duplicate from a union merge changes nothing."""
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and str(r.get("season")) == str(season) and isinstance(r.get("settings"), dict):
                    rows.append(r)
    except OSError:
        return []
    rows.sort(key=lambda r: str(r.get("observed_at") or ""))
    out, last = [], None
    for r in rows:
        d = _digest(r["settings"])
        if d != last:
            out.append({"observed_at": r.get("observed_at"), "week": r.get("week"), "settings": r["settings"]})
            last = d
    return out


def record_settings(path, season, week, settings, now):
    """Append the league's settings when they differ from the last recorded for the season.
    True when a row was written."""
    known = eras(path, season)
    if known and _digest(known[-1]["settings"]) == _digest(settings):
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"season": str(season), "observed_at": now, "week": week,
                             "settings": settings}, sort_keys=True) + "\n")
    return True

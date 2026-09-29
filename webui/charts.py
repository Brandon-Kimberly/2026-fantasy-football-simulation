"""
webui.charts -- native charts in place of the week's matplotlib images (docs/WEB_UI_ROADMAP.md
UI-O5, V2).

sos_grid: the strength-of-schedule grid from the week's strength_of_schedule.json -- every NFL
team's implied points for its offence, week by week (with the opponent, or a bye), and each
fantasy roster's average of those. Each cell carries its number as text and a `shade` in [0, 1]
on ONE scale across the grid (a single-hue sequential ramp; colour never carries the value
alone). Rows sort by the average over a chosen window: the rest of the season, the next four
weeks, or the fantasy playoff weeks. Reads only.
"""
PLAYOFF_START = 15      # docs/WAIVER_MECHANICS.md: playoff_week_start 15
WINDOWS = (("rest", "Rest of the season"), ("next4", "Next four weeks"), ("playoffs", "Playoff weeks"))


def _window(weeks, window):
    if window == "next4":
        return weeks[:4]
    if window == "playoffs":
        return [w for w in weeks if w >= PLAYOFF_START]
    return list(weeks)


def _shade(v, lo, hi):
    if v is None or hi is None or lo is None:
        return None
    return round((v - lo) / (hi - lo), 3) if hi > lo else 0.5


def sos_grid(root, week, window="rest"):
    """{week, weeks, playoff_weeks, window, windows, nfl: [row], fantasy: [row]} or None, where a
    row is {team, avg, cells: [{week, total, opponent, bye, shade}]}."""
    if not week:
        return None
    d = root.read_json(f"weeks/week_{int(week):02d}/strength_of_schedule.json", {}) or {}
    weeks = sorted(int(w) for w in d.get("weeks_covered") or [])
    if not weeks:
        return None
    window = window if window in dict(WINDOWS) else "rest"
    win = set(_window(weeks, window))

    def rows(src, nfl):
        out = []
        for team, per in (src or {}).items():
            cells = []
            for w in weeks:
                c = (per or {}).get(str(w))
                if nfl:
                    c = c if isinstance(c, dict) else {}
                    bye = bool(c.get("is_bye"))
                    total = None if bye else c.get("total")
                    cells.append({"week": w, "total": total, "opponent": c.get("opponent"), "bye": bye})
                else:
                    cells.append({"week": w, "total": c if isinstance(c, (int, float)) else None, "opponent": None, "bye": False})
            vals = [c["total"] for c in cells if c["week"] in win and c["total"] is not None]
            out.append({"team": team, "cells": cells, "avg": round(sum(vals) / len(vals), 1) if vals else None})
        allv = [c["total"] for r in out for c in r["cells"] if c["total"] is not None]
        lo, hi = (min(allv), max(allv)) if allv else (None, None)
        for r in out:
            for c in r["cells"]:
                c["shade"] = _shade(c["total"], lo, hi)
        out.sort(key=lambda r: -(r["avg"] if r["avg"] is not None else -1))
        return out

    return {"week": int(week), "weeks": weeks, "playoff_weeks": [w for w in weeks if w >= PLAYOFF_START],
            "window": window, "windows": WINDOWS, "nfl": rows(d.get("by_nfl_team"), True),
            "fantasy": rows(d.get("by_fantasy_team"), False)}

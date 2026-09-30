"""
webui.idp -- a defender's projected stat line for the week (UI-P8, 2026-09-30).

The sync keeps Sleeper's per-category IDP projection for every defender, scored under this
league's settings (fantasy_sim.sync.idp_projection_lines -> current/idp_projections.json).
This reads it for the player page and the quick compare. It is Sleeper's line, not the model's
number: the model prices a player from the blended, smoothed baseline and the week's forecast.
"""

# The categories in the order a defender's week is read, with their plural names (the live
# feed's STAT_LABELS are singular, written for one play at a time).
LABELS = (
    ("idp_tkl_solo", "Solo tackles"), ("idp_tkl_ast", "Assisted tackles"), ("idp_tkl", "Tackles"),
    ("idp_tkl_loss", "Tackles for loss"), ("idp_sack", "Sacks"), ("idp_sack_yd", "Sack yards"),
    ("idp_qb_hit", "QB hits"), ("idp_pass_def", "Passes defended"), ("idp_int", "Interceptions"),
    ("idp_int_ret_yd", "Interception return yards"), ("idp_ff", "Forced fumbles"),
    ("idp_fum_rec", "Fumble recoveries"), ("idp_fum_ret_yd", "Fumble return yards"),
    ("idp_safe", "Safeties"), ("idp_blk_kick", "Blocked kicks"), ("idp_def_td", "Defensive touchdowns"),
)
_NAME = dict(LABELS)


def _doc(root):
    return root.read_json("current/idp_projections.json", {}) or {}


def _rows(entry):
    stats, points = entry.get("stats") or {}, entry.get("points") or {}
    keys = [k for k, _l in LABELS if k in stats] + sorted(k for k in stats if k not in _NAME)
    return [{"key": k, "label": _NAME.get(k, k.replace("idp_", "").replace("_", " ")),
             "value": stats[k], "points": points.get(k)} for k in keys]


def line(root, pid):
    """{week, source, rows: [{key, label, value, points}], total} for one defender, or None."""
    doc = _doc(root)
    entry = (doc.get("players") or {}).get(str(pid))
    if not entry:
        return None
    meta = doc.get("_meta") or {}
    return {"week": meta.get("week"), "source": meta.get("source"), "rows": _rows(entry), "total": entry.get("total")}


def table(root, players):
    """The quick compare's side-by-side lines: {week, names, rows: [{label, vals}], totals}
    when two or more of `players` ({name, pid}) are defenders with a line, else None."""
    doc = _doc(root)
    have = [(p["name"], (doc.get("players") or {}).get(str(p.get("pid")))) for p in players]
    have = [(n, e) for n, e in have if e]
    if len(have) < 2:
        return None
    keys = [k for k, _l in LABELS if any(k in (e.get("stats") or {}) for _n, e in have)]
    return {"week": (doc.get("_meta") or {}).get("week"), "names": [n for n, _e in have],
            "rows": [{"label": _NAME[k], "vals": [(e.get("stats") or {}).get(k) for _n, e in have]} for k in keys],
            "totals": [e.get("total") for _n, e in have]}

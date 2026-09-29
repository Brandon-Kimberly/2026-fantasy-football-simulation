"""webui.records_view -- the Records page grouped by run, with headlines and diffs
(docs/WEB_UI_ROADMAP.md UI-A7; developer view only).

  runs(entries)          records written within two minutes of each other are one run -- a
                         weekly digest writes its records seconds apart, and a fixed clock
                         minute would split a run that crosses one
  annotate(root, ...)    each JSON record's headline (render.record_view's title and first
                         figure) and the previous record of the same tool, for "compare"
  diff(a, b)             two records of one tool: for a lineup (or a matchup's points
                         lineup) the slots whose starters changed -- names sorted inside a
                         slot, so the same players in another order are no change -- and the
                         expected total's move; otherwise the top-level values that differ
"""
import datetime as _dt

from webui import render


def _t(stamp):
    try:
        return _dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
    except (TypeError, ValueError):
        return None


def runs(entries, gap_seconds=120):
    items = sorted(entries, key=lambda e: (e.get("stamp") or "", e.get("name") or ""), reverse=True)
    out = []
    for e in items:
        t = _t(e.get("stamp"))
        last = out[-1] if out else None
        if t is not None and last is not None and last["_oldest"] is not None and (last["_oldest"] - t).total_seconds() <= gap_seconds:
            last["entries"].append(e)
            last["_oldest"] = t
        else:
            out.append({"start": e.get("stamp"), "entries": [e], "_oldest": t})
    for r in out:
        r["tools"] = sorted({e.get("tool") for e in r["entries"] if e.get("tool")})
        del r["_oldest"]
    return out


def annotate(root, entries, every=None):
    """Adds `headline` to each JSON record and returns {rel: previous rel of the same tool}
    over `every` (all the week's records, so a canonical record can compare with an archived
    one)."""
    for e in entries:
        if e.get("ext") == "json":
            try:
                view = render.record_view(root.read_json(e["rel"], {}) or {})
            except Exception:
                view = {}
            tiles = view.get("tiles") or []
            first = tiles[0] if tiles else None
            bits = [view.get("title")] + ([f"{first.get('k')} {first.get('v')}"] if isinstance(first, dict) else [])
            e["headline"] = " · ".join(b for b in bits if b)
    pool = sorted((x for x in (every or entries) if x.get("ext") == "json" and x.get("stamp")),
                  key=lambda x: x["stamp"])
    prev, last = {}, {}
    for x in pool:
        if x["tool"] in last:
            prev[x["rel"]] = last[x["tool"]]
        last[x["tool"]] = x["rel"]
    return prev


def _lineup_of(rec):
    if rec.get("lineup"):
        return rec["lineup"], rec.get("expected_total")
    cons = rec.get("constructions") or {}
    c = cons.get("max_mean") or next(iter(cons.values()), None) if cons else None
    if isinstance(c, dict) and c.get("lineup"):
        return c["lineup"], c.get("mean")
    return None, None


def diff(a, b):
    la, ta = _lineup_of(a or {})
    lb, tb = _lineup_of(b or {})
    if la is not None and lb is not None:
        def group(lineup):
            g = {}
            for e in lineup:
                g.setdefault(e.get("slot"), []).append(e.get("name"))
            return {s: sorted(n for n in v if n) for s, v in g.items()}
        ga, gb = group(la), group(lb)
        order = []
        for e in list(la) + list(lb):
            if e.get("slot") not in order:
                order.append(e.get("slot"))
        slots = [{"slot": s, "before": ga.get(s, []), "after": gb.get(s, [])} for s in order if ga.get(s, []) != gb.get(s, [])]
        total = (float(tb) - float(ta)) if ta is not None and tb is not None else None
        return {"kind": "lineup", "slots": slots, "total": total, "values": []}
    skip = {"timestamp_utc"}
    keys = [k for k in dict.fromkeys(list((a or {}).keys()) + list((b or {}).keys())) if k not in skip]
    values = [{"key": k, "before": (a or {}).get(k), "after": (b or {}).get(k)} for k in keys
              if not isinstance((a or {}).get(k), (dict, list)) and not isinstance((b or {}).get(k), (dict, list))
              and (a or {}).get(k) != (b or {}).get(k)]
    return {"kind": "values", "slots": [], "total": None, "values": values}

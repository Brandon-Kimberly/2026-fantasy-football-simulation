"""webui.calibration -- how the model has done on each player (docs/WEB_UI_ROADMAP.md UI-P3).

Where each week's points landed inside the model's range for that player: a percentile, read
linearly between the points the week's forecast export states -- min, p10, p25, p50, p75, p90,
max, from weeks/week_NN/player_variance.json. Two limits, said on the page as well:
- The export is the one-week spread POOLED over the weeks that forecast simulated. The model
  does not save a spread per player per week, so a week's own matchup is not in it.
- The export leaves out weeks a player does not play, so a zero is left out here too: in the
  first-recorded scores a zero is more often a player who did not play than a real zero.

The six bins are the quantiles' own, so if the ranges are right each holds its width's share:
observed over expected is flat at 1. A handful of weeks per player is noise; the count is
always shown and one player's weeks are never a verdict.
"""
import json
import math

BINS = ((0, 10), (10, 25), (25, 50), (50, 75), (75, 90), (90, 100))
LABELS = ("under the 10th", "10th–25th", "25th–50th", "50th–75th", "75th–90th", "over the 90th")
ANCHORS = (("min", 0.0), ("p10", 10.0), ("p25", 25.0), ("p50", 50.0), ("p75", 75.0), ("p90", 90.0), ("max", 100.0))


def landing(q, x):
    """The percentile of `x` in the spread `q`, linear between its stated points; None when the
    spread does not state them."""
    try:
        pts = [(float(q[k]), p) for k, p in ANCHORS]
        x = float(x)
    except (KeyError, TypeError, ValueError):
        return None
    if x <= pts[0][0]:
        return 0.0
    if x >= pts[-1][0]:
        return 100.0
    for (x0, p0), (x1, p1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return p0 if x1 == x0 else p0 + (p1 - p0) * (x - x0) / (x1 - x0)
    return None


def bin_of(pct):
    for i, (lo, hi) in enumerate(BINS):
        if pct < hi or i == len(BINS) - 1:
            return i if pct >= lo else None
    return None


def histogram(pcts):
    """Each bin's share of the landings against its width's share, with the ratio (flat at 1
    when the ranges are right) and its standard error under that hypothesis."""
    n = len(pcts)
    counts = [0] * len(BINS)
    for p in pcts:
        b = bin_of(p)
        if b is not None:
            counts[b] += 1
    bins = []
    for (lo, hi), label, c in zip(BINS, LABELS, counts):
        expected = (hi - lo) / 100.0
        share = c / n if n else None
        se = math.sqrt(expected * (1 - expected) / n) if n else None
        bins.append({"label": label, "lo": lo, "hi": hi, "count": c, "expected": expected, "share": share,
                     "ratio": (share / expected) if share is not None else None,
                     "ratio_se": (se / expected) if se is not None else None})
    return {"n": n, "bins": bins}


def _spreads(root):
    """{week: {player name: spread}} from each week's forecast export, low-count spreads left out."""
    out = {}
    for week in root.weeks():
        try:
            data = root.read_json(f"weeks/week_{week:02d}/player_variance.json", {}) or {}
        except Exception:
            continue
        by_name = {}
        for rows in data.values() if isinstance(data, dict) else []:
            for r in rows or []:
                if isinstance(r, dict) and r.get("name") and not r.get("low_n"):
                    by_name[r["name"]] = r
        if by_name:
            out[week] = by_name
    return out


def _first_scores(root):
    """{(week, name): points} -- the first score recorded for each player-week."""
    out = {}
    try:
        path = root.resolve_file("logs/first_recorded_scores.jsonl")
    except Exception:
        return out
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
                key = (int(r["week"]), r["name"])
            except (ValueError, KeyError, TypeError):
                continue
            out.setdefault(key, r.get("points"))           # first row wins, as the log is union-merged
    return out


def _landings(root, only=None):
    spreads, scores = _spreads(root), _first_scores(root)
    out = []
    for (week, name), pts in scores.items():
        if only is not None and name != only:
            continue
        q = (spreads.get(week) or {}).get(name)
        try:
            pts = float(pts)
        except (TypeError, ValueError):
            continue                                            # a malformed row is left out, never raised (audit)
        if q is None or pts == 0.0:
            continue
        pct = landing(q, pts)
        if pct is not None:
            out.append((week, name, pct))
    return out


def player_landings(root, name):
    """{week: percentile} for one player."""
    return {week: round(pct, 1) for week, _n, pct in _landings(root, only=name)}


def league_histogram(root):
    """Every rostered player's landings, pooled."""
    rows = _landings(root)
    h = histogram([p for _w, _n, p in rows])
    h["players"] = len({n for _w, n, _p in rows})
    h["weeks"] = sorted({w for w, _n, _p in rows})
    return h


def ordinal(pct):
    n = int(round(pct))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"

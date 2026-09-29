"""webui.outcomes -- the per-simulation outcome export, read (docs/WEB_UI_ROADMAP.md Decision 1).

The engine writes, for every simulated season, each remaining regular-season game's result,
each team against each week's median, the final seeds and the champion
(weeks/week_NN/sim_outcomes_week_N.json; format in its `_meta`). The web process never
imports the engine and never re-simulates, so every conditional question -- "in the seasons
where X happened, how often did Y?" -- is a FILTER over those seasons:

  UI-E5  `conditional`: odds given pinned results, with the count behind them and their
         standard errors; below MIN_SEASONS matching seasons it refuses (None), because a
         number from 90 seasons shown beside one from 10,000 reads as equally precise.
  UI-O8  `leverage`: how much each game and each median result moves each team's odds.
  UI-O9  `rooting`: which side of every other game the owner should want.
  UI-O6  `wins_curve`: the playoff probability for each final win total.
  UI-O10 `markers`: "clinched" and "eliminated" only from a bound on the remaining schedule,
         never from simulation frequency; "over 99.9%" for frequency near-certainty.

Probabilities are 0-1 throughout. Simulated seasons are independent draws (the epistemic
draw is per season, CLAUDE.md), so a filtered share's standard error is binomial,
sqrt(p(1-p)/n). Pure: numpy and the web UI's own path chokepoint, nothing from fantasy_sim.
"""
import math
import re

import numpy as np

MIN_SEASONS = 200            # docs/WEB_UI_ROADMAP.md UI-O7: below this a number is refused, not greyed
PLAYOFF_SPOTS = 4            # fantasy_sim/simulation.py: top4 = ranked[:4] at week 14
NEAR_CERTAIN_MIN = 3000      # rule of three: 0 misses in 3,000 seasons bounds the miss rate near 0.1%
NEAR_CERTAIN = 0.999
PICK_RE = re.compile(r"^([gm])(\d+)\.(\d+)$")

_CACHE = {}                  # full path -> (mtime, Outcomes)


class Outcomes:
    """One export, decoded into arrays over seasons (rows) and teams (columns)."""

    def __init__(self, doc):
        doc = doc or {}
        self.teams = [str(t) for t in doc.get("teams") or []]
        self.index = {t: i for i, t in enumerate(self.teams)}
        self.weeks = [int(w) for w in doc.get("weeks") or []]
        self.matchups = {int(w): [tuple(p) for p in pairs] for w, pairs in (doc.get("matchups") or {}).items()}
        self.median_enabled = bool(doc.get("median_enabled", True))
        self.rows = list(doc.get("seasons") or [])
        meta = doc.get("_meta") or {}
        self.week = int(meta["week"]) if meta.get("week") is not None else (self.weeks[0] if self.weeks else None)
        self.n = len(self.rows)
        n_teams, hexn = len(self.teams), int(doc.get("median_hex") or 1)
        parts = [r.split(";") for r in self.rows]
        codes = [(p[0].split("|") if p[0] else []) for p in parts]
        self.games, self.median = {}, {}
        for wi, w in enumerate(self.weeks):
            g = len(self.matchups.get(w, []))
            col = [c[wi] if wi < len(c) else "" for c in codes]
            if self.n and all(len(c) == g + hexn for c in col):
                chars = np.frombuffer("".join(c[:g] for c in col).encode("ascii"), dtype=np.uint8)
                self.games[w] = (chars.reshape(self.n, g).astype(np.int8) - ord("0")) if g else np.zeros((self.n, 0), np.int8)
                bits = np.array([int(c[g:], 16) for c in col], dtype=np.int64)
                self.median[w] = ((bits[:, None] >> np.arange(n_teams)) & 1).astype(bool)
            else:
                self.games[w] = np.full((self.n, g), -1, dtype=np.int8)
                self.median[w] = np.zeros((self.n, n_teams), dtype=bool)
        self.rank = np.full((self.n, n_teams), n_teams, dtype=np.int16)
        self.champ = np.full(self.n, -1, dtype=np.int16)
        for s, p in enumerate(parts):
            seeds = [int(x) for x in (p[1].split(",") if len(p) > 1 and p[1] else [])]
            for r, ti in enumerate(seeds):
                if 0 <= ti < n_teams:
                    self.rank[s, ti] = r
            if len(p) > 2 and p[2] != "":
                self.champ[s] = int(p[2])
        self.playoff = self.rank < PLAYOFF_SPOTS

    def game_of(self, team, week):
        """(game index, side) of a team's game that week: side 1 when it is listed first."""
        for gi, (a, b) in enumerate(self.matchups.get(week, [])):
            if team == a:
                return gi, 1
            if team == b:
                return gi, 0
        return None, None


def parse(doc):
    return Outcomes(doc)


def load(root, at_most=None):
    """The newest export at or before week `at_most` (every week when None), cached until the
    file changes; None when there is none."""
    for w in reversed(root.weeks()):
        if at_most is not None and w > int(at_most):
            continue
        rel = f"weeks/week_{w:02d}/sim_outcomes_week_{w}.json"
        if not root.exists(rel):
            continue
        full, mtime = root.resolve_file(rel), root.mtime(rel)
        hit = _CACHE.get(full)
        if hit and hit[0] == mtime:
            return hit[1]
        o = parse(root.read_json(rel, {}))
        o.week = w
        _CACHE[full] = (mtime, o)
        return o
    return None


# ------------------------------------------------------------------------------ the filter
def picks_from_args(o, args):
    """Pins from a query string: g<week>.<game>=a|b (the first or second team listed wins),
    m<week>.<team index>=1|0 (beats or misses the median). Anything else is ignored."""
    out = []
    for key, val in dict(args).items():
        m = PICK_RE.match(str(key))
        if not m:
            continue
        kind, w, i = m.group(1), int(m.group(2)), int(m.group(3))
        if w not in o.weeks:
            continue
        if kind == "g" and i < len(o.matchups.get(w, [])) and val in ("a", "b"):
            out.append(("g", w, i, val))
        elif kind == "m" and o.median_enabled and i < len(o.teams) and val in ("1", "0"):
            out.append(("m", w, i, val == "1"))
    return out


def mask(o, picks):
    m = np.ones(o.n, dtype=bool)
    for kind, w, i, val in picks:
        if kind == "g":
            m &= o.games[w][:, i] == (1 if val == "a" else 0)
        else:
            m &= o.median[w][:, i] == bool(val)
    return m


def _share(x):
    n = int(x.size)
    if not n:
        return None, None
    p = float(x.mean())
    return p, math.sqrt(p * (1.0 - p) / n)


def conditional(o, picks, min_n=MIN_SEASONS):
    """Every team's playoff, title and seed odds over the seasons where every pick happened.
    `refused` (and every number None) when fewer than `min_n` seasons match."""
    m = mask(o, picks)
    n = int(m.sum())
    refused = n == 0 or n < min_n
    teams = {}
    for ti, t in enumerate(o.teams):
        if refused:
            teams[t] = {"playoff": None, "playoff_se": None, "champ": None, "champ_se": None, "seeds": None}
            continue
        p, se = _share(o.playoff[m, ti])
        c, cse = _share(o.champ[m] == ti)
        ranks = o.rank[m, ti]
        teams[t] = {"playoff": p, "playoff_se": se, "champ": c, "champ_se": cse,
                    "seeds": [float((ranks == r).mean()) for r in range(len(o.teams))]}
    return {"n": n, "total": o.n, "refused": refused, "min": min_n, "teams": teams}


def _lever(o, yes, no, ti, target=None):
    """A team's playoff odds when `yes` happened versus `no`; None when either side has fewer
    than MIN_SEASONS seasons."""
    target = o.playoff[:, ti] if target is None else target
    ny, nn = int(yes.sum()), int(no.sum())
    p = ny / (ny + nn) if ny + nn else None
    out = {"p": p, "n_yes": ny, "n_no": nn, "if_yes": None, "if_no": None, "diff": None, "se": None, "swing": None}
    if ny < MIN_SEASONS or nn < MIN_SEASONS:
        return out
    py, pn = float(target[yes].mean()), float(target[no].mean())
    se = math.sqrt(py * (1 - py) / ny + pn * (1 - pn) / nn)
    out.update(if_yes=py, if_no=pn, diff=py - pn, se=se, swing=2 * p * (1 - p) * (py - pn))
    return out


def leverage(o):
    """UI-O8. cells[team][week] = {"h2h": ..., "median": ...}: the team's playoff odds if it
    wins versus loses its game (beats versus misses the median), and the probability-weighted
    swing 2 x p x (1 - p) x the difference -- the expected size of the move once the result is
    known. `index` scales every swing by the season's average game swing, so the average game
    is 1.0 by construction and a median result reads on the same scale. games[week]: each
    game's summed swing across all eight teams, biggest first."""
    cells = {t: {} for t in o.teams}
    swings = []
    for w in o.weeks:
        res = o.games[w]
        for t in o.teams:
            ti = o.index[t]
            gi, side = o.game_of(t, w)
            h = None
            if gi is not None:
                h = _lever(o, res[:, gi] == side, res[:, gi] == 1 - side, ti)
                h["if_won"], h["if_lost"] = h["if_yes"], h["if_no"]
                if h["swing"] is not None:
                    swings.append(h["swing"])
            md = None
            if o.median_enabled:
                beat = o.median[w][:, ti]
                md = _lever(o, beat, ~beat, ti)
            cells[t][w] = {"h2h": h, "median": md}
    mean = sum(swings) / len(swings) if swings else 0.0
    for t in o.teams:
        for c in cells[t].values():
            for lev in (c["h2h"], c["median"]):
                if lev is not None:
                    lev["index"] = (lev["swing"] / mean) if (lev["swing"] is not None and mean > 0) else None
    games = {}
    for w in o.weeks:
        rows = []
        for gi, (a, b) in enumerate(o.matchups.get(w, [])):
            res = o.games[w][:, gi]
            ya, yb = res == 1, res == 0
            na, nb = int(ya.sum()), int(yb.sum())
            p = na / (na + nb) if na + nb else None
            total, moved = None, {}
            if na >= MIN_SEASONS and nb >= MIN_SEASONS:
                total = 0.0
                for ti, t in enumerate(o.teams):
                    d = float(o.playoff[ya, ti].mean() - o.playoff[yb, ti].mean())
                    moved[t] = d
                    total += 2 * p * (1 - p) * abs(d)
            rows.append({"week": w, "gi": gi, "a": a, "b": b, "p_a": p, "total": total, "moved": moved})
        rows.sort(key=lambda r: (r["total"] is None, -(r["total"] or 0.0)))
        games[w] = rows
    return {"cells": cells, "games": games, "mean_swing": mean}


def rooting(o, me, week):
    """UI-O9: for every other game in `week`, the owner's playoff odds if each side wins, the
    change from now and the standard error of the difference, biggest first; and, in a median
    league, each other team's result against the median."""
    out = {"games": [], "median": [], "base": None}
    if me not in o.index or week not in o.weeks:
        return out
    mi = o.index[me]
    out["base"] = float(o.playoff[:, mi].mean()) if o.n else None
    for gi, (a, b) in enumerate(o.matchups.get(week, [])):
        if me in (a, b):
            continue
        res = o.games[week][:, gi]
        lev = _lever(o, res == 1, res == 0, mi)
        row = {"gi": gi, "a": a, "b": b, "p_a": lev["p"], "if_a": lev["if_yes"], "if_b": lev["if_no"],
               "diff": lev["diff"], "se": lev["se"], "root_for": None, "change": {}}
        if lev["diff"] is not None:
            row["change"] = {a: lev["if_yes"] - out["base"], b: lev["if_no"] - out["base"]}
            row["root_for"] = a if lev["diff"] > 0 else (b if lev["diff"] < 0 else None)
        out["games"].append(row)
    out["games"].sort(key=lambda r: (r["diff"] is None, -abs(r["diff"] or 0.0)))
    if o.median_enabled:
        for t in o.teams:
            if t == me:
                continue
            beat = o.median[week][:, o.index[t]]
            lev = _lever(o, beat, ~beat, mi)
            d = lev["diff"]
            out["median"].append({"team": t, "if_beats": lev["if_yes"], "if_misses": lev["if_no"], "se": lev["se"],
                                  "diff": d, "want": None if d is None or d == 0 else ("low" if d < 0 else "high")})
        out["median"].sort(key=lambda r: (r["diff"] is None, -abs(r["diff"] or 0.0)))
    return out


def final_wins(o, banked):
    """(seasons x teams) final regular-season wins: banked + each remaining game (a tie is a
    half) + each median result. None when any team's banked wins are unknown."""
    if not o.teams or any(banked.get(t) is None for t in o.teams):
        return None
    w = np.array([float(banked[t]) for t in o.teams])[None, :].repeat(o.n, axis=0)
    for wk in o.weeks:
        res = o.games[wk]
        for gi, (a, b) in enumerate(o.matchups.get(wk, [])):
            r = res[:, gi]
            w[:, o.index[a]] += np.where(r == 1, 1.0, np.where(r == 2, 0.5, 0.0))
            w[:, o.index[b]] += np.where(r == 0, 1.0, np.where(r == 2, 0.5, 0.0))
        if o.median_enabled:
            w += o.median[wk]
    return w


def wins_curve(o, banked, team):
    """UI-O6: for each final win total the team reached in some season, the share of those
    seasons in which it made the playoffs, the count behind it and its standard error; `thin`
    below MIN_SEASONS. [] without banked wins for every team."""
    if team not in o.index:
        return []
    fw = final_wins(o, banked or {})
    if fw is None:
        return []
    ti = o.index[team]
    col = fw[:, ti]
    out = []
    for k in np.unique(col):
        sel = col == k
        p, se = _share(o.playoff[sel, ti])
        out.append({"wins": float(k), "n": int(sel.sum()), "p": p, "se": se, "thin": int(sel.sum()) < MIN_SEASONS,
                    "share": float(sel.mean())})
    return out


def markers(o, banked):
    """UI-O10. `label` is "clinched" or "eliminated" only when a bound on the remaining schedule
    proves it -- ties in wins count against the team, because the points tiebreak is still to
    be played -- and otherwise "over 99.9%" or "under 0.1%" when the simulated frequency is
    that extreme over at least NEAR_CERTAIN_MIN seasons. `destiny`: if the team wins every
    remaining game and beats every remaining median, fewer than four others can still match
    its total -- again a bound, not a frequency. Nothing is proven without banked wins."""
    out = {}
    have = bool(o.teams) and all(banked.get(t) is not None for t in o.teams)
    games = {t: 0 for t in o.teams}
    vs = {t: {u: 0 for u in o.teams} for t in o.teams}
    for w in o.weeks:
        for a, b in o.matchups.get(w, []):
            if a in games and b in games:
                games[a] += 1
                games[b] += 1
                vs[a][b] += 1
                vs[b][a] += 1
    med = len(o.weeks) if o.median_enabled else 0
    lo = {t: float(banked[t]) for t in o.teams} if have else {}
    hi = {t: lo[t] + games[t] + med for t in o.teams} if have else {}
    for ti, t in enumerate(o.teams):
        freq = float(o.playoff[:, ti].mean()) if o.n else None
        clinched = eliminated = destiny = None
        if have:
            others = [u for u in o.teams if u != t]
            clinched = sum(1 for u in others if hi[u] >= lo[t]) < PLAYOFF_SPOTS
            eliminated = sum(1 for u in others if lo[u] > hi[t]) >= PLAYOFF_SPOTS
            destiny = sum(1 for u in others if hi[u] - vs[u][t] >= hi[t]) < PLAYOFF_SPOTS
        if clinched:
            label = "clinched"
        elif eliminated:
            label = "eliminated"
        elif freq is not None and o.n >= NEAR_CERTAIN_MIN and freq >= NEAR_CERTAIN:
            label = "over 99.9%"
        elif freq is not None and o.n >= NEAR_CERTAIN_MIN and freq <= 1 - NEAR_CERTAIN:
            label = "under 0.1%"
        else:
            label = None
        out[t] = {"label": label, "destiny": destiny, "proven_in": clinched, "proven_out": eliminated, "freq": freq}
    return out


# ------------------------------------------------------------------------------ the page
def _qs(picks, extra=None):
    from urllib.parse import urlencode
    q = [((f"g{w}.{i}" if k == "g" else f"m{w}.{i}"), (v if k == "g" else ("1" if v else "0"))) for k, w, i, v in picks]
    return urlencode(q + list((extra or {}).items()))


def describe(o, pick):
    """A pin as the page shows it: who won, or who beat or missed the median."""
    kind, w, i, v = pick
    if kind == "g":
        a, b = o.matchups[w][i]
        win, lose = (a, b) if v == "a" else (b, a)
        return {"kind": "g", "week": w, "winner": win, "loser": lose}
    return {"kind": "m", "week": w, "team": o.teams[i], "beat": bool(v)}


def report(root, args, me):
    """Everything /playoffs shows. `available` False when no export exists at or before the
    sync week -- the forecasts on file predate the export, and the next one writes it."""
    from webui.glance import freshness_report, odds_at
    from webui.results import week_results
    sync = freshness_report(root).get("week")
    sync = int(sync) if sync else None
    o = load(root, at_most=sync)
    if o is None or not o.n:
        return {"available": False}
    # The same run writes the forecast file beside the export; an export whose teams are not
    # that forecast's (a leaked test fixture, a half-written run) is not this league's.
    odds = odds_at(root, o.week)
    if not odds or set(odds) != set(o.teams):
        return {"available": False}
    args = dict(args)
    picks = picks_from_args(o, args)
    base = conditional(o, [])
    res = conditional(o, picks) if picks else base
    banked = {t: (v or {}).get("banked") for t, v in odds.items()}
    this_week = sync if sync in o.weeks else (o.weeks[0] if o.weeks else None)
    lev = leverage(o)
    team = args.get("team") if args.get("team") in o.index else (me if me in o.index else o.teams[0])
    played = week_results(root)
    presets = []
    if this_week is not None:
        fav, dog = [], []
        for gi, (a, b) in enumerate(o.matchups.get(this_week, [])):
            r = o.games[this_week][:, gi]
            favourite_a = (r == 1).sum() >= (r == 0).sum()
            fav.append(("g", this_week, gi, "a" if favourite_a else "b"))
            dog.append(("g", this_week, gi, "b" if favourite_a else "a"))
        presets += [{"key": "fav", "label": f"Favourites win week {this_week}", "href": "/playoffs?" + _qs(fav)},
                    {"key": "chaos", "label": f"Upsets everywhere in week {this_week}", "href": "/playoffs?" + _qs(dog)}]
    if me in o.index:
        mine = [("g", w, gi, "a" if side == 1 else "b") for w in o.weeks for gi, side in [o.game_of(me, w)] if gi is not None]
        if mine:
            presets.append({"key": "mine", "label": "wins out", "team": me, "href": "/playoffs?" + _qs(mine)})
    for w in o.weeks:
        rows = played.get(w) or {}
        if sync is None or w >= sync or not rows:
            continue
        pins = []
        for gi, (a, b) in enumerate(o.matchups.get(w, [])):
            ra, rb = (rows.get(a) or {}).get("h2h_win"), (rows.get(b) or {}).get("h2h_win")
            if ra is not None and rb is not None and float(ra) != float(rb):
                pins.append(("g", w, gi, "a" if float(ra) > float(rb) else "b"))
        if o.median_enabled:
            for ti, t in enumerate(o.teams):
                mw = (rows.get(t) or {}).get("median_win")
                if mw is not None:
                    pins.append(("m", w, ti, bool(float(mw))))
        if pins:
            presets.append({"key": "played", "label": f"Week {w} as it was played", "href": "/playoffs?" + _qs(pins)})
    order = sorted(o.teams, key=lambda t: -(base["teams"][t]["playoff"] or 0.0))
    return {"available": True, "o": o, "week": o.week, "sync": sync, "behind": (sync - o.week) if sync else 0,
            "picks": [dict(describe(o, p), drop="/playoffs?" + _qs([q for q in picks if q != p])) for p in picks],
            "chosen": {(f"g{w}.{i}" if k == "g" else f"m{w}.{i}"): (v if k == "g" else ("1" if v else "0")) for k, w, i, v in picks},
            "qs": _qs(picks), "base": base, "res": res, "order": order, "marks": markers(o, banked),
            "lev": lev, "this_week": this_week, "rooting": rooting(o, me, this_week) if this_week is not None else None,
            "team": team, "curve": wins_curve(o, banked, team), "presets": presets, "me": me,
            "game_p": {f"{g['week']}.{g['gi']}": g["p_a"] for rows in lev["games"].values() for g in rows},
            "min": MIN_SEASONS, "near_min": NEAR_CERTAIN_MIN}

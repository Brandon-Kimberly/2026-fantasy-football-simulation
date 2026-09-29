"""webui.accuracy -- the model's own track record (docs/WEB_UI.md W17).

Every probability this project quotes is a count, every constant cites a source, and the
season's success criteria were hashed before a game was played. None of that was ever
shown back. This module scores what the model actually said against what actually
happened, from two files already on disk: the predictions log and the weekly actuals.

TWO RULES MAKE IT HONEST RATHER THAN FLATTERING.

Only the QUOTED forecast counts: the newest COMMITTED row logged BEFORE the week's first
kickoff. A row logged after the games began knows too much to be scored, and a week whose
only committed row came later is skipped and said to be skipped -- never quietly replaced
with a row that had the benefit of hindsight. A week with no result yet is not scored.

Everything after that is counting. Brier and hit rate on the matchup calls; bias, absolute
error and z on the points (the record carries `sd_total`, so z is the model's own spread,
not one invented here); the same treatment for the beat-the-median calls.

Nothing here is a verdict. `ENOUGH_WEEKS` marks where the quoted-versus-realised read
first means anything (AUDIT_PLAN F25: weeks 5-6), and until then the page leads with how
thin the sample is.
"""
import datetime as _dt
import math

ENOUGH_WEEKS = 5        # F25: the calibration read is first measurable at weeks 5-6
Z80 = 1.2816            # |z| inside this is the middle 80% of a Normal


def _parse(t):
    try:
        return _dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _first_kickoff(root):
    """{week: the earliest kickoff datetime} from the synced schedule."""
    sched = root.read_json("current/nfl_schedule.json", {}) or {}
    out = {}
    for week, stamps in ((sched.get("_meta") or {}).get("kickoffs") or {}).items():
        times = [d for d in (_parse(s) for s in (stamps or [])) if d]
        if times:
            try:
                out[int(week)] = min(times)
            except (TypeError, ValueError):
                continue
    return out


def _prediction_rows(root):
    rows = []
    for e in root.logs():
        if e["name"].startswith("predictions_") and e["ext"] == "jsonl":
            got, _n = root.tail_jsonl(e["rel"], n=1000000)
            rows.extend(got)
    return [r for r in rows if isinstance(r, dict) and r.get("record_type") == "week_predictions"]


def quoted(rows, week, kickoff):
    """The newest COMMITTED row for `week` logged before `kickoff` -- the last thing the
    model said while it could still be wrong. None when there is no such row."""
    if kickoff is None:
        return None
    best = None
    for r in rows:
        if str(r.get("week")) != str(week) or not r.get("canonical"):
            continue
        at = _parse(r.get("logged_at"))
        if at is None or at >= kickoff:
            continue
        if best is None or at > _parse(best.get("logged_at")):
            best = r
    return best


def quoted_week(root, week):
    """UI-Q2: THE forecast the model quoted for `week` -- the same row report() scores --
    so a page showing "what the model said at the time" can never disagree with Accuracy."""
    try:
        wk = int(week)
    except (TypeError, ValueError):
        return None
    return quoted(_prediction_rows(root), wk, _first_kickoff(root).get(wk))


def chances_in(row, team):
    """{h2h, median}: `team`'s quoted chance to win its game and to beat the median, from one
    quoted row; None when there is no row."""
    if not row:
        return None
    h2h = None
    for m in row.get("matchups") or []:
        if m.get("a") == team:
            h2h = m.get("p_a")
        elif m.get("b") == team:
            h2h = m.get("p_b")
    med = ((row.get("median") or {}).get(team) or {}).get("p_beat_median")
    return {"h2h": h2h, "median": med}


def quoted_chances(root, week, team):
    return chances_in(quoted_week(root, week), team)


def _sd(values):
    """Sample standard deviation; None below two values."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def report(root):
    """What the model quoted, against what happened. See the module docstring for the two
    rules; everything here is counting, and nothing is written."""
    rows = _prediction_rows(root)
    kicks = _first_kickoff(root)
    from webui.results import week_results
    by_week = week_results(root)          # results as the league counts them (F83): as played
    weeks, skipped = [], []
    calls, hits, briers = 0, 0, []
    errors, zs, med_calls, med_hits, med_briers = [], [], 0, 0, []

    for week in sorted({int(r["week"]) for r in rows if str(r.get("week", "")).isdigit()}):
        results = by_week.get(week) or {}
        if not results:
            skipped.append({"week": week, "why": "no result on file yet"})
            continue
        row = quoted(rows, week, kicks.get(week))
        if row is None:
            skipped.append({"week": week, "why": "no committed forecast logged before the first kickoff"})
            continue
        w = {"week": week, "at": row.get("logged_at"), "matchups": [], "teams": []}
        for m in row.get("matchups") or []:
            a, b, p = m.get("a"), m.get("b"), m.get("p_a")
            won = (results.get(a) or {}).get("h2h_win")
            if p is None or won is None:
                continue
            p, won = float(p), float(won)
            briers.append((p - won) ** 2)
            call = None if abs(p - 0.5) < 1e-9 else (p > 0.5)
            hit = None if call is None else (call == (won >= 0.5))
            if call is not None:
                calls += 1
                hits += 1 if hit else 0
            w["matchups"].append({"a": a, "b": b, "p": round(p, 4), "won": won >= 0.5, "hit": hit})
        for team, q in (row.get("median") or {}).items():
            got = results.get(team) or {}
            actual, expected, sd = got.get("points_scored"), q.get("expected_total"), q.get("sd_total")
            if actual is None or expected is None:
                continue
            err = float(actual) - float(expected)
            z = (err / float(sd)) if sd else None
            errors.append(err)
            if z is not None:
                zs.append(z)
            pm, beat = q.get("p_beat_median"), got.get("median_win")
            if pm is not None and beat is not None:
                pm, beat = float(pm), float(beat)
                med_briers.append((pm - beat) ** 2)
                if abs(pm - 0.5) > 1e-9:
                    med_calls += 1
                    med_hits += 1 if ((pm > 0.5) == (beat >= 0.5)) else 0
            w["teams"].append({"team": team, "expected": round(float(expected), 1), "actual": round(float(actual), 1),
                               "error": round(err, 1), "z": None if z is None else round(z, 2),
                               "p_median": None if pm is None else round(float(pm), 4),
                               "beat": None if beat is None else beat >= 0.5})
        w["teams"].sort(key=lambda t: -abs(t["error"]))
        weeks.append(w)

    n_weeks = len(weeks)
    sd_z = _sd(zs)
    return {
        "weeks": weeks, "skipped": skipped, "n_weeks": n_weeks,
        "matchups": {"n": len(briers), "calls": calls, "hits": hits,
                     "rate": round(hits / calls, 4) if calls else None,
                     "brier": round(sum(briers) / len(briers), 6) if briers else None},
        "points": {"n": len(errors),
                   "bias": round(sum(errors) / len(errors), 2) if errors else None,
                   "mae": round(sum(abs(e) for e in errors) / len(errors), 2) if errors else None,
                   "mean_z": round(sum(zs) / len(zs), 4) if zs else None,
                   "sd_z": None if sd_z is None else round(sd_z, 4),
                   "within80": round(sum(1 for z in zs if abs(z) <= Z80) / len(zs), 4) if zs else None},
        "median": {"n": len(med_briers), "calls": med_calls, "hits": med_hits,
                   "rate": round(med_hits / med_calls, 4) if med_calls else None,
                   "brier": round(sum(med_briers) / len(med_briers), 6) if med_briers else None},
        "enough": n_weeks >= ENOUGH_WEEKS,
        "note": (f"{n_weeks} week{'s' if n_weeks != 1 else ''} scored. A read on how well these "
                 f"probabilities are calibrated first means something at week {ENOUGH_WEEKS} or "
                 f"{ENOUGH_WEEKS + 1}; until then these are counts, not conclusions."),
    }


def backtest_read(root):
    """UI-Q4: the points backtest's own figure, read from the newest line of its log
    (data/logs/points_backtest.jsonl) -- never a number copied into a page or a doc, which is
    how docs/LUCK_LEDGER.md came to cite an older run's 0.654. The 80% band's coverage, its
    sample, the checkpoints it was forecast from, and the standard error a coverage rate has
    at that sample if the band were exactly right. None when no run is logged."""
    try:
        rows, _n = root.tail_jsonl("logs/points_backtest.jsonl", 1)
    except (FileNotFoundError, ValueError):
        return None
    row = rows[0] if rows and isinstance(rows[0], dict) else None
    overall = (row or {}).get("overall") or {}
    if overall.get("cover80") is None or not overall.get("n"):
        return None
    n = int(overall["n"])
    return {"cover80": float(overall["cover80"]), "n": n, "checkpoints": row.get("checkpoints") or [],
            "at": row.get("timestamp_utc"), "commit": (row.get("git_commit") or "")[:7],
            "se": (0.8 * 0.2 / n) ** 0.5}


def reliability(pairs, edges=None):
    """UI-Q1 characterisation stub."""
    return []
